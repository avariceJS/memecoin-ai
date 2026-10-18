#!/usr/bin/env python3
"""Verify a read-only, source-pinned Agave persistence/finality seam map."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


class MapError(RuntimeError):
    """The source checkout does not match the reviewed seam map."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_source_file(root: Path, relative: str) -> bytes:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise MapError("source_path_invalid")
    current = root
    for part in path.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise MapError("source_path_unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise MapError("source_symlink_rejected")
    if not stat.S_ISREG(info.st_mode):
        raise MapError("source_not_regular")
    try:
        return current.read_bytes()
    except OSError as exc:
        raise MapError("source_read_failed") from exc


def _git_value(root: Path, *args: str) -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "-C", os.fspath(root), *args], stderr=subprocess.DEVNULL
            )
            .decode("ascii")
            .strip()
        )
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as exc:
        raise MapError("git_source_probe_failed") from exc


def verify_map(source_root: Path, payload: Any) -> dict[str, Any]:
    """Check commit, composite diff, file hashes, and unique symbol anchors."""
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "agave-transaction-persistence-finality-source-map"
    ):
        raise MapError("map_schema_invalid")
    if payload.get("schema_version") != 1:
        raise MapError("map_version_invalid")

    root = source_root.absolute()
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise MapError("source_root_unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise MapError("source_root_invalid")

    source = payload.get("source")
    if not isinstance(source, dict):
        raise MapError("source_pin_missing")
    head = _git_value(root, "rev-parse", "HEAD")
    if head != source.get("commit"):
        raise MapError("source_commit_mismatch")
    diff = subprocess.check_output(["git", "-C", os.fspath(root), "diff", "--binary"])
    diff_sha256 = _sha256(diff)
    if diff_sha256 != source.get("composite_diff_sha256"):
        raise MapError("source_diff_mismatch")

    files = payload.get("source_files")
    if not isinstance(files, list) or not files:
        raise MapError("source_files_missing")
    content_by_path: dict[str, str] = {}
    for record in files:
        if not isinstance(record, dict):
            raise MapError("source_file_record_invalid")
        relative = record.get("path")
        expected = record.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise MapError("source_file_pin_invalid")
        raw = _read_source_file(root, relative)
        actual = _sha256(raw)
        if actual != expected:
            raise MapError("source_file_hash_mismatch")
        content_by_path[relative] = raw.decode("utf-8")

    anchors = payload.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise MapError("anchors_missing")
    checked = []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            raise MapError("anchor_record_invalid")
        relative = anchor.get("path")
        text = anchor.get("text")
        expected_line = anchor.get("line")
        if not isinstance(relative, str):
            raise MapError("anchor_pin_invalid")
        content = content_by_path.get(relative)
        if content is None or not isinstance(text, str) or type(expected_line) is not int:
            raise MapError("anchor_pin_invalid")
        lines = content.splitlines()
        matches = [index + 1 for index, line in enumerate(lines) if text in line]
        if len(matches) != 1:
            raise MapError("anchor_missing_or_ambiguous")
        if matches[0] != expected_line:
            raise MapError("anchor_line_mismatch")
        checked.append({"path": relative, "line": matches[0]})

    return {
        "status": "source_map_verified_read_only",
        "commit": head,
        "composite_diff_sha256": diff_sha256,
        "source_file_count": len(content_by_path),
        "anchor_count": len(checked),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.map_path.read_text(encoding="utf-8"))
        result = verify_map(args.source, payload)
    except (OSError, json.JSONDecodeError, MapError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
