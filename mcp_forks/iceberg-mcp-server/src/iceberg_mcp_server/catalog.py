"""Load one pack catalog and run its SQL labels. No compiled query dictionaries."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import yaml

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_READ_PREFIXES = ("select", "with", "show", "describe")
_WRITE_PREFIXES = ("insert", "update", "delete")

QueryFn = Callable[[str], list[dict[str, Any]]]
DmlFn = Callable[[str], str]


def pack_dir() -> Path:
    raw = os.environ.get("PACK_DIR", "").strip()
    if not raw:
        raise RuntimeError("PACK_DIR is required")
    path = Path(raw).expanduser().resolve()
    catalog = path / "catalog" / "catalog.yaml"
    if not catalog.is_file():
        raise RuntimeError(f"PACK_DIR has no catalog/catalog.yaml: {path}")
    return path


def load_catalog(root: Path | None = None) -> dict[str, Any]:
    base = root or pack_dir()
    file = base / "catalog" / "catalog.yaml"
    document = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"Catalog must be a mapping: {file}")
    document["_path"] = file
    document["_root"] = base
    return document


def list_named_queries(root: Path | None = None) -> dict[str, Any]:
    catalog = load_catalog(root)
    reads = catalog.get("reads") or {}
    writes = catalog.get("writes") or {}
    return {
        "ok": True,
        "database": catalog.get("database"),
        "reads": sorted(reads),
        "writes": sorted(writes),
    }


def execute(
    op: str,
    label: str,
    params: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    query_rows: QueryFn | None = None,
    execute_dml: DmlFn | None = None,
) -> dict[str, Any]:
    catalog = load_catalog(root)
    kind = (op or "").strip().lower()
    if kind in {"list", "list_named_queries"}:
        listed = list_named_queries(root or catalog["_root"])
        return listed
    if kind not in {"query", "read", "write"}:
        return _error("op must be query, write, or list", op=kind or None)
    section = "reads" if kind in {"query", "read"} else "writes"
    name = (label or "").strip()
    reads = catalog.get("reads") or {}
    writes = catalog.get("writes") or {}
    spec = (reads if section == "reads" else writes).get(name)
    if not isinstance(spec, dict):
        known = sorted(reads if section == "reads" else writes)
        return _error("unknown label", label=name or None, known_labels=known)
    try:
        bound = _bind(catalog, spec, params or {})
    except ValueError as exc:
        return _error(str(exc), label=name)
    missing = [key for key in spec.get("required") or [] if bound.get(key) in (None, "")]
    if missing:
        return _error("missing required params", label=name, missing=missing)
    sql_rel = spec.get("sql")
    if not sql_rel:
        return _error(
            "label has no SQL in the pack catalog",
            label=name,
            handler=spec.get("handler"),
        )
    sql_path = (Path(catalog["_path"]).parent / str(sql_rel)).resolve()
    if not sql_path.is_file():
        return _error(f"SQL file not found: {sql_rel}", label=name)
    try:
        statement = _render(sql_path.read_text(encoding="utf-8"), bound)
    except ValueError as exc:
        return _error(str(exc), label=name)
    prefix = statement.split(None, 1)[0].lower() if statement else ""
    label_kind = "read" if section == "reads" else "write"
    if label_kind == "read" and prefix not in _READ_PREFIXES:
        return _error("read SQL must start with SELECT, WITH, SHOW, or DESCRIBE", label=name)
    if label_kind == "write" and prefix not in _WRITE_PREFIXES:
        return _error("write SQL must start with INSERT, UPDATE, or DELETE", label=name)
    try:
        if label_kind == "read":
            rows = (query_rows or _query_rows)(statement)
            return {
                "ok": True,
                "named_op": name,
                "named_op_kind": "read",
                "rows": rows,
            }
        message = (execute_dml or _execute_dml)(statement)
        return {
            "ok": True,
            "named_op": name,
            "named_op_kind": "write",
            "message": message,
        }
    except Exception as exc:
        return _error(str(exc), label=name)


def _bind(catalog: dict[str, Any], spec: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    bound = {str(key): value for key, value in params.items() if value not in (None, "")}
    if not bound.get("claim_id") and bound.get("case_id"):
        bound["claim_id"] = bound["case_id"]
    if "database" not in bound:
        bound["database"] = catalog.get("database") or os.getenv("IMPALA_DATABASE", "default")
    allowed = set(spec.get("required") or []) | set(spec.get("optional") or [])
    allowed.update({"database", "claim_id", "case_id", "fnol_id", "party_id", "submission_id"})
    extra = sorted(key for key in bound if key not in allowed)
    if extra:
        raise ValueError("unknown params: " + ", ".join(extra))
    database = str(bound["database"])
    if not _IDENT.match(database):
        raise ValueError("invalid database")
    for key in ("claim_id", "fnol_id", "party_id", "submission_id", "case_id"):
        if key in bound:
            text = str(bound[key]).strip()
            if not text.isdigit():
                raise ValueError(f"invalid {key}")
            bound[key] = str(int(text))
    return bound


def _render(sql: str, bound: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in bound or bound[key] in (None, ""):
            raise ValueError(f"SQL placeholder ${{{key}}} has no value")
        return str(bound[key])

    rendered = _PLACEHOLDER.sub(repl, sql)
    if "${" in rendered or ";" in rendered:
        raise ValueError("SQL must be one statement with known placeholders only")
    return rendered.strip()


def _query_rows(sql: str) -> list[dict[str, Any]]:
    from iceberg_mcp_server.impala import query_rows

    return query_rows(sql)


def _execute_dml(sql: str) -> str:
    from iceberg_mcp_server.impala import execute_dml

    return execute_dml(sql)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return payload


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str)
