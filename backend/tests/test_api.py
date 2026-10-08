from app.errors import AppError
from conftest import FakeRouter, make_planner

START = {"lat": 28.6139, "lon": 77.2090}


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["valhalla"] is True


def test_parse_endpoint(client):
    r = client.post("/api/parse-request", json={"prompt": "5 km shaded run avoiding highways"})
    assert r.json()["constraints"]["shade_preference"] == "high"


def test_valid_request_returns_ranked_routes(client):
    r = client.post("/api/routes", json={"prompt": "5 km loop, shaded, low traffic", "start": START})
    body = r.json()
    assert r.status_code == 200 and 1 <= len(body["routes"]) <= 3
    best = body["routes"][0]
    assert best["label"] == "Best match" and abs(best["distance_km"] - 5) < 1.0
    assert best["geometry"]["type"] == "LineString" and best["explanation"]
    assert any("Air-quality data unavailable" in w for w in body["warnings"])
    assert client.get(f"/api/route/{best['id']}").json()["id"] == best["id"]


def test_ambiguous_request_asks_instead_of_guessing(client):
    body = client.post("/api/routes", json={"prompt": "a nice shaded run", "start": START}).json()
    assert body["routes"] == [] and body["clarification"]


def test_malformed_requests(client):
    assert client.post("/api/routes", json={"prompt": "5 km", "start": {"lat": 120, "lon": 0}}).status_code == 422
    r = client.post("/api/routes", json={"start": START})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_missing_location(client):
    r = client.post("/api/routes", json={"prompt": "5 km run"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "missing_start"


def test_impossible_distance(client):
    r = client.post("/api/routes", json={"prompt": "500 km walk", "start": START})
    assert r.status_code == 422 and r.json()["error"]["code"] == "impossible_distance"


def test_routing_failure(override):
    c = override(make_planner(FakeRouter(fail=AppError(503, "routing_unavailable", "down"))))
    r = c.post("/api/routes", json={"prompt": "5 km run", "start": START})
    assert r.status_code == 503 and r.json()["error"]["code"] == "routing_unavailable"


def test_no_route_found(override):
    c = override(make_planner(FakeRouter(fail=AppError(422, "no_route", "none"))))
    assert c.post("/api/routes", json={"prompt": "5 km run", "start": START}).json()["error"]["code"] == "no_route"


def test_llm_failure_in_llm_mode(override):
    c = override(make_planner(parser_mode="llm"))
    r = c.post("/api/routes", json={"prompt": "5 km run", "start": START})
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_unavailable"


def test_aqi_available_adds_metric(override):
    from app.services.aqi.openmeteo import _key
    from datetime import datetime, timezone

    class AllKeys(dict):
        def __contains__(self, k): return True
        def __getitem__(self, k): return {"aqi": 80.0, "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")}
    c = override(make_planner(aqi=AllKeys({"x": 1})))
    body = c.post("/api/routes", json={"prompt": "5 km loop, avoid polluted roads", "start": START}).json()
    assert body["routes"][0]["metrics"]["air_quality"] is not None
    assert body["routes"][0]["details"]["aqi"]["status"] == "ok"
    assert not any("Air-quality data unavailable" in w for w in body["warnings"])
