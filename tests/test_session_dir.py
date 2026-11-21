"""Session artifacts stay inside the project when SESSION_DIRECTORY is unset."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from rules_engine import studio_io


def test_session_dir_defaults_inside_the_project(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SESSION_DIRECTORY", raising=False)
    monkeypatch.setattr(studio_io, "_default_session_dir", lambda: tmp_path / "workspace")

    path = studio_io.session_dir()

    assert path == tmp_path / "workspace"
    assert path.is_dir()
    assert path != Path("/workspace")
    studio_io.write_json_artifact(path / "claim_401_case.json", {"claim_id": 401})
    studio_io.write_text_artifact(path / "claim_401_letter.txt", "Draft letter.\n")
    assert (path / "claim_401_case.json").is_file()
    assert (path / "claim_401_letter.txt").is_file()


def test_default_session_dir_is_the_project_workspace():
    path = studio_io._default_session_dir()
    assert path == REPO / "workspace"
    assert path != Path("/workspace")
