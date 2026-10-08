import importlib.util, json, pathlib
import pytest
from app.config import Settings
from conftest import make_planner, make_settings

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("setup_region", ROOT / "scripts" / "setup_region.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)


def square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


INDEX = {"features": [
    {"properties": {"name": "Bigland", "urls": {"pbf": "https://x/bigland.osm.pbf"}}, "geometry": square(0, 0, 20, 20)},
    {"properties": {"name": "Bigland North", "urls": {"pbf": "https://x/north.osm.pbf"}}, "geometry": square(0, 10, 20, 20)},
    {"properties": {"name": "No download", "urls": {}}, "geometry": square(5, 12, 6, 13)},
]}


def test_smallest_containing_region_wins():
    assert sr.find_region(INDEX, lat=15, lon=5)["properties"]["name"] == "Bigland North"
    assert sr.find_region(INDEX, lat=5, lon=5)["properties"]["name"] == "Bigland"
    assert sr.find_region(INDEX, lat=50, lon=50) is None


def test_holes_are_excluded():
    donut = {"type": "Polygon", "coordinates": [square(0, 0, 10, 10)["coordinates"][0], square(4, 4, 6, 6)["coordinates"][0]]}
    assert sr.contains(donut, 1, 1) and not sr.contains(donut, 5, 5)


def test_apply_writes_env_and_region(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "ENV", tmp_path / ".env"); monkeypatch.setattr(sr, "ENV_EXAMPLE", tmp_path / "none")
    monkeypatch.setattr(sr, "DATA", tmp_path / "data")
    (tmp_path / ".env").write_text("A=1\nVALHALLA_PBF_URL=old\n")
    tiles = tmp_path / "data" / "valhalla"; tiles.mkdir(parents=True); (tiles / "t").write_text("x")
    assert sr.apply("Bigland", "https://x/b.osm.pbf", [0, 0, 20, 20], assume_yes=True) is True
    assert "VALHALLA_PBF_URL=https://x/b.osm.pbf" in (tmp_path / ".env").read_text() and "A=1" in (tmp_path / ".env").read_text()
    assert json.loads((tmp_path / "data" / "region.json").read_text())["name"] == "Bigland" and not tiles.exists()
    assert sr.apply("Bigland", "https://x/b.osm.pbf", [0, 0, 20, 20], assume_yes=True) is False  # same region: keep tiles


def test_outside_region_is_a_clear_error(tmp_path, override):
    f = tmp_path / "region.json"; f.write_text(json.dumps({"name": "Test Zone", "bbox": [77.0, 28.0, 78.0, 29.0]}))
    c = override(make_planner(region_file=str(f)))
    r = c.post("/api/routes", json={"prompt": "5 km run", "start": {"lat": 48.8, "lon": 2.3}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "outside_region" and "Test Zone" in r.json()["error"]["message"]
    ok = c.post("/api/routes", json={"prompt": "5 km run", "start": {"lat": 28.6, "lon": 77.37}})
    assert ok.status_code == 200
