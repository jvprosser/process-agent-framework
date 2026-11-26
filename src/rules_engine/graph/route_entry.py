"""First-match playbook probes (CEL, with YAML match/SELECT fallback) + actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from rules_engine.graph.cel_rules import eval_cel
from rules_engine.graph.yaml_rules import eval_match, get_path
from rules_engine.paths import default_playbook_path

_LATER_NOTE = "Later playbook checks were not run."
_DEFAULT_TITLE = "No earlier check assigned work"


def route_entry(
    entry: dict[str, Any],
    entry_id: int | str,
    *,
    playbook_path: str | Path | None = None,
    pack_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run playbook probes in priority order; return next step / agent / tools."""
    if not isinstance(entry, dict):
        raise TypeError("route_entry expects an entry JSON dict")
    graph = pack_graph or {}
    playbook = _load_playbook(playbook_path)
    _validate_playbook(playbook, pack_graph)
    if playbook.get("exception_probes"):
        return _route_with_exceptions(entry, entry_id, playbook, graph)
    return _route_lanes(entry, entry_id, playbook, graph)


def _route_lanes(
    entry: dict[str, Any],
    entry_id: int | str,
    playbook: dict[str, Any],
    pack_graph: dict[str, Any],
    *,
    stop_after_exceptions: bool = False,
) -> dict[str, Any]:
    """Blocking gates halt the walk. Each other lane keeps its first match."""
    priorities = list(playbook.get("priorities") or [])
    exception_ids = set(playbook.get("exception_probes") or []) if stop_after_exceptions else set()
    version = _playbook_version(playbook)
    trace: list[dict[str, Any]] = []
    directives: list[dict[str, Any]] = []
    lanes_seen: set[str] = set()
    exception_hits: list[dict[str, Any]] = []
    primary: dict[str, Any] | None = None

    for idx, probe_id in enumerate(priorities):
        action, probe = _probe_outcome(entry, playbook, probe_id, pack_graph)
        if probe_id in exception_ids:
            probe["lane"] = _review_lane(playbook)
            trace.append(probe)
            if action is not None:
                exception_hits.append(probe)
            continue
        if exception_hits:
            return _exception_review_decision(
                entry, entry_id, playbook, trace, exception_hits, version
            )
        if action is None:
            probe["lane"] = _shared_action_lane(playbook, probe_id)
            trace.append(probe)
            continue
        lane = str(action.get("lane") or "").strip()
        probe["lane"] = lane or None
        if action.get("blocking") is True:
            trace.append(probe)
            decision = _decision(
                entry_id,
                action,
                trace,
                remaining=priorities[idx + 1 :],
                terminal=bool(action.get("terminal")),
                playbook_version=version,
            )
            decision["routing_reason"] = f"{probe['detail']} → {action.get('step')}."
            if probe.get("cel"):
                decision["matched_cel"] = probe["cel"]
            decision["routing_summary"] = format_routing_summary(decision)
            decision["routing_directives"] = [_directive(probe_id, action, probe)]
            return decision
        if lane in lanes_seen:
            probe["status"] = "skipped"
            probe["skipped_reason"] = "lane_already_matched"
            trace.append(probe)
            continue
        lanes_seen.add(lane)
        trace.append(probe)
        if primary is None:
            primary = action
        directives.append(_directive(probe_id, action, probe))

    if exception_hits:
        return _exception_review_decision(
            entry, entry_id, playbook, trace, exception_hits, version
        )
    if primary is None:
        return _default_decision(entry, entry_id, playbook, trace, version)
    decision = _decision(
        entry_id,
        primary,
        trace,
        remaining=[],
        terminal=bool(primary.get("terminal")),
        playbook_version=version,
    )
    decision["routing_directives"] = directives
    return decision


def _route_with_exceptions(
    entry: dict[str, Any],
    entry_id: int | str,
    playbook: dict[str, Any],
    pack_graph: dict[str, Any],
) -> dict[str, Any]:
    """Score every document exception into one review, else walk dispositions."""
    return _route_lanes(
        entry,
        entry_id,
        playbook,
        pack_graph,
        stop_after_exceptions=True,
    )


def _exception_review_decision(
    entry: dict[str, Any],
    entry_id: int | str,
    playbook: dict[str, Any],
    trace: list[dict[str, Any]],
    hits: list[dict[str, Any]],
    version: str | None,
) -> dict[str, Any]:
    review = dict(playbook.get("exception_review") or {})
    details = "; ".join(str(item.get("detail") or "") for item in hits)
    step = review.get("step")
    decision = _decision(
        entry_id,
        review,
        trace,
        remaining=[],
        terminal=bool(review.get("terminal", True)),
        playbook_version=version,
        reason_text=details,
    )
    reason = f"{details} → {step}."
    decision["routing_reason"] = reason
    decision["routing_summary"] = format_routing_summary(decision)
    directive = _directive("exception_review", review, {"detail": details})
    directive["routing_reason"] = reason
    decision["routing_directives"] = [directive]
    decision["exceptions"] = [
        {"probe_id": item.get("probe_id"), "detail": item.get("detail")} for item in hits
    ]
    return decision


def _default_decision(
    entry: dict[str, Any],
    entry_id: int | str,
    playbook: dict[str, Any],
    trace: list[dict[str, Any]],
    version: str | None,
) -> dict[str, Any]:
    default = dict(playbook.get("default_action") or {})
    reason = _default_disposition_reason(entry)
    decision = _decision(
        entry_id,
        default,
        trace,
        remaining=[],
        terminal=bool(default.get("terminal", True)),
        playbook_version=version,
        reason_text=reason,
    )
    directive = _directive("default_action", default, {"detail": reason})
    directive["routing_reason"] = decision["routing_reason"]
    decision["routing_directives"] = [directive]
    return decision


_RESERVED_FORM_FIELDS = {
    "entry_exists",
    "next_step",
    "agent_role",
    "lane",
    "write",
    "allowed_tools",
    "run_id",
}
_TEXT_FIELD_TYPES = {"text", "date", "select"}


def _validate_playbook(playbook: dict[str, Any], pack_graph: dict[str, Any] | None = None) -> None:
    """Every action, the default, and exception_review declare blocking and lane."""
    actions = playbook.get("actions") or {}
    if not isinstance(actions, dict):
        raise RuntimeError("Playbook actions must be a mapping")
    for probe_id, entries in actions.items():
        if not isinstance(entries, list):
            raise RuntimeError(f"Playbook actions[{probe_id}] must be a list")
        for index, action in enumerate(entries):
            _require_blocking_lane(action, f"actions[{probe_id}][{index}]")
    _require_blocking_lane(playbook.get("default_action") or {}, "default_action")
    if "exception_review" in playbook:
        _require_blocking_lane(playbook.get("exception_review") or {}, "exception_review")
    validate_playbook_forms(playbook, graph=pack_graph)


def validate_playbook_forms(
    playbook: dict[str, Any],
    ontology_fields: set[str] | None = None,
    graph: dict[str, Any] | None = None,
) -> None:
    """Action form ids must name a forms entry. Probe form: ASK is not a form id."""
    forms = playbook.get("forms") or {}
    if forms and not isinstance(forms, dict):
        raise RuntimeError("Playbook forms must be a mapping")
    for form_id, where in _action_form_refs(playbook):
        spec = forms.get(form_id) if isinstance(forms, dict) else None
        if not isinstance(spec, dict):
            raise RuntimeError(f"Playbook {where} form '{form_id}' is not defined")
        _validate_form_fields(form_id, spec, ontology_fields, graph)


def _action_form_refs(playbook: dict[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    actions = playbook.get("actions") or {}
    if isinstance(actions, dict):
        for probe_id, entries in actions.items():
            if not isinstance(entries, list):
                continue
            for index, action in enumerate(entries):
                form_id = _action_form_id(action)
                if form_id:
                    refs.append((form_id, f"actions[{probe_id}][{index}]"))
    for where in ("default_action", "exception_review"):
        form_id = _action_form_id(playbook.get(where))
        if form_id:
            refs.append((form_id, where))
    return refs


def _action_form_id(action: Any) -> str:
    if not isinstance(action, dict):
        return ""
    return str(action.get("form") or "").strip()


def _validate_form_fields(
    form_id: str,
    spec: dict[str, Any],
    ontology_fields: set[str] | None,
    graph: dict[str, Any] | None,
) -> None:
    fields = spec.get("fields") or []
    if not isinstance(fields, list):
        raise RuntimeError(f"Playbook form '{form_id}' fields must be a list")
    strings = set((graph or {}).get("strings") or [])
    numbers = set((graph or {}).get("numbers") or [])
    booleans = set((graph or {}).get("booleans") or [])
    for field in fields:
        if not isinstance(field, dict):
            raise RuntimeError(f"Playbook form '{form_id}' has a field that is not a mapping")
        name = str(field.get("id") or "").strip()
        kind = str(field.get("type") or "").strip()
        if not name or not kind:
            raise RuntimeError(f"Playbook form '{form_id}' has a field missing id or type")
        if ontology_fields is not None and (name in _RESERVED_FORM_FIELDS or name not in ontology_fields):
            raise RuntimeError(f"Playbook form '{form_id}' field '{name}' is not an ontology fact")
        if graph is not None:
            if kind == "boolean" and name not in booleans:
                raise RuntimeError(f"Playbook form '{form_id}' field '{name}' type {kind} disagrees with graph")
            if kind == "number" and name not in numbers:
                raise RuntimeError(f"Playbook form '{form_id}' field '{name}' type {kind} disagrees with graph")
            if kind in _TEXT_FIELD_TYPES and name not in strings:
                raise RuntimeError(f"Playbook form '{form_id}' field '{name}' type {kind} disagrees with graph")
        if kind == "select" and not field.get("options"):
            raise RuntimeError(f"Playbook form '{form_id}' field '{name}' select is missing options")


def _review_lane(playbook: dict[str, Any]) -> str | None:
    lane = str((playbook.get("exception_review") or {}).get("lane") or "").strip()
    return lane or None


def _shared_action_lane(playbook: dict[str, Any], probe_id: str) -> str | None:
    """One lane when every candidate action agrees. None when they disagree."""
    entries = (playbook.get("actions") or {}).get(probe_id) or []
    lanes: list[str] = []
    if isinstance(entries, list):
        for action in entries:
            if not isinstance(action, dict):
                continue
            lane = str(action.get("lane") or "").strip()
            if lane and lane not in lanes:
                lanes.append(lane)
    if len(lanes) == 1:
        return lanes[0]
    return None


def _require_blocking_lane(action: dict[str, Any], where: str) -> None:
    if not isinstance(action, dict) or not isinstance(action.get("blocking"), bool):
        raise RuntimeError(f"Playbook {where} is missing blocking")
    lane = action.get("lane")
    if not isinstance(lane, str) or not lane.strip():
        raise RuntimeError(f"Playbook {where} is missing lane")


def _probe_outcome(
    entry: dict[str, Any],
    playbook: dict[str, Any],
    probe_id: str,
    pack_graph: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    probe_cfg = (playbook.get("probes") or {})[probe_id]
    form = str(probe_cfg.get("form") or "ASK").upper()
    if str(probe_cfg.get("cel") or "").strip():
        form = "ASK"
    result = _exec_probe(entry, probe_cfg, form, pack_graph)
    action = _match_action(playbook, probe_id, form, result)
    return action, _trace_entry(probe_id, probe_cfg, form, result, action is not None)


def _directive(
    probe_id: str, action: dict[str, Any], probe: dict[str, Any]
) -> dict[str, Any]:
    step = action.get("step")
    detail = probe.get("detail") or ""
    directive = {
        "directive_id": probe_id,
        "lane": str(action.get("lane") or "").strip(),
        "agent_role": action.get("agent"),
        "next_step": step,
        "routing_reason": f"{detail} → {step}.",
        "blocking": bool(action.get("blocking")),
        "terminal": bool(action.get("terminal")),
        "write": _optional_str(action, "write", "write_label"),
        "allowed_tools": list(action.get("tools") or []),
        "letter_on_request": bool(action.get("letter_on_request", False)),
    }
    form_id = _action_form_id(action)
    if form_id:
        directive["form"] = form_id
    return directive


def _default_disposition_reason(_entry: dict[str, Any]) -> str:
    """Generic fallback when no probe assigns the entry."""
    return "No probes matched the entry state."


def _playbook_version(playbook: dict[str, Any]) -> str | None:
    version = playbook.get("version")
    if version is None:
        return None
    text = str(version).strip()
    return text or None


def _load_playbook(playbook_path: str | Path | None) -> dict[str, Any]:
    path = Path(playbook_path) if playbook_path else default_playbook_path()
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _exec_probe(
    entry: dict[str, Any],
    probe_cfg: dict[str, Any],
    form: str,
    pack_graph: dict[str, Any],
) -> Any:
    cel = probe_cfg.get("cel")
    if cel is not None and str(cel).strip():
        return eval_cel(str(cel), entry, pack_graph)
    if form == "ASK":
        return eval_match(probe_cfg.get("match") or {}, entry)
    if form == "SELECT":
        path = probe_cfg.get("path") or ""
        value = get_path(entry, str(path))
        return [] if value is None else [{"value": value}]
    raise ValueError(f"Unsupported probe form: {form}")


def _match_action(
    playbook: dict[str, Any], probe_id: str, form: str, result: Any
) -> dict[str, Any] | None:
    for action in playbook.get("actions", {}).get(probe_id, []):
        when = action.get("when") or action.get("on")
        if when == "ASK_TRUE" and form == "ASK" and result is True:
            return action
        if when == "ASK_FALSE" and form == "ASK" and result is False:
            return action
        if when == "SELECT_EQUALS" and form == "SELECT":
            expected = action.get("match_value")
            for row in result or []:
                if expected in row.values():
                    return action
        if when == "ALWAYS":
            return action
    return None


def _select_value(result: Any) -> str:
    if isinstance(result, list) and result and isinstance(result[0], dict):
        value = result[0].get("value")
        if value is not None:
            return str(value)
    return ""


def _detail(
    probe_id: str,
    probe_cfg: dict[str, Any],
    form: str,
    result: Any,
    *,
    assigned: bool,
) -> str:
    title = str(probe_cfg.get("title") or probe_id)
    if form == "ASK":
        key = "when_true" if result is True else "when_false"
        return str(probe_cfg.get(key) or title)
    if form == "SELECT":
        value = _select_value(result)
        key = "when_equals" if assigned else "otherwise"
        text = str(probe_cfg.get(key) or title)
        return text.replace("{value}", value)
    return title


def _trace_entry(
    probe_id: str,
    probe_cfg: dict[str, Any],
    form: str,
    result: Any,
    assigned: bool,
) -> dict[str, Any]:
    title = str(probe_cfg.get("title") or probe_id)
    entry = {
        "probe_id": probe_id,
        "form": form,
        "result": result,
        "title": title,
        "status": "assigned" if assigned else "did_not_apply",
        "detail": _detail(probe_id, probe_cfg, form, result, assigned=assigned),
    }
    cel = str(probe_cfg.get("cel") or "").strip()
    if cel:
        entry["cel"] = cel
    return entry


def _optional_str(action: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = action.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _decision(
    entry_id: int | str,
    action: dict[str, Any],
    probe_trace: list[dict[str, Any]],
    *,
    remaining: list[str],
    terminal: bool,
    playbook_version: str | None = None,
    reason_text: str | None = None,
) -> dict[str, Any]:
    step = action.get("step")
    assigned = next((p for p in probe_trace if p.get("status") == "assigned"), None)
    if assigned:
        reason = f"{assigned['detail']} → {step}."
    elif reason_text:
        reason = f"{reason_text} → {step}."
    else:
        fallback = str(action.get("title") or _DEFAULT_TITLE)
        reason = f"{fallback} → {step}."
    later = bool(remaining)
    letter_on_request = bool(action.get("letter_on_request", False))
    letter_note = str(action.get("letter_note") or "").strip() or None
    if letter_on_request and not letter_note:
        letter_note = (
            "A letter is recommended as the next step. "
            "It will not be drafted unless you ask."
        )
    checks = []
    for probe in probe_trace:
        item = {
            "probe_id": probe["probe_id"],
            "title": probe["title"],
            "status": probe["status"],
            "detail": probe["detail"],
            "lane": probe.get("lane"),
        }
        if probe.get("skipped_reason"):
            item["skipped_reason"] = probe["skipped_reason"]
        checks.append(item)
    decision = {
        "entry_id": str(entry_id),
        "lane": action.get("lane"),
        "next_step": step,
        "agent_role": action.get("agent"),
        "coworker": _optional_str(action, "coworker", "coworker_role"),
        "write": _optional_str(action, "write", "write_label"),
        "task_type_code": _optional_str(action, "task_type_code"),
        "allowed_tools": list(action.get("tools") or []),
        "letter_on_request": letter_on_request,
        "letter_note": letter_note,
        "terminal": terminal,
        "reason_probe_ids": [p["probe_id"] for p in probe_trace],
        "probe_trace": probe_trace,
        "routing_reason": reason,
        "checks": checks,
        "later_checks_not_run": later,
        "later_checks_note": _LATER_NOTE if later else None,
    }
    if playbook_version:
        decision["playbook_version"] = playbook_version
    reason_code = _optional_str(action, "reason_code")
    if reason_code:
        decision["reason_code"] = reason_code
    if assigned and assigned.get("cel"):
        decision["matched_cel"] = assigned["cel"]
    decision["routing_summary"] = format_routing_summary(decision)
    return decision


def format_routing_summary(decision: dict[str, Any]) -> str:
    """Plain-language block for Studio Observation / Final Answer (no probe ids)."""
    lines = [
        f"Next step: {decision.get('next_step')}",
        f"Lane: {decision.get('lane')}",
        f"Assigned agent: {decision.get('agent_role')}",
        "",
        f"Why this routing: {decision.get('routing_reason')}",
    ]
    if decision.get("letter_note"):
        lines.extend(["", str(decision["letter_note"])])
    if decision.get("disposition_note"):
        lines.extend(["", str(decision["disposition_note"])])
    lines.extend(
        [
            "",
            "Checks on this snapshot:",
        ]
    )
    for check in decision.get("checks") or []:
        tag = (
            "assigned this work"
            if check.get("status") == "assigned"
            else "did not apply"
        )
        lines.append(f"- {check.get('title')}: {check.get('detail')} ({tag})")
    if decision.get("later_checks_not_run"):
        lines.append("")
        lines.append(str(decision.get("later_checks_note") or _LATER_NOTE))
    return "\n".join(lines)
