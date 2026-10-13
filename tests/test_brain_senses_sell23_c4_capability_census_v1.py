from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from brain_senses_sell23_c4_capability_census_v1 import (  # noqa: E402
    _destination_attribution,
    _endpoint_status,
    _validate_outcome_partition,
)


@pytest.mark.parametrize(
    ("pre", "post", "touches", "expected"),
    [
        ("missing", "present", 0, "endpoint_missing"),
        ("present", "missing", 3, "endpoint_missing"),
        ("present", "present", 0, "no_other_touch_observed"),
        ("present", "present", 1, "present_but_unattributed"),
    ],
)
def test_destination_endpoint_attribution_is_stable(pre, post, touches, expected):
    assert _destination_attribution(pre, post, touches) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("present", "present"),
        ("missing", "missing"),
        ("present_but_unattributed", "present_but_unattributed"),
        (True, "true"),
        (False, "false"),
        (0, "zero"),
        (2, "positive"),
        (None, "not_applicable"),
    ],
)
def test_endpoint_status_preserves_distinct_evidence_states(value, expected):
    assert _endpoint_status(value) == expected


def test_outcome_partition_closes_388_structural_and_342_native_rows():
    rows = (
        [{"outcome": "native_close"}] * 341
        + [{"outcome": "native_close_unsupported_touch"}]
        + [{"outcome": "successful_outside_native_close"}] * 45
        + [{"outcome": "failed_structural_attempt"}]
    )
    counts = _validate_outcome_partition(rows)
    assert sum(counts.values()) == 388
    assert counts["native_close"] + counts["native_close_unsupported_touch"] == 342


def test_outcome_partition_rejects_denominator_drift():
    rows = (
        [{"outcome": "native_close"}] * 342
        + [{"outcome": "successful_outside_native_close"}] * 45
        + [{"outcome": "failed_structural_attempt"}]
    )
    with pytest.raises(ValueError, match="outcome_denominator_mismatch"):
        _validate_outcome_partition(rows)
