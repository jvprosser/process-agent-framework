"""POST /api/v1/chat. The body is the four-node UI envelope."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.agent_loop import run_intake, run_orchestrator, run_specialist
from core.mistral_client import local_client
from core.pack_manager import PackManager

_PACKS = Path(__file__).resolve().parents[2] / "packs"
_MISSING_ID = "I couldn't find an ID in your request. Include the entity id and try again."

router = APIRouter()


class ChatRequest(BaseModel):
    user_text: str
    pack_id: str


class Meta(BaseModel):
    pack_id: str
    run_id: str
    entity_id: str | None
    terminal: bool
    debug_cot: list[str] = Field(default_factory=list)


class Layout(BaseModel):
    template: str
    widgets: list[str]
    focus: str


class RoutingSummary(BaseModel):
    next_step: str | None = None
    agent_role: str | None = None
    lane: str | None = None
    routing_reason: str | None = None


class CheckItem(BaseModel):
    probe_id: str | None = None
    title: str | None = None
    status: str | None = None
    detail: str | None = None


class Content(BaseModel):
    chat_text: str
    routing_summary: RoutingSummary
    checks: list[CheckItem]
    view_data: dict[str, Any] = Field(default_factory=dict)


class Actions(BaseModel):
    quick_replies: list[str] = Field(default_factory=list)
    tool_invocations: list[Any] = Field(default_factory=list)


class ChatResponse(BaseModel):
    meta: Meta
    layout: Layout
    content: Content
    actions: Actions


def _layout() -> Layout:
    return Layout(
        template="split_pane",
        widgets=["chat_feed", "routing_summary_panel"],
        focus="chat_feed",
    )


def _actions(route: dict[str, Any] | None, entity_id: str | None) -> Actions:
    if not route or route.get("letter_on_request") is not True or not entity_id:
        return Actions(quick_replies=[], tool_invocations=[])
    return Actions(
        quick_replies=["Please write the recommended letter."],
        tool_invocations=[
            {
                "label": "Draft Recommended Letter",
                "intent": "save_claim_letter",
                "payload": {"entity_id": entity_id},
            }
        ],
    )


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
    routing_summary: RoutingSummary | None = None,
    checks: list[dict[str, Any]] | None = None,
    route: dict[str, Any] | None = None,
    debug_cot: list[str] | None = None,
) -> ChatResponse:
    return ChatResponse(
        meta=Meta(
            pack_id=pack_id,
            run_id=run_id,
            entity_id=entity_id,
            terminal=terminal,
            debug_cot=list(debug_cot or []),
        ),
        layout=_layout(),
        content=Content(
            chat_text=chat_text,
            routing_summary=routing_summary or RoutingSummary(),
            checks=[CheckItem.model_validate(item) for item in (checks or [])],
            view_data=_view_data(route),
        ),
        actions=_actions(route, entity_id),
    )


@router.post("/api/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    run_id = str(uuid.uuid4())
    try:
        pack = PackManager(request.pack_id, str(_PACKS))
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    client = local_client()
    entity_id = None
    try:
        entity_id = run_orchestrator(request.user_text, client)
        if entity_id is None:
            return _response(
                pack_id=request.pack_id,
                run_id=run_id,
                entity_id=None,
                terminal=False,
                chat_text=_MISSING_ID,
                debug_cot=["orchestrator: no entity id"],
            )

        route = run_intake(entity_id, client, pack)
        specialist = run_specialist(route, client, pack)
    except Exception as exc:
        return _response(
            pack_id=request.pack_id,
            run_id=run_id,
            entity_id=entity_id,
            terminal=False,
            chat_text=f"{exc.__class__.__name__}: {exc}",
            debug_cot=[f"{exc.__class__.__name__}: {exc}"],
        )
    return _response(
        pack_id=request.pack_id,
        run_id=run_id,
        entity_id=entity_id,
        terminal=bool(route.get("terminal", False)),
        chat_text=str(specialist.get("text") or ""),
        routing_summary=RoutingSummary(
            next_step=route.get("next_step"),
            agent_role=route.get("agent_role"),
            lane=route.get("lane"),
            routing_reason=route.get("routing_reason"),
        ),
        checks=list(route.get("checks") or []),
        route=route,
        debug_cot=[f"orchestrator entity_id={entity_id}", *list(specialist.get("debug_cot") or [])],
    )
