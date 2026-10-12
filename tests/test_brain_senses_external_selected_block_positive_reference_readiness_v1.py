from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest

from tools import brain_senses_external_selected_block_positive_reference_readiness_v1 as readiness
from tools import brain_senses_external_structural_input_schema_v1 as structural_schema


def _fixture() -> tuple[dict[str, Any], ...]:
    queue: dict[str, Any] = {
        "scope": {"plan_sha256": readiness.EXPECTED_PLAN_SHA256},
        "denominators": {"scheduled_frames": 12},
        "observed": {"captures": [], "references": []},
    }
    schedule: list[dict[str, Any]] = []
    captures = queue["observed"]["captures"]
    references = queue["observed"]["references"]
    for frame_idx, bucket in enumerate(("bucket-a", "bucket-b", "bucket-c")):
        schedule.append(
            {
                "bucket": bucket,
                "start_ns": 100 + frame_idx * 10,
                "end_ns": 110 + frame_idx * 10,
                "state": "sealed",
                "counts": {
                    "selected_blocks": 16,
                    "reference_occurrences": (1616, 1616, 1633)[frame_idx],
                    "produced_slots": (1388, 1388, 1388)[frame_idx],
                },
            }
        )
        for block_idx in range(16):
            idx = frame_idx * 16 + block_idx
            capture_id = f"capture-{idx:02d}"
            captures.append(
                {
                    "capture_id": capture_id,
                    "bucket": bucket,
                    "slot": 500_000 + idx,
                    "blockhash": f"blockhash-{idx:02d}",
                    "frame_index": frame_idx,
                    "available_ns": 1000 + idx,
                    "capture_receipt_sha256": "a" * 64,
                    "request_sha256": "b" * 64,
                    "response_sha256": "c" * 64,
                    "result_sha256": "d" * 64,
                }
            )
    for idx in range(9):
        schedule.append(
            {
                "bucket": f"unobserved-{idx}",
                "start_ns": 200 + idx * 10,
                "end_ns": 210 + idx * 10,
                "state": "callback_failed_unobserved" if idx == 0 else "expired_unobserved",
                "counts": {
                    "selected_blocks": None,
                    "reference_occurrences": None,
                    "produced_slots": None,
                },
            }
        )

    for idx in range(4_865):
        capture = captures[min(idx // 101, 47)]
        status = "success" if idx < 4_176 else "failed"
        references.append(
            {
                "program_id": structural_schema.PUMP_PROGRAM_ID,
                "capture_id": capture["capture_id"],
                "bucket": capture["bucket"],
                "slot": capture["slot"],
                "blockhash": capture["blockhash"],
                "transaction_status": status,
                "reference_id": f"reference-{idx:04d}",
                "signature_sha256": f"{idx:064x}",
                "instruction_path": {"outer_index": 0, "inner_index": idx},
            }
        )
    archive = {"schedule": deepcopy(schedule), "references": deepcopy(references)}
    family = {"references": deepcopy(references)}
    unknown = {
        "entries": deepcopy(references),
        "readiness": {"anomaly_source_qualified": False, "decision_ns": None},
        "scope": {"projection_known_ns": 10_000},
    }
    replay = {
        "status": "verified_not_published",
        "family_count": 68,
        "denominators": {"selected_blocks_in_sealed_frames": 48},
        "source_binding": {
            "idl_history_receipt_sha256": readiness.family_triage.IDL_HISTORY_RECEIPT_SHA256,
            "idl_manifest_sha256": readiness.family_triage.IDL_MANIFEST_SHA256,
            "idl_blobs": [{} for _ in range(18)],
        },
    }
    return queue, archive, family, unknown, replay


def _build(parts: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    queue, archive, family, unknown, replay = parts
    return cast(
        dict[str, Any],
        readiness.validate_and_build(
            queue=queue,
            archive=archive,
            family=family,
            unknown=unknown,
            source_replay=replay,
            projection_known_ns=20_000,
        ),
    )


def test_builds_12_frames_48_blocks_and_keeps_time_axes_separate() -> None:
    artifact = _build(_fixture())
    assert len(artifact["frames"]) == 12
    assert len(artifact["selected_blocks"]) == 48
    assert artifact["denominators"]["positive_reference_occurrences"] == 4_865
    assert artifact["denominators"]["success_reference_occurrences"] == 4_176
    assert artifact["denominators"]["failed_reference_occurrences"] == 689
    assert artifact["denominators"]["expected_event_count"] is None
    assert all(row["event_time_utc_ns"] is None for row in artifact["selected_blocks"])
    assert all(row["expected_event_count"] is None for row in artifact["frames"])
    assert [row["captured_selected_blocks"] for row in artifact["frames"][:3]] == [16, 16, 16]
    assert all(row["positive_reference_occurrences"] is None for row in artifact["frames"][3:])


def test_unobserved_zero_cannot_replace_null() -> None:
    parts = _fixture()
    parts[1]["schedule"][3]["counts"]["reference_occurrences"] = 0
    with pytest.raises(readiness.ReadinessError, match="unobserved_metrics_not_null"):
        _build(parts)


def test_reference_slot_blockhash_mismatch_is_rejected() -> None:
    parts = _fixture()
    parts[0]["observed"]["references"][0]["blockhash"] = "wrong-block"
    with pytest.raises(readiness.ReadinessError, match="reference_blockhash_join"):
        _build(parts)


def test_duplicate_slot_block_pair_is_rejected() -> None:
    parts = _fixture()
    parts[0]["observed"]["captures"][1]["slot"] = parts[0]["observed"]["captures"][0]["slot"]
    parts[0]["observed"]["captures"][1]["blockhash"] = parts[0]["observed"]["captures"][0][
        "blockhash"
    ]
    with pytest.raises(readiness.ReadinessError, match="duplicate_slot_block"):
        _build(parts)


def test_unknown_review_identity_set_must_match_source_queue() -> None:
    parts = _fixture()
    parts[3]["entries"][0]["signature_sha256"] = "f" * 64
    with pytest.raises(readiness.ReadinessError, match="unknown_identity_set"):
        _build(parts)


def test_replay_must_cover_all_selected_blocks() -> None:
    parts = _fixture()
    parts[4]["denominators"]["selected_blocks_in_sealed_frames"] = 47
    with pytest.raises(readiness.ReadinessError, match="replay_blocks"):
        _build(parts)
