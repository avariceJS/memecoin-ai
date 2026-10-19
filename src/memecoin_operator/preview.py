"""Fail-closed validation of disposable developer preview bindings."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


def validate_preview(root: Path) -> dict[str, Any]:
    """Validate before opening any SQLite connection or starting the HTTP server."""
    root = Path(root)
    protected = {"d43", "f01", "holdout", "reserved", "protected"}
    if (
        root.is_symlink()
        or not root.is_dir()
        or any(
            protected.intersection(re.split(r"[^a-z0-9]+", part.casefold()))
            for part in root.resolve().parts
        )
    ):
        raise ValueError("developer_preview_root_invalid")
    root = root.resolve(strict=True)
    marker = root / "dev-preview.json"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("developer_preview_metadata_required")
    info = json.loads(marker.read_text(encoding="utf-8"))
    if (
        not isinstance(info, dict)
        or info.get("schema") != "brain-dev-operator-preview-v1"
        or not isinstance(info.get("source_snapshot_at"), str)
    ):
        raise ValueError("developer_preview_metadata_invalid")

    # No linked file/directory may redirect GET-side SQLite/schema/log operations.
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if protected.intersection(re.split(r"[^a-z0-9]+", name.casefold())):
                raise ValueError("developer_preview_scope_invalid")
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
                raise ValueError("developer_preview_link_invalid")
    if any((root / name).exists() for name in ("deployment.json", "successor-ready.json", "watch")):
        raise ValueError("developer_preview_operational_state_forbidden")

    def read(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("developer_preview_config_invalid")
        return value

    config = read(root / "config.json")
    source = root / "source"
    if config.get("source_root") != str(source) or config.get("champion_model_path") != str(
        root / "models" / "not-copied"
    ):
        raise ValueError("developer_preview_binding_invalid")
    legacy = config.get("legacy_papers", [])
    if not isinstance(legacy, list):
        raise ValueError("developer_preview_binding_invalid")
    paper_roots = []
    for entry in legacy:
        if not isinstance(entry, dict) or not isinstance(entry.get("root"), str):
            raise ValueError("developer_preview_binding_invalid")
        path = Path(entry["root"])
        if path.parent != root / "legacy" or path.resolve() != path or not path.is_dir():
            raise ValueError("developer_preview_binding_invalid")
        paper_roots.append(path)
    for path in (root / "exams").glob("*.json"):
        record = read(path)
        raw = record.get("exam_root")
        if not isinstance(raw, str):
            raise ValueError("developer_preview_binding_invalid")
        paper = Path(raw)
        if paper.parent != root / "paper" or paper.resolve() != paper:
            raise ValueError("developer_preview_binding_invalid")
        paper_roots.append(paper)
    for paper in paper_roots:
        if (paper / "config.json").exists() and read(paper / "config.json").get("source") != str(
            source
        ):
            raise ValueError("developer_preview_binding_invalid")
    for relative, key in (
        ("operator.sqlite", "operator_database_sha256"),
        ("source/index.sqlite", "source_index_sha256"),
    ):
        database = root / relative
        wal = Path(str(database) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("developer_preview_database_changed")
        with database.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if info.get(key) != digest:
            raise ValueError("developer_preview_database_changed")
    return info
