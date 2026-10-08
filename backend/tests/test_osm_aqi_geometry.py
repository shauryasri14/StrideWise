import numpy as np
from datetime import datetime, timezone
from app.geometry import decode_polyline, haversine_m, resample
from app.services.aqi.openmeteo import summarize, _key
from app.services.osm.analyzer import analyze_route, parse_features

ORIGIN = (28.6, 77.2)


def test_polyline_decode_google_example():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
    assert pts[0] == (38.5, -120.2) and pts[-1] == (43.252, -126.453)


def test_resample_spacing():
    a, b = (28.6, 77.2), (28.61, 77.2)
    pts = resample([a, b], 100)
    assert abs(haversine_m(pts[0], pts[1]) - 100) < 1 and pts[-1] == b


def _way(wid, hw, pts, **tags):
    return {"type": "way", "id": wid, "tags": {"highway": hw, **tags}, "geometry": [{"lat": a, "lon": b} for a, b in pts]}


def test_quiet_path_vs_busy_road():
    line = [(28.6, 77.2), (28.605, 77.2)]
    quiet = parse_features({"elements": [_way(1, "footway", line, surface="asphalt")]}, ORIGIN)
    busy = parse_features({"elements": [_way(1, "primary", line)]}, ORIGIN)
    q, b = analyze_route(line, quiet), analyze_route(line, busy)
    assert q["traffic"] == 1.0 and b["traffic"] < 0.2
    assert q["surface"]["paved"] == 1.0 and b["major_road_fraction"] == 1.0


def test_sidewalk_beside_primary_is_not_quiet():
    side = [(28.6, 77.20015), (28.605, 77.20015)]   # ~15 m east of the road
    road = [(28.6, 77.2), (28.605, 77.2)]
    f = parse_features({"elements": [_way(1, "primary", road), _way(2, "footway", side, footway="sidewalk")]}, ORIGIN)
    assert analyze_route(side, f)["traffic"] < 0.6


def test_shade_from_trees_and_park():
    line = [(28.6, 77.2), (28.604, 77.2)]
    trees = [{"type": "node", "id": i, "lat": 28.6 + i * 0.0001, "lon": 77.20005} for i in range(40)]
    f = parse_features({"elements": [_way(1, "footway", line), *trees]}, ORIGIN)
    r = analyze_route(line, f)
    assert r["shade"] > 0.8 and r["shade_confidence"] == "medium"
    bare = analyze_route(line, parse_features({"elements": [_way(1, "footway", line)]}, ORIGIN))
    assert bare["shade"] == 0 and bare["shade_confidence"] == "low"


def test_aqi_summary_fresh_stale_missing():
    coords = [(28.6, 77.2), (28.601, 77.2)]
    k = _key(coords[0])
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    ok = summarize(coords, {k: {"aqi": 120.0, "time": "2026-10-07T11:00"}}, 180, now)
    assert ok["status"] == "ok" and ok["avg"] == 120 and ok["pct_poor"] == 1
    assert summarize(coords, {k: {"aqi": 50.0, "time": "2026-10-07T05:00"}}, 180, now)["status"] == "stale"
    assert summarize(coords, {}, 180, now)["status"] == "unavailable"
