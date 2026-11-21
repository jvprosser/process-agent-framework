"""Orchestrator, intake, and specialist handoff. Routing stays in the playbook."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from core.mistral_client import local_model
from core.pack_manager import PackManager
from core.tools import (
    build_session_graph,
    evaluate_playbook,
    run_named_query,
    run_named_write,
)
from mistralai.client import Mistral

_ROUTE_KEYS = ("next_step", "agent_role", "allowed_tools", "write")
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_BLOCK = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_MODEL_ERRORS = (httpx.HTTPError, ConnectionError, TimeoutError)
_CORE_TOOLS = {
    "run_named_query": run_named_query,
    "run_named_write": run_named_write,
}


class EntityExtraction(BaseModel):
    """Structured orchestrator result. Null when the message has no entity id."""

    entity_id: str | None = Field(
        default=None,
        description="The single entity id from the user message, or null.",
    )


def run_orchestrator(user_text: str, client: Mistral) -> str | None:
    """Extract one entity id. Returns None when the message has no id."""
    response = client.chat.parse(
        model=local_model(),
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "Return only the alphanumeric entity ID from the user text, "
                    "such as 9100 or 401. If no ID exists, return NONE. "
                    "Reply with JSON only: {\"entity_id\": \"<id>\"} or "
                    "{\"entity_id\": \"NONE\"}. No other text."
                ),
            },
            {"role": "user", "content": user_text},
        ],
        response_format=EntityExtraction,
    )
    message = _choice_message(response)
    parsed = getattr(message, "parsed", None)
    if isinstance(parsed, EntityExtraction):
        return _clean_entity_id(parsed.entity_id)
    content = _content(message)
    if not content:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return _clean_entity_id(payload.get("entity_id"))


def run_intake(entity_id: str, client: Mistral, pack: PackManager) -> dict:
    """Build the session case and return the playbook JSON unchanged."""
    entity = _clean_entity_id(entity_id)
    if entity is None:
        raise RuntimeError("entity_id is required")
    pack.activate()
    logs = _offer_intake(client, entity)
    build_session_graph(entity)
    logs.append(f"build_session_graph {entity}")
    route = evaluate_playbook()
    logs.append(
        "evaluate_playbook "
        f"next_step={route.get('next_step')} agent_role={route.get('agent_role')}"
    )
    missing = [key for key in _ROUTE_KEYS if key not in route]
    if missing:
        raise RuntimeError(
            "Playbook result is missing " + ", ".join(missing)
        )
    route["debug_cot"] = logs
    return route


def run_specialist(route_json: dict, client: Mistral, pack: PackManager) -> dict:
    """Run the playbook role with only its allowed tools, then summarize."""
    if not isinstance(route_json, dict):
        raise RuntimeError("route_json must be an object")
    role = str(route_json.get("agent_role") or "").strip()
    if not role:
        raise RuntimeError("route_json is missing agent_role")
    pack.activate()
    logs = _stored_cot(route_json)
    prompt = _agent_prompt(pack, role)
    allowed = [str(name) for name in (route_json.get("allowed_tools") or [])]
    view_label = _view_label(allowed)
    if view_label:
        params = _read_params(pack, route_json)
        viewed = run_named_query(view_label, params)
        route_json["view_data"] = viewed if isinstance(viewed, dict) else {"result": viewed}
        logs.append(f"run_named_query {view_label} {json.dumps(params)}")
    else:
        route_json["view_data"] = {}
    write_label = _permitted_write(route_json, pack, allowed)
    bound = _bound_tools(pack, allowed)
    text, invocations, model_logs = _specialist_text(
        client, prompt, route_json, bound, allowed, write_label
    )
    logs.extend(model_logs)
    if write_label and not any(item.get("label") == write_label for item in invocations):
        invocations.extend(_execute_write(write_label, route_json))
        logs.append(f"run_named_write {write_label}")
    text = _sanitize_chat_text(text)
    if not text:
        text = _summary(route_json)
    return {
        "text": text,
        "route_json": route_json,
        "tool_invocations": invocations,
        "debug_cot": logs,
    }


def _view_label(allowed: list[str]) -> str | None:
    """The one specialist read, the first allowed label that names a view."""
    for name in allowed:
        if name.startswith("get_"):
            return name
    return None


def _read_params(pack: PackManager, route_json: dict[str, Any]) -> dict[str, str]:
    entity = str(route_json.get("entity_id") or "").strip()
    if not entity:
        raise RuntimeError("route_json is missing entity_id")
    key = str(pack.manifest.get("case_id_param") or "").strip()
    if not key:
        raise RuntimeError(f"Pack '{pack.pack_name}' manifest is missing case_id_param")
    return {key: entity}


def _stored_cot(route_json: dict[str, Any]) -> list[str]:
    raw = route_json.pop("debug_cot", [])
    if not isinstance(raw, list):
        return []
    return [str(line) for line in raw]


def _offer_intake(client: Mistral, entity_id: str) -> list[str]:
    """Show the intake tools to the model. The playbook result is computed later."""
    tools = [_function_spec(build_session_graph), _function_spec(evaluate_playbook)]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Call build_session_graph for the given entity id, then "
                "evaluate_playbook. Do not invent next_step, agent_role, "
                "allowed_tools, or write."
            ),
        },
        {
            "role": "user",
            "content": f"Prepare entity {entity_id} and evaluate the playbook.",
        },
    ]
    try:
        response = _complete(client, messages, tools, tool_choice="auto")
    except _MODEL_ERRORS as exc:
        return [f"intake model error: {exc}"]
    message = _choice_message(response)
    logs = _message_lines("intake", message)
    for call in _tool_calls(message):
        name = _call_name(call)
        try:
            if name == "build_session_graph":
                build_session_graph(entity_id)
            elif name == "evaluate_playbook":
                evaluate_playbook()
        except RuntimeError as exc:
            logs.append(f"intake tool {name} failed: {exc}")
            return logs
    return logs


def _specialist_text(
    client: Mistral,
    prompt: str,
    route_json: dict[str, Any],
    bound: list[Any],
    allowed: list[str],
    write_label: str | None,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    instruction = (
        "Follow the routing JSON. Do not change next_step, agent_role, "
        "or allowed_tools. "
    )
    if write_label:
        instruction += (
            f"Call run_named_write once with label {write_label}. "
        )
    instruction += "Then reply with a short plain-language summary."
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt + "\n\n" + instruction},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "routing_summary": route_json.get("routing_summary"),
                    "next_step": route_json.get("next_step"),
                    "agent_role": route_json.get("agent_role"),
                    "write": write_label,
                    "allowed_tools": allowed,
                }
            ),
        },
    ]
    tools = [_function_spec(tool) for tool in bound]
    try:
        response = _complete(
            client,
            messages,
            tools or None,
            tool_choice="auto" if tools else None,
        )
    except _MODEL_ERRORS as exc:
        return "", [], [f"specialist model error: {exc}"]
    message = _choice_message(response)
    logs = _message_lines("specialist", message)
    raw = _content(message)
    invocations: list[dict[str, Any]] = []
    for call in _tool_calls(message):
        recorded = _dispatch_allowed(call, allowed, write_label, route_json)
        if recorded is not None:
            invocations.append(recorded)
    for name, label in _xml_tool_calls(raw):
        if any(item.get("tool") == name and item.get("label") == label for item in invocations):
            continue
        if not _label_allowed(name, label, allowed, write_label):
            logs.append(f"specialist xml tool {name} {label} ignored")
            continue
        result = run_named_write(label, _write_params(route_json))
        invocations.append({"tool": name, "label": label, "result": result})
        logs.append(f"specialist xml run_named_write {label}")
    return raw.strip(), invocations, logs


def _sanitize_chat_text(raw_text: str) -> str:
    """Drop leaked chain-of-thought and XML tool calls from the UI reply."""
    clean = _THINK_BLOCK.sub("", raw_text or "")
    clean = _TOOL_CALL_BLOCK.sub("", clean)
    return clean.strip()


def _xml_tool_calls(raw_text: str) -> list[tuple[str, str]]:
    """Labels the model wrote as XML instead of a native tool_calls object."""
    found: list[tuple[str, str]] = []
    for match in _TOOL_CALL_BLOCK.finditer(raw_text or ""):
        name, label = _parse_tool_call_body(match.group(1))
        if name and label:
            found.append((name, label))
    return found


def _parse_tool_call_body(body: str) -> tuple[str, str]:
    text = body.strip()
    payload: Any
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        name = str(payload.get("name") or payload.get("tool") or "").strip()
        args = payload.get("arguments") or payload.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        label = str(args.get("label") or "").strip() if isinstance(args, dict) else ""
        return name, label
    if "run_named_write" not in text:
        return "", ""
    label_match = re.search(r"""label["'\s:=]+["']?([A-Za-z_][A-Za-z0-9_]*)""", text)
    if label_match is None:
        return "", ""
    return "run_named_write", label_match.group(1)


def _execute_write(write_label: str | None, route_json: dict[str, Any]) -> list[dict[str, Any]]:
    if not write_label:
        return []
    result = run_named_write(write_label, _write_params(route_json))
    return [
        {
            "tool": "run_named_write",
            "label": write_label,
            "result": result,
        }
    ]


def _dispatch_allowed(
    call: Any,
    allowed: list[str],
    write_label: str | None,
    route_json: dict[str, Any],
) -> dict[str, Any] | None:
    name = _call_name(call)
    tool = _CORE_TOOLS.get(name)
    if tool is None:
        return None
    args = _call_args(call)
    if name == "run_named_write":
        label = write_label or ""
        params = _write_params(route_json)
    else:
        label = str(args.get("label") or "").strip()
        raw_params = args.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
    if not label or not _label_allowed(name, label, allowed, write_label):
        return None
    return {
        "tool": name,
        "label": label,
        "result": tool(label, params),
    }


def _label_allowed(
    tool_name: str,
    label: str,
    allowed: list[str],
    write_label: str | None,
) -> bool:
    if tool_name == "run_named_write":
        return bool(write_label) and label == write_label
    if tool_name == "run_named_query":
        return label in allowed
    return False


def _permitted_write(
    route_json: dict[str, Any],
    pack: PackManager,
    allowed: list[str],
) -> str | None:
    label = route_json.get("write")
    if label in (None, ""):
        return None
    text = str(label).strip()
    writes = set((pack.catalog.get("writes") or {}))
    if text in allowed or (text in writes and "run_named_write" in allowed):
        return text
    raise RuntimeError(f"Write label {text} is not in allowed_tools")


def _bound_tools(pack: PackManager, allowed: list[str]) -> list[Any]:
    allowed_set = set(allowed)
    reads = set((pack.catalog.get("reads") or {}))
    writes = set((pack.catalog.get("writes") or {}))
    bound = []
    if "run_named_write" in allowed_set or allowed_set & writes:
        bound.append(run_named_write)
    if "run_named_query" in allowed_set or allowed_set & reads:
        bound.append(run_named_query)
    return bound


def _write_params(route_json: dict[str, Any]) -> dict[str, str]:
    entity = str(route_json.get("entity_id") or "").strip()
    run_id = str(route_json.get("run_id") or f"run-{entity or 'session'}")
    return {"run_id": run_id, "event_json": json.dumps(route_json)}


def _agent_prompt(pack: PackManager, agent_role: str) -> str:
    if any(part in agent_role for part in ("/", "\\", "..")):
        raise RuntimeError("agent_role is not a file name")
    agents_dir = pack.root / "agents"
    if not agents_dir.is_dir():
        return (
            f"You are {agent_role}. Follow the routing JSON. "
            "Do not change next_step, agent_role, or allowed_tools."
        )
    stems = {agent_role, _snake(agent_role)}
    matches = [
        path
        for path in sorted(agents_dir.glob("*.md"))
        if path.stem in stems or path.stem.lower() in {stem.lower() for stem in stems}
    ]
    if len(matches) != 1:
        found = ", ".join(path.name for path in sorted(agents_dir.glob("*.md"))) or "none"
        raise RuntimeError(
            f"Pack '{pack.pack_name}' has no single agent prompt for "
            f"{agent_role}. Found: {found}"
        )
    return matches[0].read_text(encoding="utf-8")


def _snake(name: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index and name[index - 1].islower():
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def _summary(route_json: dict[str, Any]) -> str:
    role = route_json.get("agent_role") or "Specialist"
    step = route_json.get("next_step") or "the playbook step"
    label = route_json.get("write")
    if label:
        return f"{role} completed {step}. The write {label} was executed."
    return f"{role} completed {step}."


def _clean_entity_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none"}:
        return None
    return text


def _function_spec(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _complete(
    client: Mistral,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    tool_choice: str | None,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": local_model(),
        "temperature": 0,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
    return client.chat.complete(**kwargs)


def _choice_message(response: Any) -> Any:
    choices = getattr(response, "choices", None) or response["choices"]
    choice = choices[0]
    return choice.message if hasattr(choice, "message") else choice["message"]


def _message_lines(prefix: str, message: Any) -> list[str]:
    lines: list[str] = []
    reasoning = _field(message, "reasoning") or _field(message, "reasoning_content")
    if reasoning:
        lines.append(f"{prefix} reasoning: {reasoning}")
    content = _content(message)
    if content:
        lines.append(f"{prefix}: {content}")
    for call in _tool_calls(message):
        name = _call_name(call)
        try:
            args = json.dumps(_call_args(call), default=str)
        except (RuntimeError, json.JSONDecodeError, TypeError) as exc:
            lines.append(f"{prefix} tool {name} arguments invalid: {exc}")
            continue
        lines.append(f"{prefix} tool {name} {args}")
    return lines


def _field(message: Any, name: str) -> str:
    value = message.get(name) if isinstance(message, dict) else getattr(message, name, None)
    return value.strip() if isinstance(value, str) else ""


def _content(message: Any) -> str:
    if isinstance(message, dict):
        value = message.get("content")
    else:
        value = getattr(message, "content", None)
    return value if isinstance(value, str) else ""


def _tool_calls(message: Any) -> list[Any]:
    if isinstance(message, dict):
        calls = message.get("tool_calls")
    else:
        calls = getattr(message, "tool_calls", None)
    return list(calls or [])


def _call_name(call: Any) -> str:
    function = call["function"] if isinstance(call, dict) else call.function
    name = function["name"] if isinstance(function, dict) else function.name
    return str(name)


def _call_args(call: Any) -> dict[str, Any]:
    function = call["function"] if isinstance(call, dict) else call.function
    raw = function["arguments"] if isinstance(function, dict) else function.arguments
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
    else:
        parsed = raw or {}
    if not isinstance(parsed, dict):
        raise RuntimeError("tool arguments must be an object")
    return parsed
