"""Air quality from Open-Meteo (free, no key). NOTE: this is MODELLED data on a ~10 km grid, not street-level sensors.

Privacy: coordinates are rounded to ~2 km before leaving the machine, and sent in one request.
"""
from datetime import datetime, timezone

import httpx

from ...geometry import LatLng, resample

POOR_AQI = 100  # US AQI above this = unhealthy for sensitive groups
SOURCE = "Open-Meteo air quality (modelled, ~10 km grid)"


class AqiUnavailable(Exception):
    pass


def _key(p: LatLng) -> tuple[float, float]:
    return round(p[0] * 50) / 50, round(p[1] * 50) / 50


def sample_keys(coords: list[LatLng]) -> list[tuple[float, float]]:
    return list(dict.fromkeys(_key(p) for p in resample(coords, 1000)))


async def fetch_grid(routes: list[list[LatLng]], url: str, client: httpx.AsyncClient | None = None) -> dict:
    keys = list(dict.fromkeys(k for r in routes for k in sample_keys(r)))[:40]
    params = {"latitude": ",".join(str(k[0]) for k in keys), "longitude": ",".join(str(k[1]) for k in keys), "current": "us_aqi"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise AqiUnavailable(str(e)) from e
    finally:
        if own:
            await client.aclose()
    items = data if isinstance(data, list) else [data]
    grid = {}
    for k, it in zip(keys, items):
        cur = it.get("current") or {}
        if cur.get("us_aqi") is not None:
            grid[k] = {"aqi": float(cur["us_aqi"]), "time": cur["time"]}
    if not grid:
        raise AqiUnavailable("no AQI values returned")
    return grid


def summarize(coords: list[LatLng], grid: dict, max_age_min: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    vals = [grid[k] for k in sample_keys(coords) if k in grid]
    if not vals:
        return {"status": "unavailable", "source": SOURCE}
    aqis = [v["aqi"] for v in vals]
    newest = max(datetime.fromisoformat(v["time"]).replace(tzinfo=timezone.utc) for v in vals)
    age = (now - newest).total_seconds() / 60
    return {
        "status": "stale" if age > max_age_min else "ok",
        "avg": round(sum(aqis) / len(aqis), 1), "max": max(aqis),
        "pct_poor": round(sum(a > POOR_AQI for a in aqis) / len(aqis), 2),
        "age_minutes": round(age), "points": len(aqis), "source": SOURCE,
    }
