"""Load one pack catalog and run its SQL labels. No compiled query dictionaries."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN = re.compile(
    r"\$\{var:(?P<var>[A-Za-z_][A-Za-z0-9_]*)\}"
    r"|\$\{(?P<dollar>[A-Za-z_][A-Za-z0-9_]*)\}"
    r"|\{(?P<brace>[A-Za-z_][A-Za-z0-9_]*)\}"
)
_QUOTED_BRACE = re.compile(r"'\{([A-Za-z_][A-Za-z0-9_]*)\}'")
_QUOTED_DOLLAR = re.compile(r"'\$\{(?:var:)?([A-Za-z_][A-Za-z0-9_]*)\}'")
_READ_PREFIXES = ("select", "with", "show", "describe", "refresh")
_QUERY_PREFIXES = ("select", "with", "show", "describe")
_WRITE_PREFIXES = ("insert", "update", "delete", "select", "with")

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
    label_kind = "read" if section == "reads" else "write"
    try:
        statements = _statements(catalog, spec)
    except ValueError as exc:
        return _error(str(exc), label=name)
    if statements is None:
        return _error(
            "label has no SQL in the pack catalog",
            label=name,
            handler=spec.get("handler"),
        )
    if not statements:
        if label_kind == "write":
            return {
                "ok": True,
                "named_op": name,
                "named_op_kind": "write",
                "mode": "table_append",
                "statements": [],
            }
        return _error("label has no SQL in the pack catalog", label=name)
    try:
        rendered = [_render(sql, _values(bound)) for _name, sql in statements]
    except ValueError as exc:
        return _error(str(exc), label=name)
    for statement in rendered:
        prefix = statement.split(None, 1)[0].lower() if statement else ""
        allowed = _READ_PREFIXES if label_kind == "read" else _WRITE_PREFIXES
        if prefix not in allowed:
            return _error(f"statement cannot start with {prefix or 'empty'}", label=name)
    _log_sql(name, rendered)
    try:
        ran = _run_all(rendered, query_rows=query_rows, execute_dml=execute_dml)
    except Exception as exc:
        return _error(str(exc), label=name)
    _log_rows(name, ran)
    return _payload(name, label_kind, statements, ran)


def _log_sql(label: str, statements: list[str]) -> None:
    for sql in statements:
        print(f"iceberg-mcp-server SQL [{label}]: {sql}", file=sys.stderr, flush=True)


def _log_rows(label: str, ran: list[dict[str, Any]]) -> None:
    for item in ran:
        if item.get("kind") != "read":
            continue
        rows = item.get("rows") or []
        print(
            f"iceberg-mcp-server rows [{label}]: {len(rows)} {json.dumps(rows, default=str)}",
            file=sys.stderr,
            flush=True,
        )


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
            if key == "fnol_id" and text.upper().startswith("FNOL-"):
                text = text.rsplit("-", 1)[-1]
            if not text.isdigit():
                raise ValueError(f"invalid {key}")
            bound[key] = str(int(text))
    return bound


def _statements(catalog: dict[str, Any], spec: dict[str, Any]) -> list[tuple[str | None, str]] | None:
    raw = spec.get("sql", spec.get("statements", None))
    if raw is None:
        return None
    if isinstance(raw, list):
        found: list[tuple[str | None, str]] = []
        for item in raw:
            if isinstance(item, str):
                found.extend(split_statements(item))
            elif isinstance(item, dict):
                name = item.get("name") or item.get("result")
                body = item.get("statement") or item.get("sql")
                if body is None and item.get("file"):
                    body = _read_sql(catalog, str(item["file"]))
                if body is None:
                    raise ValueError("SQL list entry needs statement text or a file")
                pieces = split_statements(str(body))
                if name and len(pieces) == 1:
                    found.append((str(name), pieces[0][1]))
                else:
                    found.extend(pieces)
            else:
                raise ValueError("SQL list entries must be strings or mappings")
        return found
    text = _read_sql(catalog, str(raw)) if not _looks_like_sql(str(raw)) else str(raw)
    return split_statements(text)


def _looks_like_sql(text: str) -> bool:
    prefix = text.strip().split(None, 1)[0].lower() if text.strip() else ""
    return prefix in _READ_PREFIXES or prefix in ("insert", "update", "delete")


def _read_sql(catalog: dict[str, Any], relative: str) -> str:
    path = (Path(catalog["_path"]).parent / relative).resolve()
    if not path.is_file():
        raise ValueError(f"SQL file not found: {relative}")
    return path.read_text(encoding="utf-8")


def split_statements(text: str) -> list[tuple[str | None, str]]:
    """Split a script on semicolons. `-- result: name` names the next statement."""
    statements: list[tuple[str | None, str]] = []
    name: str | None = None
    buf: list[str] = []
    in_quote = False
    i = 0

    def flush() -> None:
        nonlocal name
        sql = "".join(buf).strip()
        buf.clear()
        if sql:
            statements.append((name, sql))
        name = None

    while i < len(text):
        at_line = i == 0 or text[i - 1] == "\n"
        if at_line and not in_quote and text.startswith("-- result:", i):
            flush()
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            name = text[i + len("-- result:") : end].strip() or None
            i = end + 1
            continue
        if at_line and not in_quote and text.startswith("--", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end + 1
            continue
        char = text[i]
        if char == "'":
            in_quote = not in_quote
            buf.append(char)
        elif char == ";" and not in_quote:
            flush()
        else:
            buf.append(char)
        i += 1
    flush()
    return statements


def _values(bound: dict[str, Any]) -> dict[str, Any]:
    values = dict(bound)
    for key in ("event_json", "evidence_json"):
        raw = values.get(key)
        payload = raw if isinstance(raw, dict) else None
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                payload = parsed
        if isinstance(payload, dict):
            for field, value in payload.items():
                values.setdefault(str(field), value)
    if values.get("claim_id") in (None, "") and values.get("case_id") not in (None, ""):
        values["claim_id"] = values["case_id"]
    return values


def _render(sql: str, bound: dict[str, Any]) -> str:
    def quoted(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in bound or bound[key] in (None, ""):
            return "NULL"
        return "'" + str(bound[key]).replace("'", "''") + "'"

    sql = _QUOTED_DOLLAR.sub(quoted, sql)
    sql = _QUOTED_BRACE.sub(quoted, sql)

    def repl(match: re.Match[str]) -> str:
        key = match.group("var") or match.group("dollar") or match.group("brace")
        assert key is not None
        if key not in bound or bound[key] in (None, ""):
            if match.group("brace"):
                return "NULL"
            raise ValueError(f"SQL placeholder ${{{key}}} has no value")
        return str(bound[key]).replace("'", "''")

    rendered = _TOKEN.sub(repl, sql).strip()
    if "${" in rendered or re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", rendered):
        raise ValueError("SQL placeholder was not substituted")
    return rendered


def _run_all(
    statements: list[str],
    *,
    query_rows: QueryFn | None,
    execute_dml: DmlFn | None,
) -> list[dict[str, Any]]:
    if query_rows is None and execute_dml is None:
        from iceberg_mcp_server.impala import run_statements

        return run_statements(statements)
    results: list[dict[str, Any]] = []
    for sql in statements:
        prefix = sql.split(None, 1)[0].lower()
        if prefix in _QUERY_PREFIXES:
            rows = (query_rows or _query_rows)(sql)
            results.append({"kind": "read", "rows": rows})
        else:
            message = (execute_dml or _execute_dml)(sql)
            results.append({"kind": "write", "message": message})
    return results


def _payload(
    name: str,
    label_kind: str,
    named: list[tuple[str | None, str]],
    ran: list[dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "named_op": name,
        "named_op_kind": label_kind,
    }
    reads = [
        (label, item["rows"])
        for (label, _sql), item in zip(named, ran)
        if item["kind"] == "read"
    ]
    if label_kind == "read" and len(reads) == 1 and reads[0][0] is None:
        payload["rows"] = reads[0][1]
        return payload
    for index, (label, rows) in enumerate(reads, start=1):
        payload[label or f"statement_{index}"] = rows
    messages = [item.get("message") for item in ran if item["kind"] == "write"]
    if messages:
        payload["messages"] = messages
    return payload


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
