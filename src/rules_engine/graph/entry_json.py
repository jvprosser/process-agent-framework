"""Normalize a pack spine and signals into one entry document for playbook probes."""

from __future__ import annotations

from datetime import date
from typing import Any

from rules_engine.pack import Pack
from rules_engine.studio_io import normalize_signals_payload, normalize_spine_payload


def build_entry_graph(
    entry_id: int | str,
    *,
    pack: Pack,
    spine: dict[str, Any] | str | None = None,
    signals: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Pack-driven entry document. Domain flags stay on the spine and signals."""
    spine = normalize_spine_payload(spine or {})
    signals = normalize_signals_payload(signals or {})
    merged: dict[str, Any] = {}
    if isinstance(spine, dict):
        inner = spine.get("spine") if isinstance(spine.get("spine"), dict) else spine
        merged.update(inner)
    if isinstance(signals, dict):
        inner_s = signals.get("signals") if isinstance(signals.get("signals"), dict) else signals
        merged.update(inner_s)

    entry_class = pack.graph.get("entry_class") or pack.graph.get("case_class") or "Entry"
    entry: dict[str, Any] = {
        "entry_id": str(entry_id),
        "entry_class": str(entry_class),
    }
    for field in _field_names(pack.graph.get("literals")):
        entry[field] = merged.get(field)
    for field in _field_names(pack.graph.get("booleans")):
        entry[field] = _as_bool(merged.get(field))
    entry["spine"] = spine
    entry["signals"] = signals
    entry.update({k: v for k, v in merged.items() if k not in entry})
    _fill_missing_entry_flags(entry, spine if isinstance(spine, dict) else {}, merged)
    return entry


def _present(value: Any) -> bool:
    return value not in (None, "")


def _spine_has_row(spine: dict[str, Any], merged: dict[str, Any]) -> bool:
    """True when the spine row carries a value, not merely the requested id."""
    for source in (spine, merged):
        for key, value in source.items():
            if key in {"spine", "signals"}:
                continue
            if isinstance(value, (list, dict)):
                if value:
                    return True
            elif _present(value):
                return True
    return False


def _fill_missing_entry_flags(
    entry: dict[str, Any],
    spine: dict[str, Any],
    merged: dict[str, Any],
) -> None:
    """Fill root flags the spine row does not store."""
    entry["entry_exists"] = _spine_has_row(spine, merged)
    if not _present(entry.get("eval_date")):
        entry["eval_date"] = date.today().isoformat()

    roles = entry.get("roles") if isinstance(entry.get("roles"), list) else spine.get("roles") or []
    if entry.get("has_adjuster") is None:
        entry["has_adjuster"] = any(
            str((row or {}).get("role_type_code") or "").upper() == "ADJUSTER"
            for row in roles
            if isinstance(row, dict)
        )

    policy_id = entry.get("policy_id") if _present(entry.get("policy_id")) else spine.get("policy_id")
    vehicle_id = (
        entry.get("insurable_object_id")
        if _present(entry.get("insurable_object_id"))
        else spine.get("insurable_object_id")
    )
    covers = entry.get("policy_covers_vehicle")
    if covers is None:
        covers = spine.get("policy_covers_vehicle")
    if covers is None:
        covers = _present(policy_id) and _present(vehicle_id)
    if entry.get("has_policy") is None:
        entry["has_policy"] = _present(policy_id)
    if entry.get("has_vehicle") is None:
        entry["has_vehicle"] = _present(vehicle_id)
    if entry.get("policy_covers_vehicle") is None:
        entry["policy_covers_vehicle"] = _as_bool(covers)
    if entry.get("triangle") is None:
        entry["triangle"] = bool(
            entry.get("has_policy")
            and entry.get("has_vehicle")
            and _as_bool(entry.get("policy_covers_vehicle"))
        )


def _field_names(mapping_or_list: Any) -> list[str]:
    if isinstance(mapping_or_list, dict):
        return [str(k) for k in mapping_or_list]
    return [str(x) for x in (mapping_or_list or [])]


def _as_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes"}
