import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_planner
from app.config import Settings
from app.errors import AppError
from app.geometry import haversine_m, path_length_m
from app.main import app
from app.planner import Planner
from app.services.aqi.openmeteo import AqiUnavailable
from app.services.routing.valhalla import RoutedPath


def make_settings(**kw):
    base = dict(ollama_url="http://127.0.0.1:1", ollama_model="x", parser_mode="rules", valhalla_url="http://x", overpass_url="http://x",
                aqi_enabled=True, aqi_url="http://x", aqi_max_age_minutes=180, elevation_enabled=False, cors_origins=["*"])
    return Settings(**{**base, **kw})


class FakeRouter:
    """Straight-line 'routing' through the waypoints: lets tests run without Valhalla."""
    def __init__(self, fail=None):
        self.fail = fail

    async def route(self, waypoints, activity="walk", style="balanced", terrain="any"):
        if self.fail:
            raise self.fail
        pts = []
        for a, b in zip(waypoints, waypoints[1:]):
            n = max(2, int(haversine_m(a, b) / 50))
            pts += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n)]
        pts.append(waypoints[-1])
        km = path_length_m(pts) / 1000
        return RoutedPath(pts, km, km / 5 * 60)

    async def elevation_gain_m(self, coords):
        return None

    async def healthy(self):
        return True


def make_planner(router=None, aqi="unavailable", **kw):
    p = Planner(make_settings(**kw), router or FakeRouter())

    async def feats(bbox):
        return {"elements": [{"type": "node", "lat": 28.0, "lon": 77.0, "id": 1}]}

    async def aqi_fn(routes):
        if aqi == "unavailable":
            raise AqiUnavailable("down")
        return aqi

    p.fetch_features, p.fetch_aqi = feats, aqi_fn
    return p


@pytest.fixture
def client():
    p = make_planner()
    app.dependency_overrides[get_planner] = lambda: p
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def override():
    def _set(planner):
        app.dependency_overrides[get_planner] = lambda: planner
        return TestClient(app)
    yield _set
    app.dependency_overrides.clear()
