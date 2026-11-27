"""Deterministic lane-chat classification. No model call and no domain names."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from core.pack_manager import PackManager

_RESERVED = {
    "entry_exists",
    "next_step",
    "agent_role",
    "lane",
    "write",
    "allowed_tools",
    "run_id",
}
_GRAPH_KINDS = (
    ("strings", "string"),
    ("numbers", "number"),
    ("booleans", "boolean"),
    ("lists", "list"),
)
_ASK = re.compile(
    r"^\s*(what|which|who|where|when|why|how|is|are|does|do|did|can|could|has|have)\b",
    re.IGNORECASE,
)
_VALUE = re.compile(r"\bis\b\s*|[=:]\s*", re.IGNORECASE)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_TRUE = {"true", "yes", "1"}
_FALSE = {"false", "no", "0"}


@dataclass(frozen=True)
class Turn:
    kind: str
    field_id: str | None = None
    value: str | None = None


def classify_turn(user_text: str, pack: PackManager) -> Turn:
    """Fact or question when exactly one ontology phrase remains. Otherwise a directive."""
    text = str(user_text or "")
    spans = _match_spans(text, _catalog(pack))
    if len(spans) != 1:
        return Turn("directive")
    field_id, _start, end = spans[0]
    value = _value_span(text, end)
    if value is not None:
        return Turn("fact", field_id, value)
    if "?" in text or _ASK.match(text):
        return Turn("question", field_id)
    return Turn("directive")


def coerce_fact_value(field_id: str, span: str, pack: PackManager) -> Any:
    """Strict graph coercion. Invalid spans raise ValueError."""
    kind = _kind(pack, field_id)
    text = str(span or "").strip()
    if kind == "string":
        if not text:
            raise ValueError("empty string")
        return text
    if kind == "boolean":
        token = text.casefold()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
        raise ValueError("boolean")
    if kind == "number":
        if not _NUMBER.fullmatch(text):
            raise ValueError("number")
        number = float(text)
        if not math.isfinite(number):
            raise ValueError("number")
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        return number
    if kind == "list":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("list") from exc
        if not isinstance(parsed, list):
            raise ValueError("list")
        return parsed
    raise ValueError("type")


def declared_lanes(playbook: dict[str, Any]) -> set[str]:
    """Lane strings declared on actions, the default, and exception review."""
    found: set[str] = set()
    actions = playbook.get("actions") or {}
    if isinstance(actions, dict):
        for entries in actions.values():
            if not isinstance(entries, list):
                continue
            for action in entries:
                _add_lane(found, action)
    for key in ("default_action", "exception_review"):
        _add_lane(found, playbook.get(key))
    return found


def _add_lane(found: set[str], action: Any) -> None:
    if not isinstance(action, dict):
        return
    lane = str(action.get("lane") or "").strip()
    if lane:
        found.add(lane)


def _catalog(pack: PackManager) -> list[str]:
    names = set(pack.ontology_field_names())
    typed = {name for name, _kind_name in _typed_fields(pack)}
    derived = set(getattr(pack, "derived", None) or {})
    return sorted(name for name in names if name not in _RESERVED and (name in typed or name in derived))


def _typed_fields(pack: PackManager) -> list[tuple[str, str]]:
    graph = pack.manifest.get("graph") or {}
    found: list[tuple[str, str]] = []
    if not isinstance(graph, dict):
        return found
    for key, kind in _GRAPH_KINDS:
        for name in graph.get(key) or []:
            found.append((str(name), kind))
    return found


def _kind(pack: PackManager, field_id: str) -> str | None:
    for name, kind in _typed_fields(pack):
        if name == field_id:
            return kind
    return None


def _phrases(field_id: str) -> list[str]:
    spaced = field_id.replace("_", " ")
    if spaced.casefold() == field_id.casefold():
        return [field_id]
    return [field_id, spaced]


def _match_spans(text: str, fields: list[str]) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    for field_id in fields:
        for phrase in _phrases(field_id):
            pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
            for match in pattern.finditer(text):
                found.append((field_id, match.start(), match.end()))
    kept = [
        span
        for span in found
        if not any(_contains(other, span) for other in found if other != span)
    ]
    unique: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()
    for span in kept:
        if span in seen:
            continue
        seen.add(span)
        unique.append(span)
    return unique


def _contains(outer: tuple[str, int, int], inner: tuple[str, int, int]) -> bool:
    return outer[1] <= inner[1] and inner[2] <= outer[2] and (outer[1] < inner[1] or inner[2] < outer[2])


def _value_span(text: str, end: int) -> str | None:
    match = _VALUE.search(text[end:])
    if match is None:
        return None
    trimmed = text[end:][match.end() :].strip()
    while trimmed and trimmed[-1] in ".!?":
        trimmed = trimmed[:-1].rstrip()
    return trimmed or None
