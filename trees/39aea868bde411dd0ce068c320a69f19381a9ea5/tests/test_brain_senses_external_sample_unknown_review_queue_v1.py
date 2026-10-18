from __future__ import annotations

import pytest

from tools import brain_senses_external_sample_unknown_review_queue_v1 as review
from tools import brain_senses_external_structural_family_triage_v1 as family_triage
from tools import brain_senses_external_structural_input_schema_v1 as schema


def _fixture() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    path = {"outer_index": 3, "inner_index": None}
    capture_id = "capture-a"
    signature = schema.digest(b"signature-a")
    data = bytes.fromhex("010203040506070809")
    reference_id = schema.digest(
        schema.canonical(
            {
                "capture_id": capture_id,
                "signature_sha256": signature,
                "instruction_path": path,
            }
        )
    )
    identity_sha = schema.digest(
        schema.canonical(
            {
                "capture_id": capture_id,
                "signature_sha256": signature,
                "instruction_path": path,
            }
        )
    )
    family_key = {
        "schema": family_triage.FAMILY_SCHEMA,
        "raw_prefix_hex": data[:8].hex(),
        "raw_prefix_width": 8,
        "instruction_data_length_bytes": len(data),
        "account_count": 2,
        "context": "outer",
        "transaction_status": "success",
    }
    family_id = schema.digest(schema.canonical(family_key))
    source = {
        "reference_id": reference_id,
        "capture_id": capture_id,
        "bucket": "20261012T060500Z",
        "slot": 10,
        "blockhash": "blockhash-a",
        "signature_sha256": signature,
        "instruction_path": path,
        "program_id": family_triage.PROGRAM_ID,
        "transaction_status": "success",
        "instruction_data_sha256": schema.digest(data),
        "account_count": 2,
        "class_fingerprint_schema": "pump_program_structural_fingerprint_v1",
        "class_fingerprint_sha256": schema.digest(b"class-a"),
        "capture_receipt_sha256": schema.digest(b"receipt-a"),
        "response_sha256": schema.digest(b"response-a"),
        "request_sha256": schema.digest(b"request-a"),
        "result_sha256": schema.digest(b"result-a"),
        "source_event_time_ns": 100,
        "received_ns": 110,
        "available_ns": 120,
        "decision_ns": None,
    }
    archive_ref = {
        **source,
        "capture_available_ns": source["available_ns"],
    }
    archive_ref.pop("available_ns")
    family_ref = {
        **source,
        "capture_available_ns": source["available_ns"],
        "reference_identity_sha256": identity_sha,
        "family_id": family_id,
        "raw_prefix_hex": family_key["raw_prefix_hex"],
        "raw_prefix_width": family_key["raw_prefix_width"],
        "instruction_data_length_bytes": family_key["instruction_data_length_bytes"],
        "source_reconciliation": "exact_capture_signature_typed_path_match",
        "idl_match_classification": "unresolved_no_pinned_idl_discriminator_match",
        "documented_idl_name": None,
    }
    family_ref.pop("available_ns")
    family_row = {
        "family_id": family_id,
        "family_key": family_key,
        "reference_occurrences": 1,
        "transaction_status_counts": {"success": 1, "failed": 0, "unknown": 0},
        "idl_match_classification": "unresolved_no_pinned_idl_discriminator_match",
        "documented_idl_name": None,
    }
    schedule = []
    metric_names = (
        "produced_slots",
        "selected_blocks",
        "scanned_transactions",
        "success_transactions",
        "failed_transactions",
        "unknown_transactions",
        "reference_occurrences",
        "unknown_scan_transactions",
    )
    for index in range(12):
        if index < 3:
            schedule.append({"state": "sealed", "counts": {name: 1 for name in metric_names}})
        else:
            state = "callback_failed_unobserved" if index == 3 else "expired_unobserved"
            schedule.append({"state": state, "counts": {name: None for name in metric_names}})
    queue = {
        "schema": "brain-senses-external-partial-structural-queue-v2",
        "scope": {"plan_sha256": family_triage.EXPECTED_PLAN_SHA256},
        "denominators": {
            "scheduled_frames": 12,
            "sealed_frames": 3,
            "unobserved_frames": 9,
            "produced_slots_in_sealed_frames": 4_164,
            "selected_blocks_in_sealed_frames": 48,
            "scanned_transactions": 46_222,
            "success_transactions": 43_636,
            "failed_transactions": 2_586,
            "unknown_transactions": 0,
            "reference_occurrences": 1,
            "class_count": 1,
            "unknown_scan_transactions": 0,
        },
        "observed": {"references": [source]},
    }
    archive = {"schedule": schedule, "references": [archive_ref]}
    artifact = {
        "schedule": schedule,
        "scope": {"projection_known_ns": 130},
        "references": [family_ref],
        "families": [family_row],
    }
    return queue, archive, artifact


def _build(
    queue: dict[str, object], archive: dict[str, object], artifact: dict[str, object]
) -> dict[str, object]:
    return review.validate_and_build(
        queue=queue,
        archive=archive,
        family_artifact=artifact,
        projection_known_ns=140,
        expected_reference_counts={"success": 1, "failed": 0, "unknown": 0},
        expected_queue_denominators=queue["denominators"],
        expected_reference_count=1,
        expected_family_count=1,
    )


def test_positive_reference_and_unmatched_shape_are_sample_local_only() -> None:
    queue, archive, artifact = _fixture()
    output = _build(queue, archive, artifact)
    row = output["entries"][0]
    assert row["diagnostic_classes"] == [
        "observed_positive_reference",
        "unmatched_or_ambiguous_shape",
    ]
    assert row["first_observed_in_this_sample"] is True
    assert row["decision_ns"] is None
    assert output["readiness"]["sample_local_diagnostic_queue_ready"] is True
    assert output["readiness"]["anomaly_source_qualified"] is False


def test_documented_idl_match_is_explicitly_unverified() -> None:
    queue, archive, artifact = _fixture()
    family = artifact["families"][0]
    family["idl_match_classification"] = "documented_idl_name_match_sample_only"
    family["documented_idl_name"] = "buy"
    artifact["references"][0]["idl_match_classification"] = family["idl_match_classification"]
    artifact["references"][0]["documented_idl_name"] = "buy"
    row = _build(queue, archive, artifact)["entries"][0]
    assert row["diagnostic_classes"] == [
        "observed_positive_reference",
        "documented_IDL_shape_semantics_unverified",
    ]


@pytest.mark.parametrize(
    "mutation,expected_code",
    [
        ("hash", "family_instruction_data_sha256_mismatch"),
        ("status", "archive_transaction_status_mismatch"),
        ("path", "reference_identity_set"),
        ("family", "family_id_set"),
        ("time", "projection_time_order"),
        ("idl", "family_idl_assignment"),
    ],
)
def test_changed_source_join_fields_fail_closed(mutation: str, expected_code: str) -> None:
    queue, archive, artifact = _fixture()
    if mutation == "hash":
        artifact["references"][0]["instruction_data_sha256"] = schema.digest(b"changed")
    elif mutation == "status":
        archive["references"][0]["transaction_status"] = "failed"
    elif mutation == "path":
        artifact["references"][0]["instruction_path"] = {
            "outer_index": 3,
            "inner_index": 1,
        }
    elif mutation == "family":
        artifact["references"][0]["family_id"] = schema.digest(b"wrong family")
    elif mutation == "time":
        artifact["scope"]["projection_known_ns"] = 145
    else:
        artifact["references"][0]["documented_idl_name"] = "different"
    with pytest.raises(review.ReviewQueueError, match=expected_code):
        _build(queue, archive, artifact)


def test_projection_time_cannot_precede_capture_availability() -> None:
    queue, archive, artifact = _fixture()
    with pytest.raises(review.ReviewQueueError, match="projection_time_order"):
        review.validate_and_build(
            queue=queue,
            archive=archive,
            family_artifact=artifact,
            projection_known_ns=120,
            expected_reference_counts={"success": 1, "failed": 0, "unknown": 0},
            expected_queue_denominators=queue["denominators"],
            expected_reference_count=1,
            expected_family_count=1,
        )


def test_unobserved_frame_zero_cannot_replace_null() -> None:
    queue, archive, artifact = _fixture()
    archive["schedule"][3]["counts"]["reference_occurrences"] = 0
    artifact["schedule"] = archive["schedule"]
    with pytest.raises(review.ReviewQueueError, match="unobserved_metrics_not_null"):
        _build(queue, archive, artifact)


def test_publication_receipt_replay_binds_content_and_keeps_time() -> None:
    queue, archive, artifact = _fixture()
    output = _build(queue, archive, artifact)
    output["source_binding"] = {
        "family_triage_sha256": schema.digest(b"family artifact"),
        "queue_artifact_sha256": schema.digest(b"queue"),
    }
    output["denominators"]["reference_occurrences"] = 1
    artifact_bytes = review._canonical(output)
    receipt_bytes = review._canonical(review._build_publication_receipt(artifact_bytes))
    prior = review._verify_existing_publication(
        artifact_bytes, receipt_bytes, expected_entry_count=1
    )
    assert prior["projection_known_ns"] == output["scope"]["projection_known_ns"]
    assert review._publication_action(True, True) == "verify_existing"
    assert artifact_bytes == review._canonical(output)
    with pytest.raises(review.ReviewQueueError, match="incomplete_publication_pair"):
        review._publication_action(True, False)

    duplicate = dict(output)
    duplicate["entries"] = [*output["entries"], output["entries"][0]]
    duplicate["denominators"] = {**output["denominators"], "reference_occurrences": 2}
    duplicate_bytes = review._canonical(duplicate)
    duplicate_receipt = review._canonical(review._build_publication_receipt(duplicate_bytes))
    with pytest.raises(review.ReviewQueueError, match="existing_duplicate_entry"):
        review._verify_existing_publication(
            duplicate_bytes, duplicate_receipt, expected_entry_count=2
        )


def test_run_publish_reuses_stored_time_and_returns_publisher_result(monkeypatch) -> None:
    existing = {"scope": {"projection_known_ns": 1234}}
    candidate = {"scope": {"projection_known_ns": 1234}, "entries": []}
    published = {"status": "created_or_verified", "artifact_sha256": "a" * 64}
    monkeypatch.setattr(
        review, "_read_existing_publication_pair", lambda *, config_path: (b"artifact", b"receipt")
    )
    monkeypatch.setattr(review, "_load_json", lambda data, label: existing)
    monkeypatch.setattr(
        review, "_build_candidate", lambda *, config_path, projection_known_ns: candidate
    )
    monkeypatch.setattr(review, "_canonical", lambda value: b"artifact")
    monkeypatch.setattr(review, "publish_create_only", lambda artifact, *, config_path: published)

    assert review.run_publish() == published


def test_cli_publish_and_verify_paths_are_exclusive_and_dispatch(monkeypatch, capsys) -> None:
    monkeypatch.setattr(review.sys, "argv", ["review-queue", "--publish"])
    monkeypatch.setattr(review, "run_publish", lambda: {"status": "published"})
    monkeypatch.setattr(review, "run_verify_only", lambda: pytest.fail("verify path selected"))
    assert review.main() == 0
    assert '"status": "published"' in capsys.readouterr().out

    monkeypatch.setattr(review.sys, "argv", ["review-queue", "--verify-only"])
    monkeypatch.setattr(review, "run_publish", lambda: pytest.fail("publish path selected"))
    monkeypatch.setattr(review, "run_verify_only", lambda: {"status": "verified_not_published"})
    assert review.main() == 0
    assert '"status": "verified_not_published"' in capsys.readouterr().out

    monkeypatch.setattr(review.sys, "argv", ["review-queue", "--publish", "--verify-only"])
    with pytest.raises(SystemExit):
        review.main()
