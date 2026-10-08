# Route Builder (privacy-first, local-first)

Describe a walk or run in plain language. A local model turns it into structured constraints, a real routing engine
builds several candidate routes, OpenStreetMap data is used to estimate traffic, shade and surface, optional air-quality
data is added, and routes are scored with transparent weights and shown on a map.

```
prompt -> Ollama (JSON constraints) -> Valhalla (8 real candidates) -> OSM analysis -> AQI -> scoring -> top 3 -> map
```

The LLM only produces the constraints JSON. It never produces coordinates, roads, distances or AQI values.

## What works today (and what does not)

| Area | Status |
|---|---|
| Constraint parsing (Ollama, schema-constrained JSON, keyword fallback) | Done |
| Valhalla routing: loops, out-and-back, point-to-point, "finish near a park / metro / cafe" | Done |
| 3 alternatives: Best match, Lowest pollution, Quietest (same candidate pool, different weights) | Done |
| OSM-based traffic, surface and shade **estimates** | Done, see limits below |
| AQI (Open-Meteo, modelled) with freshness and missing-data handling | Done, see limits below |
| Elevation gain (needs elevation data in Valhalla) | Done if Valhalla was built with elevation, otherwise skipped with a warning |
| Geolocation and map click for start | Done |
| Typed start address (needs a geocoder) | Not built. It would send text to a third party. |
| Local route history and "delete my data" | Not built. Nothing is stored: results live in memory and expire. |
| Map layers for parks and AQI sensors | Not built |

The automated tests use a fake router, so the full pipeline has been tested end to end except against a live Valhalla,
Ollama, Overpass and Open-Meteo. Run it once against real services (steps below) before relying on it.

## Quick start (run services individually)

1. **Ollama** (host): install from https://ollama.com, then `ollama pull qwen2.5:3b`. Any model that supports structured output works; set `OLLAMA_MODEL`.
2. **Choose your map region (one command, no manual editing)**: `python scripts/setup_region.py`
   It asks where you will use the app (a place name or `lat,lon`), finds the smallest Geofabrik extract that contains it,
   writes `VALHALLA_PBF_URL` to `.env` and records the region's bounding box in `data/region.json`.
   Non-interactive: `--lat 48.85 --lon 2.35`, `--place "Lisbon"` (sends the place name to Nominatim), or `--pbf-url <extract>`.
   Moving to another region later: run it again. It asks before deleting old routing tiles. Add `--start` to launch Valhalla.
3. **Valhalla**: `docker compose up valhalla`. The first build downloads the extract and builds tiles (10+ minutes, several GB RAM for large regions).
   Check: `curl localhost:8002/status` (use `curl.exe` in PowerShell).
4. **Backend**
   ```bash
   cp .env.example .env
   cd backend && python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload      # http://localhost:8000/api/health
   ```
5. **Frontend**: `cd frontend && npm install && npm run dev`, open http://localhost:5173, click the map **inside your loaded region**, type a request, press Build route.

## Full local deployment

```bash
python scripts/setup_region.py   # choose your map region once
docker compose up --build   # UI on http://localhost:8080
```
Ollama stays on the host (reached through `host.docker.internal`).

## Example prompts

- 5 km, shaded, low traffic, finishing near a park
- 3 km easy walk, avoid busy roads and highways
- 8 km running route with the least pollution possible
- 5 km loop, mostly shaded, avoid steep roads
- 4 km route starting here and ending near a metro station

If the request is ambiguous (for example no distance), the app asks a question instead of guessing.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/parse-request` | `{prompt}` -> constraints JSON |
| `POST /api/routes` | `{prompt, start:{lat,lon}}` -> up to 3 scored routes, warnings, clarification |
| `GET /api/route/{id}` | A recent route (in-memory cache) |
| `GET /api/air-quality?lat=&lon=` | AQI summary for a point |
| `GET /api/health` | Ollama and Valhalla status |

Errors use `{"error": {"code", "message"}}`: `routing_unavailable`, `no_route`, `llm_unavailable`, `llm_invalid_json`,
`missing_start`, `impossible_distance`, `no_destination`, `osm_unavailable`, `invalid_request`.

## Scoring (edit `backend/app/services/scoring/scorer.py`)

Components are 0 to 1, higher is better: distance, shade, traffic (1 = quiet), air quality, surface, elevation.
Base weights: distance 0.25, shade 0.20, traffic 0.20, air 0.15, surface 0.10, elevation 0.10.
"Best match" multiplies a weight when you ask for it (for example "mainly pollution" raises air quality to double weight; "car-free" raises traffic to 2.5x).
Missing components (no AQI, no elevation) are removed and the rest re-normalised, so missing data never counts as good or bad.
"Avoid highways / major roads" applies a penalty up to 50% based on the estimated share of major roads.
Every route returns the weights actually used.

## Honest limits of the data

- **Traffic** is an estimate from OSM road class (motorway ... footway). There are no traffic counts. Sidewalks next to busy roads inherit part of that road's level.
- **Shade** is estimated from mapped trees, tree rows, wooded areas and parks. OSM tree coverage is incomplete, so the app marks shade as low confidence when few trees are mapped. Parks or woods mapped only as multipolygon relations are not counted.
- **AQI** comes from Open-Meteo's air-quality model (about 10 km grid), not street-level sensors, so it barely differs between routes in one city. When it is nearly identical, "Lowest pollution" is decided by road exposure and the explanation says so. Swap in OpenAQ station data in `services/aqi/` for sensor readings (needs an API key).
- Wording is "estimated traffic", never "safe".

## What leaves your machine

| Request | Sent to | Contents |
|---|---|---|
| Parsing | Ollama on your host | Your prompt (stays local) |
| Routing | Valhalla in Docker | Coordinates (stays local) |
| Map features | Overpass (`OVERPASS_URL`) | A bounding box around the candidate routes, plus a destination search around your start point |
| Air quality | Open-Meteo | Coordinates rounded to about 2 km, along the routes. Set `AQI_ENABLED=false` to turn off |
| Map tiles | tile.openstreetmap.org | Tile requests for the area you view. Set `VITE_TILE_URL` to a self-hosted tile server |

To make it fully offline, self-host Overpass (or replace `osm/analyzer.py` with a local PBF reader), disable AQI and self-host tiles.
No analytics, no fonts from a CDN, and nothing is written to disk by the backend.

## Tests

```bash
cd backend && pytest
```
Covers the parser, scorer, geometry, OSM analysis, AQI freshness and the API (valid, malformed, missing location, routing failure, LLM failure).

## Layout

```
backend/app/{api,services/{llm,routing,osm,aqi,scoring},planner.py,schemas.py}
frontend/src/{components,pages,services,hooks,types}
docker-compose.yml  .env.example
```
Routing is behind `ValhallaRouter`'s `route()` interface, so an OSRM client can replace it. Note that OSRM has no per-request costing, so "quietest" would need a different approach.

A start point outside the loaded region returns `outside_region` with the fix, instead of a generic routing error.
