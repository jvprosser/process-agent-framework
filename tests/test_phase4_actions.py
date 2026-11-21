"""Phase 4: view reads, letter actions, and the local model URL."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from api import chat as chat_api
from core import mistral_client
from core.mistral_client import LOCAL_SERVER_URL, local_api_key, local_client, local_server_url
from main import app

VIEW = {"named_op": "get_pd_view", "incident_report_number": "PD-19"}
QUERIES: list[tuple[str, str]] = []


class _Message:
    def __init__(self, content="", parsed=None):
        self.content = content
        self.tool_calls = None
        self.parsed = parsed


class _Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class _Chat:
    def parse(self, response_format, **kwargs):
        user = kwargs["messages"][-1]["content"]
        entity_id = "401" if "401" in user else None
        parsed = response_format(entity_id=entity_id)
        return _Response(_Message(content=parsed.model_dump_json(), parsed=parsed))

    def complete(self, **kwargs):
        return _Response(_Message(content="The police report still needs to be requested."))


class _Client:
    chat = _Chat()


def _execute(op, label, params, manager):
    QUERIES.append((op, label))
    if op == "query" and label == "get_pd_view":
        assert params["claim_id"] == "401"
        return dict(VIEW)
    if op == "write":
        return {"ok": True, "named_op": label, "named_op_kind": "write"}
    raise AssertionError(f"unexpected {op} {label}")


def _route(letter: bool) -> dict:
    tools = ["get_pd_view", "create_pd_task", "save_claim_letter"]
    if not letter:
        tools = ["create_pd_task"]
    return {
        "entity_id": "401",
        "next_step": "RequestPoliceReport" if letter else "DetermineFault",
        "agent_role": "UnderwritingIntakeSpecialist",
        "allowed_tools": tools,
        "write": "create_pd_task",
        "lane": "PD",
        "terminal": True,
        "letter_on_request": letter,
        "routing_reason": "No police report on file.",
        "checks": [
            {
                "probe_id": "R2.1",
                "title": "Police report",
                "status": "assigned",
                "detail": "missing",
            }
        ],
    }


def test_view_label_and_letter_actions(monkeypatch):
    QUERIES.clear()
    import core.tools as tools

    monkeypatch.setattr(tools, "_call_mcp", _execute)
    monkeypatch.setattr(chat_api, "local_client", lambda: _Client())
    monkeypatch.setattr(chat_api, "run_intake", lambda entity_id, client, pack: _route(True))
    response = TestClient(app).post(
        "/api/v1/chat",
        json={"user_text": "Please process claim 401", "pack_id": "underwriting"},
    )

    assert response.status_code == 200
    body = response.json()
    assert ("query", "get_pd_view") in QUERIES
    assert body["content"]["view_data"]["incident_report_number"] == "PD-19"
    assert body["content"]["routing_summary"]["next_step"] == "RequestPoliceReport"
    assert body["actions"]["quick_replies"] == ["Please write the recommended letter."]
    assert body["actions"]["tool_invocations"] == [
        {
            "label": "Draft Recommended Letter",
            "intent": "save_claim_letter",
            "payload": {"entity_id": "401"},
        }
    ]


def test_actions_stay_empty_without_a_letter(monkeypatch):
    QUERIES.clear()
    import core.tools as tools

    monkeypatch.setattr(tools, "_call_mcp", _execute)
    monkeypatch.setattr(chat_api, "local_client", lambda: _Client())
    monkeypatch.setattr(chat_api, "run_intake", lambda entity_id, client, pack: _route(False))
    response = TestClient(app).post(
        "/api/v1/chat",
        json={"user_text": "Please process claim 401", "pack_id": "underwriting"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"]["view_data"] == {}
    assert body["actions"] == {"quick_replies": [], "tool_invocations": []}
    assert ("query", "get_pd_view") not in QUERIES


def test_cloudera_inference_uses_the_workbench_jwt(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MISTRAL_LOCAL_URL", "https://serving.example/v1")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("CDP_TOKEN", raising=False)
    monkeypatch.setenv("CDSW_API_KEY", "not-the-inference-token")
    jwt = tmp_path / "jwt"
    jwt.write_text('{"access_token": "workload-jwt"}', encoding="utf-8")
    monkeypatch.setattr(mistral_client, "_JWT_PATH", jwt)
    assert local_api_key() == "workload-jwt"


def test_knox_error_page_is_not_sent_as_the_bearer_token(monkeypatch, tmp_path: Path):
    html = (
        "<html><td>/go01-aw-dl-md/kt-kerberos/knoxtoken/api/v2/token</td></html>"
    )
    monkeypatch.setenv("MISTRAL_LOCAL_URL", "https://serving.example/v1")
    monkeypatch.setenv("MISTRAL_API_KEY", html)
    monkeypatch.setenv("CDP_TOKEN", html)
    monkeypatch.delenv("CDSW_API_KEY", raising=False)
    jwt = tmp_path / "jwt"
    jwt.write_text('{"access_token": "workload-jwt"}', encoding="utf-8")
    monkeypatch.setattr(mistral_client, "_JWT_PATH", jwt)
    assert local_api_key() == "workload-jwt"


def test_knox_error_page_names_the_token_endpoint(monkeypatch, tmp_path: Path):
    html = (
        "<html><td>/go01-aw-dl-md/kt-kerberos/knoxtoken/api/v2/token</td></html>"
    )
    monkeypatch.setenv(
        "MISTRAL_LOCAL_URL",
        "https://ml.example.cloudera.site/namespaces/serving-default/endpoints/nemotron/v1",
    )
    monkeypatch.setenv("MISTRAL_API_KEY", html)
    monkeypatch.setenv("CDP_TOKEN", html)
    monkeypatch.delenv("CDSW_API_KEY", raising=False)
    jwt = tmp_path / "jwt"
    jwt.write_text(html, encoding="utf-8")
    monkeypatch.setattr(mistral_client, "_JWT_PATH", jwt)
    try:
        local_api_key()
    except RuntimeError as exc:
        text = str(exc)
    else:
        raise AssertionError("expected a token error")
    assert "/go01-aw-dl-md/kt-kerberos/knoxtoken/api/v2/token" in text


def test_local_url_override(monkeypatch):
    monkeypatch.setattr(mistral_client, "_JWT_PATH", Path("/no/such/jwt"))
    monkeypatch.delenv("MISTRAL_LOCAL_URL", raising=False)
    assert local_server_url() == LOCAL_SERVER_URL
    monkeypatch.setenv("MISTRAL_LOCAL_URL", "http://localhost:11434/v1")
    client = local_client()
    url, _params = client.sdk_configuration.get_server_details()
    assert url == "http://localhost:11434"
    assert "api.mistral.ai" not in url
