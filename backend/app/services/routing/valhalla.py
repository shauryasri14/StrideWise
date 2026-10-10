"""Valhalla routing client. Swap this module for an OSRM client with the same interface."""
from dataclasses import dataclass

import httpx

from ...errors import AppError
from ...geometry import LatLng, decode_polyline

# Lower factors = Valhalla prefers that way type more.
STYLES = {
    "balanced": {},
    "quiet": {"walkway_factor": 0.6, "sidewalk_factor": 0.8, "use_living_streets": 1.0},
}
HILLS = {"any": 0.5, "flat": 0.0, "moderate": 0.5, "hilly": 1.0}
SPEED_KMH = {"walk": 5.1, "run": 9.0}


@dataclass
class RoutedPath:
    coords: list[LatLng]
    distance_km: float
    duration_min: float


class ValhallaRouter:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30)

    async def route(self, waypoints: list[LatLng], activity: str = "walk", style: str = "balanced", terrain: str = "any") -> RoutedPath:
        if activity == "cycle":
            costing, opts = "bicycle", {"use_hills": HILLS[terrain]}
        else:
            opts = {"use_hills": HILLS[terrain], "walking_speed": SPEED_KMH[activity], **STYLES[style]}
            costing = "pedestrian"
        body = {
            "locations": [{"lat": a, "lon": b, "type": "break" if i in (0, len(waypoints) - 1) else "through"} for i, (a, b) in enumerate(waypoints)],
            "costing": costing,
            "costing_options": {costing: opts},
            "directions_options": {"units": "kilometers"},
        }
        try:
            r = await self._client.post(f"{self.base_url}/route", json=body)
        except httpx.HTTPError as e:
            raise AppError(503, "routing_unavailable", f"The routing engine (Valhalla) is not reachable at {self.base_url}. If you just started it, it may still be building map tiles, which takes 10 to 30 minutes for a large region. Check progress with: docker compose logs -f valhalla") from e
        if r.status_code == 400:
            raise AppError(422, "no_route", "No route found. The start point may be outside the loaded map area.")
        if r.status_code >= 500:
            raise AppError(503, "routing_unavailable", f"Valhalla returned HTTP {r.status_code}.")
        trip = r.json()["trip"]
        coords: list[LatLng] = []
        for leg in trip["legs"]:
            pts = decode_polyline(leg["shape"])
            coords.extend(pts[1:] if coords else pts)
        return RoutedPath(coords, trip["summary"]["length"], trip["summary"]["time"] / 60)

    async def elevation_gain_m(self, coords: list[LatLng]) -> float | None:
        """Total ascent from Valhalla's /height service. None if elevation data is not loaded."""
        step = max(1, len(coords) // 300)
        shape = [{"lat": a, "lon": b} for a, b in coords[::step]]
        try:
            r = await self._client.post(f"{self.base_url}/height", json={"shape": shape, "height_precision": 1})
            heights = r.json().get("height") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        if not heights or len(heights) < 3:
            return None
        sm = [sum(heights[max(0, i - 1) : i + 2]) / len(heights[max(0, i - 1) : i + 2]) for i in range(len(heights))]
        return round(sum(max(0.0, b - a) for a, b in zip(sm, sm[1:])), 1)

    async def healthy(self) -> bool:
        try:
            return (await self._client.get(f"{self.base_url}/status", timeout=3)).status_code == 200
        except httpx.HTTPError:
            return False
