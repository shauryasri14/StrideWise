#!/usr/bin/env python3
"""Choose the right OpenStreetMap extract for where YOU are and configure Valhalla for it.

Examples:
  python scripts/setup_region.py --lat 48.85 --lon 2.35
  python scripts/setup_region.py --place "Lisbon"        # place name is sent to nominatim.openstreetmap.org
  python scripts/setup_region.py --pbf-url https://download.geofabrik.de/europe/monaco-latest.osm.pbf
  python scripts/setup_region.py            # asks you
Add --start to launch Valhalla afterwards, --yes to skip the "delete old tiles?" question.
"""
import argparse
import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ENV, ENV_EXAMPLE = ROOT / ".env", ROOT / ".env.example"
INDEX_URL = "https://download.geofabrik.de/index-v1.json"
INDEX_CACHE = DATA / ".geofabrik-index.json"
UA = {"User-Agent": "route-builder-setup/0.1 (local setup script)"}


# ---------- geometry (stdlib only) ----------
def _ring_has(ring, lon, lat) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _polygons(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def contains(geom, lat, lon) -> bool:
    for poly in _polygons(geom):
        if _ring_has([tuple(p[:2]) for p in poly[0]], lon, lat) and not any(_ring_has([tuple(p[:2]) for p in h], lon, lat) for h in poly[1:]):
            return True
    return False


def area(geom) -> float:
    total = 0.0
    for poly in _polygons(geom):
        r = poly[0]
        total += abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:] + r[:1]))) / 2
    return total


def bbox(geom) -> list[float]:
    pts = [p for poly in _polygons(geom) for p in poly[0]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def find_region(index: dict, lat: float, lon: float):
    """Smallest Geofabrik region that contains the point and has a .osm.pbf download."""
    best = None
    for f in index.get("features", []):
        pbf = (f.get("properties", {}).get("urls") or {}).get("pbf")
        if pbf and f.get("geometry") and contains(f["geometry"], lat, lon):
            if best is None or area(f["geometry"]) < area(best["geometry"]):
                best = f
    return best


# ---------- network helpers ----------
def load_index() -> dict:
    if INDEX_CACHE.exists():
        return json.loads(INDEX_CACHE.read_bytes())
    print("Downloading the Geofabrik region index (one time, cached in data/)...")
    req = urllib.request.Request(INDEX_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    DATA.mkdir(exist_ok=True)
    INDEX_CACHE.write_bytes(raw)
    return json.loads(raw)


def geocode(place: str) -> tuple[float, float]:
    print(f"Looking up '{place}' on nominatim.openstreetmap.org (the place name leaves your machine).")
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        hits = json.loads(r.read())
    if not hits:
        sys.exit(f"Could not find '{place}'. Try --lat/--lon instead.")
    return float(hits[0]["lat"]), float(hits[0]["lon"])


# ---------- config files ----------
def read_env_value(key: str) -> str | None:
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


def set_env_value(key: str, value: str) -> None:
    if not ENV.exists() and ENV_EXAMPLE.exists():
        shutil.copy(ENV_EXAMPLE, ENV)
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    new = f"{key}={value}"
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = new
            break
    else:
        lines.append(new)
    ENV.write_text("\n".join(lines) + "\n")


def apply(name: str, pbf_url: str, box: list[float] | None, assume_yes: bool) -> bool:
    """Write .env and data/region.json. Returns True if old routing tiles were removed."""
    old = read_env_value("VALHALLA_PBF_URL")
    cleared = False
    tiles = DATA / "valhalla"
    if old and old != pbf_url and tiles.exists() and any(tiles.iterdir()):
        ok = assume_yes or input(f"Map region changes ({old} -> {pbf_url}). Delete old routing tiles in {tiles} and rebuild? [y/N] ").lower() == "y"
        if not ok:
            sys.exit("Cancelled. Nothing changed.")
        shutil.rmtree(tiles)
        cleared = True
    set_env_value("VALHALLA_PBF_URL", pbf_url)
    DATA.mkdir(exist_ok=True)
    region = DATA / "region.json"
    if box:
        region.write_text(json.dumps({"name": name, "pbf_url": pbf_url, "bbox": box}, indent=2))
    elif region.exists():
        region.unlink()  # unknown coverage: skip the backend's 'outside region' pre-check
    return cleared


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float)
    ap.add_argument("--place"); ap.add_argument("--pbf-url")
    ap.add_argument("--start", action="store_true"); ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()

    if a.pbf_url:
        name, url, box = Path(a.pbf_url).name.replace("-latest.osm.pbf", ""), a.pbf_url, None
    else:
        if a.lat is None or a.lon is None:
            place = a.place or input("Where will you use this? Enter a place name, or 'lat,lon': ").strip()
            if "," in place and all(p.replace(".", "", 1).replace("-", "", 1).strip().isdigit() for p in place.split(",", 1)):
                a.lat, a.lon = (float(p) for p in place.split(",", 1))
            else:
                a.lat, a.lon = geocode(place)
        feat = find_region(load_index(), a.lat, a.lon)
        if not feat:
            sys.exit("No Geofabrik extract covers that point. Provide one with --pbf-url.")
        name, url, box = feat["properties"]["name"], feat["properties"]["urls"]["pbf"], bbox(feat["geometry"])
    cleared = apply(name, url, box, a.yes)
    print(f"\nRegion: {name}\nExtract: {url}")
    print("Old routing tiles removed; Valhalla will rebuild." if cleared else "Routing tiles kept (same region or none built yet).")
    print("First build can take 10+ minutes and several GB of RAM for large regions.")
    if a.start:
        subprocess.run(["docker", "compose", "up", "-d", "valhalla"], cwd=ROOT, check=True)
        print("Valhalla started. Follow progress: docker compose logs -f valhalla")
    else:
        print("Next, pick one:")
        print("  Development:  docker compose up valhalla   (then run the backend and frontend yourself)")
        print("  Everything:   docker compose up --build    (backend and frontend in Docker too)")


if __name__ == "__main__":
    main()
