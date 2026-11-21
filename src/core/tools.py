"""Mistral tools over the legacy catalog, session graph, and CEL playbook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mistralai import agents

from core.pack_manager import PackManager

tool = agents.tool

_session_entity_id: str | None = None
_session_case_path: Path | None = None


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

    spine_label = _graph_label(manager, "_spine", "session_spine")
    signals_label = _graph_label(manager, "_routing_signals", "session_signals")
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
    }
    if decision.get("reason_code"):
        route["reason_code"] = decision["reason_code"]
    return route


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
    request = json.dumps({"op": op, "label": label, "params": params})
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
    key = str(manager.manifest.get("case_id_param") or "").strip()
    if not key:
        raise RuntimeError(
            f"Pack '{manager.pack_name}' manifest is missing case_id_param"
        )
    return {key: entity_id}


def _graph_label(manager: PackManager, suffix: str, manifest_key: str) -> str:
    chosen = str(manager.manifest.get(manifest_key) or "").strip()
    if chosen:
        return chosen
    return _single_label(manager.catalog, suffix)


def _as_graph_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Turn a multi-statement read into the object the case builder expects."""
    rows = payload.get(key)
    if not isinstance(rows, list):
        return payload
    object_row = dict(rows[0]) if rows and isinstance(rows[0], dict) else {}
    shaped = dict(payload)
    shaped[key] = object_row
    if key == "spine" and isinstance(payload.get("roles"), list):
        object_row["roles"] = payload["roles"]
    return shaped


def _single_label(catalog: dict[str, Any], suffix: str) -> str:
    reads = catalog.get("reads") or {}
    matches = [name for name in reads if str(name).endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(
            f"Catalog must declare exactly one read ending in '{suffix}'. "
            f"Found: {', '.join(sorted(matches)) or 'none'}"
        )
    return matches[0]


def _require_read(payload: dict[str, Any], label: str) -> dict[str, Any]:
    if payload.get("ok") is False:
        detail = payload.get("error") or payload
        raise RuntimeError(f"Catalog read '{label}' failed: {detail}")
    return payload


def _project_ontology_fields(entry: dict[str, Any], field_names: list[str]) -> None:
    """Keep ontology-declared keys present on the session entry."""
    for name in field_names:
        entry.setdefault(name, None)


_SMS_LETTER_TYPES = frozenset({"sms", "collectincidentreportnumber"})


@tool
def save_claim_letter(entry_id: str, letter_type: str = "letter") -> dict:
    """Write a drafted letter or SMS copy into the session directory. Does not send it."""
    from rules_engine.studio_io import session_dir, write_text_artifact

    entity = str(entry_id).strip()
    if not entity or any(part in entity for part in ("/", "\\", "..")):
        raise ValueError("entry_id is not a safe artifact name")
    kind = "sms" if _is_sms(letter_type) else "letter"
    filename = f"claim_{entity}_{kind}.txt"
    heading = "SMS draft" if kind == "sms" else "Letter draft"
    body = (
        f"{heading}\n"
        f"Entity: {entity}\n"
        f"Letter type: {letter_type}\n"
        "Saved locally. Not sent.\n"
    )
    write_text_artifact(session_dir() / filename, body)
    return {"status": "success", "artifact": filename}


def _is_sms(letter_type: str) -> bool:
    token = "".join(ch for ch in str(letter_type).lower() if ch.isalnum())
    return token in _SMS_LETTER_TYPES


CORE_TOOLS = (
    run_named_query,
    run_named_write,
    build_session_graph,
    evaluate_playbook,
)
