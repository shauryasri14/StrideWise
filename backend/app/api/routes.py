from fastapi import APIRouter, Depends, Query

from ..errors import AppError
from ..planner import Planner
from ..schemas import ParseRequest, RouteRequest
from ..services.aqi import openmeteo as aqi_svc
from ..services.osm.analyzer import bbox_of  # noqa: F401  (re-exported for convenience)

router = APIRouter(prefix="/api")
_planner: Planner | None = None


def get_planner() -> Planner:
    global _planner
    if _planner is None:
        _planner = Planner()
    return _planner


@router.post("/parse-request")
async def parse_request(body: ParseRequest, p: Planner = Depends(get_planner)):
    c, parser, warnings = await p.parse(body.prompt)
    return {"constraints": c.model_dump(), "parser": parser, "warnings": warnings}


@router.post("/routes")
async def build_routes(body: RouteRequest, p: Planner = Depends(get_planner)):
    return await p.run(body)


@router.get("/route/{route_id}")
async def get_route(route_id: str, p: Planner = Depends(get_planner)):
    if route_id not in p.cache:
        raise AppError(404, "route_not_found", "Route not found. Routes are kept in memory only and expire.")
    return p.cache[route_id]


@router.get("/air-quality")
async def air_quality(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), p: Planner = Depends(get_planner)):
    try:
        grid = await aqi_svc.fetch_grid([[(lat, lon), (lat, lon)]], p.s.aqi_url)
    except aqi_svc.AqiUnavailable:
        raise AppError(503, "aqi_unavailable", "Air-quality data unavailable.")
    return aqi_svc.summarize([(lat, lon), (lat, lon)], grid, p.s.aqi_max_age_minutes)


@router.get("/health")
async def health(p: Planner = Depends(get_planner)):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            ollama = (await c.get(f"{p.s.ollama_url}/api/tags")).status_code == 200
    except httpx.HTTPError:
        ollama = False
    return {"status": "ok", "ollama": ollama, "valhalla": await p.router.healthy(), "region": (p.region() or {}).get("name"), "parser_mode": p.s.parser_mode, "aqi_enabled": p.s.aqi_enabled}
