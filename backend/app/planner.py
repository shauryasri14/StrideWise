"""Orchestrates: parse -> POIs -> candidates -> OSM analysis -> AQI -> scoring -> top 3."""
import asyncio
import json
from pathlib import Path
from collections import OrderedDict

import httpx

from . import config
from .errors import AppError
from .geometry import haversine_m
from .schemas import Constraints, RouteRequest
from .services.aqi import openmeteo as aqi_svc
from .services.llm.parser import parse_request
from .services.osm import analyzer as osm
from .services.routing.generator import generate
from .services.routing.valhalla import ValhallaRouter
from .services.scoring import scorer

MAX_KM = {"walk": 40, "run": 50, "cycle": 150}
NO_AQI = "Air-quality data unavailable. Route ranking was performed without AQI."


class Planner:
    def __init__(self, settings: config.Settings | None = None, router=None):
        self.s = settings or config.get_settings()
        self.router = router or ValhallaRouter(self.s.valhalla_url)
        self.cache: OrderedDict[str, dict] = OrderedDict()  # in-memory only: nothing is written to disk
        # Replaceable in tests:
        self.fetch_features = lambda bbox: osm.fetch_features(bbox, self.s.overpass_url)
        self.find_pois = lambda kind, ctr, r: osm.find_pois(kind, ctr, r, self.s.overpass_url)
        self.fetch_aqi = lambda routes: aqi_svc.fetch_grid(routes, self.s.aqi_url)

    def region(self) -> dict | None:
        """Loaded map region (bounding box) written by scripts/setup_region.py. Read each call so a rebuild needs no restart."""
        try:
            return json.loads(Path(self.s.region_file).read_text()) if self.s.region_file else None
        except (OSError, ValueError):
            return None

    async def parse(self, prompt: str):
        return await parse_request(prompt, self.s)

    async def run(self, req: RouteRequest) -> dict:
        if req.start is None:
            raise AppError(422, "missing_start", "A start location is required. Use your location or click the map.")
        start = (req.start.lat, req.start.lon)
        reg = self.region()
        if reg:
            w, so, e, n = reg["bbox"]
            if not (so <= start[0] <= n and w <= start[1] <= e):
                raise AppError(422, "outside_region", f"Your start point is outside the loaded map region ({reg['name']}). Run: python scripts/setup_region.py")
        c, parser, warnings = await self.parse(req.prompt)
        base = {"request": {"prompt": req.prompt, "start": req.start.model_dump()}, "constraints": c.model_dump(), "parser": parser}
        if c.clarification:
            return {**base, "clarification": c.clarification, "routes": [], "warnings": warnings}

        d = c.distance_km
        if d < 0.3 or d > MAX_KM[c.activity]:
            raise AppError(422, "impossible_distance", f"{d} km is outside the supported range (0.3 to {MAX_KM[c.activity]} km for {c.activity}).")
        target = d * 1.15 if c.distance_mode == "min" else d * 0.85 if c.distance_mode == "max" else d
        tol = c.distance_tolerance_km or max(0.3, 0.1 * d)

        pois = []
        if c.route_shape == "point_to_point" and c.destination_type in osm.POI_FILTERS:
            try:
                pois = await self.find_pois(c.destination_type, start, int(max(1500, target * 1000 * 0.9)))
            except osm.OsmUnavailable:
                raise AppError(503, "osm_unavailable", "Could not look up destinations in OpenStreetMap (Overpass unreachable).")
            if not pois:
                raise AppError(422, "no_destination", f"No {c.destination_type} found within reach of the start point.")

        cands = await generate(self.router, start, c, target, tol, pois)
        coords = [x.path.coords for x in cands]

        feats = None
        try:
            feats = osm.parse_features(await self.fetch_features(osm.bbox_of(coords)), start)
        except osm.OsmUnavailable:
            warnings.append("OpenStreetMap data unavailable. Traffic, shade and surface were not estimated and are excluded from ranking.")
        analyses = [osm.analyze_route(x.path.coords, feats) if feats else None for x in cands]

        grid = None
        if self.s.aqi_enabled:
            try:
                grid = await self.fetch_aqi(coords)
            except aqi_svc.AqiUnavailable:
                warnings.append(NO_AQI)
        aqis = [aqi_svc.summarize(x, grid, self.s.aqi_max_age_minutes) if grid else None for x in coords]
        if any(a and a["status"] == "stale" for a in aqis):
            warnings.append("Air-quality data is stale. Route ranking was performed without AQI.")
        if grid and all(a["status"] == "unavailable" for a in aqis):
            warnings.append(NO_AQI)

        gains = await asyncio.gather(*(self.router.elevation_gain_m(x) if self.s.elevation_enabled else asyncio.sleep(0, None) for x in coords))
        if all(g is None for g in gains):
            warnings.append("Elevation data is not available from the routing engine, so hills were not scored." + (" 'Avoid steep' could not be enforced." if "steep" in c.avoid else ""))

        scored = []
        for cand, an, aq, gain in zip(cands, analyses, aqis, gains):
            dist = cand.path.distance_km
            usable_aqi = aq if aq and aq["status"] == "ok" else None
            comps = {
                "distance": scorer.distance_score(dist, d, tol, c.distance_mode),
                "shade": an["shade"] if an else None,
                "traffic": an["traffic"] if an else None,
                "surface": scorer.surface_score(an["surface"], c.surface_preferences) if an else None,
                "air_quality": scorer.air_quality_score(usable_aqi["avg"], usable_aqi["max"]) if usable_aqi else None,
                "elevation": scorer.elevation_score(gain, dist, c.terrain),
            }
            scored.append({"id": cand.id, "cand": cand, "an": an, "aqi": aq, "gain": gain, "components": comps,
                           "major_fraction": an["major_road_fraction"] if an else None})

        has_aqi = any(x["components"]["air_quality"] is not None for x in scored)
        picks = scorer.pick_top(scored, c, has_aqi)
        aqi_values = [x["components"]["air_quality"] for x in scored if x["components"]["air_quality"] is not None]
        aqi_flat = bool(aqi_values) and (max(aqi_values) - min(aqi_values) < 0.02)

        routes = [self._render(p, lab, item, c, d, aqi_flat) for p, lab, item in picks]
        for r in routes:
            self.cache[r["id"]] = r
            while len(self.cache) > 50:
                self.cache.popitem(last=False)
        return {**base, "clarification": None, "routes": routes, "warnings": warnings,
                "data_sources": {"routing": "Valhalla (self-hosted)", "map": "OpenStreetMap via Overpass", "air_quality": aqi_svc.SOURCE if has_aqi else None,
                                 "estimates": "traffic, shade and surface are estimates derived from OSM tags"}}

    def _render(self, profile, label, item, c: Constraints, target, aqi_flat) -> dict:
        cand, an, aq = item["cand"], item["an"], item["aqi"]
        dist = cand.path.distance_km
        comps = {k: (round(v, 3) if v is not None else None) for k, v in item["components"].items()}
        warns = list(an["notes"]) if an else []
        if an and an["major_road_fraction"] and an["major_road_fraction"] > 0.15:
            warns.append(f"About {round(an['major_road_fraction'] * 100)}% of this route runs along or beside major roads (estimated).")
        if aq and aq["status"] == "ok" and aq["max"] > 150:
            warns.append(f"Very poor air quality modelled along part of this route (US AQI up to {round(aq['max'])}). Consider another time.")
        if dist > (21 if c.activity == "run" else 30):
            warns.append("This is a long route. Plan water, daylight and a way home.")
        parts = [f"{dist:.1f} km" + (f" against {target:g} km requested" if c.distance_mode in ('approx', 'exact') else "")]
        if an and an["traffic"] is not None:
            t = an["traffic"]
            parts.append(f"{'very low' if t >= .85 else 'low' if t >= .7 else 'moderate' if t >= .5 else 'high'} estimated traffic")
        if an and an["parks_passed"]:
            parts.append(f"passes through {len(an['parks_passed'])} park{'s' if len(an['parks_passed']) > 1 else ''}")
        if an:
            parts.append(f"estimated shade {round(an['shade'] * 100)}% ({an['shade_confidence']} confidence)")
        if aq and aq["status"] == "ok":
            parts.append(f"modelled AQI averages {aq['avg']:g}")
        why = "Chosen because: " + "; ".join(parts) + "."
        if profile == "lowest_pollution" and aqi_flat:
            why += " Modelled AQI is nearly identical across candidates (about 10 km grid), so lower road exposure broke the tie."
        return {
            "id": cand.id, "label": label, "profile": profile, "shape": cand.shape,
            "distance_km": round(dist, 2), "duration_minutes": round(cand.path.duration_min), "elevation_gain_m": item["gain"],
            "score": round(item["score"], 3), "metrics": comps, "weights": item["weights_used"], "avoid_penalty": round(item["avoid_penalty"], 3),
            "details": {"aqi": aq, "parks_passed": an["parks_passed"] if an else [], "surface_fractions": an["surface"] if an else None,
                        "shade_confidence": an["shade_confidence"] if an else None, "major_road_fraction": an["major_road_fraction"] if an else None},
            "explanation": why, "warnings": warns,
            "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in cand.path.coords]},
        }
