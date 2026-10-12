from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools.brain_senses_gecko_rotating_scout_v1 import (  # noqa: E402
    MAX_INTENTS_PER_UTC_DAY,
    NS,
)
from tools.brain_senses_gecko_shared_usage_v1 import (  # noqa: E402
    UsageError,
    assess_usage,
    make_verified_root_snapshot,
)

NOW = 1_800_000_000 * NS
ROOTS = [
    "services/gecko-market-v1/capture-only-v1/service-a",
    "services/gecko-market-v1/v3-pilots/pilot-a",
    "services/gecko-market-v1/v4",
]
SOURCE_HASH = hashlib.sha256(b"verified source prefix").hexdigest()
RESPONSE_HASH = hashlib.sha256(b"known response").hexdigest()


def _attempt(
    request_id: str,
    decision_ns: int,
    *,
    maximum: int = 16 * 1024,
    response_bytes: int | None = None,
    response_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "decision_ns": decision_ns,
        "maximum_response_bytes": maximum,
        "response_bytes": response_bytes,
        "response_sha256": response_hash,
    }


def _snapshot(root: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    return make_verified_root_snapshot(
        root=root,
        status="verified_prefix" if attempts else "verified_none",
        source_prefix_sha256=SOURCE_HASH,
        attempts=attempts,
    )


def _empty_snapshots() -> list[dict[str, Any]]:
    return [_snapshot(root, []) for root in ROOTS]


def test_all_versions_aggregate_and_ambiguous_attempts_reserve_maximum_bytes() -> None:
    snapshots = _empty_snapshots()
    snapshots[0] = _snapshot(
        ROOTS[0],
        [_attempt("known", NOW - 10 * NS, response_bytes=10, response_hash=RESPONSE_HASH)],
    )
    snapshots[1] = _snapshot(
        ROOTS[1],
        [_attempt("ambiguous", NOW - 5 * NS, maximum=700)],
    )
    result = assess_usage(
        snapshots,
        expected_roots=ROOTS,
        checked_ns=NOW,
        prospective_request_id="next",
        prospective_maximum_response_bytes=500,
    )
    assert result["admitted"] is True
    assert result["utc_day"]["request_count_including_prospective"] == 3
    assert result["utc_day"]["charged_response_bytes_including_prospective"] == 1_210
    assert result["unresolved_attempt_count_in_rolling_30d"] == 2
    assert result["actual_provider_charge"] == "unknown_unverified"


def test_incomplete_or_unexpected_root_inventory_fails_closed() -> None:
    with pytest.raises(UsageError, match="root_inventory_incomplete_or_unexpected"):
        assess_usage(
            _empty_snapshots()[:-1],
            expected_roots=ROOTS,
            checked_ns=NOW,
            prospective_request_id="next",
        )
    unexpected = _empty_snapshots()
    unexpected[1] = _snapshot("services/gecko-market-v1/v2-legacy", [])
    with pytest.raises(UsageError, match="root_inventory_incomplete_or_unexpected"):
        assess_usage(
            unexpected,
            expected_roots=ROOTS,
            checked_ns=NOW,
            prospective_request_id="next",
        )


def test_absent_root_is_only_an_explicit_verified_absence_record() -> None:
    snapshots = _empty_snapshots()
    snapshots[0] = make_verified_root_snapshot(
        root=ROOTS[0],
        status="verified_absent",
        source_prefix_sha256=SOURCE_HASH,
        attempts=[],
    )
    assert assess_usage(
        snapshots,
        expected_roots=ROOTS,
        checked_ns=NOW,
        prospective_request_id="next",
    )["admitted"]
    malformed = dict(snapshots[0])
    malformed["attempts"] = [_attempt("orphan", NOW - NS)]
    with pytest.raises(UsageError, match="attempts_hash_invalid"):
        assess_usage(
            [malformed, *snapshots[1:]],
            expected_roots=ROOTS,
            checked_ns=NOW,
            prospective_request_id="next",
        )


def test_day_request_limit_counts_ambiguous_attempts_and_candidate() -> None:
    rows = [
        _attempt(f"req-{index:04d}", NOW - index * NS, maximum=1)
        for index in range(MAX_INTENTS_PER_UTC_DAY)
    ]
    snapshots = [_snapshot(ROOTS[0], rows), *[_snapshot(root, []) for root in ROOTS[1:]]]
    result = assess_usage(
        snapshots,
        expected_roots=ROOTS,
        checked_ns=NOW,
        prospective_request_id="one-more",
        prospective_maximum_response_bytes=1,
    )
    assert result["admitted"] is False
    assert result["reasons"] == ["utc_day_request_cap"]
    assert result["utc_day"]["request_count_including_prospective"] == MAX_INTENTS_PER_UTC_DAY + 1


def test_raw_byte_cap_reserves_full_maximum_for_old_ambiguous_requests() -> None:
    rows = [
        _attempt(f"large-{index:04d}", NOW - index * NS, maximum=22_528) for index in range(500)
    ]
    snapshots = [_snapshot(ROOTS[0], rows), *[_snapshot(root, []) for root in ROOTS[1:]]]
    result = assess_usage(
        snapshots,
        expected_roots=ROOTS,
        checked_ns=NOW,
        prospective_request_id="next",
        prospective_maximum_response_bytes=16 * 1024,
    )
    assert result["admitted"] is False
    assert result["reasons"] == ["utc_day_raw_byte_cap"]


def test_old_attempts_outside_rolling_window_do_not_consume_current_caps() -> None:
    snapshots = [
        _snapshot(ROOTS[0], [_attempt("old", NOW - 31 * 86_400 * NS)]),
        *_empty_snapshots()[1:],
    ]
    result = assess_usage(
        snapshots,
        expected_roots=ROOTS,
        checked_ns=NOW,
        prospective_request_id="next",
    )
    assert result["admitted"]
    assert result["rolling_30d"]["request_count_including_prospective"] == 1


def test_known_hash_cannot_claim_more_response_bytes_than_pinned_maximum() -> None:
    snapshot = _snapshot(
        ROOTS[0],
        [_attempt("known", NOW - NS, maximum=100, response_bytes=101, response_hash=RESPONSE_HASH)],
    )
    with pytest.raises(UsageError, match="response_bytes_invalid"):
        assess_usage(
            [snapshot, *_empty_snapshots()[1:]],
            expected_roots=ROOTS,
            checked_ns=NOW,
            prospective_request_id="next",
        )
