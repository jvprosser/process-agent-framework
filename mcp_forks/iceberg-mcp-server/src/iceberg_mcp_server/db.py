"""Pick the Impala or Trino client for one catalog call."""

from __future__ import annotations

from typing import Any


def run_statements(statements: list[str], dialect: str = "impala") -> list[dict[str, Any]]:
    if dialect == "trino":
        from iceberg_mcp_server.trino_client import run_statements as run
    else:
        from iceberg_mcp_server.impala import run_statements as run
    return run(statements)
