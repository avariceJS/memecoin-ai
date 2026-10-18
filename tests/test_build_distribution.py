from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.build_distribution import source_date_epoch


def test_source_date_epoch_uses_explicit_environment_value(tmp_path: Path) -> None:
    assert source_date_epoch(tmp_path, {"SOURCE_DATE_EPOCH": "1760745600"}) == 1760745600


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("not-a-number", "source_date_epoch_invalid"),
        ("0", "source_date_epoch_out_of_zip_range"),
        ("4354819200", "source_date_epoch_out_of_zip_range"),
    ],
)
def test_source_date_epoch_rejects_invalid_values(tmp_path: Path, raw: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        source_date_epoch(tmp_path, {"SOURCE_DATE_EPOCH": raw})


def test_source_date_epoch_uses_commit_timestamp_when_unset(tmp_path: Path) -> None:
    result = subprocess.CompletedProcess([], 0, "1760745600\n", "")
    with patch("tools.build_distribution.subprocess.run", return_value=result) as run:
        assert source_date_epoch(tmp_path, {}) == 1760745600
    assert run.call_args.args[0] == [
        "git",
        "-C",
        str(tmp_path),
        "log",
        "-1",
        "--format=%ct",
    ]


def test_source_date_epoch_requires_pin_for_source_archives(tmp_path: Path) -> None:
    with patch(
        "tools.build_distribution.subprocess.run",
        side_effect=FileNotFoundError,
    ):
        with pytest.raises(ValueError, match="source_date_epoch_required_outside_git"):
            source_date_epoch(tmp_path, {})
