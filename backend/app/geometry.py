"""Small geographic helpers (no external deps beyond numpy)."""
import math

import numpy as np

R = 6371008.8  # mean Earth radius, metres
LatLng = tuple[float, float]


def haversine_m(a: LatLng, b: LatLng) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def destination(lat: float, lon: float, bearing_deg: float, dist_m: float) -> LatLng:
    br, d = math.radians(bearing_deg), dist_m / R
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(br))
    l2 = l1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def bearing_deg(a: LatLng, b: LatLng) -> float:
    p1, p2, dl = math.radians(a[0]), math.radians(b[0]), math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def path_length_m(coords: list[LatLng]) -> float:
    return sum(haversine_m(a, b) for a, b in zip(coords, coords[1:]))


def decode_polyline(encoded: str, precision: int = 6) -> list[LatLng]:
    """Decode a Google-style polyline (Valhalla uses precision 6)."""
    coords, idx, lat, lng, factor = [], 0, 0, 0, 10**precision
    while idx < len(encoded):
        for is_lat in (True, False):
            shift = result = 0
            while True:
                b = ord(encoded[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += delta
            else:
                lng += delta
        coords.append((lat / factor, lng / factor))
    return coords


def resample(coords: list[LatLng], step_m: float) -> list[LatLng]:
    """Points spaced step_m apart along the path (first and last point included)."""
    out, carry = [coords[0]], 0.0
    for a, b in zip(coords, coords[1:]):
        seg = haversine_m(a, b)
        if seg == 0:
            continue
        d = step_m - carry
        while d <= seg:
            f = d / seg
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            d += step_m
        carry = seg - (d - step_m)
    if out[-1] != coords[-1]:
        out.append(coords[-1])
    return out


class LocalProjection:
    """Equirectangular projection to metres around an origin (fine for city-scale areas)."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.radians(1) * R * math.cos(math.radians(lat0))
        self.ky = math.radians(1) * R

    def xy(self, pts) -> np.ndarray:
        arr = np.asarray(pts, dtype=float).reshape(-1, 2)
        return np.column_stack([(arr[:, 1] - self.lon0) * self.kx, (arr[:, 0] - self.lat0) * self.ky])


def nearest_segment(P: np.ndarray, A: np.ndarray, B: np.ndarray, chunk: int = 16):
    """For each point in P, distance (m) and index of the closest segment A[i]-B[i]."""
    if len(A) == 0:
        return np.full(len(P), np.inf), np.zeros(len(P), dtype=int)
    AB = B - A
    L2 = (AB**2).sum(1) + 1e-9
    dmin, imin = np.full(len(P), np.inf), np.zeros(len(P), dtype=int)
    for i in range(0, len(P), chunk):
        p = P[i : i + chunk][:, None, :]
        t = np.clip(((p - A[None]) * AB[None]).sum(2) / L2[None], 0, 1)
        d = np.sqrt(((p - (A[None] + t[..., None] * AB[None])) ** 2).sum(2))
        j = d.argmin(1)
        dmin[i : i + chunk], imin[i : i + chunk] = d[np.arange(len(j)), j], j
    return dmin, imin


def points_in_polygon(P: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Vectorised ray casting: boolean array, True where the point is inside."""
    x, y = P[:, 0][:, None], P[:, 1][:, None]
    x1, y1 = poly[:, 0][None], poly[:, 1][None]
    x2, y2 = np.roll(poly[:, 0], -1)[None], np.roll(poly[:, 1], -1)[None]
    cond = ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1)
    return cond.sum(1) % 2 == 1
