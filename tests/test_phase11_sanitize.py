"""Phase 11: strip leaked model tags and log the SQL sent to Impala."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "mcp_forks" / "iceberg-mcp-server" / "src"))

from core.agent_loop import _sanitize_chat_text, run_intake, run_specialist
from core.pack_manager import PackManager
from iceberg_mcp_server import catalog

PACKS = REPO / "packs"
PROMPT_MARK = "You record a playbook decline after route_claim assigns"


class _Message:
    def __init__(self, content=""):
        self.content = content
        self.tool_calls = None
        self.parsed = None


class _Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class _Chat:
    def __init__(self, specialist_text: str):
        self.specialist_text = specialist_text

    def complete(self, **kwargs):
        system = kwargs["messages"][0]["content"]
        if PROMPT_MARK in system:
            return _Response(_Message(content=self.specialist_text))
        return _Response(_Message(content="{}"))


class _Client:
    def __init__(self, specialist_text: str):
        self.chat = _Chat(specialist_text)


def test_specialist_chat_text_drops_think_and_tool_call_xml(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))

    def fake_execute(op, label, params, manager):
        if op == "write":
            return {"ok": True, "named_op": label, "named_op_kind": "write"}
        if "fact_assertions" in label:
            return {"ok": True, "assertions": []}
        fixture = PACKS / "underwriting" / "fixtures" / label / f"{params['claim_id']}.json"
        return json.loads(fixture.read_text(encoding="utf-8"))

    import core.tools as tools

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)
    pack = PackManager("underwriting", str(PACKS))
    raw = (
        "<think>The playbook already chose the write.</think>\n"
        "The decline was recorded.\n"
        '<tool_call>{"name": "run_named_write", "arguments": {"label": "write_submission_decline"}}</tool_call>'
    )
    client = _Client(raw)
    route = run_intake("9100", client, pack)

    result = run_specialist(route, client, pack)

    assert result["text"] == "The decline was recorded."
    assert "<think>" not in result["text"]
    assert "<tool_call>" not in result["text"]
    cot = "\n".join(result["debug_cot"])
    assert "<think>" in cot
    assert result["tool_invocations"][0]["label"] == "write_submission_decline"
    assert sum(1 for item in result["tool_invocations"] if item["label"] == "write_submission_decline") == 1


def test_stray_think_closer_is_removed_from_chat_text():
    assert _sanitize_chat_text("The decline was recorded.</think>") == "The decline was recorded."
    assert "<think>" not in _sanitize_chat_text("<think>hidden reasoning without a closer")


def test_reasoning_before_think_closer_and_tool_call_is_not_the_reply():
    raw = (
        "We need to process claim 401. Let's first call run_named_query.\n"
        "</think>\n"
        "<tool_call>\n"
        "<function=run_named_query>\n"
        "<parameter=label>\nget_pd_view\n</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    assert _sanitize_chat_text(raw) == ""


def test_get_claim_spine_logs_the_rendered_sql(capsys):
    seen: list[str] = []

    def query_rows(sql: str):
        seen.append(sql)
        return [{"claim_id": 401}]

    catalog.execute(
        "query",
        "get_claim_spine",
        {"claim_id": "401"},
        root=PACKS / "claims",
        query_rows=query_rows,
    )

    err = capsys.readouterr().err
    assert any("claim_id = 401" in sql and "claim_id = '401'" not in sql for sql in seen)
    assert "iceberg-mcp-server SQL [get_claim_spine]:" in err
    assert "claim_id = 401" in err
    assert "claim_id = '401'" not in err
    assert "iceberg-mcp-server rows [get_claim_spine]: 1" in err
