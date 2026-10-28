from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from engineering import paperclip_native_delivery as delivery


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _seed_envelope(repo: Path) -> dict[str, object]:
    return {"source": {"seed_repo": str(repo), "base_sha": _git(repo, "rev-parse", "HEAD")}}


def test_preparation_seed_allows_untracked_worker_worktrees_but_keeps_other_checks(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "seed"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    tracked = repo / ".paperclip" / "worktrees" / "tracked.txt"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("frozen\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "frozen seed")
    envelope = _seed_envelope(repo)

    worker_artifact = repo / ".paperclip" / "worktrees" / "MEM-221" / "notes.txt"
    worker_artifact.parent.mkdir()
    worker_artifact.write_text("worker state\n")
    delivery._validate_preparation_seed(envelope)

    unrelated = repo / "unrelated.txt"
    unrelated.write_text("must be rejected\n")
    with pytest.raises(delivery.DeliveryError, match="preparation_seed_not_clean_frozen_base"):
        delivery._validate_preparation_seed(envelope)
    unrelated.unlink()

    tracked.write_text("modified\n")
    with pytest.raises(delivery.DeliveryError, match="preparation_seed_not_clean_frozen_base"):
        delivery._validate_preparation_seed(envelope)
