"""Agent Studio helpers: workflow_data, session artifacts, MCP payload normalize."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def workflow_data_dir() -> Path:
    return Path(os.environ.get("WORKFLOW_DATA_DIRECTORY", "/workflow_data")).expanduser()


def _default_session_dir() -> Path:
    """Project-local folder. `/workspace` is a Cloudera mount, not a local path."""
    return Path(__file__).resolve().parents[2] / "workspace"


def session_dir() -> Path:
    configured = os.environ.get("SESSION_DIRECTORY", "").strip()
    path = Path(configured).expanduser() if configured else _default_session_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_workflow_assets(assets_root: str | None = None) -> Path:
    """Point ``PACK_ROOT`` at a pack."""
    from rules_engine.pack import is_pack_root
    from rules_engine.paths import repo_root

    if assets_root:
        root = Path(assets_root).expanduser().resolve()
    else:
        wf = workflow_data_dir()
        if is_pack_root(wf):
            root = wf.resolve()
        else:
            return repo_root()

    if not is_pack_root(root):
        raise FileNotFoundError(f"Assets root missing pack.yaml: {root}.")
    os.environ["PACK_ROOT"] = str(root)
    return root


def graph_artifact_path(entry_id: str | int) -> Path:
    """Session entry JSON."""
    return session_dir() / f"entry_{entry_id}_case.json"


def validation_artifact_path(entry_id: str | int) -> Path:
    return session_dir() / f"entry_{entry_id}_validation.json"


def decision_artifact_path(entry_id: str | int) -> Path:
    return session_dir() / f"entry_{entry_id}_route.json"


def artifact_path(entry_id: str | int, artifact_type: str = "letter") -> Path:
    """Session artifact. The type is the filename suffix, such as letter or sms."""
    return session_dir() / f"entry_{_safe_entry_id(entry_id)}_{_safe_artifact_type(artifact_type)}.txt"


def save_entry_artifact(
    entry_id: str | int,
    body: str,
    *,
    artifact_type: str = "letter",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Write a drafted artifact to SESSION_DIRECTORY. Does not send it."""
    text = (body or "").strip()
    if not text:
        raise ValueError("body is required (drafted artifact text)")
    if not text.endswith("\n"):
        text = text + "\n"
    kind = _safe_artifact_type(artifact_type)
    path = artifact_path(entry_id, kind)
    write_text_artifact(path, text)
    return {
        "entry_id": _safe_entry_id(entry_id),
        "artifact_type": kind,
        "run_id": run_id,
        "artifact": str(path.resolve()),
        "session_directory": str(session_dir()),
        "bytes": path.stat().st_size,
    }


def write_text_artifact(path: Path, text: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path.resolve())


def _safe_entry_id(entry_id: str | int) -> str:
    eid = str(entry_id).strip()
    if not eid or any(part in eid for part in ("/", "\\", "..")):
        raise ValueError("entry_id is not a safe artifact name")
    return eid


def _safe_artifact_type(artifact_type: str) -> str:
    token = "".join(ch for ch in str(artifact_type).lower() if ch.isalnum())
    return token or "letter"


def write_json_artifact(path: Path, data: Any) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    return str(path.resolve())


def parse_json_arg(raw: Any, *, label: str = "json") -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if raw is None:
        return {}
    if not isinstance(raw, str):
        raise ValueError(f"{label} must be a JSON string or object")
    text = raw.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc


def _lower_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k).lower(): _lower_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_lower_keys(v) for v in obj]
    return obj


def normalize_spine_payload(raw: Any) -> dict[str, Any]:
    """Accept fork MCP envelope or flat spine dict (SQL fallback / tests)."""
    payload = _lower_keys(parse_json_arg(raw, label="spine_json"))
    if not isinstance(payload, dict):
        raise ValueError("spine_json must be a JSON object")
    if payload.get("error") and "spine" not in payload:
        raise ValueError(str(payload["error"]))
    if isinstance(payload.get("spine"), dict):
        spine = dict(payload["spine"])
        return spine
    return payload


def normalize_signals_payload(raw: Any) -> dict[str, Any]:
    """Accept fork MCP envelope or flat signals dict."""
    payload = _lower_keys(parse_json_arg(raw, label="signals_json"))
    if not isinstance(payload, dict):
        raise ValueError("signals_json must be a JSON object")
    if payload.get("error") and "signals" not in payload:
        raise ValueError(str(payload["error"]))
    if isinstance(payload.get("signals"), dict):
        return dict(payload["signals"])
    return payload
