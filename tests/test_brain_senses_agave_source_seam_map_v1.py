from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from tools.brain_senses_agave_source_seam_map_v1 import MapError, verify_map


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", os.fspath(root), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def _fixture(tmp_path: Path, content: str = "fn seam() {}\n") -> tuple[Path, dict[str, object]]:
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", os.fspath(root)], check=True, capture_output=True, text=True)
    (root / "src.rs").write_text(content, encoding="utf-8")
    _git(root, "add", "src.rs")
    subprocess.run(
        [
            "git",
            "-C",
            os.fspath(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    raw = (root / "src.rs").read_bytes()
    payload: dict[str, object] = {
        "schema": "agave-transaction-persistence-finality-source-map",
        "schema_version": 1,
        "source": {
            "commit": _git(root, "rev-parse", "HEAD"),
            "composite_diff_sha256": hashlib.sha256(b"").hexdigest(),
        },
        "source_files": [{"path": "src.rs", "sha256": hashlib.sha256(raw).hexdigest()}],
        "anchors": [{"path": "src.rs", "text": "fn seam()", "line": 1}],
    }
    return root, payload


def test_verifies_pinned_commit_diff_file_and_unique_anchor(tmp_path: Path) -> None:
    root, payload = _fixture(tmp_path)

    result = verify_map(root, payload)

    assert result == {
        "status": "source_map_verified_read_only",
        "commit": payload["source"]["commit"],  # type: ignore[index]
        "composite_diff_sha256": hashlib.sha256(b"").hexdigest(),
        "source_file_count": 1,
        "anchor_count": 1,
    }


def test_rejects_source_hash_drift(tmp_path: Path) -> None:
    root, payload = _fixture(tmp_path)
    (root / "src.rs").write_text("fn seam() { changed }\n", encoding="utf-8")
    payload["source"]["composite_diff_sha256"] = hashlib.sha256(  # type: ignore[index]
        subprocess.check_output(["git", "-C", os.fspath(root), "diff", "--binary"])
    ).hexdigest()

    with pytest.raises(MapError, match="source_file_hash_mismatch"):
        verify_map(root, payload)


def test_rejects_missing_or_ambiguous_anchor(tmp_path: Path) -> None:
    root, payload = _fixture(tmp_path, "fn seam() {}\nfn seam_again() {}\n")
    payload["anchors"][0]["text"] = "fn seam"  # type: ignore[index]

    with pytest.raises(MapError, match="anchor_missing_or_ambiguous"):
        verify_map(root, payload)


def test_rejects_symlinked_source_file(tmp_path: Path) -> None:
    root, payload = _fixture(tmp_path)
    (root / "outside.rs").write_text("fn seam() {}\n", encoding="utf-8")
    (root / "src.rs").unlink()
    (root / "src.rs").symlink_to(root / "outside.rs")
    payload["source"]["composite_diff_sha256"] = hashlib.sha256(  # type: ignore[index]
        subprocess.check_output(["git", "-C", os.fspath(root), "diff", "--binary"])
    ).hexdigest()
    payload["source_files"][0]["sha256"] = hashlib.sha256(  # type: ignore[index]
        (root / "outside.rs").read_bytes()
    ).hexdigest()

    with pytest.raises(MapError, match="source_symlink_rejected"):
        verify_map(root, payload)
