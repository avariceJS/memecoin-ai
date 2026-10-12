from __future__ import annotations

import copy
import hashlib
import json

import pytest

from tools import brain_senses_external_archival_projection_v1 as projection
from tools import brain_senses_external_structural_input_schema_v1 as source_schema


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixture() -> tuple[bytes, bytes, dict[str, int], dict[str, int]]:
    available = projection.INPUT_RECEIPT_NS - 20_000_000_000
    received = available - 1_000_000_000
    event = received - 1_000_000_000
    captures: list[dict[str, object]] = []
    refs: list[dict[str, object]] = []
    blocks: list[dict[str, object]] = []
    class_rows: dict[str, dict[str, object]] = {}
    for index, status in enumerate(("success", "failed")):
        bucket = "20261012T060500Z"
        capture_id = f"capture-{index}"
        signature = hashlib.sha256(f"sig-{index}".encode()).hexdigest()
        receipt_sha = hashlib.sha256(f"receipt-{index}".encode()).hexdigest()
        response_sha = hashlib.sha256(f"response-{index}".encode()).hexdigest()
        request_sha = hashlib.sha256(f"request-{index}".encode()).hexdigest()
        result_sha = hashlib.sha256(f"result-{index}".encode()).hexdigest()
        data_sha = hashlib.sha256(f"data-{index}".encode()).hexdigest()
        class_sha = hashlib.sha256(f"class-{index}".encode()).hexdigest()
        slot = 100 + index
        blockhash = hashlib.sha256(f"block-{index}".encode()).hexdigest()
        capture = {
            "capture_id": capture_id,
            "bucket": bucket,
            "slot": slot,
            "blockhash": blockhash,
            "capture_receipt_sha256": receipt_sha,
            "response_sha256": response_sha,
            "request_sha256": request_sha,
            "result_sha256": result_sha,
            "request_path": f"requests/{index}.json",
            "response_path": f"responses/{index}.json",
            "result_path": f"results/{index}.json",
            "capture_receipt_path": f"receipts/{index}.json",
            "source_event_time_ns": event,
            "received_ns": received,
            "available_ns": available,
        }
        captures.append(capture)
        tx_counts = {"success": 1, "failed": 0, "unknown": 0}
        tx_count = 1
        if index == 1:
            tx_counts = {"success": 0, "failed": 1, "unknown": 0}
        blocks.append(
            {
                **capture,
                "transaction_count": tx_count,
                "transaction_status_counts": tx_counts,
                "unknown_structural_scan_transactions": 0,
            }
        )
        path = {"outer_index": index, "inner_index": None}
        reference_id = source_schema.digest(
            source_schema.canonical(
                {"capture_id": capture_id, "signature_sha256": signature, "instruction_path": path}
            )
        )
        refs.append(
            {
                **capture,
                "reference_id": reference_id,
                "signature_sha256": signature,
                "transaction_status": status,
                "instruction_path": path,
                "program_id": projection.PUMP_AMM,
                "instruction_data_sha256": data_sha,
                "account_count": index + 2,
                "class_fingerprint_schema": "pump_program_structural_fingerprint_v1",
                "class_fingerprint_sha256": class_sha,
                "decision_ns": None,
            }
        )
        class_rows[class_sha] = {
            "program_id": projection.PUMP_AMM,
            "instruction_data_sha256": data_sha,
            "account_count": index + 2,
            "class_fingerprint_sha256": class_sha,
            "classification": projection.CLASSIFICATION,
            "reference_occurrences": 1,
            "transaction_status_counts": {
                "success": int(status == "success"),
                "failed": int(status == "failed"),
                "unknown": 0,
            },
        }

    sealed_buckets = ("20261012T060500Z", "20261012T061000Z", "20261012T061500Z")
    schedule: list[dict[str, object]] = []
    observed_frames: list[dict[str, object]] = []
    for frame_index, bucket in enumerate(sealed_buckets):
        local_blocks = blocks if frame_index == 0 else []
        txs = (
            {"success": 1, "failed": 1, "unknown": 0}
            if frame_index == 0
            else {"success": 0, "failed": 0, "unknown": 0}
        )
        counts = {
            "produced_slots": 1 if frame_index == 0 else 0,
            "selected_blocks": len(local_blocks),
            "scanned_transactions": sum(txs.values()),
            "success_transactions": txs["success"],
            "failed_transactions": txs["failed"],
            "unknown_transactions": txs["unknown"],
            "reference_occurrences": len(refs) if frame_index == 0 else 0,
            "unknown_scan_transactions": 0,
        }
        schedule.append(
            {
                "bucket": bucket,
                "state": "sealed",
                "start_ns": event,
                "end_ns": available,
                "counts": counts,
            }
        )
        observed_frames.append({"bucket": bucket, "counts": counts, "blocks": local_blocks})
    for index in range(9):
        schedule.append(
            {
                "bucket": f"unobserved-{index}",
                "state": "callback_failed_unobserved" if index == 0 else "expired_unobserved",
                "start_ns": event + index,
                "end_ns": available + index,
                **{
                    metric: None
                    for metric in (
                        "produced_slots",
                        "selected_blocks",
                        "scanned_transactions",
                        "success_transactions",
                        "failed_transactions",
                        "unknown_transactions",
                        "reference_occurrences",
                        "unknown_scan_transactions",
                    )
                },
            }
        )
    denominators = {
        "scheduled_frames": 12,
        "sealed_frames": 3,
        "unobserved_frames": 9,
        "produced_slots_in_sealed_frames": 1,
        "selected_blocks_in_sealed_frames": 2,
        "scanned_transactions": 2,
        "success_transactions": 1,
        "failed_transactions": 1,
        "unknown_transactions": 0,
        "reference_occurrences": 2,
        "class_count": 2,
        "unknown_scan_transactions": 0,
    }
    source = {
        "schema": "brain-senses-external-partial-structural-queue-v2",
        "schema_version": 1,
        "status": "partial_observed_prefix",
        "scope": {
            "service_id": "external-v2-20261012T0600Z",
            "plan_sha256": "a" * 64,
        },
        "denominators": denominators,
        "maximum_source_availability_ns": available,
        "nonclaims": {
            key: False
            for key in (
                "full_schedule_complete",
                "no_new_class_claim",
                "market_completeness",
                "point_in_time_eligible",
                "economic_admission",
                "successor_eligible",
            )
        },
        "schedule": {"frames": schedule},
        "observed": {
            "frames": observed_frames,
            "captures": captures,
            "references": refs,
            "classes": list(class_rows.values()),
        },
    }
    source_bytes = canonical(source)
    receipt = {
        "schema": "brain-senses-external-partial-structural-queue-publication-v2",
        "schema_version": 1,
        "status": "created_or_verified",
        "artifact_path": projection.INPUT_PATH_RELATIVE,
        "artifact_sha256": sha(source_bytes),
        "availability_ns": projection.INPUT_RECEIPT_NS,
        "provider_io": False,
        "point_in_time_eligible": False,
        "source_root_id": projection.STORAGE_ROOT_ID,
        "volume_uuid": projection.STORAGE_VOLUME_UUID,
    }
    receipt_bytes = canonical(receipt)
    expected = dict(denominators)
    expected_status = {"success": 1, "failed": 1, "unknown": 0}
    return source_bytes, receipt_bytes, expected, expected_status


def project(
    source: bytes, receipt: bytes, expected: dict[str, int], status: dict[str, int]
) -> dict[str, object]:
    receipt_row = json.loads(receipt)
    receipt_row["artifact_sha256"] = sha(source)
    rebound_receipt = canonical(receipt_row)
    return projection.validate_and_project(
        source,
        rebound_receipt,
        projection_known_ns=projection.INPUT_RECEIPT_NS + 10,
        expected_denominators=expected,
        expected_reference_status_counts=status,
        enforce_frozen_input=False,
    )


def mutate(source_bytes: bytes, fn: object) -> bytes:
    source = json.loads(source_bytes)
    fn(source)  # type: ignore[operator]
    return canonical(source)


def test_projects_structural_presence_and_failed_reference_without_effect_claims() -> None:
    source, receipt, expected, status = fixture()
    output = project(source, receipt, expected, status)
    assert len(output["references"]) == 2  # type: ignore[arg-type]
    assert sum(row["reference_occurrences"] for row in output["classes"]) == len(
        output["references"]
    )  # type: ignore[index]
    failed = next(row for row in output["references"] if row["transaction_status"] == "failed")  # type: ignore[index]
    assert failed["executed_instruction"] is None
    assert failed["instruction_effect"] is None
    assert failed["settlement"] is None
    assert failed["decision_ns"] is None
    assert output["nonclaims"]["economic_or_wallet_claim"] is False  # type: ignore[index]
    assert output["scope"]["sample_only"] is True  # type: ignore[index]


def test_row_and_class_order_is_canonical_and_replay_is_idempotent() -> None:
    source, receipt, expected, status = fixture()
    original = project(source, receipt, expected, status)
    changed = json.loads(source)
    changed["observed"]["references"].reverse()
    changed["observed"]["classes"].reverse()
    reordered = project(canonical(changed), receipt, expected, status)
    assert canonical(original["references"]) == canonical(reordered["references"])
    assert canonical(original["classes"]) == canonical(reordered["classes"])
    assert (
        original["source_binding"]["source_artifact_sha256"]
        != reordered["source_binding"]["source_artifact_sha256"]
    )
    assert canonical(original) == canonical(project(source, receipt, expected, status))


@pytest.mark.parametrize(
    ("name", "edit"),
    [
        (
            "duplicate",
            lambda s: s["observed"]["references"].__setitem__(
                1, copy.deepcopy(s["observed"]["references"][0])
            ),
        ),
        ("drop", lambda s: s["observed"]["references"].pop()),
        ("splice", lambda s: s["observed"]["references"][0].__setitem__("capture_id", "missing")),
        (
            "status",
            lambda s: s["observed"]["references"][0].__setitem__("transaction_status", "failed"),
        ),
        (
            "path",
            lambda s: s["observed"]["references"][0].__setitem__(
                "instruction_path", {"outer_index": -1, "inner_index": None}
            ),
        ),
        ("class", lambda s: s["observed"]["classes"][0].__setitem__("classification", "decoded")),
        (
            "capture_hash",
            lambda s: s["observed"]["references"][0].__setitem__(
                "capture_receipt_sha256", "0" * 64
            ),
        ),
        (
            "response_hash",
            lambda s: s["observed"]["references"][0].__setitem__("response_sha256", "0" * 64),
        ),
        ("unobserved_zero", lambda s: s["schedule"]["frames"][3].__setitem__("selected_blocks", 0)),
        ("claim", lambda s: s["nonclaims"].__setitem__("economic_admission", True)),
    ],
)
def test_corrupt_rows_classes_paths_and_claims_fail_closed(name: str, edit: object) -> None:
    source, receipt, expected, status = fixture()
    corrupted = mutate(source, edit)
    with pytest.raises(projection.ProjectionError):
        project(corrupted, receipt, expected, status)


def test_class_count_reconciliation_rejects_mismatched_class_summary() -> None:
    source, receipt, expected, status = fixture()
    corrupted = mutate(
        source,
        lambda s: s["observed"]["classes"][0].__setitem__("reference_occurrences", 7),
    )
    with pytest.raises(projection.ProjectionError, match="class_occurrence_count"):
        project(corrupted, receipt, expected, status)


def test_time_order_and_queue_receipt_hash_are_enforced() -> None:
    source, receipt, expected, status = fixture()
    corrupted = mutate(
        source,
        lambda s: s["observed"]["references"][0].__setitem__(
            "received_ns", s["observed"]["references"][0]["source_event_time_ns"] - 1
        ),
    )
    with pytest.raises(projection.ProjectionError):
        project(corrupted, receipt, expected, status)
    bad_receipt = json.loads(receipt)
    bad_receipt["artifact_sha256"] = "0" * 64
    with pytest.raises(projection.ProjectionError, match="receipt_artifact_hash"):
        projection.validate_and_project(
            source,
            canonical(bad_receipt),
            projection_known_ns=projection.INPUT_RECEIPT_NS + 10,
            expected_denominators=expected,
            expected_reference_status_counts=status,
            enforce_frozen_input=False,
        )


def test_projection_known_time_must_follow_receipt() -> None:
    source, receipt, expected, status = fixture()
    with pytest.raises(
        projection.ProjectionError, match="projection_time_not_after_source_availability"
    ):
        projection.validate_and_project(
            source,
            receipt,
            projection_known_ns=projection.INPUT_RECEIPT_NS,
            expected_denominators=expected,
            expected_reference_status_counts=status,
            enforce_frozen_input=False,
        )


def test_existing_publication_pair_is_verified_and_reused_unchanged() -> None:
    source, source_receipt, expected, status = fixture()
    artifact = project(source, source_receipt, expected, status)
    artifact_bytes = projection._canonical(artifact)
    input_sha = sha(source)
    input_receipt_sha = sha(source_receipt)
    publication_receipt = projection._build_publication_receipt(
        artifact_bytes,
        projection_known_ns=projection.INPUT_RECEIPT_NS + 10,
        input_sha256=input_sha,
        input_receipt_sha256=input_receipt_sha,
    )
    receipt_bytes = canonical(publication_receipt)

    prior = projection._verify_existing_publication(
        artifact_bytes,
        receipt_bytes,
        input_sha256=input_sha,
        input_receipt_sha256=input_receipt_sha,
    )
    assert prior["artifact_sha256"] == sha(artifact_bytes)
    assert prior["projection_known_ns"] == projection.INPUT_RECEIPT_NS + 10
    assert prior["publication_receipt_sha256"] == sha(receipt_bytes)
    assert projection._publication_action(True, True) == "verify_existing"
    assert projection._publication_action(False, False) == "create"


@pytest.mark.parametrize("artifact_exists,receipt_exists", [(True, False), (False, True)])
def test_partial_existing_publication_fails_closed(
    artifact_exists: bool, receipt_exists: bool
) -> None:
    with pytest.raises(projection.ProjectionError, match="incomplete_existing_publication"):
        projection._publication_action(artifact_exists, receipt_exists)


def test_existing_publication_binding_or_hash_corruption_fails_closed() -> None:
    source, source_receipt, expected, status = fixture()
    artifact = project(source, source_receipt, expected, status)
    artifact_bytes = projection._canonical(artifact)
    input_sha = sha(source)
    input_receipt_sha = sha(source_receipt)
    receipt = projection._build_publication_receipt(
        artifact_bytes,
        projection_known_ns=projection.INPUT_RECEIPT_NS + 10,
        input_sha256=input_sha,
        input_receipt_sha256=input_receipt_sha,
    )
    receipt_bytes = canonical(receipt)

    changed_receipt = dict(receipt)
    changed_receipt["source_artifact_sha256"] = "0" * 64
    changed_receipt.pop("content_sha256")
    changed_receipt["content_sha256"] = projection._canonical_sha256(changed_receipt)
    with pytest.raises(projection.ProjectionError, match="existing_source_hash"):
        projection._verify_existing_publication(
            artifact_bytes,
            canonical(changed_receipt),
            input_sha256=input_sha,
            input_receipt_sha256=input_receipt_sha,
        )

    altered_artifact = dict(artifact)
    altered_artifact["status"] = "corrupt"
    with pytest.raises(projection.ProjectionError, match="existing_artifact_hash"):
        projection._verify_existing_publication(
            projection._canonical(altered_artifact),
            receipt_bytes,
            input_sha256=input_sha,
            input_receipt_sha256=input_receipt_sha,
        )
