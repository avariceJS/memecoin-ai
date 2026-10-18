from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_external_schema_v2 as lease_schema
from tools import brain_senses_external_structural_input_v1 as builder
from tools import brain_senses_external_verify_v2 as lease_verify
from tools.brain_senses_external_structural_input_schema_v1 import (
    BOOTSTRAP_BUCKET,
    FRAME_BUCKETS,
    PUMP_PROGRAM_ID,
    StructuralInputError,
    canonical,
    digest,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter


class MemoryReader:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.reads: list[str] = []

    def read_bytes(self, relative: str, *, limit: int = 64_000_000) -> bytes:
        self.reads.append(relative)
        value = self.files[relative]
        if len(value) > limit:
            raise ValueError("read limit")
        return value

    def list_files(self) -> list[str]:
        return sorted(self.files)


def _scheduled_plan() -> dict[str, Any]:
    start = int(datetime(2026, 10, 12, 6, 0, tzinfo=UTC).timestamp() * 1_000_000_000)
    frames = []
    for index, bucket in enumerate(FRAME_BUCKETS):
        frame_start = start + (5 + index * 5) * 60_000_000_000
        frames.append(
            {
                "index": index,
                "bucket": bucket,
                "start_ns": frame_start,
                "end_ns": frame_start + 180_000_000_000,
            }
        )
    certificate = {
        "frontier": 100,
        "gap_intervals": [{"start": 50, "end_exclusive": 75, "reason": "inherited_unknown"}],
    }
    return {
        "schema": lease_schema.PLAN_SCHEMA,
        "schema_version": 2,
        "service_id": "synthetic-v2",
        "provider": "helius-free",
        "endpoint_origin": "https://mainnet.helius-rpc.com",
        "storage": {
            "root_id": "root-id-test",
            "volume_uuid": "volume-id-test",
            "relative_root": "services/synthetic-v2",
        },
        "code_sha256": {"v2.py": "a" * 64},
        "predecessor": {
            "certificate": certificate,
            "certificate_sha256": digest(canonical(certificate)),
            "relative_root": "services/v1-synthetic",
        },
        "schedule": {
            "bootstrap": {
                "bucket": BOOTSTRAP_BUCKET,
                "start_ns": start,
                "end_ns": start + 60_000_000_000,
            },
            "frames": frames,
            "expires_ns": frames[-1]["end_ns"],
        },
        "caps": {},
    }


def _source_files() -> tuple[MemoryReader, dict[str, Any], dict[str, Any]]:
    plan = _scheduled_plan()
    plan_raw = lease_verify.canonical(plan)
    plan_hash = lease_verify.sha(plan_raw)
    bucket = FRAME_BUCKETS[0]
    slot = 101
    signature = "1" * 64
    raw_block = {
        "blockhash": "block-101",
        "previousBlockhash": "block-100",
        "parentSlot": 100,
        "blockTime": 1_800_000_000,
        "blockHeight": 101,
        "transactions": [
            {
                "version": "legacy",
                "transaction": {
                    "signatures": [signature],
                    "message": {
                        "accountKeys": [PUMP_PROGRAM_ID],
                        "instructions": [{"programIdIndex": 0, "accounts": [], "data": "1"}],
                    },
                },
                "meta": {
                    "err": None,
                    "loadedAddresses": {"writable": [], "readonly": []},
                    "innerInstructions": [
                        {
                            "index": 0,
                            "instructions": [
                                {"programIdIndex": 0, "accounts": [], "data": "1", "stackHeight": 2}
                            ],
                        }
                    ],
                },
            }
        ],
    }
    response_raw = canonical({"jsonrpc": "2.0", "id": 1, "result": raw_block})
    ordinal = 3
    request = {
        "schema": lease_schema.REQUEST_SCHEMA,
        "schema_version": 2,
        "provider": "helius-free",
        "request_id": "request-3",
        "request_sha256": "b" * 64,
        "wire_request_sha256": "c" * 64,
        "method": "getBlock",
        "params": [slot, {"transactionDetails": "full"}],
        "decision_ns": 1_800_000_000_000_000_000,
        "request_started_ns": 1_800_000_000_000_000_000,
    }
    request_raw = canonical(request)
    received_ns = 1_800_000_000_100_000_000
    available_ns = 1_800_000_000_200_000_000
    receipt = {
        "schema": lease_schema.RECEIPT_SCHEMA,
        "schema_version": 2,
        "provider": "helius-free",
        "request_id": "request-3",
        "request_sha256": request["request_sha256"],
        "wire_request_sha256": request["wire_request_sha256"],
        "request_started_ns": request["request_started_ns"],
        "received_ns": received_ns,
        "availability_ns": None,
        "response_sha256": digest(response_raw),
        "response_bytes": len(response_raw),
        "storage": {"path": "response.json", "bytes": len(response_raw), "codec": "raw"},
    }
    receipt_raw = canonical(receipt)
    result = {
        "schema": lease_schema.RESULT_SCHEMA,
        "schema_version": 2,
        "provider": "helius-free",
        "request_id": "request-3",
        "request_sha256": request["request_sha256"],
        "wire_request_sha256": request["wire_request_sha256"],
        "status": "captured",
        "result_status": "parsed",
        "response_sha256": digest(response_raw),
        "response_bytes": len(response_raw),
        "stored_response_bytes": len(response_raw),
        "availability_ns": available_ns,
        "decision_ns": request["decision_ns"],
        "method": "getBlock",
        "event_slot": slot,
    }
    result_raw = canonical(result)
    identity = {
        "blockhash": raw_block["blockhash"],
        "previousBlockhash": raw_block["previousBlockhash"],
        "parentSlot": raw_block["parentSlot"],
        "blockTime": raw_block["blockTime"],
        "blockHeight": raw_block["blockHeight"],
    }
    empty_bundle = {
        "phase": "frame",
        "frame_index": 0,
        "bucket": bucket,
        "frame": {
            "anchor_slot": slot,
            "inventory_sha256": "d" * 64,
            "denominator": 2,
            "selection_count": 1,
            "selected_slots": [slot],
            "inclusion_probability": "1/2",
            "selection_rule": ("uniform_without_replacement_from_finalized_getblocks_members"),
        },
        "selected_blocks": [{"slot": slot, "signatures": [signature], "block_identity": identity}],
    }
    verified = {
        "phase": "frame",
        "bundle": empty_bundle,
        "frontier": 102,
    }
    files = {
        lease_schema.PLAN_FILE: plan_raw,
        f"buckets/{bucket}/requests/{ordinal:02d}.json": request_raw,
        f"buckets/{bucket}/captures/{ordinal:02d}/receipt.json": receipt_raw,
        f"buckets/{bucket}/captures/{ordinal:02d}/response.json": response_raw,
        f"buckets/{bucket}/results/{ordinal:02d}.json": result_raw,
    }
    return MemoryReader(files), {"plan": plan, "plan_sha256": plan_hash}, {bucket: verified}


def _verified_state(plan: dict[str, Any], verified_buckets: dict[str, Any]) -> dict[str, Any]:
    sealed = {BOOTSTRAP_BUCKET: True, **{bucket: True for bucket in FRAME_BUCKETS}}
    bootstrap_gap = {
        "start": plan["predecessor"]["certificate"]["frontier"],
        "end_exclusive": 100,
        "reason": "uncovered_since_predecessor_no_backfill",
        "bootstrap_bucket": BOOTSTRAP_BUCKET,
    }
    inherited = plan["predecessor"]["certificate"]["gap_intervals"]
    return {
        "status": "verified_prefix",
        "plan": plan,
        "plan_sha256": lease_verify.sha(lease_verify.canonical(plan)),
        "event_tail_sha256": "f" * 64,
        "sealed": sealed,
        "missed": set(),
        "intents": {bucket: [0, 1, 2, 3] for bucket in FRAME_BUCKETS},
        "events": [],
        "buckets": {
            BOOTSTRAP_BUCKET: {"bundle": {"bootstrap_slot": 100}},
            **verified_buckets,
        },
        "gap_intervals": [*inherited, bootstrap_gap],
    }


def _compile(
    monkeypatch: Any, reader: MemoryReader, pins: dict[str, Any], verified: dict[str, Any]
) -> dict[str, Any]:
    lease_pins = pins["plan"]["code_sha256"]
    monkeypatch.setattr(builder, "_binding_sources", lambda: {"builder.py": "9" * 64})
    monkeypatch.setattr(
        builder.lease_verify,
        "verify",
        lambda *_args, **_kwargs: _verified_state(pins["plan"], verified),
    )
    monkeypatch.setattr(
        builder.lease_verify,
        "_verify_bucket",
        lambda _reader, _plan, bucket, frontier: verified.get(
            bucket,
            {
                "frontier": frontier + 1,
                "bundle": {
                    "frame": {
                        "anchor_slot": frontier,
                        "inventory_sha256": "d" * 64,
                        "denominator": 0,
                        "selection_count": 0,
                        "selected_slots": [],
                        "inclusion_probability": "0/0",
                        "selection_rule": (
                            "uniform_without_replacement_from_finalized_getblocks_members"
                        ),
                    },
                    "selected_blocks": [],
                },
            },
        ),
    )
    return builder.compile_from_sealed_v2(
        reader,
        reader,
        expected_plan_sha256=pins["plan_sha256"],
        expected_lease_sources=lease_pins,
    )


def test_synthetic_sealed_frame_binds_each_outer_inner_reference_and_sample_denominators(
    monkeypatch: Any,
) -> None:
    reader, pins, verified = _source_files()
    artifact = _compile(monkeypatch, reader, pins, verified)
    assert artifact["publishable"] is True
    assert artifact["point_in_time_eligible"] is False
    assert artifact["decision_ns"] is None
    assert artifact["schedule_denominators"] == {
        "scheduled_frames": 12,
        "sealed_frames": 12,
        "missed_frames": 0,
        "unknown_incomplete_frames": 0,
        "request_intents_in_incomplete_frames": 0,
    }
    assert artifact["totals"]["produced_inventory_slots"] == 2
    assert artifact["totals"]["selected_blocks"] == 1
    assert artifact["totals"]["unselected_produced_slots"] == 1
    assert artifact["totals"]["scanned_transactions"] == 1
    assert artifact["totals"]["pump_program_references"] == 2
    assert [row["instruction_path"] for row in artifact["references"]] == [
        {"outer_index": 0, "inner_index": None},
        {"outer_index": 0, "inner_index": 0},
    ]
    assert {row["transaction_status"] for row in artifact["references"]} == {"success"}
    assert all(row["decision_ns"] is None for row in artifact["references"])
    assert len({row["class_fingerprint_sha256"] for row in artifact["references"]}) == 1
    assert all(
        row["class_fingerprint_schema"] == "pump_program_structural_fingerprint_v1"
        for row in artifact["references"]
    )
    expected_fingerprint = digest(
        canonical(
            {
                "program_id": PUMP_PROGRAM_ID,
                "instruction_data_sha256": digest(bytes.fromhex("00")),
                "account_count": 0,
            }
        )
    )
    assert {row["class_fingerprint_sha256"] for row in artifact["references"]} == {
        expected_fingerprint
    }
    assert artifact["bootstrap_uncovered_interval"]["start"] == 100
    assert artifact["bootstrap_uncovered_interval"]["end_exclusive"] == 100
    assert artifact["predecessor_certificate_sha256"] == digest(
        canonical(pins["plan"]["predecessor"]["certificate"])
    )
    assert artifact["verified_gap_intervals"] == [
        *artifact["predecessor_gap_intervals"],
        artifact["bootstrap_uncovered_interval"],
    ]


def test_failed_transaction_status_remains_failed_without_effect_claim(monkeypatch: Any) -> None:
    reader, pins, verified = _source_files()
    response_path = f"buckets/{FRAME_BUCKETS[0]}/captures/03/response.json"
    body = json.loads(reader.files[response_path])
    body["result"]["transactions"][0]["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    response_raw = canonical(body)
    reader.files[response_path] = response_raw
    receipt_path = f"buckets/{FRAME_BUCKETS[0]}/captures/03/receipt.json"
    receipt = json.loads(reader.files[receipt_path])
    receipt.update(
        response_sha256=digest(response_raw),
        response_bytes=len(response_raw),
        storage={"path": "response.json", "bytes": len(response_raw), "codec": "raw"},
    )
    reader.files[receipt_path] = canonical(receipt)
    result_path = f"buckets/{FRAME_BUCKETS[0]}/results/03.json"
    result = json.loads(reader.files[result_path])
    result.update(
        response_sha256=digest(response_raw),
        response_bytes=len(response_raw),
        stored_response_bytes=len(response_raw),
    )
    reader.files[result_path] = canonical(result)
    artifact = _compile(monkeypatch, reader, pins, verified)
    assert artifact["totals"]["failed_transactions"] == 1
    assert artifact["totals"]["economic_trade_count"] is None
    assert all(row["transaction_status"] == "failed" for row in artifact["references"])


def test_hash_or_availability_mutation_rejects_capture(monkeypatch: Any) -> None:
    reader, pins, verified = _source_files()
    response_path = f"buckets/{FRAME_BUCKETS[0]}/captures/03/response.json"
    reader.files[response_path] += b" "
    with pytest.raises(StructuralInputError, match="capture_result_receipt_binding"):
        _compile(monkeypatch, reader, pins, verified)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("receipt_request", "capture_result_receipt_binding"),
        ("availability", "capture_result_receipt_binding"),
        ("provider_error", "capture_result_receipt_binding"),
        ("block_identity", "selected_block_identity"),
    ],
)
def test_receipt_status_availability_and_block_mutations_fail_closed(
    monkeypatch: Any, mutation: str, reason: str
) -> None:
    reader, pins, verified = _source_files()
    base = f"buckets/{FRAME_BUCKETS[0]}"
    if mutation == "receipt_request":
        receipt_path = f"{base}/captures/03/receipt.json"
        receipt = json.loads(reader.files[receipt_path])
        receipt["request_id"] = "different-request"
        reader.files[receipt_path] = canonical(receipt)
    elif mutation == "availability":
        result_path = f"{base}/results/03.json"
        result = json.loads(reader.files[result_path])
        result["availability_ns"] = result["availability_ns"] - 1_000_000_000
        reader.files[result_path] = canonical(result)
    elif mutation == "provider_error":
        result_path = f"{base}/results/03.json"
        result = json.loads(reader.files[result_path])
        result["result_status"] = "provider_error"
        reader.files[result_path] = canonical(result)
    else:
        response_path = f"{base}/captures/03/response.json"
        body = json.loads(reader.files[response_path])
        body["result"]["blockhash"] = "different-block"
        response_raw = canonical(body)
        reader.files[response_path] = response_raw
        receipt_path = f"{base}/captures/03/receipt.json"
        receipt = json.loads(reader.files[receipt_path])
        receipt.update(
            response_sha256=digest(response_raw),
            response_bytes=len(response_raw),
            storage={"path": "response.json", "bytes": len(response_raw), "codec": "raw"},
        )
        reader.files[receipt_path] = canonical(receipt)
        result_path = f"{base}/results/03.json"
        result = json.loads(reader.files[result_path])
        result.update(
            response_sha256=digest(response_raw),
            response_bytes=len(response_raw),
            stored_response_bytes=len(response_raw),
        )
        reader.files[result_path] = canonical(result)
    with pytest.raises(StructuralInputError, match=reason):
        _compile(monkeypatch, reader, pins, verified)


def test_incomplete_missed_and_unspent_frames_are_unknown_and_withhold_publication(
    monkeypatch: Any,
) -> None:
    reader, pins, verified = _source_files()
    state = _verified_state(pins["plan"], verified)
    state["sealed"][FRAME_BUCKETS[1]] = False
    state["missed"] = {FRAME_BUCKETS[1]}
    state["intents"][FRAME_BUCKETS[1]] = []
    state["intents"][FRAME_BUCKETS[2]] = [0, 1, 2]
    for bucket in FRAME_BUCKETS[2:]:
        state["sealed"][bucket] = False
    reader.files[f"buckets/{FRAME_BUCKETS[2]}/results/00.json"] = canonical(
        {"status": "ambiguous", "result_status": "unknown"}
    )
    reader.files[f"buckets/{FRAME_BUCKETS[2]}/results/01.json"] = canonical(
        {"status": "captured", "result_status": "provider_error"}
    )
    monkeypatch.setattr(builder, "_binding_sources", lambda: {"builder.py": "9" * 64})
    monkeypatch.setattr(builder.lease_verify, "verify", lambda *_a, **_k: state)
    before = set(reader.reads)
    artifact = builder.compile_from_sealed_v2(
        reader,
        reader,
        expected_plan_sha256=pins["plan_sha256"],
        expected_lease_sources=pins["plan"]["code_sha256"],
    )
    assert artifact["publishable"] is False
    assert artifact["status"] == "withheld_incomplete"
    assert artifact["schedule_denominators"]["missed_frames"] == 1
    assert artifact["schedule_denominators"]["unknown_incomplete_frames"] == 10
    second_frame = artifact["frame_states"][2]
    assert second_frame["request_result_status_counts"] == {
        "parsed": 0,
        "provider_error": 1,
        "invalid_response": 0,
        "ambiguous": 1,
        "missing": 1,
        "unknown_status": 0,
    }
    assert artifact["references"] == []
    assert not any("/captures/" in path for path in set(reader.reads) - before)


def test_fixed_schedule_mismatch_rejects_even_if_other_source_is_valid(monkeypatch: Any) -> None:
    reader, pins, verified = _source_files()
    plan = copy.deepcopy(pins["plan"])
    plan["schedule"]["frames"][0]["bucket"] = "20261012T060600Z"
    reader.files[lease_schema.PLAN_FILE] = lease_verify.canonical(plan)
    monkeypatch.setattr(builder, "_binding_sources", lambda: {"builder.py": "9" * 64})
    with pytest.raises(StructuralInputError, match="lease_schedule_not_predeclared_0600"):
        builder.compile_from_sealed_v2(
            reader,
            reader,
            expected_plan_sha256=digest(reader.files[lease_schema.PLAN_FILE]),
            expected_lease_sources=plan["code_sha256"],
        )


def test_gap_vector_must_match_verifier_and_preserve_inherited_rows(monkeypatch: Any) -> None:
    reader, pins, verified = _source_files()
    state = _verified_state(pins["plan"], verified)
    state["gap_intervals"] = [
        {"start": 50, "end_exclusive": 75, "reason": "inherited_unknown"},
        {
            "start": 100,
            "end_exclusive": 101,
            "reason": "uncovered_since_predecessor_no_backfill",
            "bootstrap_bucket": BOOTSTRAP_BUCKET,
        },
    ]
    monkeypatch.setattr(builder, "_binding_sources", lambda: {"builder.py": "9" * 64})
    monkeypatch.setattr(builder.lease_verify, "verify", lambda *_a, **_k: state)
    with pytest.raises(StructuralInputError, match="verified_gap_lineage_mismatch"):
        builder.compile_from_sealed_v2(
            reader,
            reader,
            expected_plan_sha256=pins["plan_sha256"],
            expected_lease_sources=pins["plan"]["code_sha256"],
        )


def test_descriptor_publication_is_create_only_and_idempotent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        writer = DescriptorWriter(root_fd)
        artifact = {
            "schema": "brain-senses-external-structural-input-v1",
            "publishable": True,
            "scope": {
                "plan_sha256": "a" * 64,
                "source_code_sha256": {"lease.py": "b" * 64},
                "builder_code_sha256": {"builder.py": "c" * 64},
                "event_tail_sha256": "d" * 64,
                "storage": {
                    "root_id": "root",
                    "volume_uuid": "uuid",
                    "relative_root": "services/svc",
                },
            },
            "predecessor_certificate_sha256": "e" * 64,
            "predecessor_gap_intervals": [],
            "bootstrap_uncovered_interval": {
                "start": 10,
                "end_exclusive": 10,
                "reason": "uncovered_since_predecessor_no_backfill",
                "bootstrap_bucket": BOOTSTRAP_BUCKET,
            },
            "verified_gap_intervals": [],
            "maximum_source_availability_ns": 100,
        }
        monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_args, **_kwargs: artifact)
        replay_reader = MemoryReader({})
        publish_args = {
            "source_reader": replay_reader,
            "predecessor_reader": replay_reader,
            "expected_plan_sha256": "a" * 64,
            "expected_lease_sources": {"lease.py": "b" * 64},
            "service_id": "svc",
            "source_relative_root": "services/svc",
            "output_relative_root": "derived/structural-input-v1/svc",
            "identity_check": lambda: {"root_id": "root", "volume_uuid": "uuid"},
            "revalidate": lambda: None,
        }
        first = builder.publish_create_only(
            writer,
            artifact,
            **publish_args,
            relative_directory="",
            confirmed_clock=lambda: 200,
        )
        second = builder.publish_create_only(
            writer,
            artifact,
            **publish_args,
            relative_directory="",
            confirmed_clock=lambda: 300,
        )
        assert first == second
        assert first["confirmed_ns"] == 200
        assert first["decision_ns"] is None
        assert writer.read_bytes("manifest.json") == canonical(artifact)
        assert len(writer.list_files()) == 2
        writer.close()
    finally:
        os.close(root_fd)


def test_nonpublishable_or_traversal_publication_is_rejected(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        writer = DescriptorWriter(root_fd)
        replay_reader = MemoryReader({})
        publish_args = {
            "source_reader": replay_reader,
            "predecessor_reader": replay_reader,
            "expected_plan_sha256": "a" * 64,
            "expected_lease_sources": {"lease.py": "b" * 64},
            "service_id": "svc",
            "source_relative_root": "services/svc",
            "output_relative_root": "derived/structural-input-v1/svc",
            "identity_check": lambda: {"root_id": "root", "volume_uuid": "uuid"},
            "revalidate": lambda: None,
        }
        with pytest.raises(StructuralInputError, match="artifact_not_publishable"):
            builder.publish_create_only(
                writer,
                {"schema": "brain-senses-external-structural-input-v1", "publishable": False},
                **publish_args,
                relative_directory="structural-input/test",
            )
        artifact = {
            "schema": "brain-senses-external-structural-input-v1",
            "publishable": True,
            "scope": {
                "plan_sha256": "a" * 64,
                "source_code_sha256": {"lease.py": "b" * 64},
                "builder_code_sha256": {"builder.py": "c" * 64},
                "event_tail_sha256": "d" * 64,
                "storage": {
                    "root_id": "root",
                    "volume_uuid": "uuid",
                    "relative_root": "services/svc",
                },
            },
            "predecessor_certificate_sha256": "e" * 64,
            "predecessor_gap_intervals": [],
            "bootstrap_uncovered_interval": {
                "start": 10,
                "end_exclusive": 10,
                "reason": "uncovered_since_predecessor_no_backfill",
                "bootstrap_bucket": BOOTSTRAP_BUCKET,
            },
            "verified_gap_intervals": [],
        }
        monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_args, **_kwargs: artifact)
        mutated = copy.deepcopy(artifact)
        mutated["totals"] = {"pump_program_references": 999}
        with pytest.raises(StructuralInputError, match="artifact_source_replay_mismatch"):
            builder.publish_create_only(
                writer,
                mutated,
                **publish_args,
                relative_directory="structural-input/rejected",
            )
        with pytest.raises(StructuralInputError, match="relative_path_invalid"):
            builder.publish_create_only(
                writer,
                artifact,
                **publish_args,
                relative_directory="../escape",
            )
        assert writer.list_files() == []
        writer.close()
    finally:
        os.close(root_fd)
