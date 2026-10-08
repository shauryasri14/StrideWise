from typing import Literal, Optional

from pydantic import BaseModel, Field

Level = Literal["none", "low", "medium", "high"]


class Constraints(BaseModel):
    """Structured request. This is the ONLY thing the LLM produces."""

    activity: Literal["walk", "run", "cycle"] = "walk"
    distance_km: Optional[float] = Field(None, gt=0)
    distance_mode: Literal["exact", "approx", "min", "max"] = "approx"
    distance_tolerance_km: Optional[float] = Field(None, gt=0)
    route_shape: Literal["loop", "point_to_point", "out_and_back"] = "loop"
    terrain: Literal["any", "flat", "moderate", "hilly"] = "any"
    shade_preference: Level = "none"
    traffic_preference: Literal["any", "low", "very_low"] = "any"
    surface_preferences: list[Literal["paved", "sidewalk", "trail", "dirt", "avoid_unpaved"]] = []
    avoid: list[Literal["highway", "major_roads", "steep"]] = []
    destination_type: Optional[Literal["park", "metro", "cafe", "start"]] = None
    air_quality_priority: Level = "none"
    clarification: Optional[str] = None  # set when the request is ambiguous


class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ParseRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=500)


class RouteRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=500)
    start: Optional[LatLon] = None
