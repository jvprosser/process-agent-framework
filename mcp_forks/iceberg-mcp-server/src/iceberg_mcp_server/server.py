"""Stdio server for the four catalog tools. PACK_DIR selects the pack."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from iceberg_mcp_server import catalog

TOOLS = (
    "get_server_info",
    "list_named_queries",
    "run_named_query",
    "run_named_write",
)


def get_server_info() -> dict[str, Any]:
    return {
        "ok": True,
        "server": "iceberg-mcp-server",
        "registered_tools": list(TOOLS),
        "pack_dir": str(catalog.pack_dir()),
    }


def dispatch(message: dict[str, Any]) -> dict[str, Any]:
    tool = str(message.get("tool") or message.get("op") or "").strip()
    if tool in {"get_server_info", "server_info"}:
        return get_server_info()
    if tool in {"list_named_queries", "list"}:
        return catalog.list_named_queries()
    if tool in {"run_named_query", "query", "read"}:
        return catalog.execute("query", str(message.get("label") or ""), message.get("params") or {})
    if tool in {"run_named_write", "write"}:
        return catalog.execute("write", str(message.get("label") or ""), message.get("params") or {})
    if message.get("op") and message.get("label"):
        return catalog.execute(str(message["op"]), str(message["label"]), message.get("params") or {})
    return {"ok": False, "error": "unknown tool", "tool": tool or None}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Unified Iceberg catalog server")
    parser.add_argument("--pack-dir", default="", help="Pack directory. Overrides PACK_DIR.")
    parser.add_argument("--call", default="", help="One JSON request. Prints one JSON response.")
    args = parser.parse_args(argv)
    if args.pack_dir:
        import os

        os.environ["PACK_DIR"] = args.pack_dir
    if args.call:
        message = json.loads(args.call)
        if not isinstance(message, dict):
            raise SystemExit("call JSON must be an object")
        sys.stdout.write(catalog.dumps(dispatch(message)))
        sys.stdout.write("\n")
        return
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError as exc:
            payload = {"ok": False, "error": f"invalid JSON: {exc}"}
        else:
            payload = dispatch(message if isinstance(message, dict) else {})
        sys.stdout.write(catalog.dumps(payload))
        sys.stdout.write("\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
