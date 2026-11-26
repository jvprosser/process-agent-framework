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
    patch_session_field,
    run_named_query,
    run_named_write,
    set_assertion_context,
    state_mutated,
)
from mistralai.client import Mistral

_ROUTE_KEYS = ("next_step", "agent_role", "allowed_tools", "write")
_THINK_BLOCK = re.compile(
    r"<(think|thought)>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_REASONING_TYPES = {"thinking", "reasoning", "thought"}
_TOOL_CALL_BLOCK = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_MODEL_ERRORS = (httpx.HTTPError, ConnectionError, TimeoutError)
_CORE_TOOLS = {
    "run_named_query": run_named_query,
    "run_named_write": run_named_write,
    "patch_session_field": patch_session_field,
}
_LANE_CHARS = re.compile(r"[^A-Za-z0-9_-]")


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


def reject_entity_id(entity_id: str) -> str:
    """Refuse a session id that could escape the session directory."""
    entity = str(entity_id or "").strip()
    if not entity or any(part in entity for part in ("/", "\\", "..")):
        raise RuntimeError("entity_id is not a safe session name")
    return entity


def sanitize_lane(lane: str) -> str:
    return _LANE_CHARS.sub("", str(lane or "").strip())


def select_directive(
    directives: list[dict[str, Any]], requested_lane: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """A blocking gate wins. Otherwise the requested lane, else the first directive."""
    blocking = [item for item in directives if item.get("blocking") is True]
    if blocking:
        return blocking[0], None
    lane = str(requested_lane or "").strip()
    if lane:
        match = next((item for item in directives if item.get("lane") == lane), None)
        if match is None:
            return None, f"Lane {lane} has no active directive."
        return match, None
    if directives:
        return directives[0], None
    return None, "No playbook directive is active."


def project_directive(
    directives: list[dict[str, Any]], requested_lane: str | None
) -> dict[str, Any] | None:
    """Summary fields follow the gate, the requested lane, then the first directive."""
    chosen, _notice = select_directive(directives, requested_lane)
    return chosen


def build_layout(
    routing_directives: list[dict[str, Any]] | None,
    pending_forms: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Tile when several lanes or a form are on screen. Otherwise the split pane."""
    forms = list(pending_forms or [])
    directives = list(routing_directives or [])
    if len(directives) > 1 or forms:
        widgets = [
            "chat_feed",
            "routing_summary_panel",
            "active_directives",
            "data_entry_panel",
            "rule_probes",
            "graph_explorer",
        ]
        if not forms:
            widgets = [name for name in widgets if name != "data_entry_panel"]
        return {
            "template": "tile_grid",
            "widgets": widgets,
            "focus": "data_entry_panel" if forms else "chat_feed",
        }
    return {
        "template": "split_pane",
        "widgets": ["chat_feed", "routing_summary_panel", "rule_probes"],
        "focus": "chat_feed",
    }


def pending_forms_for(
    directives: list[dict[str, Any]] | None,
    playbook: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Forms named by the directives route_entry actually returned."""
    forms = (playbook or {}).get("forms") or {}
    if not isinstance(forms, dict):
        return []
    found: list[dict[str, Any]] = []
    for directive in directives or []:
        form_id = str(directive.get("form") or "").strip()
        spec = forms.get(form_id)
        if not form_id or not isinstance(spec, dict):
            continue
        found.append(
            {
                "form_id": form_id,
                "lane": str(directive.get("lane") or ""),
                "title": str(spec.get("title") or form_id),
                "submit_intent": str(spec.get("submit_intent") or "submit_fact_form"),
                "fields": list(spec.get("fields") or []),
            }
        )
    return found


def synthesize_lane_response(
    directives: list[dict[str, Any]] | None,
    checks: list[dict[str, Any]] | None,
    pending_forms: list[dict[str, Any]] | None,
    speaking_lane: str,
    playbook: dict[str, Any] | None,
    recorded: dict[str, Any] | None = None,
) -> str:
    """User-facing sentence after routing. The specialist's own text stays in debug_cot."""
    lane = str(speaking_lane or "")
    parts: list[str] = []
    if recorded:
        rendered = ", ".join(f"{key} {_fact_text(value)}" for key, value in recorded.items())
        parts.append(f"Recorded {rendered}.")
    rows = list(directives or [])
    gate = next((item for item in rows if item.get("blocking") is True), None)
    if gate is not None and str(gate.get("lane") or "") != lane:
        parts.append(
            f"{lane} is paused because blocking gate {gate.get('lane')} "
            f"({gate.get('next_step')} — {gate.get('routing_reason')}) is now active."
        )
    else:
        match = next((item for item in rows if str(item.get("lane") or "") == lane), None)
        if match is not None:
            sentence = (
                f"{match.get('agent_role')} is on {match.get('next_step')}. "
                f"{match.get('routing_reason')}"
            )
            title = next(
                (
                    str(form.get("title") or "")
                    for form in (pending_forms or [])
                    if str(form.get("lane") or "") == lane and form.get("title")
                ),
                "",
            )
            if title:
                sentence += f" Form: {title}."
            if match.get("terminal") is True:
                sentence += " This step is terminal."
            parts.append(sentence.strip())
        else:
            active = [str(item.get("lane") or "") for item in rows if item.get("lane")]
            listed = ", ".join(active) if active else "none"
            parts.append(f"{lane} has no remaining active probes. Active lanes: {listed}.")
    clause = _check_clause(checks or [], lane, playbook or {})
    if clause:
        parts.append(clause)
    return " ".join(parts)


def _fact_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _check_clause(
    checks: list[dict[str, Any]],
    speaking_lane: str,
    playbook: dict[str, Any],
) -> str:
    pieces: list[str] = []
    for check in checks:
        action = _playbook_action(playbook, check.get("probe_id"))
        action_lane = str(action.get("lane") or "")
        if action_lane != speaking_lane and action.get("blocking") is not True:
            continue
        pieces.append(f"{check.get('title')} ({check.get('status')})")
    if not pieces:
        return ""
    return "Checks: " + "; ".join(pieces) + "."


def _playbook_action(playbook: dict[str, Any], probe_id: Any) -> dict[str, Any]:
    key = str(probe_id or "")
    if key == "default_action":
        action = playbook.get("default_action") or {}
        return action if isinstance(action, dict) else {}
    if key == "exception_review":
        action = playbook.get("exception_review") or {}
        return action if isinstance(action, dict) else {}
    entries = (playbook.get("actions") or {}).get(key)
    if isinstance(entries, list):
        for action in entries:
            if isinstance(action, dict):
                return action
    return {}


def load_lane_transcript(entity_id: str, lane: str) -> list[dict[str, Any]]:
    entity = reject_entity_id(entity_id)
    safe_lane = sanitize_lane(lane)
    if not safe_lane:
        raise RuntimeError("lane is not a safe session name")
    path = _lane_path(entity, safe_lane)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def save_lane_transcript(entity_id: str, lane: str, messages: list[dict[str, Any]]) -> None:
    entity = reject_entity_id(entity_id)
    safe_lane = sanitize_lane(lane)
    path = _lane_path(entity, safe_lane)
    path.write_text(json.dumps(messages, indent=2) + "\n", encoding="utf-8")


def _lane_path(entity: str, lane: str):
    from rules_engine.studio_io import session_dir

    return session_dir() / f"entry_{entity}_chat_{lane}.json"


def run_specialist(
    route_json: dict,
    client: Mistral,
    pack: PackManager,
    history: list[dict[str, Any]] | None = None,
) -> dict:
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
    set_assertion_context(
        role=role,
        lane=str(route_json.get("lane") or ""),
        http_run_id=str(route_json.get("http_run_id") or ""),
    )
    bound = _bound_tools(pack, allowed)
    text, invocations, model_logs = _specialist_text(
        client, prompt, route_json, bound, allowed, write_label, pack, history
    )
    logs.extend(model_logs)
    patched = state_mutated() and any(
        item.get("tool") == "patch_session_field" for item in invocations
    )
    if (
        write_label
        and not patched
        and not any(item.get("label") == write_label for item in invocations)
    ):
        invocations.extend(_execute_write(write_label, route_json, pack))
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
    key = str(pack.manifest.get("entry_id_param") or "").strip()
    if not key:
        raise RuntimeError(f"Pack '{pack.pack_name}' manifest is missing entry_id_param")
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
                "Call build_session_graph for the given entry id, then "
                "evaluate_playbook. Do not invent next_step, agent_role, "
                "allowed_tools, or write."
            ),
        },
        {
            "role": "user",
            "content": f"Prepare entry {entity_id} and evaluate the playbook.",
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
    pack: PackManager,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    instruction = (
        "Follow the routing JSON. Do not change next_step, agent_role, "
        "or allowed_tools. "
    )
    if write_label:
        instruction += (
            f"Call run_named_write once with label {write_label}, unless this "
            "turn records a user fact with patch_session_field. "
        )
    instruction += (
        "Then reply with a short plain-language summary. "
        "Respond directly to the user in plain, concise language. Do not include "
        "internal planning, tool-call deliberation, or chain-of-thought reasoning "
        "in your response."
    )
    route_brief = json.dumps(
        {
            "routing_summary": route_json.get("routing_summary"),
            "next_step": route_json.get("next_step"),
            "agent_role": route_json.get("agent_role"),
            "write": write_label,
            "allowed_tools": allowed,
        }
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt + "\n\n" + instruction + "\n" + route_brief},
    ]
    if history:
        for item in history:
            role_name = item.get("role")
            messages.append(
                {
                    "role": role_name if role_name in {"user", "assistant"} else "user",
                    "content": str(item.get("text") or ""),
                }
            )
    else:
        messages.append({"role": "user", "content": route_brief})
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
        recorded = _dispatch_allowed(call, allowed, write_label, route_json, pack)
        if recorded is not None:
            invocations.append(recorded)
    for name, label in _xml_tool_calls(raw):
        if any(item.get("tool") == name and item.get("label") == label for item in invocations):
            continue
        if not _label_allowed(name, label, allowed, write_label):
            logs.append(f"specialist xml tool {name} {label} ignored")
            continue
        result = run_named_write(label, _write_params(route_json, pack))
        invocations.append({"tool": name, "label": label, "result": result})
        logs.append(f"specialist xml run_named_write {label}")
    return raw.strip(), invocations, logs


def _sanitize_chat_text(raw_text: str) -> str:
    """Drop leaked chain-of-thought and XML tool calls from the UI reply."""
    raw = raw_text or ""
    closer = re.search(r"</(think|thought)>", raw, flags=re.IGNORECASE)
    if closer and _TOOL_CALL_BLOCK.search(raw[closer.end() :]):
        after = _remainder_after_think(raw[closer.end() :])
        if after:
            return after
        return ""
    return _remainder_after_think(raw)


def _remainder_after_think(raw_text: str) -> str:
    clean = _THINK_BLOCK.sub("", raw_text or "")
    clean = re.sub(r"<(think|thought)>.*", "", clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"</(think|thought)>", "", clean, flags=re.IGNORECASE)
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


def _execute_write(
    write_label: str | None,
    route_json: dict[str, Any],
    pack: PackManager,
) -> list[dict[str, Any]]:
    if not write_label:
        return []
    result = run_named_write(write_label, _write_params(route_json, pack))
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
    pack: PackManager,
) -> dict[str, Any] | None:
    name = _call_name(call)
    tool = _CORE_TOOLS.get(name)
    if tool is None:
        return None
    args = _call_args(call)
    if name == "patch_session_field":
        return {
            "tool": name,
            "label": str(args.get("field") or ""),
            "result": patch_session_field(str(args.get("field") or ""), args.get("value")),
        }
    if name == "run_named_write":
        label = write_label or ""
        params = _write_params(route_json, pack)
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
    bound = [patch_session_field]
    if "run_named_write" in allowed_set or allowed_set & writes:
        bound.append(run_named_write)
    if "run_named_query" in allowed_set or allowed_set & reads:
        bound.append(run_named_query)
    return bound


def _write_params(route_json: dict[str, Any], pack: PackManager) -> dict[str, str]:
    entity = str(route_json.get("entity_id") or "").strip()
    run_id = str(route_json.get("run_id") or f"run-{entity or 'session'}")
    params = _read_params(pack, route_json)
    return {"run_id": run_id, "event_json": json.dumps(route_json), **params}


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
    reasoning = _reasoning_text(message)
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
    visible, _ = _split_message_text(message)
    return visible


def _reasoning_text(message: Any) -> str:
    _, reasoning = _split_message_text(message)
    return reasoning


def _split_message_text(message: Any) -> tuple[str, str]:
    """Separate a user-facing reply from reasoning the model attached."""
    extras: list[str] = []
    for name in ("reasoning_content", "reasoning"):
        value = _field(message, name)
        if value:
            extras.append(value)
    content = _raw_content(message)
    visible_parts: list[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                visible_parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "").lower()
            text = _part_text(part)
            if kind in _REASONING_TYPES:
                if text:
                    extras.append(text)
            elif text:
                visible_parts.append(text)
        raw = "\n".join(visible_parts)
    elif isinstance(content, str):
        raw = content
    else:
        raw = ""
    visible, tagged = _split_tagged(raw)
    if tagged:
        extras.append(tagged)
    return visible, "\n".join(extras)


def _raw_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _part_text(part: dict[str, Any]) -> str:
    for key in ("thinking", "reasoning", "text", "content"):
        value = part.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _split_tagged(raw: str) -> tuple[str, str]:
    tags = [match.group(0) for match in _THINK_BLOCK.finditer(raw or "")]
    return _sanitize_chat_text(raw), "\n".join(tags)


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
