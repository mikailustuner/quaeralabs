"""Structured output layer (capacity plan R1): a parse failure never ends a research while budget remains.

Levels, cheapest first:
1. native JSON mode on API providers that support it (the gateway passes `json_mode`),
2. lenient parse (`gateway.parse_json`) + field coercion against the expected shape of the prompt,
3. a repair pass: a cheap-profile call that turns the broken answer into the expected JSON (no new reasoning),
4. the original question again (shorter, stricter), then on another family (orchestrator),
5. degradation: call sites that can continue without the answer return their default; the others stop.

Shapes are deliberately loose (required keys and their container types); content rules stay in the contracts
(`contracts.py`) and the store, which still reject an invalid object.
"""

from __future__ import annotations

import json
import re

from .gateway import ModelError, parse_json

# Purpose (prompt name) -> {key: type}; "list" / "dict" / "str" / "num" / "bool". Only the top level is coerced.
SHAPES: dict[str, dict[str, str]] = {
    "HYPOTHESIS": {"hypotheses": "list"},
    "HYPOTHESIS_ML": {"hypotheses": "list"},
    "RANK_HYPOTHESES": {"ranking": "list"},
    "CRITIC_STATEMENT": {"faithful": "bool", "issues": "list"},
    "CRITIC_RESULT": {"concerns": "list"},
    "CRITIC_EXPERIMENT": {"issues": "list"},
    "BACKTRANSLATE": {"translation": "str", "oddities": "list"},
    "LANDSCAPE": {"approaches": "list", "barriers": "list"},
    "TARGET": {"statement": "str"},
    "IDEATE": {"strategies": "list"},
    "CROSS_REVIEW": {"plausibility": "num", "novelty": "num", "barrierAwareness": "num", "testability": "num"},
    "PROGRAM": {"lemmas": "list"},
    "REPAIR": {"decision": "str"},
    "REVISE": {"decision": "str"},
    "PROVE_STEP": {"tactics": "list"},
}

REPAIR_JSON = """You convert a model's answer into valid JSON. Do not add new reasoning, do not change the content.
Keep every field the answer contains; fill a missing required field with an empty value of the right type.
Return ONLY the JSON object, nothing else."""

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _coerce(value, kind: str):
    if kind == "list":
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
        if isinstance(value, str) and value.strip():
            return [value]
        return []
    if kind == "dict":
        return value if isinstance(value, dict) else {}
    if kind == "str":
        if isinstance(value, str):
            return value
        return "" if value is None else json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    if kind == "num":
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return value
        m = _NUM.search(str(value or ""))        # "7/10", "score: 7" → 7
        return float(m.group()) if m else 0.0
    if kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "yes", "1", "faithful")
    return value


def normalize(obj, purpose: str):
    """Coerces the top-level fields of a parsed answer to the expected shape. A list where an object was expected is
    wrapped when the shape has exactly one list field (models often return the bare list)."""
    shape = SHAPES.get(purpose)
    if shape is None:
        if not isinstance(obj, dict):
            raise ValueError(f"expected a JSON object, got {type(obj).__name__}")
        return obj
    if isinstance(obj, list):
        lists = [k for k, t in shape.items() if t == "list"]
        if len(lists) != 1:
            raise ValueError("expected a JSON object, got a list")
        obj = {lists[0]: obj}
    if not isinstance(obj, dict):
        raise ValueError(f"expected a JSON object, got {type(obj).__name__}")
    out = dict(obj)
    for key, kind in shape.items():
        if key in out:
            out[key] = _coerce(out[key], kind)
    return out


def parse(text: str, purpose: str):
    return normalize(parse_json(text), purpose)


def repair_prompt(text: str, purpose: str, system: str) -> str:
    keys = SHAPES.get(purpose)
    want = (f"Required top-level fields: {json.dumps(keys)}." if keys else "The answer must be one JSON object.")
    spec = re.findall(r"Return only JSON:?\s*(.*)", system, re.S)
    return (f"{want}\nThe original instructions asked for:\n{(spec[0] if spec else '')[:1500]}\n\n"
            f"Answer to convert:\n{text[-12000:]}")


def try_parse(text: str, purpose: str):
    try:
        return parse(text, purpose), None
    except (ModelError, ValueError) as exc:
        return None, exc
