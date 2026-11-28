"""POST /api/v1/chat. The body is the four-node UI envelope."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from datetime import datetime, timezone

from core.agent_loop import (
    build_layout,
    load_lane_transcript,
    pending_forms_for,
    project_directive,
    reject_entity_id,
    run_intake,
    run_orchestrator,
    run_specialist,
    save_lane_transcript,
    select_directive,
    synthesize_lane_response,
)
from core.config import fact_assertions_enabled
from core.mistral_client import local_client
from core.pack_manager import PackManager
from core.tools import (
    build_session_graph,
    catalog_dialect,
    evaluate_playbook,
    flush_fact_assertions,
    patch_session_field,
    reset_assertion_state,
    set_assertion_context,
    state_mutated,
)
from core.turn_classifier import classify_turn, coerce_fact_value, declared_lanes

_PACKS = Path(__file__).resolve().parents[2] / "packs"
_MISSING_ID = "I couldn't find an ID in your request. Include the entity id and try again."

router = APIRouter()


class ChatRequest(BaseModel):
    user_text: str
    pack_id: str
    dialect: str | None = None
    entity_id: str | None = None
    lane: str | None = None


class Meta(BaseModel):
    pack_id: str
    run_id: str
    entity_id: str | None
    active_lane: str | None = None
    terminal: bool
    dialect: str = "impala"
    debug_cot: list[str] = Field(default_factory=list)


LayoutTemplate = Literal["split_pane", "tile_grid"]
FormFieldType = Literal["text", "number", "boolean", "date", "select"]


class FormFieldSchema(BaseModel):
    id: str
    type: FormFieldType
    label: str
    required: bool = True
    options: list[str] | None = None


class PendingForm(BaseModel):
    form_id: str
    lane: str
    title: str
    submit_intent: str
    fields: list[FormFieldSchema] = Field(default_factory=list)


class Layout(BaseModel):
    template: LayoutTemplate = "split_pane"
    widgets: list[str] = Field(
        default_factory=lambda: ["chat_feed", "routing_summary_panel"]
    )
    focus: str = "chat_feed"


class RoutingSummary(BaseModel):
    next_step: str | None = None
    agent_role: str | None = None
    lane: str | None = None
    routing_reason: str | None = None


class CheckItem(BaseModel):
    probe_id: str
    title: str
    status: Literal["assigned", "did_not_apply", "skipped"]
    lane: str | None = None
    detail: Any | None = None
    skipped_reason: str | None = None


class RoutingDirective(BaseModel):
    directive_id: str
    lane: str
    agent_role: str
    next_step: str
    routing_reason: str
    blocking: bool
    terminal: bool = False
    write: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    letter_on_request: bool = False
    form: str | None = None


class LaneMessage(BaseModel):
    role: str
    agent_role: str | None = None
    lane: str
    text: str
    timestamp: str


class Content(BaseModel):
    chat_text: str
    lane_messages: list[LaneMessage] = Field(default_factory=list)
    routing_summary: RoutingSummary
    routing_directives: list[RoutingDirective] = Field(default_factory=list)
    checks: list[CheckItem]
    view_data: dict[str, Any] = Field(default_factory=dict)
    session_graph: dict[str, Any] = Field(default_factory=dict)


class Actions(BaseModel):
    quick_replies: list[str] = Field(default_factory=list)
    tool_invocations: list[Any] = Field(default_factory=list)
    pending_forms: list[PendingForm] = Field(default_factory=list)


class ChatResponse(BaseModel):
    meta: Meta
    layout: Layout
    content: Content
    actions: Actions


def _layout(
    directives: list[dict[str, Any]] | None = None,
    forms: list[dict[str, Any]] | None = None,
) -> Layout:
    return Layout.model_validate(build_layout(directives, forms))


def _actions(
    route: dict[str, Any] | None,
    entity_id: str | None,
    forms: list[dict[str, Any]] | None = None,
) -> Actions:
    pending = [PendingForm.model_validate(item) for item in (forms or [])]
    if not route or route.get("letter_on_request") is not True or not entity_id:
        return Actions(quick_replies=[], tool_invocations=[], pending_forms=pending)
    return Actions(
        quick_replies=["Please write the recommended letter."],
        tool_invocations=[
            {
                "label": "Draft Recommended Letter",
                "intent": "save_entry_artifact",
                "payload": {"entry_id": entity_id},
            }
        ],
        pending_forms=pending,
    )


def _session_graph(entity_id: str | None) -> dict[str, Any]:
    """The case file intake wrote. Empty when that file is missing."""
    if not entity_id:
        return {}
    from rules_engine.studio_io import graph_artifact_path

    path = graph_artifact_path(entity_id)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _view_data(route: dict[str, Any] | None) -> dict[str, Any]:
    if not route:
        return {}
    viewed = route.get("view_data")
    return viewed if isinstance(viewed, dict) else {}


def _response(
    *,
    pack_id: str,
    run_id: str,
    entity_id: str | None,
    terminal: bool,
    chat_text: str,
    dialect: str = "impala",
    routing_summary: RoutingSummary | None = None,
    checks: list[dict[str, Any]] | None = None,
    route: dict[str, Any] | None = None,
    debug_cot: list[str] | None = None,
    active_lane: str | None = None,
    directives: list[dict[str, Any]] | None = None,
    lane_messages: list[dict[str, Any]] | None = None,
    playbook: dict[str, Any] | None = None,
) -> ChatResponse:
    forms = pending_forms_for(directives, playbook)
    return ChatResponse(
        meta=Meta(
            pack_id=pack_id,
            run_id=run_id,
            entity_id=entity_id,
            active_lane=active_lane,
            terminal=terminal,
            dialect=dialect,
            debug_cot=list(debug_cot or []),
        ),
        layout=_layout(directives, forms),
        content=Content(
            chat_text=chat_text,
            lane_messages=[LaneMessage.model_validate(item) for item in (lane_messages or [])],
            routing_summary=routing_summary or RoutingSummary(),
            routing_directives=[
                RoutingDirective.model_validate(item) for item in (directives or [])
            ],
            checks=[CheckItem.model_validate(item) for item in (checks or [])],
            view_data=_view_data(route),
            session_graph=_session_graph(entity_id),
        ),
        actions=_actions(route, entity_id, forms),
    )


def _summary_from(directive: dict[str, Any] | None) -> RoutingSummary:
    if not directive:
        return RoutingSummary()
    return RoutingSummary(
        next_step=directive.get("next_step"),
        agent_role=directive.get("agent_role"),
        lane=directive.get("lane"),
        routing_reason=directive.get("routing_reason"),
    )


def _route_from_directive(directive: dict[str, Any], entity_id: str) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "next_step": directive.get("next_step"),
        "agent_role": directive.get("agent_role"),
        "allowed_tools": list(directive.get("allowed_tools") or []),
        "write": directive.get("write"),
        "lane": directive.get("lane"),
        "terminal": bool(directive.get("terminal")),
        "routing_reason": directive.get("routing_reason"),
        "letter_on_request": bool(directive.get("letter_on_request")),
    }


def _directives_of(route: dict[str, Any]) -> list[dict[str, Any]]:
    found = route.get("routing_directives")
    if isinstance(found, list) and found:
        return list(found)
    return [
        {
            "directive_id": str(route.get("next_step") or "route"),
            "lane": str(route.get("lane") or ""),
            "agent_role": route.get("agent_role"),
            "next_step": route.get("next_step"),
            "routing_reason": str(route.get("routing_reason") or ""),
            "blocking": True,
            "terminal": bool(route.get("terminal")),
            "write": route.get("write"),
            "allowed_tools": list(route.get("allowed_tools") or []),
            "letter_on_request": bool(route.get("letter_on_request")),
        }
    ]


def _recorded_facts(entity_id: str, http_run_id: str) -> dict[str, Any]:
    """Sidecar values stamped with this request. Empty when the turn stored nothing."""
    from rules_engine.studio_io import session_dir

    path = session_dir() / f"entry_{entity_id}_overrides.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    found: dict[str, Any] = {}
    for field, meta in payload.items():
        if isinstance(meta, dict) and meta.get("http_run_id") == http_run_id:
            found[str(field)] = meta.get("value")
    return found


def _agent_for_lane(directives: list[dict[str, Any]], lane: str) -> str | None:
    for item in directives:
        if str(item.get("lane") or "") == lane and item.get("agent_role"):
            return str(item.get("agent_role"))
    return None


def _question_line(field_id: str, value: Any) -> str:
    if value is None or value == "":
        return f"{field_id} is not on file yet."
    if isinstance(value, bool):
        shown = "True" if value else "False"
    else:
        shown = value
    return f"{field_id} is {shown}."


def _append_exchange(
    entity_id: str,
    lane: str,
    user_text: str,
    assistant_text: str,
    agent_role: str | None,
) -> list[dict[str, Any]]:
    transcript = load_lane_transcript(entity_id, lane)
    transcript.append(_stamp("user", None, lane, user_text))
    transcript.append(_stamp("assistant", agent_role, lane, assistant_text))
    save_lane_transcript(entity_id, lane, transcript)
    return transcript


def _stamp(role: str, agent_role: str | None, lane: str, text: str) -> dict[str, Any]:
    return {
        "role": role,
        "agent_role": agent_role,
        "lane": lane,
        "text": text,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _classified_turn(
    *,
    pack: PackManager,
    pack_id: str,
    run_id: str,
    entity_id: str,
    lane: str,
    user_text: str,
    dialect: str,
    turn: Any,
    route: dict[str, Any],
    directives: list[dict[str, Any]],
    intake_cot: list[str],
) -> ChatResponse:
    """Fact and question turns stay on the requested lane and skip the specialist."""
    gate = next((item for item in directives if item.get("blocking") is True), None)
    if gate is None:
        active = any(str(item.get("lane") or "") == lane for item in directives)
    else:
        active = lane in declared_lanes(pack.playbook)
    if not active:
        return _response(
            pack_id=pack_id,
            run_id=run_id,
            entity_id=entity_id,
            terminal=False,
            dialect=dialect,
            chat_text=f"Lane {lane} has no active directive.",
            routing_summary=_summary_from(project_directive(directives, lane)),
            checks=list(route.get("checks") or []),
            directives=directives,
            debug_cot=intake_cot,
            playbook=pack.playbook,
        )

    field_id = str(turn.field_id or "")
    if turn.kind == "question":
        reply = _question_line(field_id, _session_graph(entity_id).get(field_id))
        final_route = route
        final_directives = directives
    elif not fact_assertions_enabled():
        reply = "Direct fact overrides are currently disabled."
        final_route = route
        final_directives = directives
    elif field_id in set(pack.derived):
        reply = f"{field_id} is a derived field and cannot be set directly."
        final_route = route
        final_directives = directives
    else:
        try:
            coerced = coerce_fact_value(field_id, str(turn.value or ""), pack)
        except ValueError:
            reply = f"The value for {field_id} could not be coerced and was not recorded."
            final_route = route
            final_directives = directives
        else:
            set_assertion_context(role="UserLaneChat", lane=lane, http_run_id=run_id)
            patch_session_field(field_id, coerced)
            if state_mutated():
                flush_fact_assertions(entity_id)
                pack.activate()
                build_session_graph(entity_id)
                final_route = evaluate_playbook()
                final_directives = _directives_of(final_route)
            else:
                final_route = route
                final_directives = directives
            forms = pending_forms_for(final_directives, pack.playbook)
            reply = synthesize_lane_response(
                final_directives,
                list(final_route.get("checks") or []),
                forms,
                lane,
                pack.playbook,
                {field_id: coerced},
            )
    projected = project_directive(final_directives, lane)
    transcript = _append_exchange(
        entity_id,
        lane,
        user_text,
        reply,
        _agent_for_lane(final_directives, lane),
    )
    acted = {"letter_on_request": bool((projected or {}).get("letter_on_request"))}
    return _response(
        pack_id=pack_id,
        run_id=run_id,
        entity_id=entity_id,
        active_lane=lane,
        terminal=bool((projected or {}).get("terminal", False)),
        dialect=dialect,
        chat_text=reply,
        routing_summary=_summary_from(projected),
        checks=list(final_route.get("checks") or []),
        route=acted,
        directives=final_directives,
        lane_messages=transcript,
        debug_cot=intake_cot,
        playbook=pack.playbook,
    )


@router.post("/api/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    run_id = str(uuid.uuid4())
    try:
        pack = PackManager(request.pack_id, str(_PACKS))
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    dialect = catalog_dialect(request.dialect, pack)
    client = local_client()
    entity_id = None
    reset_assertion_state()
    try:
        supplied = str(request.entity_id or "").strip()
        if supplied:
            entity_id = reject_entity_id(supplied)
            orchestrator_note: list[str] = []
        else:
            entity_id = run_orchestrator(request.user_text, client)
            if entity_id is None:
                return _response(
                    pack_id=request.pack_id,
                    run_id=run_id,
                    entity_id=None,
                    terminal=False,
                    dialect=dialect,
                    chat_text=_MISSING_ID,
                    debug_cot=["orchestrator: no entity id"],
                )
            entity_id = reject_entity_id(entity_id)
            orchestrator_note = [f"orchestrator entity_id={entity_id}"]

        route = run_intake(entity_id, client, pack)
        directives = _directives_of(route)
        intake_cot = [str(line) for line in (route.get("debug_cot") or [])]
        requested_lane = str(request.lane or "").strip()
        if supplied and requested_lane:
            turn = classify_turn(request.user_text, pack)
            if turn.kind in {"fact", "question"}:
                return _classified_turn(
                    pack=pack,
                    pack_id=request.pack_id,
                    run_id=run_id,
                    entity_id=entity_id,
                    lane=requested_lane,
                    user_text=request.user_text,
                    dialect=dialect,
                    turn=turn,
                    route=route,
                    directives=directives,
                    intake_cot=intake_cot,
                )
        selected, notice = select_directive(directives, request.lane)
        if selected is None:
            return _response(
                pack_id=request.pack_id,
                run_id=run_id,
                entity_id=entity_id,
                terminal=False,
                dialect=dialect,
                chat_text=notice or _MISSING_ID,
                routing_summary=_summary_from(project_directive(directives, request.lane)),
                checks=list(route.get("checks") or []),
                directives=directives,
                debug_cot=[*orchestrator_note, *list(route.get("debug_cot") or [])],
                playbook=pack.playbook,
            )

        active_lane = str(selected.get("lane") or "")
        spoken = _route_from_directive(selected, entity_id)
        spoken["routing_summary"] = route.get("routing_summary")
        spoken["checks"] = route.get("checks")
        spoken["http_run_id"] = run_id
        spoken["debug_cot"] = [*orchestrator_note, *list(route.get("debug_cot") or [])]
        transcript = load_lane_transcript(entity_id, active_lane)
        transcript.append(_stamp("user", None, active_lane, request.user_text))
        specialist = run_specialist(spoken, client, pack, transcript)
        if state_mutated():
            flush_fact_assertions(entity_id)
            pack.activate()
            build_session_graph(entity_id)
            route = evaluate_playbook()
            directives = _directives_of(route)
        projected = project_directive(directives, request.lane) or selected
        summary = _summary_from(projected)
        forms = pending_forms_for(directives, pack.playbook)
        chat_text = synthesize_lane_response(
            directives,
            list(route.get("checks") or []),
            forms,
            active_lane,
            pack.playbook,
            _recorded_facts(entity_id, run_id),
        )
        transcript.append(
            _stamp("assistant", selected.get("agent_role"), active_lane, chat_text)
        )
        save_lane_transcript(entity_id, active_lane, transcript)
        acted = dict(spoken)
        acted["letter_on_request"] = bool(projected.get("letter_on_request"))
        acted["view_data"] = specialist.get("route_json", {}).get("view_data")
        specialist_text = str(specialist.get("text") or "").strip()
        debug_cot = list(specialist.get("debug_cot") or [])
        if specialist_text:
            debug_cot.append(specialist_text)
    except Exception as exc:
        return _response(
            pack_id=request.pack_id,
            run_id=run_id,
            entity_id=entity_id,
            terminal=False,
            dialect=dialect,
            chat_text=f"{exc.__class__.__name__}: {exc}",
            debug_cot=[f"{exc.__class__.__name__}: {exc}"],
        )
    return _response(
        pack_id=request.pack_id,
        run_id=run_id,
        entity_id=entity_id,
        active_lane=active_lane,
        terminal=bool(projected.get("terminal", False)),
        dialect=dialect,
        chat_text=chat_text,
        routing_summary=summary,
        checks=list(route.get("checks") or []),
        route=acted,
        directives=directives,
        lane_messages=transcript,
        debug_cot=debug_cot,
        playbook=pack.playbook,
    )
