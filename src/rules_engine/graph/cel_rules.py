"""Evaluate playbook CEL expressions against an entry document."""

from __future__ import annotations

from datetime import date
from typing import Any

import celpy
from celpy.adapter import json_to_cel
from celpy.celtypes import BoolType
from celpy.evaluation import CELEvalError

_ENV = celpy.Environment()
_PROGRAMS: dict[str, Any] = {}


def flatten_entry(entry: dict[str, Any], pack_graph: dict[str, Any]) -> dict[str, Any]:
    """Lift nested signals onto the root the same way YAML get_path did."""
    out = {k: v for k, v in entry.items() if k != "signals"}
    nested = entry.get("signals")
    if isinstance(nested, dict):
        inner = nested.get("signals") if isinstance(nested.get("signals"), dict) else nested
        if isinstance(inner, dict):
            for key, val in inner.items():
                if key == "signals":
                    continue
                cur = out.get(key)
                empty = cur is None or cur is False or cur == [] or cur == ""
                if empty:
                    if val not in (None, False, "", []):
                        out[key] = val
                    elif cur is None:
                        out[key] = val
    list_keys = pack_graph.get("lists") or []
    str_keys = pack_graph.get("strings") or []
    num_keys = pack_graph.get("numbers") or []
    bool_keys = pack_graph.get("booleans") or []
    for key in list_keys:
        if not isinstance(out.get(key), list):
            out[key] = []
    for key in str_keys:
        value = out.get(key)
        if value in (None,):
            out[key] = ""
        elif not isinstance(value, (list, dict, bool)):
            out[key] = str(value).strip()
    for key in num_keys:
        raw = out.get(key)
        if isinstance(raw, bool) or raw in (None, ""):
            out[key] = 0.0
        elif isinstance(raw, (int, float)):
            out[key] = float(raw)
        else:
            try:
                out[key] = float(raw)
            except (TypeError, ValueError):
                out[key] = 0.0
    for key in bool_keys:
        value = out.get(key)
        if value in (None, ""):
            out[key] = False
        elif not isinstance(value, bool):
            out[key] = str(value).strip().lower() in {"true", "1", "yes"}
    out["eval_date"] = date.today().isoformat()
    return out


def compile_cel(expr: str) -> Any:
    text = (expr or "").strip()
    if not text:
        raise ValueError("CEL expression is empty")
    program = _PROGRAMS.get(text)
    if program is None:
        program = _ENV.program(_ENV.compile(text))
        _PROGRAMS[text] = program
    return program


def eval_cel(expr: str, entry: dict[str, Any], pack_graph: dict[str, Any]) -> bool:
    """Return True when the CEL expression is true on the flattened entry."""
    program = compile_cel(expr)
    activation = json_to_cel(flatten_entry(entry, pack_graph))
    try:
        result = program.evaluate(activation)
    except CELEvalError:
        return False
    if isinstance(result, CELEvalError):
        return False
    return bool(result)


def eval_cel_value(expr: str, entry: dict[str, Any], pack_graph: dict[str, Any]) -> Any:
    """Return the CEL value evaluated on the graph-coerced entry."""
    program = compile_cel(expr)
    activation = json_to_cel(flatten_entry(entry, pack_graph))
    try:
        result = program.evaluate(activation)
    except CELEvalError as exc:
        raise RuntimeError(str(exc)) from exc
    if isinstance(result, CELEvalError):
        raise RuntimeError(str(result))
    return _cel_python(result)


def _cel_python(result: Any) -> Any:
    if isinstance(result, BoolType):
        return bool(result)
    if isinstance(result, bool):
        return result
    if isinstance(result, str):
        return str(result)
    if isinstance(result, int):
        return int(result)
    if isinstance(result, float):
        return float(result)
    if isinstance(result, list):
        return [_cel_python(item) for item in result]
    return result
