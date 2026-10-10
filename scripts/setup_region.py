#!/usr/bin/env python3
"""Choose the right OpenStreetMap extract for where YOU are, download it safely, and configure Valhalla.

Examples:
  python3 scripts/setup_region.py --lat 48.85 --lon 2.35
  python3 scripts/setup_region.py --place "Lisbon"      # place name is sent to nominatim.openstreetmap.org
  python3 scripts/setup_region.py --pbf-url https://download.geofabrik.de/europe/monaco-latest.osm.pbf
  python3 scripts/setup_region.py                       # asks you
  python3 scripts/setup_region.py --download-only       # re-download / re-verify the map already chosen in .env

The map is downloaded HERE (not inside Docker) so problems show up with a clear message. The download resumes after
interruptions, retries when the server is busy, and is checked before Valhalla ever sees it: it must be a real
OpenStreetMap file of the right size, and its checksum is compared with Geofabrik's when available.
(On Windows use `python` instead of `python3`.)

Options: --start launches Valhalla afterwards, --yes skips the "delete old tiles?" question,
         --no-download only writes the configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
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
MB = 1 << 20
CHUNK = MB
PBF_MARKER = b"OSMHeader"      # every real OpenStreetMap .pbf starts with a header block of this type
MIN_PBF_BYTES = 1000           # the smallest real extracts are far larger; error pages are a few hundred bytes

CERT_HELP = """
SSL certificate check failed: Python cannot find trusted certificates (common with the python.org installer on macOS).
Fix, pick one:
  1) Run the installer's certificate script, then retry:
       open "/Applications/Python 3.12/Install Certificates.command"     (adjust the version folder)
  2) pip3 install certifi        (this script uses it automatically when installed)
  3) Download the map in your browser instead (see the message printed if the download fails).
"""


class DownloadError(Exception):
    """A problem worth retrying (connection dropped, server busy). `wait` = seconds the server asked us to wait."""

    def __init__(self, message: str, wait: int | None = None):
        super().__init__(message)
        self.wait = wait


class FatalDownloadError(Exception):
    """A problem retrying will not fix (404, refused, bad file, no disk space)."""


def _open(req, timeout: int, fatal: bool = True):
    """urlopen that uses certifi when installed and explains failures.
    fatal=True exits with a message; fatal=False raises HTTPError / DownloadError for the caller to handle."""
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = None
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.HTTPError as e:
        if fatal:
            sys.exit(f"HTTP {e.code} from {req.full_url}")
        raise
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            sys.exit(CERT_HELP)
        if "tunnel connection failed" in str(e.reason).lower():  # a proxy / firewall refused the connection
            msg = f"a proxy or firewall on your network blocked the connection to {req.full_url} ({e.reason})"
            if fatal:
                sys.exit(msg)
            raise FatalDownloadError(msg + ". Try another network (for example a phone hotspot) or download in your browser.") from e
        if fatal:
            sys.exit(f"Network error while contacting {req.full_url}: {e.reason}")
        raise DownloadError(f"network error: {e.reason}") from e
    except OSError as e:  # timeouts and resets
        if fatal:
            sys.exit(f"Network error while contacting {req.full_url}: {e}")
        raise DownloadError(f"network error: {e}") from e


def load_index() -> dict:
    if INDEX_CACHE.exists():
        return json.loads(INDEX_CACHE.read_bytes())
    print("Downloading the Geofabrik region index (one time, cached in data/)...")
    req = urllib.request.Request(INDEX_URL, headers=UA)
    with _open(req, 120) as r:
        raw = r.read()
    DATA.mkdir(exist_ok=True)
    INDEX_CACHE.write_bytes(raw)
    return json.loads(raw)


def geocode(place: str) -> tuple[float, float]:
    print(f"Looking up '{place}' on nominatim.openstreetmap.org (the place name leaves your machine).")
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    with _open(urllib.request.Request(url, headers=UA), 30) as r:
        hits = json.loads(r.read())
    if not hits:
        sys.exit(f"Could not find '{place}'. Try --lat/--lon instead.")
    return float(hits[0]["lat"]), float(hits[0]["lon"])


# ---------- safe map download ----------
def file_md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_md5(url: str) -> str | None:
    """Geofabrik publishes <file>.md5 next to every extract. None if it is not available."""
    try:
        with _open(urllib.request.Request(url + ".md5", headers=UA), 30, fatal=False) as r:
            text = r.read(512).decode("ascii", "ignore")
    except (urllib.error.HTTPError, DownloadError):
        return None
    m = re.match(r"\s*([0-9a-fA-F]{32})\b", text)
    return m.group(1).lower() if m else None


def verify_pbf(path: Path, expected_md5: str | None = None) -> str | None:
    """None if the file looks like a complete OpenStreetMap extract, otherwise a human-readable reason."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(max(64, min(size, 300)))
    if size < MIN_PBF_BYTES:
        return f"it is only {size} bytes (a real map is many MB). It contains: {head[:150]!r}"
    if PBF_MARKER not in head[:64]:
        return f"it is not an OpenStreetMap .pbf file (it starts with {head[:40]!r})"
    if expected_md5 and file_md5(path) != expected_md5:
        return "its checksum does not match the one published by Geofabrik"
    return None


def _retry_after(err) -> int | None:
    v = err.headers.get("Retry-After") if err.headers else None
    return min(int(v), 120) if v and v.isdigit() else None


def _check_disk(folder: Path, total: int | None) -> None:
    if not total:
        return
    free = shutil.disk_usage(folder).free
    need = total * 4  # the map itself plus roughly three times its size for the routing tiles
    if free < need:
        raise FatalDownloadError(f"not enough free disk space in {folder}: {free // MB} MB free, about {need // MB} MB needed.")


def _stream(url: str, part: Path) -> None:
    """Download into `part`, resuming from what is already there. Raises DownloadError / FatalDownloadError."""
    have = part.stat().st_size if part.exists() else 0
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        resp = _open(urllib.request.Request(url, headers=headers), 60, fatal=False)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:  # we already hold every byte; verification decides if it is good
            return
        if e.code in (429, 500, 502, 503, 504):
            raise DownloadError(f"the server is busy or limiting downloads (HTTP {e.code})", _retry_after(e)) from e
        if e.code == 404:
            raise FatalDownloadError(f"HTTP 404: {url} does not exist. Check the current file name on https://download.geofabrik.de") from e
        raise FatalDownloadError(f"HTTP {e.code} from {url}. The server refused the download (possibly a rate limit or a network block).") from e
    with resp:
        if "text/html" in resp.headers.get("Content-Type", "").lower():
            raise FatalDownloadError(f"the server sent a web page instead of a map (a block page or a redirect): {resp.read(150)!r}")
        length = resp.headers.get("Content-Length")
        mode, done = ("ab", have) if getattr(resp, "status", 200) == 206 else ("wb", 0)
        total = done + int(length) if length and length.isdigit() else None
        _check_disk(part.parent, total)
        last = 0.0
        try:
            with open(part, mode) as f:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if time.time() - last > 1:
                        last = time.time()
                        print(f"\r  downloaded {done // MB} MB" + (f" of {total // MB} MB" if total else ""), end="", flush=True)
        except (OSError, http.client.IncompleteRead) as e:
            raise DownloadError(f"connection dropped after {done // MB} MB ({e})") from e
        if total is not None and done != total:
            raise DownloadError(f"incomplete download: got {done} of {total} bytes")
        print(f"\r  downloaded {done // MB} MB" + " " * 20)


def download_pbf(url: str, folder: Path, retries: int = 5) -> tuple[Path, bool]:
    """Download `url` into `folder` safely. Returns (file, checksum_was_verified)."""
    folder.mkdir(parents=True, exist_ok=True)
    name = url.split("?")[0].rsplit("/", 1)[-1]
    final, part = folder / name, folder / (name + ".part")
    expected = fetch_md5(url)
    if final.exists():
        problem = verify_pbf(final, expected)
        if problem is None:
            print(f"Map already downloaded and verified: {final.name}")
            return final, expected is not None
        print(f"The existing {final.name} cannot be used: {problem}. Replacing it.")
        final.unlink()
    if "onedrive" in str(folder).lower():
        print("WARNING: this folder is inside OneDrive. OneDrive can lock or replace large files while Docker reads them.\n"
              "         Moving the project to a plain folder such as C:\\dev avoids that.")
    print(f"Downloading {name} (large regions are several hundred MB)...")
    attempt, restarted = 0, False
    while True:
        attempt += 1
        try:
            _stream(url, part)
            problem = verify_pbf(part, expected)
            if problem is None:
                part.replace(final)  # only a verified file ever gets the real name
                return final, expected is not None
            part.unlink(missing_ok=True)
            if restarted:
                raise FatalDownloadError(f"the downloaded file failed verification: {problem}.")
            restarted = True
            expected = fetch_md5(url)  # the extract may have been refreshed while we downloaded
            print(f"Downloaded file failed verification ({problem}). Downloading again from the start.")
        except DownloadError as e:
            if attempt >= retries:
                raise FatalDownloadError(f"giving up after {retries} attempts: {e}") from e
            wait = e.wait if e.wait is not None else min(60, 5 * attempt)
            print(f"\n{e}. Retrying in {wait}s (attempt {attempt}/{retries})...")
            time.sleep(wait)


# ---------- config files ----------
def read_env_value(key: str) -> str | None:
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


def set_env_value(key: str, value: str) -> None:
    if not ENV.exists() and ENV_EXAMPLE.exists():
        shutil.copy(ENV_EXAMPLE, ENV)
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    new = f"{key}={value}"
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = new
            break
    else:
        lines.append(new)
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        region.write_text(json.dumps({"name": name, "pbf_url": pbf_url, "bbox": box}, indent=2), encoding="utf-8")
    elif region.exists():
        region.unlink()  # unknown coverage: skip the backend's 'outside region' pre-check
    return cleared


def manual_download_help(url: str, folder: Path) -> str:
    name = url.split("?")[0].rsplit("/", 1)[-1]
    return (
        "\nThe automatic download did not work. Manual alternative:\n"
        f"  1. Download {url}\n"
        "     in your web browser (or from https://download.geofabrik.de).\n"
        f"  2. Save it as exactly: {folder / name}\n"
        "  3. Run: python3 scripts/setup_region.py --download-only    (it checks the file before Docker uses it)\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float)
    ap.add_argument("--place"); ap.add_argument("--pbf-url")
    ap.add_argument("--start", action="store_true"); ap.add_argument("--yes", action="store_true")
    ap.add_argument("--no-download", action="store_true", help="only write the configuration")
    ap.add_argument("--download-only", action="store_true", help="(re)download and verify the map chosen in .env")
    a = ap.parse_args()
    tiles_dir = DATA / "valhalla"

    if a.download_only:
        url = read_env_value("VALHALLA_PBF_URL")
        if not url:
            sys.exit("No map region chosen yet. Run: python3 scripts/setup_region.py")
        name, cleared = url.rsplit("/", 1)[-1], False
    else:
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
    if not a.download_only:
        print("Old routing tiles removed; Valhalla will rebuild." if cleared else "Routing tiles kept (same region or none built yet).")

    if not a.no_download:
        try:
            path, checked = download_pbf(url, tiles_dir)
        except FatalDownloadError as e:
            sys.exit(f"\nERROR: {e}\n" + manual_download_help(url, tiles_dir))
        note = "checksum verified" if checked else "checksum not available, size and format checked"
        print(f"Map ready: {path} ({path.stat().st_size // MB} MB, {note}).")
    print("First routing build takes 10+ minutes and several GB of RAM for large regions.")
    if a.start:
        subprocess.run(["docker", "compose", "up", "-d", "valhalla"], cwd=ROOT, check=True)
        print("Valhalla started. Follow progress: docker compose logs -f valhalla")
    else:
        print("Next, pick one:")
        print("  Development:  docker compose up valhalla   (then run the backend and frontend yourself)")
        print("  Everything:   docker compose up --build    (backend and frontend in Docker too)")


if __name__ == "__main__":
    main()
