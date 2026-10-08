"""Transparent weighted scoring. Edit BASE_WEIGHTS / PROFILES to change behaviour.

Every component is 0..1 with 1 = better. Missing components (e.g. no AQI) are dropped and the
remaining weights are re-normalised, so a missing data source never counts as a good or bad score.
"""
from ...schemas import Constraints

BASE_WEIGHTS = {"distance": 0.25, "shade": 0.20, "traffic": 0.20, "air_quality": 0.15, "surface": 0.10, "elevation": 0.10}
LEVEL_MULT = {"none": 0.25, "low": 0.75, "medium": 1.0, "high": 2.0}
TRAFFIC_MULT = {"any": 0.25, "low": 1.5, "very_low": 2.5}

# Alternative rankings shown to the user (all keep a distance term so results stay on target).
PROFILES = {
    "lowest_pollution": {"distance": 0.25, "air_quality": 0.40, "traffic": 0.25, "shade": 0.05, "surface": 0.05},
    "quietest": {"distance": 0.25, "traffic": 0.50, "surface": 0.10, "shade": 0.10, "air_quality": 0.05},
}


def dynamic_weights(c: Constraints) -> dict[str, float]:
    """Weights for 'Best match': what the user asked for gets more weight."""
    w = dict(BASE_WEIGHTS)
    w["shade"] *= LEVEL_MULT[c.shade_preference]
    w["traffic"] *= TRAFFIC_MULT[c.traffic_preference]
    w["air_quality"] *= LEVEL_MULT[c.air_quality_priority]
    w["surface"] *= 2.0 if c.surface_preferences else 0.5
    w["elevation"] *= 0.25 if c.terrain == "any" and "steep" not in c.avoid else 1.5
    return w


def distance_score(d: float, target: float, tol: float, mode: str) -> float:
    dev = max(0.0, target - d) if mode == "min" else max(0.0, d - target) if mode == "max" else abs(d - target)
    if dev <= tol:
        return 1.0 - 0.1 * dev / tol
    return max(0.0, 0.9 - 0.9 * (dev - tol) / (4 * tol))


def air_quality_score(avg_aqi: float, max_aqi: float) -> float:
    """AQI 0 -> 1.0, 100 -> 0.5, 200+ -> 0. Mixes average (70%) and worst point (30%)."""
    return max(0.0, min(1.0, 1 - (0.7 * avg_aqi + 0.3 * max_aqi) / 200))


def surface_score(frac: dict, prefs: list[str]) -> float | None:
    if frac.get("paved") is None:
        return None
    paved, unpaved, unk = frac["paved"], frac["unpaved"], frac["unknown"]
    if "trail" in prefs or "dirt" in prefs:
        val = (0.4, 1.0, 0.7)
    elif "avoid_unpaved" in prefs:
        val = (1.0, 0.0, 0.5)
    elif prefs:
        val = (1.0, 0.2, 0.7)
    else:
        val = (1.0, 0.6, 0.8)
    return paved * val[0] + unpaved * val[1] + unk * val[2]


def elevation_score(gain_m: float | None, dist_km: float, terrain: str) -> float | None:
    if gain_m is None:
        return None
    g = gain_m / max(dist_km, 0.1)  # metres climbed per km
    if terrain == "hilly":
        return min(1.0, g / 40)
    if terrain == "moderate":
        return max(0.0, 1 - abs(g - 20) / 40)
    return max(0.0, 1 - g / (40 if terrain == "flat" else 60))


def combine(components: dict[str, float | None], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    used = {k: weights.get(k, 0.0) for k, v in components.items() if v is not None and weights.get(k, 0.0) > 0}
    total = sum(used.values()) or 1.0
    norm = {k: round(v / total, 3) for k, v in used.items()}
    return sum(components[k] * w for k, w in norm.items()), norm


def avoid_penalty(c: Constraints, major_fraction: float | None) -> float:
    if major_fraction and ("highway" in c.avoid or "major_roads" in c.avoid):
        return min(0.5, major_fraction * 2)
    return 0.0


def score_candidate(components: dict, weights: dict, c: Constraints, major_fraction: float | None):
    raw, used = combine(components, weights)
    pen = avoid_penalty(c, major_fraction)
    return raw * (1 - pen), used, pen


def pick_top(cands: list[dict], c: Constraints, has_aqi: bool, n_best: int = 3) -> list[tuple[str, str, dict]]:
    """cands: dicts with 'components' and 'major_fraction'. Returns [(profile, label, cand)] without repeats."""
    profiles = {"best_match": dynamic_weights(c), **PROFILES}
    labels = {"best_match": "Best match", "lowest_pollution": "Lowest pollution" if has_aqi else "Lowest road exposure", "quietest": "Quietest"}
    picked, seen = [], set()
    for name, w in profiles.items():
        ranked = sorted(cands, key=lambda x: score_candidate(x["components"], w, c, x["major_fraction"])[0], reverse=True)
        choice = next((x for x in ranked if x["id"] not in seen), None)
        if choice is None:
            continue
        seen.add(choice["id"])
        choice["profile_scores"] = choice.get("profile_scores", {})
        score, used, pen = score_candidate(choice["components"], w, c, choice["major_fraction"])
        picked.append((name, labels[name], {**choice, "score": score, "weights_used": used, "avoid_penalty": pen}))
    return picked[:n_best]
