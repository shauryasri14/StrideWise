"""Candidate generation: ask the routing engine for several real routes near the target distance."""
import asyncio
import math
import uuid
from dataclasses import dataclass

from ...errors import AppError
from ...geometry import LatLng, bearing_deg, destination, haversine_m
from ...schemas import Constraints
from .valhalla import RoutedPath

DETOUR = 1.3  # typical routed/straight-line ratio, only used for the first guess; routes are then corrected


@dataclass
class Candidate:
    id: str
    path: RoutedPath
    style: str
    shape: str


async def _fit(router, builder, scale0, target, tol, c, style) -> RoutedPath:
    """Route, then rescale the waypoint distance up to 2 times so the real routed length hits the target."""
    scale, path = scale0, None
    for i in range(3):
        path = await router.route(builder(scale), c.activity, style, c.terrain)
        if abs(path.distance_km - target) <= tol or i == 2:
            break
        scale *= min(2.0, max(0.5, target / max(path.distance_km, 0.05)))
    return path


def _jobs(start: LatLng, c: Constraints, target: float, pois: list[LatLng], n: int):
    """Yield (shape, style, builder, first_scale_metres)."""
    t_m = target * 1000
    for i in range(n):
        brg = (i * 360 / n + 15 * (i % 2)) % 360
        style = "quiet" if i % 3 == 1 else "balanced"
        if c.route_shape == "loop":
            spread = (60, 80)[i % 2]
            yield "loop", style, (lambda r, b=brg, s=spread: [start, destination(*start, b, r), destination(*start, b + s, r), start]), t_m / (3 * DETOUR)
        elif c.route_shape == "out_and_back":
            yield "out_and_back", style, (lambda r, b=brg: [start, destination(*start, b, r), start]), t_m / (2 * DETOUR)
        elif not pois:
            yield "point_to_point", style, (lambda r, b=brg: [start, destination(*start, b, r)]), t_m / DETOUR
    for j, dest in enumerate(pois[: max(2, n // 2)]):
        L = haversine_m(start, dest)
        brg = bearing_deg(start, dest)
        half = max(0.0, (t_m / DETOUR / 2) ** 2 - (L / 2) ** 2)
        h0 = math.sqrt(half) if half else 0.0
        for side in ((90,) if h0 == 0 else (90, -90)):
            if h0 == 0:
                yield "point_to_point", "balanced", (lambda r, d=dest: [start, d]), 1.0
            else:
                mid = destination(*start, brg, L / 2)
                yield "point_to_point", "quiet" if side < 0 else "balanced", (lambda h, m=mid, b=brg, sd=side, d=dest: [start, destination(*m, b + sd, h), d]), h0


async def generate(router, start: LatLng, c: Constraints, target: float, tol: float, pois: list[LatLng], n: int = 8) -> list[Candidate]:
    jobs = list(_jobs(start, c, target, pois, n))
    results = await asyncio.gather(*(_fit(router, b, s0, target, tol, c, st) for _, st, b, s0 in jobs), return_exceptions=True)
    out, errors = [], []
    for (shape, style, _, _), res in zip(jobs, results):
        if isinstance(res, AppError):
            errors.append(res)
        elif isinstance(res, Exception):
            raise res
        elif len(res.coords) >= 2:
            out.append(Candidate(f"route_{uuid.uuid4().hex[:8]}", res, style, shape))
    if not out:
        raise errors[0] if errors else AppError(422, "no_route", "No route found.")
    return out
