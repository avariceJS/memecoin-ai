from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from memecoin_ai.domain import canonical
from tools import brain_senses_c4_terminal_qualifier as qualifier


def test_unified_log_timestamp_uses_macos_log_show_format() -> None:
    assert qualifier._iso_utc(1791677100418875000) == "2026-10-11 00:05:00"


@pytest.mark.parametrize(
    "values",
    [
        [1],
        [1, 2],
        [8, 1, 5, 3, 2],
        list(range(1, 101)),
    ],
)
def test_nearest_rank_is_permutation_invariant_and_selects_ceiling_rank(values: list[int]) -> None:
    expected = sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]

    assert qualifier._nearest_rank(values, 0.95) == expected
    assert qualifier._nearest_rank(list(reversed(values)), 0.95) == expected


def test_distribution_keeps_units_and_integer_bytes() -> None:
    result = qualifier._distribution([4, 1, 3, 2], "decimal bytes")

    assert result == {
        "samples": 4,
        "unit": "decimal bytes",
        "min": 1,
        "median": 2.5,
        "p95_nearest_rank": 4,
        "max": 4,
    }


def test_post_stop_suffix_rejects_late_requests_and_reservations(tmp_path: Path) -> None:
    root = tmp_path / "c4"
    result = root / "epochs/20261010T120000Z/request-results/000000000001.json"
    result.parent.mkdir(parents=True)
    result.write_text(
        canonical(
            {
                "request_started_ns": 11,
                "receipt_ns": 12,
                "availability_ns": 13,
            }
        )
    )

    with pytest.raises(ValueError, match="terminal_post_stop_request_found"):
        qualifier._assert_zero_request_suffix(root, 12)


def test_post_stop_suffix_rejects_late_reservation(tmp_path: Path) -> None:
    root = tmp_path / "c4"
    request = root / "bootstrap/20261010T120000Z/requests/000000000001.json"
    request.parent.mkdir(parents=True)
    request.write_text(json.dumps({"reserved_ns": 101}))

    with pytest.raises(ValueError, match="terminal_post_stop_reservation_found"):
        qualifier._assert_zero_request_suffix(root, 100)
