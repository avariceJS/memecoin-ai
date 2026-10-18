"""Synthetic crash boundaries: D41 must not inherit generic campaign retries."""

import json
import runpy
from pathlib import Path

import pytest

from memecoin_ai.domain import ValidationError

guard = runpy.run_path(str(Path(__file__).parents[1] / "tools/qualified_http_corpus.py"))[
    "require_clean_stage_boundary"
]


@pytest.mark.parametrize("state", ["unsealed", "interrupted", "failed", "closed"])
def test_failed_or_interrupted_stage_cannot_resume_without_network(tmp_path, state):
    segment = tmp_path / "segments/000"
    segment.mkdir(parents=True)
    if state == "closed":
        (tmp_path / "closure.json").write_text("{}")
    elif state != "unsealed":
        (segment / "seal.json").write_text(
            json.dumps(dict(interrupted=state == "interrupted", report=dict(complete=False)))
        )
    with pytest.raises(ValidationError, match="D41"):
        guard(tmp_path)


def test_only_clean_boundary_allows_existing_campaign_validation(tmp_path):
    (tmp_path / "segments").mkdir()
    guard(tmp_path)
    segment = tmp_path / "segments/000"
    segment.mkdir()
    (segment / "seal.json").write_text(
        json.dumps(dict(interrupted=False, report=dict(complete=True)))
    )
    guard(tmp_path)
