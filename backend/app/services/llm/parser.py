"""Natural language -> Constraints.

Primary path: a local Ollama model constrained to the Constraints JSON schema.
Fallback path: a deterministic keyword parser (also used for tests / when Ollama is down).
The LLM never produces coordinates, roads or map facts, only the structured request.
"""
import json
import re

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...errors import AppError
from ...schemas import Constraints

SYSTEM_PROMPT = """You convert a runner's or walker's request into JSON that matches the given schema.
Rules:
- Extract only what the user actually said. Use schema defaults for everything else.
- Never invent places, coordinates, distances or road names.
- 'run', 'jog' -> activity "run". distance_mode: "min" for 'at least', "max" for 'under'/'up to', else "approx".
- shade_preference "high" for 'shaded'/'mostly shaded'; traffic_preference "low" for 'low traffic'/'quiet'.
- If the distance is missing, leave distance_km null and write a short question in "clarification".
Return only JSON."""

MILE_KM = 1.609344
DESTINATIONS = {
    "park": "park", "parks": "park", "garden": "park", "gardens": "park",
    "metro": "metro", "subway": "metro", "station": "metro", "metro station": "metro",
    "cafe": "cafe", "coffee": "cafe", "coffee shop": "cafe",
}


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text) is not None


def finalize(c: Constraints) -> Constraints:
    """Apply consistency rules shared by the LLM and rule-based paths."""
    if c.destination_type == "start":
        c.route_shape = "loop"
    if c.distance_km is None and not c.clarification:
        c.clarification = "How far would you like to go? For example: '5 km'."
    if c.distance_km is not None and c.distance_mode != "approx" and c.distance_tolerance_km is None:
        c.distance_tolerance_km = 0.5 if c.distance_mode != "exact" else 0.25
    if ("highway" in c.avoid or "major_roads" in c.avoid) and c.traffic_preference == "any":
        c.traffic_preference = "low"
    if "steep" in c.avoid and c.terrain == "any":
        c.terrain = "flat"
    return c


def parse_with_rules(prompt: str) -> Constraints:
    t = prompt.lower().strip()
    c = Constraints()

    m = re.search(r"(\d+(?:\.\d+)?)\s*(km|kms|kilometers?|kilometres?|k|mi|miles?)\b", t)
    if m:
        value = float(m.group(1)) * (MILE_KM if m.group(2).startswith("mi") else 1.0)
        c.distance_km = round(value, 2)
        before = t[max(0, m.start() - 18) : m.start()]
        if _has(before, r"at least|minimum|min\b|more than|over"):
            c.distance_mode = "min"
        elif _has(before, r"under|maximum|max\b|at most|less than|no more than|up to|within"):
            c.distance_mode = "max"
        elif _has(before, r"exactly"):
            c.distance_mode = "exact"

    if _has(t, r"\b(run|running|jog|jogging)\b"):
        c.activity = "run"
    elif _has(t, r"\b(cycle|cycling|bike|biking)\b"):
        c.activity = "cycle"

    if _has(t, r"some shade|bit of shade|partly shaded"):
        c.shade_preference = "medium"
    elif _has(t, r"shad(e|ed|y)\b|\btrees?\b|tree-lined"):
        c.shade_preference = "high"

    if _has(t, r"car.free|traffic.free|pedestrian.friendly|no traffic|pedestrian only"):
        c.traffic_preference = "very_low"
    elif _has(t, r"low traffic|quiet|calm|avoid (busy|major|main) roads|away from (busy|major) roads"):
        c.traffic_preference = "low"

    if _has(t, r"highway|motorway|expressway"):
        c.avoid.append("highway")
    if _has(t, r"(avoid|no|without|skip)\s+(busy|major|main)\s+roads|major roads"):
        c.avoid.append("major_roads")
    if _has(t, r"(avoid|no|not too|without)\s+(steep|hilly|hills?)"):
        c.avoid.append("steep")

    if _has(t, r"moderate hills?"):
        c.terrain = "moderate"
    elif _has(t, r"hills? preferred|prefer hills|hill training|hilly route"):
        c.terrain = "hilly"
    elif _has(t, r"\bflat\b"):
        c.terrain = "flat"

    if _has(t, r"avoid (unpaved|dirt|gravel)|no unpaved"):
        c.surface_preferences.append("avoid_unpaved")
    else:
        if _has(t, r"\bpaved\b"):
            c.surface_preferences.append("paved")
        if _has(t, r"\bsidewalks?\b"):
            c.surface_preferences.append("sidewalk")
        if _has(t, r"\btrails?\b"):
            c.surface_preferences.append("trail")
        if _has(t, r"\bdirt\b"):
            c.surface_preferences.append("dirt")

    if _has(t, r"pollut|\baqi\b|clean air|air quality"):
        strong = _has(t, r"least|cleanest|clean air|mainly|most|priorit|lowest")
        c.air_quality_priority = "high" if strong else "medium"

    dest = re.search(r"\b(?:finish(?:ing)?|end(?:ing)?|stop(?:ping)?)\s+(?:at|near|by|close to|next to)\s+(?:a|an|the|my)?\s*([a-z]+(?:\s[a-z]+)?)", t)
    if dest:
        word = dest.group(1).strip()
        if word.startswith("start") or word.startswith("where i started") or word == "home":
            c.destination_type = "start"
        else:
            key = next((w for w in (word, word.split()[0]) if w in DESTINATIONS), None)
            if key:
                c.destination_type = DESTINATIONS[key]
                c.route_shape = "point_to_point"
            else:
                c.clarification = "I can end a route near a park, a metro station or a cafe. Which would you like?"
    if _has(t, r"out.and.back"):
        c.route_shape = "out_and_back"
    elif _has(t, r"point.to.point|one.way") and c.destination_type is None:
        c.route_shape = "point_to_point"
    elif _has(t, r"\bloop\b|circuit|round trip") and c.destination_type in (None, "start"):
        c.route_shape = "loop"
    return finalize(c)


async def parse_with_llm(prompt: str, s: Settings, client: httpx.AsyncClient | None = None) -> Constraints:
    body = {
        "model": s.ollama_model,
        "stream": False,
        "format": Constraints.model_json_schema(),
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
    }
    own = client is None
    client = client or httpx.AsyncClient(timeout=60)
    try:
        r = await client.post(f"{s.ollama_url}/api/chat", json=body)
        r.raise_for_status()
        return finalize(Constraints.model_validate_json(r.json()["message"]["content"]))
    except (httpx.HTTPError, KeyError) as e:
        raise AppError(503, "llm_unavailable", f"Ollama is not reachable at {s.ollama_url}: {e}") from e
    except (ValidationError, json.JSONDecodeError) as e:
        raise AppError(502, "llm_invalid_json", "The local model returned JSON that does not match the schema.") from e
    finally:
        if own:
            await client.aclose()


async def parse_request(prompt: str, s: Settings) -> tuple[Constraints, str, list[str]]:
    """Returns (constraints, parser_used, warnings)."""
    if s.parser_mode == "rules":
        return parse_with_rules(prompt), "rules", []
    try:
        return await parse_with_llm(prompt, s), "llm", []
    except AppError as e:
        if s.parser_mode == "llm":
            raise
        warn = f"Local LLM unavailable or invalid ({e.code}); used the built-in keyword parser instead."
        return parse_with_rules(prompt), "rules", [warn]
