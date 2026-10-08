from app.schemas import Constraints
from app.services.scoring import scorer as s


def test_closer_distance_scores_higher():
    vals = [s.distance_score(d, 5, 0.5, "approx") for d in (5.0, 5.4, 6.0, 7.0, 9.0)]
    assert vals == sorted(vals, reverse=True) and vals[0] == 1.0 and vals[-1] == 0.0


def test_min_and_max_modes():
    assert s.distance_score(8, 5, 0.5, "min") == 1.0 and s.distance_score(4, 5, 0.5, "min") < 1
    assert s.distance_score(3, 5, 0.5, "max") == 1.0 and s.distance_score(7, 5, 0.5, "max") < 1


def _rank(c, a, b):
    w = s.dynamic_weights(c)
    return s.combine(a, w)[0], s.combine(b, w)[0]


QUIET = {"distance": 1, "shade": 0.5, "traffic": 0.9, "surface": 1, "air_quality": 0.5, "elevation": None}
BUSY = {"distance": 1, "shade": 0.5, "traffic": 0.2, "surface": 1, "air_quality": 0.5, "elevation": None}
SHADY = {**BUSY, "shade": 0.9, "traffic": 0.5}
BARE = {**BUSY, "shade": 0.1, "traffic": 0.5}


def test_traffic_matters_when_requested():
    q, b = _rank(Constraints(traffic_preference="very_low"), QUIET, BUSY)
    assert q > b
    q2, b2 = _rank(Constraints(), QUIET, BUSY)
    assert (q - b) > (q2 - b2)  # gap is larger when the user cares


def test_shade_matters_when_requested():
    sh, bare = _rank(Constraints(shade_preference="high"), SHADY, BARE)
    sh2, bare2 = _rank(Constraints(), SHADY, BARE)
    assert sh > bare and (sh - bare) > (sh2 - bare2)


def test_aqi_affects_ranking_when_enabled():
    clean = {**BUSY, "air_quality": 0.9}
    dirty = {**BUSY, "air_quality": 0.2}
    a, b = _rank(Constraints(air_quality_priority="high"), clean, dirty)
    assert a > b
    # when AQI is missing it is dropped, not counted as zero
    score, used = s.combine({**clean, "air_quality": None}, s.dynamic_weights(Constraints()))
    assert "air_quality" not in used and abs(sum(used.values()) - 1) < 0.01


def test_air_quality_score_scale():
    assert s.air_quality_score(0, 0) == 1 and s.air_quality_score(200, 200) == 0
    assert s.air_quality_score(50, 50) == 0.75


def test_avoid_highway_penalty():
    assert s.avoid_penalty(Constraints(avoid=["highway"]), 0.2) == 0.4
    assert s.avoid_penalty(Constraints(), 0.2) == 0
