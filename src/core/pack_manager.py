"""Load one domain pack without embedding domain names in the runtime."""

from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_FOLDERS = ("ontology", "playbook", "catalog")
_active_pack: ContextVar["PackManager"] = ContextVar("active_pack")


class PackManager:
    """Mount one pack directory and hold its ontology, playbook, and catalog."""

    def __init__(self, pack_name: str, base_dir: str):
        name = str(pack_name or "").strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise RuntimeError(
                "pack_name must be a single directory name under base_dir"
            )
        if not str(base_dir or "").strip():
            raise RuntimeError("base_dir is required")

        self.pack_name = name
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.root = (self.base_dir / name).resolve()
        try:
            self.root.relative_to(self.base_dir)
        except ValueError as exc:
            raise RuntimeError(
                f"Pack path escapes base_dir: {self.root}"
            ) from exc
        if not self.root.is_dir():
            raise RuntimeError(
                f"Pack '{name}' not found under {self.base_dir}"
            )

        missing = [
            folder
            for folder in _REQUIRED_FOLDERS
            if not (self.root / folder).is_dir()
        ]
        if missing:
            raise RuntimeError(
                f"Pack '{name}' is missing required folders: {', '.join(missing)}"
            )

        self.manifest = self._read_manifest()
        self.ontology = self.load_ontology()
        self.playbook = self.load_playbook()
        self.catalog = self.load_catalog()
        from rules_engine.graph.route_entry import load_derived, validate_playbook_forms

        self.derived = load_derived(
            self.manifest.get("derived"),
            set(self.ontology_field_names()),
            self.playbook,
            self.pack_name,
        )
        validate_playbook_forms(
            self.playbook,
            set(self.ontology_field_names()),
            dict(self.manifest.get("graph") or {}),
        )
        self._activate()

    def load_ontology(self) -> dict[str, Any]:
        """Load every JSON schema under ontology/."""
        ontology_dir = self.root / "ontology"
        files = sorted(ontology_dir.glob("*.json"))
        if not files:
            raise RuntimeError(
                f"Pack '{self.pack_name}' has no JSON schema in {ontology_dir}"
            )
        loaded: dict[str, Any] = {}
        for path in files:
            loaded[path.stem] = _read_json(path)
        declared = self._declared_ontology_stem()
        if declared is not None and declared not in loaded:
            raise RuntimeError(
                f"Pack '{self.pack_name}' ontology entry '{declared}' "
                f"is not a file under {ontology_dir}"
            )
        return loaded

    def load_playbook(self) -> dict[str, Any]:
        """Load playbook/playbook.yaml."""
        path = self.playbook_path
        if not path.is_file():
            raise RuntimeError(
                f"Pack '{self.pack_name}' is missing {path.relative_to(self.root)}"
            )
        document = _read_yaml(path)
        if not isinstance(document, dict):
            raise RuntimeError(f"Playbook must be a mapping: {path}")
        return document

    def load_catalog(self) -> dict[str, Any]:
        """Load the allow-listed query catalog under catalog/."""
        path = self.catalog_path
        document = _read_yaml(path)
        if not isinstance(document, dict):
            raise RuntimeError(f"Catalog must be a mapping: {path}")
        reads = document.get("reads")
        writes = document.get("writes")
        if not isinstance(reads, dict) or not isinstance(writes, dict):
            raise RuntimeError(
                f"Catalog reads and writes must be mappings: {path}"
            )
        return document

    @property
    def schema(self) -> dict[str, Any]:
        """Primary ontology document for the mounted pack."""
        stem = self._declared_ontology_stem()
        if stem is not None:
            return self.ontology[stem]
        if len(self.ontology) == 1:
            return next(iter(self.ontology.values()))
        names = ", ".join(sorted(self.ontology))
        raise RuntimeError(
            f"Pack '{self.pack_name}' has multiple ontology files ({names}) "
            "and no ontology entry selecting one"
        )

    @property
    def playbook_path(self) -> Path:
        return self.root / "playbook" / "playbook.yaml"

    @property
    def catalog_path(self) -> Path:
        catalog_dir = self.root / "catalog"
        preferred = catalog_dir / "catalog.yaml"
        if preferred.is_file():
            return preferred
        matches = sorted(catalog_dir.glob("*.yaml")) + sorted(catalog_dir.glob("*.yml"))
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise RuntimeError(
                f"Pack '{self.pack_name}' has no YAML catalog in {catalog_dir}"
            )
        names = ", ".join(path.name for path in matches)
        raise RuntimeError(
            f"Pack '{self.pack_name}' catalog/ has multiple catalogs ({names}) "
            "and no catalog.yaml"
        )

    def ontology_field_names(self) -> list[str]:
        fields = self.schema.get("fields")
        if isinstance(fields, dict):
            return [str(name) for name in fields]
        return []

    def _read_manifest(self) -> dict[str, Any]:
        path = self.root / "pack.yaml"
        if not path.is_file():
            return {}
        document = _read_yaml(path)
        if not isinstance(document, dict):
            raise RuntimeError(f"pack.yaml must be a mapping: {path}")
        return document

    def _declared_ontology_stem(self) -> str | None:
        rel = self.manifest.get("ontology") or self.manifest.get("schema")
        if not rel:
            return None
        path = _inside(self.root, str(rel))
        if path.parent != (self.root / "ontology").resolve():
            raise RuntimeError(
                f"Pack '{self.pack_name}' ontology path must stay under ontology/: {rel}"
            )
        return path.stem

    def activate(self) -> "PackManager":
        """Make this mounted pack the one the tools read."""
        self._activate()
        return self

    def _activate(self) -> None:
        _active_pack.set(self)

    @staticmethod
    def active() -> "PackManager":
        try:
            return _active_pack.get()
        except LookupError as exc:
            raise RuntimeError(
                "No pack is mounted for this request context. "
                "Construct PackManager(pack_name, base_dir) first."
            ) from exc


def _read_json(path: Path) -> Any:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"Ontology JSON must be an object: {path}")
    return document


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML in {path}: {exc}") from exc


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Path escapes pack directory: {relative}") from exc
    return path
