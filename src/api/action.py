"""POST /api/v1/action. UI tool calls skip the playbook loop."""

from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.chat import _recorded_facts, _response, _stamp, _summary_from
from core.agent_loop import (
    load_lane_transcript,
    pending_forms_for,
    project_directive,
    reject_entity_id,
    save_lane_transcript,
    synthesize_lane_response,
)
from core.config import fact_assertions_enabled
from core.pack_manager import PackManager
from core.tools import (
    CORE_TOOLS,
    build_session_graph,
    catalog_dialect,
    flush_fact_assertions,
    patch_session_field,
    reset_assertion_state,
    save_entry_artifact,
    set_assertion_context,
    state_mutated,
)
from rules_engine.graph.route_entry import route_entry

_PACKS = Path(__file__).resolve().parents[2] / "packs"
_TOOLS = {tool.name: tool for tool in (*CORE_TOOLS, save_entry_artifact)}
_ENVELOPE_KEYS = {"entry_id", "form_id"}
_RESERVED = {
    "entry_exists",
    "next_step",
    "agent_role",
    "lane",
    "write",
    "allowed_tools",
    "run_id",
}

router = APIRouter()


class ActionRequest(BaseModel):
    pack_id: str
    intent: str
    run_id: str | None = None
    entity_id: str | None = None
    lane: str | None = None
    dialect: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionResponse(BaseModel):
    status: str
    artifact: str = ""
    chat_response: Any | None = None


def _call(intent: str, payload: dict[str, Any]) -> Any:
    tool = _TOOLS.get(intent)
    if tool is None:
        raise HTTPException(status_code=400, detail=f"Unknown intent '{intent}'")
    signature = inspect.signature(tool.__wrapped__)
    kwargs: dict[str, Any] = {}
    for name, param in signature.parameters.items():
        if name in payload:
            kwargs[name] = payload[name]
        elif param.default is inspect.Parameter.empty:
            raise HTTPException(
                status_code=400, detail=f"payload is missing {name}"
            )
    try:
        return tool(**kwargs)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _entity_id(request: ActionRequest) -> str:
    raw = str(request.entity_id or "").strip()
    if not raw:
        raw = str((request.payload or {}).get("entry_id") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="entity_id is required")
    try:
        return reject_entity_id(raw)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _route(pack: PackManager, entity_id: str) -> dict[str, Any]:
    from rules_engine.studio_io import graph_artifact_path

    path = graph_artifact_path(entity_id)
    entry = json.loads(path.read_text(encoding="utf-8"))
    return route_entry(
        entry,
        entity_id,
        playbook_path=pack.playbook_path,
        pack_graph=dict(pack.manifest.get("graph") or {}),
    )


def _submit_fact_form(
    request: ActionRequest,
    pack: PackManager,
    entity_id: str,
    http_run_id: str,
) -> ActionResponse:
    lane = str(request.lane or "").strip()
    if not lane:
        raise HTTPException(status_code=400, detail="lane is required")
    reset_assertion_state()
    build_session_graph(entity_id)
    payload = dict(request.payload or {})
    facts = {key: value for key, value in payload.items() if key not in _ENVELOPE_KEYS}
    form_id = str(payload.get("form_id") or "").strip()
    if not form_id:
        current = _route(pack, entity_id)
        match = next(
            (
                item
                for item in (current.get("routing_directives") or [])
                if item.get("lane") == lane and item.get("form")
            ),
            None,
        )
        form_id = str((match or {}).get("form") or "")
    forms = pack.playbook.get("forms") or {}
    spec = forms.get(form_id) if isinstance(forms, dict) else None
    if not isinstance(spec, dict):
        raise HTTPException(status_code=400, detail="form is not defined")
    for field in spec.get("fields") or []:
        if not isinstance(field, dict) or field.get("required") is False:
            continue
        name = str(field.get("id") or "")
        if name not in facts or facts[name] is None:
            raise HTTPException(status_code=400, detail=f"required field {name} is missing")
    allowed = set(pack.ontology_field_names())
    derived = set(pack.derived)
    for name in facts:
        if name in _RESERVED or name not in allowed:
            raise HTTPException(
                status_code=400, detail=f"field is not an ontology fact: {name}"
            )
        if name in derived:
            raise HTTPException(
                status_code=400,
                detail=f"{name} is a derived field and cannot be set directly.",
            )
    set_assertion_context(role="UserFormSubmission", lane=lane, http_run_id=http_run_id)
    for name, value in facts.items():
        try:
            patch_session_field(str(name), value)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if state_mutated():
        flush_fact_assertions(entity_id)
    pack.activate()
    build_session_graph(entity_id)
    decision = _route(pack, entity_id)
    directives = list(decision.get("routing_directives") or [])
    forms_out = pending_forms_for(directives, pack.playbook)
    checks = list(decision.get("checks") or [])
    chat_text = synthesize_lane_response(
        directives,
        checks,
        forms_out,
        lane,
        pack.playbook,
        _recorded_facts(entity_id, http_run_id),
    )
    transcript = load_lane_transcript(entity_id, lane)
    transcript.append(_stamp("assistant", "UserFormSubmission", lane, chat_text))
    save_lane_transcript(entity_id, lane, transcript)
    projected = project_directive(directives, lane)
    envelope = _response(
        pack_id=pack.pack_name,
        run_id=http_run_id,
        entity_id=entity_id,
        active_lane=lane,
        terminal=bool((projected or {}).get("terminal", False)),
        dialect=catalog_dialect(request.dialect, pack),
        chat_text=chat_text,
        routing_summary=_summary_from(projected),
        checks=checks,
        directives=directives,
        lane_messages=transcript,
        playbook=pack.playbook,
    )
    return ActionResponse(status="success", artifact="", chat_response=envelope)


@router.post(
    "/api/v1/action",
    response_model=ActionResponse,
    response_model_exclude_none=True,
)
def action(request: ActionRequest) -> ActionResponse:
    try:
        pack = PackManager(request.pack_id, str(_PACKS))
        catalog_dialect(request.dialect, pack)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if request.intent == "submit_fact_form" and not fact_assertions_enabled():
        raise HTTPException(status_code=403, detail="Fact assertions are currently disabled.")

    entity_id = _entity_id(request)
    http_run_id = str(request.run_id or "").strip() or str(uuid.uuid4())
    if request.intent == "submit_fact_form":
        return _submit_fact_form(request, pack, entity_id, http_run_id)

    result = _call(request.intent, request.payload or {})
    artifact = ""
    if isinstance(result, dict) and result.get("artifact"):
        artifact = str(result["artifact"])
    return ActionResponse(status="success", artifact=artifact)
