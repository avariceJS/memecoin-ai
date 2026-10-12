from __future__ import annotations

import copy
import hashlib
import os

import pytest

from tools.brain_senses_external_writer_v1 import DescriptorWriter
from tools.brain_senses_structural_unknown_queue_schema_v1 import (
    INPUT_SCHEMA,
    QueueInputError,
    canonical,
    digest,
)
from tools.brain_senses_structural_unknown_queue_v1 import (
    QueueFailure,
    _verify_input_source_content,
    compile_queue,
    publish_create_only,
    publish_queue_with_receipt,
    verify_queue,
)


def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _block(
    bucket: str,
    slot: int,
    capture_id: str,
    *,
    success: list[str],
    failed: list[str],
    unknown: list[str] | None = None,
) -> dict[str, object]:
    return {
        "bucket": bucket,
        "slot": slot,
        "capture_id": capture_id,
        "status": "verified",
        "produced": True,
        "blockhash": f"block-{slot}",
        "event_time_ns": 100 + slot,
        "receipt_ns": 200 + slot,
        "availability_ns": 300 + slot,
        "partition_status": "verified",
        "transactions": {"success": success, "failed": failed, "unknown": unknown or []},
        "failure_code": None,
    }


def _fingerprint(kind: str, tx_status: str, discriminator: str) -> dict[str, object]:
    return {
        "instruction_kind": kind,
        "transaction_status": tx_status,
        "discriminator_hex": discriminator,
        "wire_data_length": 8,
    }


def _manifest(*, include_missed: bool = True) -> dict[str, object]:
    b1, b2, b3 = "20261010T130000Z", "20261010T140000Z", "20261010T150000Z"
    fingerprints = [
        _fingerprint("unknown_discriminator", "success", "aabbccdd"),
        _fingerprint("sell", "failed", "11223344"),
        _fingerprint("buy", "unknown", "55667788"),
        _fingerprint("future_unparsed_kind", "unknown", "99aabbcc"),
    ]
    class_rows = [
        {"fingerprint": fp, "class_sha256": digest(canonical(fp)), "count": count}
        for fp, count in zip(fingerprints, (2, 2, 1, 1), strict=True)
    ]
    ids = [_hash(f"tx-{i}") for i in range(6)]
    capture_rows = [
        {
            "capture_id": f"capture-{slot}",
            "bucket": bucket,
            "slot": slot,
            "blockhash": f"block-{slot}",
            "full_block_path": f"work/brain-senses-synthetic/{bucket}/{slot}/full.json",
            "full_block_sha256": _hash(f"full-{slot}"),
            "capture_receipt_path": f"work/brain-senses-synthetic/{bucket}/{slot}/receipt.json",
            "capture_receipt_sha256": _hash(f"receipt-{slot}"),
        }
        for bucket, slot in ((b1, 11), (b2, 12))
    ]
    refs = []
    assignments = [
        (b1, 11, "capture-11", ids[0], class_rows[0]),
        (b1, 11, "capture-11", ids[1], class_rows[1]),
        (b1, 11, "capture-11", ids[2], class_rows[2]),
        (b2, 12, "capture-12", ids[3], class_rows[0]),
        (b2, 12, "capture-12", ids[4], class_rows[1]),
        (b2, 12, "capture-12", ids[5], class_rows[3]),
    ]
    for index, (bucket, slot, capture_id, signature, klass) in enumerate(assignments):
        instruction_path = {"outer_index": index, "inner_index": None}
        refs.append(
            {
                "reference_id": digest(
                    canonical(
                        {
                            "capture_id": capture_id,
                            "signature_sha256": signature,
                            "instruction_path": instruction_path,
                        }
                    )
                ),
                "capture_id": capture_id,
                "bucket": bucket,
                "slot": slot,
                "signature_sha256": signature,
                "instruction_path": instruction_path,
                "class_sha256": klass["class_sha256"],
            }
        )
    schedule = []
    for index, (bucket, slots) in enumerate(((b1, [11]), (b2, [12]))):
        schedule.append(
            {
                "bucket": bucket,
                "state": "sealed",
                "start_ns": 1_000 + index * 1_000,
                "end_ns": 2_000 + index * 1_000,
                "selected_slots": slots,
                "selection_sha256": digest(canonical({"bucket": bucket, "selected_slots": slots})),
            }
        )
    if include_missed:
        schedule.append(
            {
                "bucket": b3,
                "state": "missed",
                "start_ns": 3_000,
                "end_ns": 4_000,
                "selected_slots": None,
                "selection_sha256": None,
            }
        )
    blocks = [
        _block(b1, 11, "capture-11", success=[ids[0]], failed=[ids[1]], unknown=[ids[2]]),
        _block(b2, 12, "capture-12", success=[ids[3]], failed=[ids[4]], unknown=[ids[5]]),
    ]
    failure = {
        "failure_id": "candidate-parse-row-1",
        "bucket": b2,
        "failure_code": "candidate_parse_failed",
        "count": 1,
        "source_row_sha256": _hash("malformed candidate row"),
    }
    payload = {
        "schedule": schedule,
        "captures": capture_rows,
        "blocks": blocks,
        "classes": class_rows,
        "references": refs,
        "candidate_failures": [failure],
        "exclusions": [
            {
                "kind": "bucket",
                "identity": "20261010T230000Z",
                "reason": "tail2_explicitly_excluded",
            }
        ],
    }
    return {
        "schema": INPUT_SCHEMA,
        "schema_version": 1,
        "scope_id": "synthetic-selected-block-scope",
        "process": {
            "source_commit": "deadbeef",
            "source_tool_sha256": _hash("tool"),
            "source_schema_sha256": _hash("schema"),
        },
        "source": {
            "receipt_path": "docs/evidence/synthetic-structural-receipt.json",
            "receipt_sha256": _hash("receipt"),
            "detail_path": "work/brain-senses-structural-synthetic/detail.json.zst",
            "detail_sha256": _hash("detail-compressed"),
            "detail_uncompressed_sha256": _hash("detail-uncompressed"),
            "detail_uncompressed_bytes": 1_000,
            "structural_payload_sha256": digest(canonical(payload)),
        },
        **payload,
    }


def _rebind_payload(manifest: dict[str, object]) -> None:
    payload = {
        key: manifest[key]
        for key in (
            "schedule",
            "captures",
            "blocks",
            "classes",
            "references",
            "candidate_failures",
            "exclusions",
        )
    }
    source = manifest["source"]
    assert isinstance(source, dict)
    source["structural_payload_sha256"] = digest(canonical(payload))


def _mutate_reference_to_unpartitioned(manifest: dict[str, object]) -> None:
    row = manifest["references"][0]
    assert isinstance(row, dict)
    signature = _hash("not partitioned")
    row["signature_sha256"] = signature
    row["reference_id"] = digest(
        canonical(
            {
                "capture_id": row["capture_id"],
                "signature_sha256": signature,
                "instruction_path": row["instruction_path"],
            }
        )
    )


def test_compile_is_deterministic_and_preserves_reference_denominators() -> None:
    manifest = _manifest()
    queue = compile_queue(manifest)
    assert queue == compile_queue(copy.deepcopy(manifest))
    assert queue["schema"] == "brain-senses-structural-unknown-queue-v1"
    assert queue["decision_ns"] is None
    assert queue["denominators"] == {
        "scheduled_buckets": 3,
        "sealed_buckets": 2,
        "missed_buckets": 1,
        "selected_blocks": 2,
        "produced_blocks": 2,
        "verified_blocks": 2,
        "scanned_transactions": 6,
        "transaction_references": 6,
        "class_count": 4,
        "instruction_references_by_status_and_transaction_status": {
            "unsupported_instruction": {"success": 2, "failed": 0, "unknown": 0},
            "recognized_but_semantics_unresolved": {"success": 0, "failed": 2, "unknown": 1},
            "candidate_parse_failure": {"success": 0, "failed": 0, "unknown": 1},
        },
        "instruction_references_by_transaction_status": {
            "success": 2,
            "failed": 2,
            "unknown": 2,
        },
        "unattributed_candidate_failures": 1,
        "rejected_candidates": 2,
        "unresolved_candidates": 6,
    }
    assert queue["scope_findings"]["complete"] is None
    assert queue["scope_findings"]["completeness_status"] == "unknown"
    assert queue["scope_findings"]["no_new_class_in_final_bucket"] is None
    assert queue["scope_findings"]["global_or_economic_zero_claim"] is False
    assert queue["scope_findings"]["new_classes_by_bucket"] == {
        "20261010T130000Z": 3,
        "20261010T140000Z": 1,
    }
    entries = queue["entries"]
    assert {entry["status"] for entry in entries} == {
        "unsupported_instruction",
        "recognized_but_semantics_unresolved",
        "candidate_parse_failure",
    }
    class_entry = next(e for e in entries if e["instruction_kind"] == "unknown_discriminator")
    assert class_entry["event_time_ns"] == 111
    assert class_entry["source_receipt_ns"] == 211
    assert class_entry["source_available_ns"] == 311
    assert class_entry["decision_ns"] is None
    assert class_entry["representative_references"]
    assert queue["max_source_available_ns"] == 312
    unattributed_failure = next(e for e in entries if "failure_code" in e)
    assert unattributed_failure["status"] == "candidate_parse_failure"
    assert unattributed_failure["transaction_status"] is None


def test_complete_named_scope_can_claim_no_new_class_only_in_last_bucket() -> None:
    manifest = _manifest(include_missed=False)
    # Every class already appeared in the first verified bucket.
    manifest["references"] = [
        row for row in manifest["references"] if row["bucket"] == "20261010T130000Z"
    ]
    manifest["blocks"][1]["transactions"] = {"success": [], "failed": [], "unknown": []}
    manifest["classes"] = manifest["classes"][:3]
    manifest["candidate_failures"] = []
    for row, count in zip(manifest["classes"], (1, 1, 1), strict=True):
        row["count"] = count
    _rebind_payload(manifest)
    queue = compile_queue(manifest)
    assert queue["scope_findings"]["complete"] is True
    assert queue["scope_findings"]["no_new_class_in_final_bucket"] is True


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda m: m["source"].__setitem__("receipt_sha256", "x"), "source_receipt_sha256"),
        (lambda m: m["schedule"][0].__setitem__("selected_slots", [99]), "selection_hash_mismatch"),
        (lambda m: m["blocks"][0].__setitem__("availability_ns", 199), "block_time_order"),
        (
            lambda m: m["blocks"][0]["transactions"]["failed"].append(
                m["blocks"][0]["transactions"]["success"][0]
            ),
            "transaction_partition_overlap_or_duplicate",
        ),
        (
            lambda m: (
                m["blocks"][0].__setitem__("bucket", "20261010T140000Z"),
                m["blocks"][0].__setitem__("slot", 12),
                m["blocks"][0].__setitem__("capture_id", "capture-12"),
                m["blocks"][0].__setitem__("blockhash", "block-12"),
            ),
            "duplicate_selected_block",
        ),
        (
            lambda m: m["blocks"][0].__setitem__("capture_id", "capture-other"),
            "block_capture_join_mismatch",
        ),
        (
            lambda m: m["captures"][0].__setitem__("full_block_sha256", "bad"),
            "capture_full_block_sha256",
        ),
        (
            lambda m: m["captures"][0].__setitem__(
                "full_block_path", "work/brain-senses-tail2/detail.json.zst"
            ),
            "protected_or_excluded_source_path",
        ),
        (lambda m: m["classes"][0].__setitem__("count", 99), "class_reference_count_mismatch"),
        (
            _mutate_reference_to_unpartitioned,
            "reference_transaction_not_partitioned",
        ),
    ],
)
def test_manifest_mutations_fail_closed(mutate, code: str) -> None:
    manifest = copy.deepcopy(_manifest())
    mutate(manifest)
    if code != "source_receipt_sha256":
        _rebind_payload(manifest)
    with pytest.raises(QueueInputError, match=code):
        compile_queue(manifest)


def test_fingerprint_hash_mutation_is_source_integrity_stop() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["classes"][0]["fingerprint"]["discriminator_hex"] = "ffffeeee"
    _rebind_payload(manifest)
    with pytest.raises(QueueInputError, match="class_fingerprint_hash_mismatch"):
        compile_queue(manifest)


def test_duplicate_references_and_class_rows_rejected() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["references"].append(copy.deepcopy(manifest["references"][0]))
    _rebind_payload(manifest)
    with pytest.raises(QueueInputError, match="reference_identity_mismatch_or_duplicate"):
        compile_queue(manifest)
    manifest = copy.deepcopy(_manifest())
    manifest["classes"].append(copy.deepcopy(manifest["classes"][0]))
    _rebind_payload(manifest)
    with pytest.raises(QueueInputError, match="duplicate_class_fingerprint"):
        compile_queue(manifest)


def test_duplicate_unattributed_candidate_failure_ids_rejected() -> None:
    manifest = copy.deepcopy(_manifest())
    duplicate = copy.deepcopy(manifest["candidate_failures"][0])
    duplicate["count"] = 2
    manifest["candidate_failures"].append(duplicate)
    _rebind_payload(manifest)
    with pytest.raises(QueueInputError, match="duplicate_candidate_failure_id"):
        compile_queue(manifest)


@pytest.mark.parametrize(
    "path",
    [
        "work/brain-senses-tail2/detail.json.zst",
        "docs/evidence/F01/receipt.json",
        "docs/evidence/D43/receipt.json",
        "docs/evidence/reserved-holdout/receipt.json",
    ],
)
def test_excluded_and_protected_source_paths_denied(path: str) -> None:
    manifest = _manifest()
    if path.startswith("work/"):
        manifest["source"]["detail_path"] = path
    else:
        manifest["source"]["receipt_path"] = path
    _rebind_payload(manifest)
    with pytest.raises(QueueInputError):
        compile_queue(manifest)


def test_tail2_bucket_cannot_be_added_to_schedule() -> None:
    manifest = copy.deepcopy(_manifest())
    row = copy.deepcopy(manifest["schedule"][-1])
    row["bucket"] = "20261010T230000Z"
    manifest["schedule"].append(row)
    _rebind_payload(manifest)
    with pytest.raises(QueueInputError, match="excluded_tail2_bucket"):
        compile_queue(manifest)


def test_source_payload_digest_mismatch_stops_before_queue() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["source"]["structural_payload_sha256"] = _hash("wrong lineage")
    with pytest.raises(QueueInputError, match="structural_payload_hash_mismatch"):
        compile_queue(manifest)


def test_input_receipt_and_detail_must_bind_exact_synthetic_payload() -> None:
    manifest = _manifest()
    payload = {
        key: manifest[key]
        for key in (
            "schedule",
            "captures",
            "blocks",
            "classes",
            "references",
            "candidate_failures",
            "exclusions",
        )
    }
    structural_sha = digest(canonical(payload))
    detail_bytes = canonical(
        {
            "schema": "brain-senses-structural-detail-v1",
            "schema_version": 1,
            "structural_payload_sha256": structural_sha,
            "payload": payload,
            "raw_response_bodies_included": False,
        }
    )
    source = manifest["source"]
    source["structural_payload_sha256"] = structural_sha
    source["detail_uncompressed_sha256"] = digest(detail_bytes)
    source["detail_uncompressed_bytes"] = len(detail_bytes)
    receipt_bytes = canonical(
        {
            "schema": "brain-senses-structural-source-receipt-v1",
            "schema_version": 1,
            "status": "sealed",
            "structural_payload_sha256": structural_sha,
            "detail_artifact": {
                "path": source["detail_path"],
                "compressed_sha256": source["detail_sha256"],
                "uncompressed_sha256": source["detail_uncompressed_sha256"],
                "uncompressed_bytes": len(detail_bytes),
            },
        }
    )
    source["receipt_sha256"] = digest(receipt_bytes)
    _verify_input_source_content(manifest, source, receipt_bytes, detail_bytes)

    tampered = copy.deepcopy(manifest)
    tampered["references"].pop()
    with pytest.raises(QueueFailure, match="source_detail_payload_mismatch"):
        _verify_input_source_content(tampered, source, receipt_bytes, detail_bytes)


def test_source_detail_rejects_raw_body_field() -> None:
    manifest = _manifest()
    payload = {
        key: manifest[key]
        for key in (
            "schedule",
            "captures",
            "blocks",
            "classes",
            "references",
            "candidate_failures",
            "exclusions",
        )
    }
    structural_sha = digest(canonical(payload))
    detail_bytes = canonical(
        {
            "schema": "brain-senses-structural-detail-v1",
            "schema_version": 1,
            "structural_payload_sha256": structural_sha,
            "payload": payload,
            "raw_response_bodies_included": True,
        }
    )
    source = manifest["source"]
    source.update(
        {
            "structural_payload_sha256": structural_sha,
            "detail_uncompressed_sha256": digest(detail_bytes),
            "detail_uncompressed_bytes": len(detail_bytes),
        }
    )
    receipt_bytes = canonical(
        {
            "schema": "brain-senses-structural-source-receipt-v1",
            "schema_version": 1,
            "status": "sealed",
            "structural_payload_sha256": structural_sha,
            "detail_artifact": {
                "path": source["detail_path"],
                "compressed_sha256": source["detail_sha256"],
                "uncompressed_sha256": source["detail_uncompressed_sha256"],
                "uncompressed_bytes": len(detail_bytes),
            },
        }
    )
    source["receipt_sha256"] = digest(receipt_bytes)
    with pytest.raises(QueueFailure, match="source_detail_payload_mismatch"):
        _verify_input_source_content(manifest, source, receipt_bytes, detail_bytes)


def test_queue_recomputation_rejects_tampered_entry() -> None:
    manifest = _manifest()
    queue = compile_queue(manifest)
    queue["entries"][0]["status"] = "admitted_fact"
    with pytest.raises(QueueInputError, match="queue_recomputation_mismatch"):
        verify_queue(
            manifest,
            canonical(queue),
            expected_manifest_sha256=digest(canonical(manifest)),
        )


def test_create_only_publication_is_idempotent_and_collision_safe(tmp_path) -> None:
    tmp_path.chmod(0o700)
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with DescriptorWriter(root_fd) as writer:
            relative = "ResearchData/brain/queue.json"
            payload = b'{"immutable":true}\n'
            first = publish_create_only(writer, relative, payload)
            second = publish_create_only(writer, relative, payload)
            assert first == second == digest(payload)
            with pytest.raises(QueueFailure, match="immutable_queue_collision"):
                publish_create_only(writer, relative, b'{"immutable":false}\n')
    finally:
        os.close(root_fd)


def test_failed_and_unresolved_block_rows_remain_explicit() -> None:
    manifest = copy.deepcopy(_manifest())
    bucket = "20261010T140000Z"
    failed = manifest["blocks"][1]
    failed.update(
        {
            "capture_id": None,
            "status": "failed",
            "blockhash": None,
            "event_time_ns": None,
            "receipt_ns": None,
            "availability_ns": None,
            "partition_status": "unobserved",
            "transactions": {"success": [], "failed": [], "unknown": []},
            "failure_code": "selected_block_fetch_failed",
        }
    )
    manifest["captures"] = [
        row for row in manifest["captures"] if row["capture_id"] != "capture-12"
    ]
    manifest["references"] = [r for r in manifest["references"] if r["bucket"] != bucket]
    manifest["classes"] = manifest["classes"][:3]
    for row, count in zip(manifest["classes"], (1, 1, 1), strict=True):
        row["count"] = count
    _rebind_payload(manifest)
    queue = compile_queue(manifest)
    assert queue["denominators"]["selected_blocks"] == 2
    assert queue["denominators"]["verified_blocks"] == 1
    assert queue["scope_findings"]["complete"] is None
    assert queue["scope_findings"]["completeness_status"] == "unknown"


def test_reference_identity_and_instruction_path_are_canonical() -> None:
    manifest = copy.deepcopy(_manifest())
    row = manifest["references"][0]
    row["instruction_path"] = {"outer_index": 0, "inner_index": 0}
    _rebind_payload(manifest)
    with pytest.raises(QueueInputError, match="reference_identity_mismatch_or_duplicate"):
        compile_queue(manifest)


def test_publication_receipt_is_idempotent_and_recovery_is_later(tmp_path) -> None:
    tmp_path.chmod(0o700)
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    manifest = _manifest()
    queue = compile_queue(manifest)
    manifest_sha = digest(canonical(manifest))
    try:
        with DescriptorWriter(root_fd) as writer:
            initial = publish_queue_with_receipt(
                writer,
                manifest,
                queue,
                manifest_sha256=manifest_sha,
                root_id="a" * 32,
                volume_uuid="00000000-0000-4000-8000-000000000001",
                prefix="ResearchData/brain/queue",
                now_ns=lambda: 1_000,
            )
            replay = publish_queue_with_receipt(
                writer,
                manifest,
                queue,
                manifest_sha256=manifest_sha,
                root_id="a" * 32,
                volume_uuid="00000000-0000-4000-8000-000000000001",
                prefix="ResearchData/brain/queue",
                now_ns=lambda: 2_000,
            )
            assert initial == replay
            assert initial["confirmation_kind"] == "initial_durable_publish"
            assert initial["publication_receipt_path"].endswith(".publication.json")
            stored_receipt = writer.read_bytes(initial["publication_receipt_path"])
            assert stored_receipt == canonical(initial)
    finally:
        os.close(root_fd)

    recovery_root = tmp_path / "recovery"
    recovery_root.mkdir(mode=0o700)
    recovery_root.chmod(0o700)
    recovery_fd = os.open(recovery_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        queue_bytes = canonical(queue)
        queue_path = f"ResearchData/brain/queue/queue-{digest(queue_bytes)}.json"
        with DescriptorWriter(recovery_fd) as writer:
            publish_create_only(writer, queue_path, queue_bytes)
            with pytest.raises(QueueFailure, match="publication_confirmation_precedes_source"):
                publish_queue_with_receipt(
                    writer,
                    manifest,
                    queue,
                    manifest_sha256=manifest_sha,
                    root_id="b" * 32,
                    volume_uuid="00000000-0000-4000-8000-000000000002",
                    prefix="ResearchData/brain/queue",
                    now_ns=lambda: 311,
                )
            assert not writer.exists(
                f"ResearchData/brain/queue/queue-{digest(queue_bytes)}.publication.json"
            )
            recovered = publish_queue_with_receipt(
                writer,
                manifest,
                queue,
                manifest_sha256=manifest_sha,
                root_id="b" * 32,
                volume_uuid="00000000-0000-4000-8000-000000000002",
                prefix="ResearchData/brain/queue",
                now_ns=lambda: 2_000,
            )
            assert recovered["confirmation_kind"] == "recovered_queue_without_receipt"
            assert recovered["confirmation_ns"] == 2_000
            assert recovered["max_source_available_ns"] == 312
            assert recovered["decision_ns"] is None
            replayed_recovery = publish_queue_with_receipt(
                writer,
                manifest,
                queue,
                manifest_sha256=manifest_sha,
                root_id="b" * 32,
                volume_uuid="00000000-0000-4000-8000-000000000002",
                prefix="ResearchData/brain/queue",
                now_ns=lambda: 3_000,
            )
            assert replayed_recovery == recovered
    finally:
        os.close(recovery_fd)
