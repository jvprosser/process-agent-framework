"""Mistral tools over the legacy catalog, session graph, and CEL playbook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from mistralai import agents

from core.config import fact_assertions_enabled
from core.pack_manager import PackManager

tool = agents.tool

_session_entity_id: str | None = None
_session_case_path: Path | None = None
_session_dialect: ContextVar[str] = ContextVar("catalog_dialect", default="impala")
_assertion_role: ContextVar[str] = ContextVar("assertion_role", default="")
_assertion_lane: ContextVar[str] = ContextVar("assertion_lane", default="")
_assertion_http_run: ContextVar[str] = ContextVar("assertion_http_run", default="")
_state_mutated: ContextVar[bool] = ContextVar("state_mutated", default=False)

_RESERVED_FIELDS = {
    "entry_exists",
    "next_step",
    "agent_role",
    "lane",
    "write",
    "allowed_tools",
    "run_id",
}


def set_catalog_dialect(dialect: str | None) -> str:
    """Remember the SQL dialect for catalog calls on this request."""
    chosen = str(dialect or "impala").strip().lower() or "impala"
    _session_dialect.set(chosen)
    return chosen


def catalog_dialect(requested: str | None, pack: PackManager) -> str:
    """Request dialect wins. Otherwise the pack manifest, then Impala."""
    chosen = str(requested or "").strip() or str(pack.manifest.get("dialect") or "").strip()
    return set_catalog_dialect(chosen or None)


@tool
def run_named_query(label: str, params: dict) -> dict:
    """Run one allow-listed catalog read by label. Does not author SQL."""
    return _run_catalog("query", label, params)


@tool
def run_named_write(label: str, params: dict) -> dict:
    """Run one allow-listed catalog write by label. Does not author SQL."""
    return _run_catalog("write", label, params)


@tool
def build_session_graph(entry_id: str) -> dict:
    """Materialize the local session entry from the active pack ontology.

    Spine and signal reads come from the allow-listed catalog. The entry file
    is the only instance document and is not written back to the lake.
    """
    global _session_entity_id, _session_case_path

    manager = PackManager.active()
    entity = str(entry_id).strip()
    if not entity:
        raise RuntimeError("entry_id is required")

    if any(part in entity for part in ("/", "\\", "..")):
        raise RuntimeError("entry_id is not a safe session name")

    spine_label = _graph_label(manager, "session_spine")
    signals_label = _graph_label(manager, "session_signals")
    params = _entity_params(manager, entity)
    spine = _as_graph_object(
        _require_read(_run_catalog("query", spine_label, params), spine_label),
        "spine",
    )
    signals = _as_graph_object(
        _require_read(_run_catalog("query", signals_label, params), signals_label),
        "signals",
    )

    from rules_engine.graph.entry_json import build_entry_graph
    from rules_engine.pack import load_pack
    from rules_engine.studio_io import graph_artifact_path, write_json_artifact

    if not (manager.root / "pack.yaml").is_file():
        raise RuntimeError(
            f"Pack '{manager.pack_name}' is missing pack.yaml; "
            "the session graph is built from that manifest and the ontology"
        )
    entry = build_entry_graph(
        entity,
        pack=load_pack(manager.root),
        spine=spine,
        signals=signals,
    )
    _project_ontology_fields(entry, manager.ontology_field_names())
    if fact_assertions_enabled():
        _apply_assertions(entry, manager, entity)
    _apply_derived(entry, manager)
    path = graph_artifact_path(entity)
    write_json_artifact(path, entry)
    _session_entity_id = entity
    _session_case_path = path
    return {
        "entity_id": entity,
        "session_case": str(path.resolve()),
        "ontology_fields": manager.ontology_field_names(),
        "field_count": len(entry),
    }


@tool
def evaluate_playbook() -> dict:
    """Evaluate the active CEL playbook against the session entry.

    The result's next_step, agent_role, and allowed_tools come only from
    the playbook. The model does not choose them.
    """
    manager = PackManager.active()
    if _session_case_path is None or _session_entity_id is None:
        raise RuntimeError(
            "No session entry is materialized. Call build_session_graph first."
        )
    if not _session_case_path.is_file():
        raise RuntimeError(f"Session entry JSON is missing: {_session_case_path}")

    from rules_engine.graph.route_entry import route_entry

    entry = json.loads(_session_case_path.read_text(encoding="utf-8"))
    if not isinstance(entry, dict):
        raise RuntimeError(f"Session entry JSON must be an object: {_session_case_path}")
    decision = route_entry(
        entry,
        _session_entity_id,
        playbook_path=manager.playbook_path,
        pack_graph=dict(manager.manifest.get("graph") or {}),
    )
    route = {
        "entity_id": _session_entity_id,
        "next_step": decision.get("next_step"),
        "agent_role": decision.get("agent_role"),
        "allowed_tools": list(decision.get("allowed_tools") or []),
        "write": decision.get("write"),
        "lane": decision.get("lane"),
        "terminal": decision.get("terminal"),
        "checks": decision.get("checks") or [],
        "routing_summary": decision.get("routing_summary"),
        "routing_reason": decision.get("routing_reason"),
        "letter_on_request": bool(decision.get("letter_on_request", False)),
        "routing_directives": list(decision.get("routing_directives") or []),
    }
    if decision.get("reason_code"):
        route["reason_code"] = decision["reason_code"]
    return route


def reset_assertion_state() -> None:
    """Clear the per-turn patch flag before a chat request."""
    _state_mutated.set(False)


def set_assertion_context(*, role: str, lane: str, http_run_id: str) -> None:
    """Stamp sidecar rows with the specialist that is speaking."""
    _assertion_role.set(role)
    _assertion_lane.set(lane)
    _assertion_http_run.set(http_run_id)


def state_mutated() -> bool:
    return bool(_state_mutated.get())


@tool
def patch_session_field(field: str, value: Any) -> dict:
    """Record one ontology field on the session sidecar.

    The playbook still chooses the next step. This does not write the lake.
    """
    if not fact_assertions_enabled():
        raise RuntimeError("Fact assertions are currently disabled.")
    manager = PackManager.active()
    name = str(field or "").strip()
    if name in _RESERVED_FIELDS or name not in set(manager.ontology_field_names()):
        raise RuntimeError(f"field is not an ontology fact: {name}")
    if name in set(getattr(manager, "derived", None) or {}):
        raise RuntimeError(f"{name} is a derived field and cannot be set directly.")
    entity = str(_session_entity_id or "").strip()
    if not entity:
        raise RuntimeError("No session entry is materialized. Call build_session_graph first.")
    from datetime import datetime, timezone

    from rules_engine.studio_io import session_dir

    path = session_dir() / f"entry_{entity}_overrides.json"
    current = _read_json_object(path)
    current[name] = {
        "value": value,
        "asserted_by_role": _assertion_role.get(),
        "lane": _assertion_lane.get(),
        "http_run_id": _assertion_http_run.get(),
        "asserted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "committed_to_lake": False,
    }
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    _state_mutated.set(True)
    return {"field": name, "committed_to_lake": False}


def flush_fact_assertions(entity_id: str) -> bool:
    """Insert uncommitted sidecar facts once. Leave them uncommitted on failure."""
    if not fact_assertions_enabled():
        return False
    manager = PackManager.active()
    label = str(manager.manifest.get("fact_assertion_write") or "").strip()
    entity = str(entity_id or "").strip()
    path = _overrides_path(entity)
    current = _read_json_object(path)
    pending = {
        key: meta
        for key, meta in current.items()
        if isinstance(meta, dict) and meta.get("committed_to_lake") is not True
    }
    if not pending or not label:
        return False
    key = str(manager.manifest.get("entry_id_param") or "").strip()
    if not key:
        raise RuntimeError(f"Pack '{manager.pack_name}' manifest is missing entry_id_param")
    assertions = []
    for field, meta in pending.items():
        assertions.append(
            {
                "field_name": field,
                "field_value": meta.get("value"),
                "lane": meta.get("lane"),
                "agent_role": meta.get("asserted_by_role"),
                "http_run_id": meta.get("http_run_id"),
                "asserted_at": meta.get("asserted_at"),
            }
        )
    params = {
        "run_id": f"run-{entity}",
        key: entity,
        "assertions_json": json.dumps(assertions).replace("'", "''"),
    }
    try:
        result = _run_catalog("write", label, params)
    except (RuntimeError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(result, dict) or result.get("ok") is False:
        return False
    for field in pending:
        current[field]["committed_to_lake"] = True
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return True


def _mcp_process(manager: PackManager) -> tuple[list[str], dict[str, str]]:
    src = Path(__file__).resolve().parents[2] / "mcp_forks" / "iceberg-mcp-server" / "src"
    env = os.environ.copy()
    env["PACK_DIR"] = str(manager.root)
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src) + (os.pathsep + previous if previous else "")
    return [sys.executable, "-m", "iceberg_mcp_server"], env


def _call_mcp(op: str, label: str, params: dict, manager: PackManager) -> dict:
    """Spawn the unified catalog server with the active pack directory."""
    argv, env = _mcp_process(manager)
    payload = dict(params)
    payload.setdefault("dialect", _session_dialect.get())
    request = json.dumps({"op": op, "label": label, "params": payload})
    completed = subprocess.run(
        [*argv, "--call", request],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stderr.strip():
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)
    stdout = completed.stdout.strip()
    if not stdout:
        detail = completed.stderr.strip() or f"exit {completed.returncode}"
        raise RuntimeError(f"iceberg-mcp-server failed: {detail}")
    result = json.loads(stdout)
    if not isinstance(result, dict):
        raise RuntimeError(f"Catalog {op} '{label}' did not return an object")
    return result


def _run_catalog(op: str, label: str, params: dict) -> dict:
    manager = PackManager.active()
    name = str(label or "").strip()
    if not name:
        raise RuntimeError("label is required")
    if not isinstance(params, dict):
        raise RuntimeError("params must be an object")
    return _call_mcp(op, name, params, manager)


def _entity_params(manager: PackManager, entity_id: str) -> dict[str, str]:
    key = str(manager.manifest.get("entry_id_param") or "").strip()
    if not key:
        raise RuntimeError(
            f"Pack '{manager.pack_name}' manifest is missing entry_id_param"
        )
    return {key: entity_id}


def _graph_label(manager: PackManager, manifest_key: str) -> str:
    chosen = str(manager.manifest.get(manifest_key) or "").strip()
    if not chosen:
        raise RuntimeError(
            f"Pack '{manager.pack_name}' manifest is missing {manifest_key}"
        )
    return chosen


def _as_graph_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Turn a multi-statement read into the object the case builder expects."""
    rows = payload.get(key)
    if not isinstance(rows, list):
        return payload
    object_row = dict(rows[0]) if rows and isinstance(rows[0], dict) else {}
    shaped = dict(payload)
    shaped[key] = object_row
    return shaped


def _require_read(payload: dict[str, Any], label: str) -> dict[str, Any]:
    if payload.get("ok") is False:
        detail = payload.get("error") or payload
        raise RuntimeError(f"Catalog read '{label}' failed: {detail}")
    return payload


def _missing_assertion_table(detail: object) -> bool:
    text = str(detail).lower()
    return (
        "table_not_found" in text
        or "could not resolve table reference" in text
        or "table does not exist" in text
        or "table not found" in text
    )


def _assertion_rows(label: str, params: dict) -> dict[str, Any]:
    """A fact table that has not been created yet is an empty overlay."""
    try:
        return _require_read(_run_catalog("query", label, params), label)
    except RuntimeError as exc:
        if _missing_assertion_table(exc):
            return {"assertions": []}
        raise


def _project_ontology_fields(entry: dict[str, Any], field_names: list[str]) -> None:
    """Keep ontology-declared keys present on the session entry."""
    for name in field_names:
        entry.setdefault(name, None)


def _apply_assertions(entry: dict[str, Any], manager: PackManager, entity: str) -> None:
    """Lake assertions overlay the spine. The sidecar wins until the lake matches."""
    graph = dict(manager.manifest.get("graph") or {})
    allowed = set(manager.ontology_field_names())
    lake: dict[str, Any] = {}
    label = str(manager.manifest.get("session_assertions") or "").strip()
    if label:
        payload = _assertion_rows(label, _entity_params(manager, entity))
        rows = payload.get("assertions")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                field = str(row.get("field_name") or "").strip()
                if field not in allowed:
                    continue
                entry[field] = row.get("field_value")
                lake[field] = row.get("field_value")
    path = _overrides_path(entity)
    overrides = _read_json_object(path)
    if not overrides:
        return
    kept = dict(overrides)
    for field, meta in list(overrides.items()):
        if not isinstance(meta, dict) or field not in allowed:
            continue
        raw = meta.get("value")
        if field in lake and _coerce_field(field, raw, graph) == _coerce_field(field, lake[field], graph):
            kept.pop(field, None)
            continue
        entry[field] = raw
    if kept != overrides:
        if kept:
            path.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
        elif path.is_file():
            path.unlink()


def _apply_derived(entry: dict[str, Any], manager: PackManager) -> None:
    """Recompute declared fields from the coerced entry before the case is saved."""
    derived = dict(getattr(manager, "derived", None) or {})
    if not derived:
        return
    graph = dict(manager.manifest.get("graph") or {})
    from rules_engine.graph.cel_rules import eval_cel_value

    for name, expr in derived.items():
        try:
            entry[name] = eval_cel_value(expr, entry, graph)
        except RuntimeError as exc:
            raise RuntimeError(f"derived field '{name}' failed: {exc}") from exc


def _coerce_field(field: str, value: Any, graph: dict[str, Any]) -> Any:
    from rules_engine.graph.cel_rules import flatten_entry

    return flatten_entry({field: value}, graph).get(field)


def _overrides_path(entity: str) -> Path:
    from rules_engine.studio_io import session_dir

    return session_dir() / f"entry_{entity}_overrides.json"


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@tool
def save_entry_artifact(entry_id: str, artifact_type: str = "letter") -> dict:
    """Write a drafted entry artifact into the session directory. Does not send it."""
    from rules_engine.studio_io import session_dir, write_text_artifact

    entity = str(entry_id).strip()
    if not entity or any(part in entity for part in ("/", "\\", "..")):
        raise ValueError("entry_id is not a safe artifact name")
    kind = "".join(ch for ch in str(artifact_type).lower() if ch.isalnum()) or "letter"
    filename = f"entry_{entity}_{kind}.txt"
    body = (
        f"{kind} draft\n"
        f"Entity: {entity}\n"
        "Saved locally. Not sent.\n"
    )
    write_text_artifact(session_dir() / filename, body)
    return {"status": "success", "artifact": filename}


CORE_TOOLS = (
    run_named_query,
    run_named_write,
    build_session_graph,
    evaluate_playbook,
)
