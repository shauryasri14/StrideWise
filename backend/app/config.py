"""Settings are read from environment variables at call time (easy to override in tests)."""
import os
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    ollama_url: str
    ollama_model: str
    parser_mode: str  # auto (LLM, then keyword fallback) | llm | rules
    valhalla_url: str
    overpass_url: str
    aqi_enabled: bool
    aqi_url: str
    aqi_max_age_minutes: int
    elevation_enabled: bool
    cors_origins: list[str]
    region_file: str = ""  # written by scripts/setup_region.py


def get_settings() -> Settings:
    env = os.getenv
    return Settings(
        ollama_url=env("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=env("OLLAMA_MODEL", "qwen2.5:3b"),
        parser_mode=env("PARSER_MODE", "auto"),
        valhalla_url=env("VALHALLA_URL", "http://localhost:8002"),
        overpass_url=env("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
        aqi_enabled=env("AQI_ENABLED", "true").lower() == "true",
        aqi_url=env("AQI_URL", "https://air-quality-api.open-meteo.com/v1/air-quality"),
        aqi_max_age_minutes=int(env("AQI_MAX_AGE_MINUTES", "180")),
        elevation_enabled=env("ELEVATION_ENABLED", "true").lower() == "true",
        cors_origins=env("CORS_ORIGINS", "http://localhost:5173").split(","),
        region_file=env("REGION_FILE", str(Path(__file__).resolve().parents[2] / "data" / "region.json")),
    )
