"""POST /api/v1/action. UI tool calls skip the playbook loop."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.pack_manager import PackManager
from core.tools import CORE_TOOLS, save_entry_artifact

_PACKS = Path(__file__).resolve().parents[2] / "packs"
_TOOLS = {tool.name: tool for tool in (*CORE_TOOLS, save_entry_artifact)}

router = APIRouter()


class ActionRequest(BaseModel):
    pack_id: str
    intent: str
    payload: dict[str, Any]


class ActionResponse(BaseModel):
    status: str
    artifact: str


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


@router.post("/api/v1/action", response_model=ActionResponse)
def action(request: ActionRequest) -> ActionResponse:
    try:
        PackManager(request.pack_id, str(_PACKS))
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result = _call(request.intent, request.payload or {})
    artifact = ""
    if isinstance(result, dict) and result.get("artifact"):
        artifact = str(result["artifact"])
    return ActionResponse(status="success", artifact=artifact)
