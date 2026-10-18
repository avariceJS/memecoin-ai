from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools import brain_senses_external_structural_family_triage_v1 as triage
from tools import brain_senses_external_structural_input_schema_v1 as schema
from tools import brain_senses_external_structural_input_v1 as structural


@pytest.mark.parametrize("data", [b"", b"x", b"1234567", b"12345678", b"123456789"])
def test_family_key_prefix_width_boundaries(data: bytes) -> None:
    key = triage._family_key(data, account_count=2, context="outer", transaction_status="success")
    assert key["raw_prefix_width"] == min(8, len(data))
    assert key["raw_prefix_hex"] == data[:8].hex()
    assert key["instruction_data_length_bytes"] == len(data)


@settings(max_examples=250, deadline=None)
@given(data=st.binary(max_size=256))
def test_family_key_properties_for_arbitrary_instruction_bytes(data: bytes) -> None:
    first = triage._family_key(data, account_count=7, context="inner", transaction_status="failed")
    second = triage._family_key(
        bytes(data), account_count=7, context="inner", transaction_status="failed"
    )
    assert first == second
    assert first["raw_prefix_width"] == min(8, len(data))
    assert first["instruction_data_length_bytes"] == len(data)
    assert structural.digest(data) == structural.digest(bytes(data))


@settings(max_examples=100, deadline=None)
@given(
    prefix=st.binary(min_size=8, max_size=8),
    suffix_a=st.binary(max_size=24),
    suffix_b=st.binary(max_size=24),
)
def test_same_raw_prefix_keeps_full_data_hash_distinct(
    prefix: bytes, suffix_a: bytes, suffix_b: bytes
) -> None:
    data_a = prefix + suffix_a
    data_b = prefix + suffix_b
    if data_a == data_b:
        return
    family_a = triage._family_key(
        data_a, account_count=3, context="outer", transaction_status="success"
    )
    family_b = triage._family_key(
        data_b, account_count=3, context="outer", transaction_status="success"
    )
    if len(data_a) == len(data_b):
        assert family_a == family_b
    else:
        assert (
            family_a["instruction_data_length_bytes"] != family_b["instruction_data_length_bytes"]
        )
    assert structural.digest(data_a) != structural.digest(data_b)


def _fixture() -> tuple[dict[str, object], dict[str, object]]:
    data = bytes.fromhex("010203040506070809")
    capture_id = "capture-one"
    signature = schema.digest(b"signature")
    path = {"outer_index": 0, "inner_index": None}
    account_count = 2
    source = {
        "reference_id": schema.digest(
            schema.canonical(
                {
                    "capture_id": capture_id,
                    "signature_sha256": signature,
                    "instruction_path": path,
                }
            )
        ),
        "capture_id": capture_id,
        "bucket": "bucket-a",
        "slot": 42,
        "blockhash": "blockhash-a",
        "signature_sha256": signature,
        "instruction_path": path,
        "program_id": triage.PROGRAM_ID,
        "transaction_status": "success",
        "instruction_data_sha256": schema.digest(data),
        "account_count": account_count,
        "class_fingerprint_schema": structural.FINGERPRINT_SCHEMA,
        "class_fingerprint_sha256": structural._fingerprint(
            triage.PROGRAM_ID, data.hex(), account_count
        ),
        "capture_receipt_sha256": schema.digest(b"receipt"),
        "response_sha256": schema.digest(b"response"),
        "request_sha256": schema.digest(b"request"),
        "result_sha256": schema.digest(b"result"),
        "source_event_time_ns": 1,
        "received_ns": 2,
        "available_ns": 3,
        "request_path": "request.json",
        "result_path": "result.json",
        "capture_receipt_path": "receipt.json",
        "response_path": "response.json",
    }
    raw = {
        "capture_id": capture_id,
        "signature_sha256": signature,
        "instruction_path": path,
        "program_id": triage.PROGRAM_ID,
        "transaction_status": "success",
        "instruction_data": data,
        "instruction_data_sha256": schema.digest(data),
        "instruction_data_length_bytes": len(data),
        "account_count": account_count,
        "context": "outer",
        "stack_height": None,
    }
    return source, raw


def _reconcile(source: dict[str, object], raw: dict[str, object]) -> dict[str, object]:
    return triage.reconcile_and_group(
        [source],
        [raw],
        idl_index={},
        expected_status_counts={"success": 1, "failed": 0, "unknown": 0},
        expected_reference_count=1,
    )


def test_reconciliation_preserves_typed_path_and_explicit_unknowns() -> None:
    source, raw = _fixture()
    output = _reconcile(source, raw)
    reference = output["references"][0]
    assert reference["source_reconciliation"] == "exact_capture_signature_typed_path_match"
    assert reference["executed_instruction"] is None
    assert reference["instruction_effect"] is None
    assert reference["settlement"] is None
    assert output["denominators"]["transaction_status_counts"] == {
        "success": 1,
        "failed": 0,
        "unknown": 0,
    }


def test_reconciliation_recomputes_source_reference_identity_hash() -> None:
    source, raw = _fixture()
    source["reference_id"] = schema.digest(b"spliced-reference-id")
    with pytest.raises(triage.TriageError, match="source_reference_id_binding"):
        _reconcile(source, raw)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("instruction_path", "raw_source_reference_set_mismatch"),
        ("transaction_status", "raw_source_transaction_status_mismatch"),
        ("instruction_data_sha256", "raw_source_instruction_data_sha256_mismatch"),
        ("account_count", "raw_source_account_count_mismatch"),
    ],
)
def test_reconciliation_rejects_raw_join_mutations(mutation: str, code: str) -> None:
    source, raw = _fixture()
    bad = deepcopy(raw)
    if mutation == "instruction_path":
        bad[mutation] = {"outer_index": 0, "inner_index": 1}
    elif mutation == "transaction_status":
        bad[mutation] = "failed"
    elif mutation == "instruction_data_sha256":
        bad[mutation] = schema.digest(b"different")
    else:
        bad[mutation] = 4
    with pytest.raises(triage.TriageError, match=code):
        _reconcile(source, bad)


def test_reconciliation_rejects_duplicate_and_missing_rows() -> None:
    source, raw = _fixture()
    with pytest.raises(triage.TriageError, match="duplicate_raw_reference_identity"):
        triage.reconcile_and_group(
            [source],
            [raw, raw],
            idl_index={},
            expected_status_counts={"success": 1, "failed": 0, "unknown": 0},
            expected_reference_count=1,
        )
    with pytest.raises(triage.TriageError, match="raw_source_reference_set_mismatch"):
        _reconcile(source, {**raw, "instruction_path": {"outer_index": 1, "inner_index": None}})


def test_same_signature_at_distinct_typed_paths_reconciles_as_two_rows() -> None:
    first_source, first_raw = _fixture()
    second_source = deepcopy(first_source)
    second_raw = deepcopy(first_raw)
    second_path = {"outer_index": 0, "inner_index": 2}
    second_source["instruction_path"] = second_path
    second_source["reference_id"] = schema.digest(
        schema.canonical(
            {
                "capture_id": second_source["capture_id"],
                "signature_sha256": second_source["signature_sha256"],
                "instruction_path": second_path,
            }
        )
    )
    second_raw["instruction_path"] = second_path
    second_raw["context"] = "inner"
    output = triage.reconcile_and_group(
        [first_source, second_source],
        [first_raw, second_raw],
        idl_index={},
        expected_status_counts={"success": 2, "failed": 0, "unknown": 0},
        expected_reference_count=2,
    )
    assert len(output["references"]) == 2
    assert len({row["signature_sha256"] for row in output["references"]}) == 1
    assert {tuple(sorted(row["instruction_path"].items())) for row in output["references"]} == {
        (("inner_index", None), ("outer_index", 0)),
        (("inner_index", 2), ("outer_index", 0)),
    }
    with pytest.raises(triage.TriageError, match="duplicate_raw_reference_identity"):
        triage.reconcile_and_group(
            [first_source, second_source],
            [first_raw, first_raw],
            idl_index={},
            expected_status_counts={"success": 2, "failed": 0, "unknown": 0},
            expected_reference_count=2,
        )


def test_idl_manifest_and_blob_mutations_fail_closed() -> None:
    files: dict[str, bytes] = {}
    rows: list[dict[str, object]] = []
    raw_blobs = [
        json.dumps(
            {"address": triage.PROGRAM_ID, "instructions": []}, separators=(",", ":")
        ).encode()
        for _ in range(18)
    ]
    padding = 1_974_484 - sum(map(len, raw_blobs))
    raw_blobs[0] += b" " * padding
    for index, raw in enumerate(raw_blobs):
        path = f"{triage.IDL_CAPTURE_DIR.as_posix()}/revision-{index}.json"
        files[path] = raw
        rows.append(
            {
                "path": path,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "commit": f"commit-{index:02d}",
                "commit_time_utc": "2026-10-11T00:00:00Z",
            }
        )
    manifest = {
        "history_entry_count": 18,
        "versions": rows,
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    files[triage.IDL_MANIFEST_RELATIVE] = manifest_raw

    def reader(path: str) -> bytes:
        return files[path]

    versions, _ = triage._load_idl_versions(
        read_bytes=reader,
        expected_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
    )
    assert len(versions) == 18
    changed_manifest = dict(manifest)
    changed_manifest["history_entry_count"] = 17
    changed_raw = json.dumps(changed_manifest, sort_keys=True, separators=(",", ":")).encode()
    changed_files = dict(files)
    changed_files[triage.IDL_MANIFEST_RELATIVE] = changed_raw
    with pytest.raises(triage.TriageError, match="idl_manifest_hash"):
        triage._load_idl_versions(
            read_bytes=lambda path: changed_files[path],
            expected_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        )
    changed_files = dict(files)
    blob_path = str(rows[0]["path"])
    changed_files[blob_path] = files[blob_path] + b" "
    with pytest.raises(triage.TriageError, match="idl_blob_binding"):
        triage._load_idl_versions(
            read_bytes=lambda path: changed_files[path],
            expected_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        )


def test_publication_pair_decision_fails_closed_on_partial_pair() -> None:
    assert triage._publication_action(True, True) == "verify_existing"
    assert triage._publication_action(False, False) == "create"
    with pytest.raises(triage.TriageError, match="incomplete_existing_publication"):
        triage._publication_action(True, False)
    with pytest.raises(triage.TriageError, match="incomplete_existing_publication"):
        triage._publication_action(False, True)


def test_archive_unobserved_window_metrics_remain_null() -> None:
    metrics = (
        "produced_slots",
        "selected_blocks",
        "scanned_transactions",
        "success_transactions",
        "failed_transactions",
        "unknown_transactions",
        "reference_occurrences",
        "unknown_scan_transactions",
    )
    schedule = []
    for index in range(12):
        if index < 3:
            schedule.append({"state": "sealed", "counts": {metric: 1 for metric in metrics}})
        else:
            state = "callback_failed_unobserved" if index == 3 else "expired_unobserved"
            schedule.append({"state": state, "counts": {metric: None for metric in metrics}})
    triage._validate_archive_schedule({"schedule": schedule})
    schedule[4]["counts"]["selected_blocks"] = 0
    with pytest.raises(triage.TriageError, match="archive_unobserved_not_null"):
        triage._validate_archive_schedule({"schedule": schedule})


def test_existing_publication_replay_keeps_original_timestamp_and_bytes() -> None:
    projection_ns = triage.archive_projection.INPUT_RECEIPT_NS + 50
    artifact = {
        "schema": triage.TRIAGE_SCHEMA,
        "schema_version": 1,
        "status": "sample_only_raw_structural_family_triage",
        "scope": {"projection_known_ns": projection_ns},
        "source_binding": {
            "queue_artifact_sha256": triage.EXPECTED_QUEUE_SHA256,
            "archive_projection_sha256": triage.EXPECTED_ARCHIVE_SHA256,
            "maximum_source_availability_ns": projection_ns - 1,
            "source_code_sha256": {
                "tools/brain_senses_external_structural_family_triage_v1.py": triage._sha(
                    triage.Path(triage.__file__).read_bytes()
                )
            },
            "idl_blobs": [],
            "idl_history_receipt_sha256": triage.IDL_HISTORY_RECEIPT_SHA256,
            "idl_manifest_sha256": triage.IDL_MANIFEST_SHA256,
        },
    }
    artifact_bytes = triage._canonical(artifact)
    receipt_bytes = triage._canonical(
        triage._build_receipt(artifact_bytes, projection_known_ns=projection_ns)
    )
    original_artifact, original_receipt = artifact_bytes, receipt_bytes
    expected_source_hashes = artifact["source_binding"]["source_code_sha256"]
    verified = triage._verify_existing_publication(
        artifact_bytes,
        receipt_bytes,
        expected_source_hashes=expected_source_hashes,
        expected_idl_blobs=[],
    )
    assert verified["projection_known_ns"] == projection_ns
    assert triage._publication_action(True, True) == "verify_existing"
    assert artifact_bytes == original_artifact
    assert receipt_bytes == original_receipt
    with pytest.raises(triage.TriageError, match="existing_publication_canonical"):
        triage._verify_existing_publication(
            artifact_bytes + b" ",
            receipt_bytes,
            expected_source_hashes=expected_source_hashes,
            expected_idl_blobs=[],
        )
