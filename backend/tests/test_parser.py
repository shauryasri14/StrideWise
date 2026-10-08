import pytest
from app.errors import AppError
from app.services.llm.parser import parse_request, parse_with_rules
from conftest import make_settings


def test_spec_example():
    c = parse_with_rules("5 km shaded run avoiding highways")
    assert (c.activity, c.distance_km, c.shade_preference, c.avoid) == ("run", 5, "high", ["highway"])


def test_destination_makes_point_to_point():
    c = parse_with_rules("5 km, shaded, low traffic, finishing near a park")
    assert c.route_shape == "point_to_point" and c.destination_type == "park" and c.traffic_preference == "low"


def test_end_at_start_is_loop():
    c = parse_with_rules("4 km walk, end at the starting point")
    assert c.route_shape == "loop" and c.destination_type == "start"


def test_distance_modes_and_miles():
    assert parse_with_rules("at least 5 km").distance_mode == "min"
    assert parse_with_rules("under 7 km").distance_mode == "max"
    assert parse_with_rules("around 5 km").distance_mode == "approx"
    assert parse_with_rules("3 miles").distance_km == pytest.approx(4.83, abs=0.01)


def test_pollution_priority():
    assert parse_with_rules("8 km run with the least pollution possible").air_quality_priority == "high"


def test_missing_distance_asks_question():
    assert parse_with_rules("a nice shaded run").clarification


def test_unsupported_destination_asks_question():
    assert "park" in parse_with_rules("5 km ending near a beach").clarification


async def test_auto_mode_falls_back_when_ollama_down():
    c, parser, warns = await parse_request("5 km run", make_settings(parser_mode="auto"))
    assert parser == "rules" and warns and c.distance_km == 5


async def test_llm_mode_fails_loudly():
    with pytest.raises(AppError) as e:
        await parse_request("5 km run", make_settings(parser_mode="llm"))
    assert e.value.code == "llm_unavailable"
