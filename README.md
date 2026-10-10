# StrideWise

**Describe a walk or run in plain English and get a real route you can follow.**

> "5 km, shaded, low traffic, finishing near a park"

A small language model running on your own machine turns that sentence into structured constraints. A real routing engine builds several candidate routes. OpenStreetMap data is used to estimate traffic, shade and surface. Optional air-quality data is added. The routes are scored with transparent weights, and the top three are shown on a map. Then you close the laptop and go outside.

Built for the Hacktoberfest "Touch Grass" challenge: open-source AI that gets people off the screen.

<!-- Add a screenshot here: ![StrideWise screenshot](docs/screenshot.png) -->

---

## How it works

```
Your sentence
   -> Ollama (local LLM)  -> structured JSON constraints (distance, shade, traffic, ...)
   -> Valhalla (routing)  -> about 8 real candidate routes (loops, out-and-back, point-to-point)
   -> OpenStreetMap       -> traffic / shade / surface estimates along each route
   -> Open-Meteo (opt.)   -> air-quality along each route
   -> Scorer              -> weighted, explainable ranking
   -> Map                 -> Best match, Lowest pollution, Quietest
```

The language model has one job: turn text into JSON. It never produces coordinates, roads, distances or air-quality values. Routing, map analysis and scoring are ordinary code, so results are testable and the model cannot invent geography.

## Features

- Natural-language requests: distance (exact, approximate, minimum, maximum), walk / run / cycle, loop / out-and-back / point-to-point, terrain, traffic, shade, surface, air quality.
- Destinations: "finish near a park / metro station / cafe", or end where you started.
- Three alternatives per request: **Best match**, **Lowest pollution**, **Quietest**.
- Every route shows its metrics, the weights used, and a plain-language "Why this route?".
- Asks a question instead of guessing when the request is unclear (for example, no distance).
- Falls back to a built-in keyword parser if Ollama is not reachable, and says so in the UI.
- Region setup in one command, and a clear error if your start point is outside the loaded map.
- Nothing is stored: results live in memory only and expire.

## Quick start

You need: Docker, Python 3, Node.js 18+, and [Ollama](https://ollama.com).

**1. Language model**

```bash
ollama pull qwen2.5:3b
```

Any Ollama model that supports structured output works. Set `OLLAMA_MODEL` in `.env` to use another.

**2. Choose your map region (once, no manual editing)**

```bash
python3 scripts/setup_region.py
```

(On Windows use `python`. Run it as a single line and do not paste a `#` comment after it.)

It asks where you will use the app (a place name or `lat,lon`), finds the smallest Geofabrik extract that contains it, writes `VALHALLA_PBF_URL` into `.env`, and saves the region's bounding box to `data/region.json`.
Other ways to run it: `--lat 28.5 --lon 77.4`, `--place "Lisbon"` (the place name is sent to Nominatim), or `--pbf-url <extract URL>`. Run it again to switch regions. It asks before deleting old routing tiles.

**3. Start the routing engine**

```bash
docker compose up valhalla
```

The first start downloads the map and builds routing tiles. For a large region (a few hundred MB) this can take 10 to 30 minutes and several GB of RAM. It is ready when `curl localhost:8002/status` returns JSON (use `curl.exe` in PowerShell).

**4. Backend**

```bash
cp .env.example .env     # skip if setup_region.py already created it
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Check `http://localhost:8000/api/health`. You want `"valhalla": true` and `"ollama": true`.

**5. Frontend**

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173, click the map inside your loaded region (or press "Use my location"), describe your route including a distance, and press **Build route**.

**Everything in Docker instead:** run `python scripts/setup_region.py`, then `docker compose up --build` and open http://localhost:8080. Ollama stays on the host.

## Example prompts

- `5 km, shaded, low traffic, finishing near a park`
- `3 km easy walk, avoid busy roads and highways`
- `8 km run with the least pollution possible`
- `5 km loop, mostly shaded, avoid steep roads`
- `4 km route starting here and ending near a metro station`

Always include a distance. Without one, the app asks "How far would you like to go?".

## How routes are scored

Each component is 0 to 1 (higher is better): distance, shade, traffic (1 = quiet), air quality, surface, elevation.

| Component | Base weight |
|---|---|
| Distance | 0.25 |
| Shade | 0.20 |
| Traffic | 0.20 |
| Air quality | 0.15 |
| Surface | 0.10 |
| Elevation | 0.10 |

"Best match" raises the weights of what you asked for (for example, "car-free" multiplies the traffic weight). "Lowest pollution" and "Quietest" use their own weight profiles. If a data source is missing (no AQI, no elevation), its component is removed and the other weights are re-normalised, so missing data never counts as good or bad. "Avoid highways / major roads" applies a penalty of up to 50% based on the estimated share of major roads. Every route returns the weights actually used. To change behaviour, edit `backend/app/services/scoring/scorer.py`.

## Honest limits

- **Traffic** is an estimate from OpenStreetMap road class. There are no traffic counts. Sidewalks next to busy roads inherit part of that road's level.
- **Shade** is estimated from mapped trees, tree rows, wooded areas and parks. OSM tree coverage is incomplete, so shade is marked low confidence when few trees are mapped.
- **Air quality** comes from Open-Meteo's model (about 10 km grid), not street-level sensors, so it barely differs between routes in one city. When it is nearly identical, "Lowest pollution" is decided by road exposure, and the explanation says so.
- **Elevation** only works if Valhalla was built with elevation data. Otherwise it is skipped with a warning.
- **Coverage:** routing works only inside the region you loaded. That is the cost of private, local routing.
- Wording is "lower estimated traffic", never "safe".
- Not built yet: typing a start address (needs a geocoder), saved route history with a delete button, and map layers for parks and AQI sensors.

## Privacy: what leaves your machine

| Step | Sent to | What |
|---|---|---|
| Understanding your sentence | Ollama on your machine | Your prompt (stays local) |
| Routing | Valhalla in Docker | Coordinates (stays local) |
| Road and tree data | Overpass (public servers, tried in order) | Route coordinates about 120 m apart (so, your start area), and a bounding box for parks |
| Air quality | Open-Meteo | Coordinates rounded to about 2 km. Set `AQI_ENABLED=false` to turn off |
| Map tiles | tile.openstreetmap.org | Tile requests for the area you view. Set `VITE_TILE_URL` for a self-hosted tile server |
| Region setup (once) | Geofabrik, optionally Nominatim | Region list download. Nominatim only if you use `--place` |

No analytics, no CDN fonts, and the backend writes nothing to disk. For fully offline use, self-host Overpass and a tile server and set `AQI_ENABLED=false`.

## Configuration

Copy `.env.example` to `.env`. The main settings:

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Where Ollama runs |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Model used to parse requests |
| `PARSER_MODE` | `auto` | `auto` = Ollama then keyword fallback, `llm` = Ollama only, `rules` = keywords only |
| `VALHALLA_URL` | `http://localhost:8002` | Routing engine |
| `OVERPASS_URL` | built-in list | Comma-separated Overpass servers |
| `AQI_ENABLED` | `true` | Use Open-Meteo air quality |
| `ELEVATION_ENABLED` | `true` | Score hills if Valhalla has elevation data |

Do not edit `VALHALLA_PBF_URL` by hand. `scripts/setup_region.py` manages it.

## Troubleshooting

| What you see | Meaning | Fix |
|---|---|---|
| "How far would you like to go?" | Your request had no distance | Add one, for example `5 km` |
| "Local LLM unavailable (llm_unavailable); used the built-in keyword parser" | Ollama is not reachable | Start Ollama, run `ollama pull qwen2.5:3b`, check `/api/health` shows `"ollama": true`. Docker backend: set `OLLAMA_URL=http://host.docker.internal:11434` |
| "No route found ... outside the loaded map area" or `outside_region` | Your start point is outside the map Valhalla loaded | Click inside your region, or run `setup_region.py` for your area |
| `curl localhost:8002/status` returns an empty reply | Valhalla is still downloading or building | `docker compose logs -f valhalla` and wait |
| `unable to read blob from file` in Valhalla logs | Corrupt or partial map download | `docker compose down`, delete `data/valhalla`, download the `.osm.pbf` with `curl -L -C -`, start again |
| "OpenStreetMap road data could not be loaded (...)" | Overpass servers are busy or unreachable. The reason is in the brackets | Wait a minute and retry. Until it is fixed, traffic, shade and surface are not used in ranking |
| `CERTIFICATE_VERIFY_FAILED` in `setup_region.py` (macOS) | Python has no trusted certificates | `open "/Applications/Python 3.12/Install Certificates.command"` (use your version's folder), or `pip3 install certifi` |
| `UnicodeDecodeError` in `setup_region.py` (Windows) | An older script version read files with the wrong encoding | Use the latest script (it reads UTF-8) |

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/parse-request` | `{prompt}` returns constraints JSON |
| `POST /api/routes` | `{prompt, start:{lat,lon}}` returns up to 3 scored routes, warnings, or a clarification question |
| `GET /api/route/{id}` | A recent route (in-memory cache) |
| `GET /api/air-quality?lat=&lon=` | AQI summary for a point |
| `GET /api/health` | Ollama, Valhalla and loaded region status |

Errors look like `{"error": {"code", "message"}}`. Codes include `routing_unavailable`, `no_route`, `llm_unavailable`, `llm_invalid_json`, `missing_start`, `impossible_distance`, `no_destination`, `osm_unavailable`, `outside_region`, `invalid_request`.

## Tests

```bash
cd backend
pytest
```

Covers the constraint parser, scorer, geometry, OSM analysis, Overpass fallback, AQI freshness, region setup, and the API (valid and malformed requests, missing location, routing failure, LLM failure). The tests use a fake router, so run the app once against real Valhalla and Ollama to confirm your setup.

## Project layout

```
backend/app/{api, services/{llm, routing, osm, aqi, scoring}, planner.py, schemas.py}
frontend/src/{components, pages, services, hooks, types}
scripts/setup_region.py
docker-compose.yml   .env.example
```

Each service is behind its own module. Routing sits behind `ValhallaRouter`, so an OSRM client can replace it (OSRM has no per-request costing, so "quietest" would need a different approach).

## Built with

[Ollama](https://ollama.com) and an open-weight model (Qwen2.5 by default), [Valhalla](https://github.com/valhalla/valhalla), [OpenStreetMap](https://www.openstreetmap.org) data via Overpass and [Geofabrik](https://download.geofabrik.de), [Open-Meteo](https://open-meteo.com) air quality, FastAPI, React, Vite, Tailwind CSS and Leaflet.

Map data © OpenStreetMap contributors.

## License

Add your license here (for example MIT) and a `LICENSE` file.
