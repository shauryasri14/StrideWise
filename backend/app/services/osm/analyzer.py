"""OpenStreetMap analysis: traffic, shade and surface estimates for routes.

All values are ESTIMATES derived from OSM tags. OSM has no traffic counts and incomplete tree data.
One Overpass query is made per request; only a bounding box leaves the machine.
"""
from dataclasses import dataclass, field

import httpx
import numpy as np

from ...errors import AppError
from ...geometry import LatLng, LocalProjection, nearest_segment, points_in_polygon, resample

# Estimated traffic level per OSM highway class (0 = none, 1 = very high). Estimates, not measurements.
TRAFFIC_LEVEL = {
    "motorway": 1.0, "motorway_link": 1.0, "trunk": 1.0, "trunk_link": 0.95,
    "primary": 0.85, "primary_link": 0.8, "secondary": 0.7, "secondary_link": 0.65,
    "tertiary": 0.5, "tertiary_link": 0.45, "unclassified": 0.3, "residential": 0.2,
    "service": 0.1, "living_street": 0.1, "pedestrian": 0.0, "footway": 0.0, "path": 0.0,
    "cycleway": 0.0, "track": 0.0, "steps": 0.0, "bridleway": 0.0,
}
PATH_CLASSES = {"pedestrian", "footway", "path", "cycleway", "track", "steps", "bridleway"}
PAVED = {"asphalt", "paved", "concrete", "paving_stones", "sett", "concrete:plates", "concrete:lanes", "metal", "wood"}
UNPAVED = {"unpaved", "gravel", "fine_gravel", "dirt", "ground", "earth", "grass", "sand", "mud", "compacted", "pebblestone"}
MAJOR_LEVEL = 0.7  # secondary and above counts as a "major road" for warnings/avoid rules
SHADE_PARK, SHADE_WOOD, SHADE_TREE_ROW = 0.5, 0.9, 0.8
PARK_TAGS = "park|garden|nature_reserve"
STEP_M = 40


class OsmUnavailable(Exception):
    pass


@dataclass
class OsmFeatures:
    proj: LocalProjection
    A: np.ndarray; B: np.ndarray; level: np.ndarray; motor: np.ndarray; surface: np.ndarray  # road segments
    trees: np.ndarray
    row_A: np.ndarray; row_B: np.ndarray
    woods: list[np.ndarray]
    parks: list[tuple[str, np.ndarray]]
    counts: dict = field(default_factory=dict)


def bbox_of(routes: list[list[LatLng]], pad_m: float = 80) -> tuple[float, float, float, float]:
    lats = [p[0] for r in routes for p in r]; lons = [p[1] for r in routes for p in r]
    dlat = pad_m / 111_320; dlon = pad_m / (111_320 * max(0.2, np.cos(np.radians(np.mean(lats)))))
    return min(lats) - dlat, min(lons) - dlon, max(lats) + dlat, max(lons) + dlon


def _surface_code(tags: dict) -> int:  # 0 paved, 1 unpaved, 2 unknown
    s = tags.get("surface")
    if s in PAVED: return 0
    if s in UNPAVED: return 1
    if s: return 2
    hw = tags.get("highway")
    if hw in ("track", "path", "bridleway"): return 2
    return 0 if hw in TRAFFIC_LEVEL else 2  # untagged roads/footways: assumed paved (an assumption)


async def fetch_features(bbox, overpass_url: str, client: httpx.AsyncClient | None = None) -> dict:
    b = f"{bbox[0]:.5f},{bbox[1]:.5f},{bbox[2]:.5f},{bbox[3]:.5f}"
    q = f"""[out:json][timeout:40];(
      way["highway"]({b}); way["leisure"~"{PARK_TAGS}"]({b});
      way["landuse"~"forest"]({b}); way["natural"~"wood|tree_row"]({b}); node["natural"="tree"]({b});
    ); out geom tags;"""
    own = client is None
    client = client or httpx.AsyncClient(timeout=60)
    try:
        r = await client.post(overpass_url, data={"data": q})
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise OsmUnavailable(str(e)) from e
    finally:
        if own:
            await client.aclose()


def parse_features(data: dict, origin: LatLng) -> OsmFeatures:
    proj = LocalProjection(*origin)
    a, b, lv, mo, su, trees, ra, rb, woods, parks = [], [], [], [], [], [], [], [], [], []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "node":
            trees.append((el["lat"], el["lon"])); continue
        geom = el.get("geometry") or []
        if len(geom) < 2: continue
        xy = proj.xy([(g["lat"], g["lon"]) for g in geom])
        closed = len(xy) >= 4 and np.allclose(xy[0], xy[-1])
        if "highway" in tags and tags["highway"] in TRAFFIC_LEVEL:
            hw = tags["highway"]
            for i in range(len(xy) - 1):
                a.append(xy[i]); b.append(xy[i + 1])
                lv.append(TRAFFIC_LEVEL[hw]); mo.append(hw not in PATH_CLASSES and hw not in ("service", "living_street"))
                su.append(_surface_code(tags))
        elif tags.get("natural") == "tree_row":
            ra.extend(xy[:-1]); rb.extend(xy[1:])
        elif closed and (tags.get("natural") == "wood" or tags.get("landuse") == "forest"):
            woods.append(xy)
        elif closed and tags.get("leisure") in PARK_TAGS.split("|"):
            parks.append((tags.get("name", "unnamed park"), xy))
    f = lambda x, shape=(0, 2): np.array(x, dtype=float).reshape(-1, 2) if x else np.zeros(shape)
    return OsmFeatures(
        proj, f(a), f(b), np.array(lv), np.array(mo, dtype=bool), np.array(su, dtype=int),
        proj.xy(trees) if trees else np.zeros((0, 2)), f(ra), f(rb), woods, parks,
        counts={"roads": len(a), "trees": len(trees), "tree_rows": len(ra), "woods": len(woods), "parks": len(parks)},
    )


def analyze_route(coords: list[LatLng], feats: OsmFeatures) -> dict:
    """Per-route estimates. Each score is 0..1 with 1 = better (quieter / more shaded)."""
    P = feats.proj.xy(resample(coords, STEP_M))
    n = len(P)
    notes: list[str] = []

    # Traffic: nearest way's class, raised if the way sits next to a motor road (sidewalks beside busy roads).
    d, idx = nearest_segment(P, feats.A, feats.B)
    known = d <= 25
    own = np.where(known, feats.level[idx] if len(feats.level) else 0, 0.0)
    mA, mB, mL = feats.A[feats.motor], feats.B[feats.motor], feats.level[feats.motor]
    md, mi = nearest_segment(P, mA, mB)
    adj = np.where(md <= 30, 0.6 * (mL[mi] if len(mL) else 0), 0.0)
    level = np.maximum(own, adj)
    traffic = None
    if known.mean() >= 0.5:
        traffic = float(1 - level[known].mean())
    else:
        notes.append("Too little road data along this route to estimate traffic.")

    # Surface fractions
    surf = feats.surface[idx][known] if known.any() else np.array([], dtype=int)
    frac = {k: float((surf == c).mean()) if len(surf) else None for k, c in (("paved", 0), ("unpaved", 1), ("unknown", 2))}

    # Shade estimate from mapped trees, tree rows, wooded areas and parks.
    shade = np.zeros(n)
    if len(feats.trees):
        td = np.sqrt(((P[:, None, :] - feats.trees[None]) ** 2).sum(2))
        shade = np.maximum(shade, np.minimum(1.0, (td <= 20).sum(1) / 3))
    if len(feats.row_A):
        rd, _ = nearest_segment(P, feats.row_A, feats.row_B)
        shade = np.where(rd <= 10, np.maximum(shade, SHADE_TREE_ROW), shade)
    for poly in feats.woods:
        shade = np.where(points_in_polygon(P, poly), np.maximum(shade, SHADE_WOOD), shade)
    parks_passed = []
    for name, poly in feats.parks:
        inside = points_in_polygon(P, poly)
        shade = np.where(inside, np.maximum(shade, SHADE_PARK), shade)
        if inside.sum() >= 3:
            parks_passed.append(name)
    mapped = feats.counts["trees"] + feats.counts["tree_rows"] + feats.counts["woods"]
    confidence = "low" if mapped < 30 else "medium"
    if confidence == "low":
        notes.append("Few trees are mapped in OpenStreetMap here, so the shade estimate is low confidence.")
    return {
        "traffic": traffic,
        "major_road_fraction": float((level[known] >= MAJOR_LEVEL).mean()) if known.any() else None,
        "surface": frac,
        "shade": float(shade.mean()),
        "shade_confidence": confidence,
        "parks_passed": sorted(set(parks_passed)),
        "notes": notes,
    }


POI_FILTERS = {
    "park": ['["leisure"="park"]'],
    "metro": ['["station"="subway"]', '["railway"="subway_entrance"]'],
    "cafe": ['["amenity"="cafe"]'],
}


async def find_pois(kind: str, center: LatLng, radius_m: int, overpass_url: str, client: httpx.AsyncClient | None = None) -> list[LatLng]:
    """Nearby destinations of a supported type, nearest first. Raises OsmUnavailable on failure."""
    parts = "".join(f'nwr{flt}(around:{radius_m},{center[0]:.5f},{center[1]:.5f});' for flt in POI_FILTERS[kind])
    own = client is None
    client = client or httpx.AsyncClient(timeout=60)
    try:
        r = await client.post(overpass_url, data={"data": f"[out:json][timeout:25];({parts});out center 60;"})
        r.raise_for_status()
        els = r.json().get("elements", [])
    except (httpx.HTTPError, ValueError) as e:
        raise OsmUnavailable(str(e)) from e
    finally:
        if own:
            await client.aclose()
    pts = [((e.get("lat") or e.get("center", {}).get("lat")), (e.get("lon") or e.get("center", {}).get("lon"))) for e in els]
    pts = [p for p in pts if p[0] is not None]
    from ...geometry import haversine_m
    return sorted(pts, key=lambda p: haversine_m(center, p))
