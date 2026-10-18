from __future__ import annotations

import hashlib
import itertools
import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from brain_senses_program_exposure_ledger_v1 import (  # noqa: E402
    SLOT_LEDGER_FIELDS,
    LedgerFailure,
    _alt_format_issue,
    _blank_counts,
    _capture,
    _compact_slot_rows,
    _freeze_counts,
    _paired_blocks,
    _raw_version_status_path_counts,
    _reproducible_content_sha256,
    _request_identity_sha256,
    _root_aggregate_counts,
    _scan_dataset,
    _transaction_instruction_paths,
    _v0_outer_program_index_issue,
    _validate_decision_time,
    _validate_program_reference_counts,
)

from memecoin_pumpswap.normalize import normalize_compiled_transaction


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pair_fixture(order: tuple[str, ...]):
    slots = (3, 7, 11)
    paths = {
        f"{slot}-{kind}": Path(f"/fixture/{slot}-{kind}.json")
        for slot in slots
        for kind in ("full", "signatures")
    }
    by_path = {str(path): name for name, path in paths.items()}
    receipt_json = {}
    captured = {}
    for slot in slots:
        for kind in ("full", "signatures"):
            name = f"{slot}-{kind}"
            options = {"commitment": "finalized", "encoding": "json", "transactionDetails": kind}
            receipt_json[str(paths[name])] = {
                "request_json": json.dumps({"method": "getBlock", "params": [slot, options]})
            }
        signatures = [f"sig-{slot}-a", f"sig-{slot}-b"]
        block = {
            "blockhash": f"hash-{slot}",
            "blockTime": 1_800_000_000 + slot,
            "parentSlot": slot - 1,
            "transactions": [
                {"transaction": {"signatures": [signature]}} for signature in signatures
            ],
        }
        sig_block = {
            "blockhash": f"hash-{slot}",
            "blockTime": 1_800_000_000 + slot,
            "parentSlot": slot - 1,
            "signatures": signatures,
        }
        captured[f"{slot}-full"] = (
            json.loads(receipt_json[str(paths[f"{slot}-full"])]["request_json"]),
            {"received_ns": 10_000 + slot, "storage": {"sha256": "f" * 64}},
            block,
        )
        captured[f"{slot}-signatures"] = (
            json.loads(receipt_json[str(paths[f"{slot}-signatures"])]["request_json"]),
            {"received_ns": 20_000 + slot, "storage": {"sha256": "s" * 64}},
            sig_block,
        )

    class Reader:
        def json(self, path: Path):
            return receipt_json[str(path)]

    spec = {
        "dataset_id": "synthetic",
        "selected_slots": list(slots),
        "receipt_paths": [paths[name] for name in order],
        "zstd": "unused",
    }
    return spec, Reader(), captured, by_path


def test_full_signature_pair_order_is_independent_of_receipt_traversal():
    names = tuple(f"{slot}-{kind}" for slot in (3, 7, 11) for kind in ("full", "signatures"))
    expected = None
    for order in itertools.permutations(names):
        spec, reader, captured, by_path = _pair_fixture(order)

        def fake_capture(_reader, path, _zstd, captured=captured, by_path=by_path):
            return captured[by_path[str(path)]]

        with patch("brain_senses_program_exposure_ledger_v1._capture", fake_capture):
            blocks = list(_paired_blocks(reader, spec))
        result = [
            (row["slot"], row["full"]["blockhash"], tuple(row["signatures"])) for row in blocks
        ]
        assert result == [
            (3, "hash-3", ("sig-3-a", "sig-3-b")),
            (7, "hash-7", ("sig-7-a", "sig-7-b")),
            (11, "hash-11", ("sig-11-a", "sig-11-b")),
        ]
        if expected is None:
            expected = result
        assert result == expected
        first_row = blocks[0]
        assert first_row["full_request"]["params"][0] == 3
        assert first_row["signature_request"]["params"][1]["transactionDetails"] == "signatures"
        assert (
            _request_identity_sha256(first_row["full_request"])
            == hashlib.sha256(
                json.dumps(
                    first_row["full_request"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )


def test_full_signature_pair_rejects_order_mismatch():
    spec, reader, captured, by_path = _pair_fixture(
        tuple(f"{slot}-{kind}" for slot in (3, 7, 11) for kind in ("full", "signatures"))
    )
    request, receipt, sig_block = captured["7-signatures"]
    sig_block["signatures"].reverse()

    def fake_capture(_reader, path, _zstd, captured=captured, by_path=by_path):
        return captured[by_path[str(path)]]

    with patch("brain_senses_program_exposure_ledger_v1._capture", fake_capture):
        with pytest.raises(LedgerFailure, match="full_signature_order_mismatch"):
            list(_paired_blocks(reader, spec))


def test_full_signature_pair_rejects_missing_selected_block():
    spec, reader, captured, by_path = _pair_fixture(
        tuple(f"{slot}-{kind}" for slot in (3, 7, 11) for kind in ("full", "signatures"))
    )
    spec["receipt_paths"] = spec["receipt_paths"][:-1]

    def fake_capture(_reader, path, _zstd, captured=captured, by_path=by_path):
        return captured[by_path[str(path)]]

    with patch("brain_senses_program_exposure_ledger_v1._capture", fake_capture):
        with pytest.raises(LedgerFailure, match="full_signature_slot_set_mismatch"):
            list(_paired_blocks(reader, spec))


def test_capture_rejects_stored_hash_corruption_before_decompression(tmp_path: Path):
    payload = b"corrupted stored response"
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    compressed = capture_dir / "response.zst"
    compressed.write_bytes(payload)
    receipt_path = capture_dir / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "request_json": json.dumps({"method": "getBlock", "params": [1, {}]}),
                "storage": {
                    "path": "response.zst",
                    "codec": "zstd",
                    "bytes": len(payload),
                    "sha256": "0" * 64,
                },
            }
        )
    )

    class Reader:
        def json(self, _path):
            return json.loads(receipt_path.read_text())

        def read(self, _path, *, cache=True):
            return payload

    with pytest.raises(LedgerFailure, match="stored_capture_hash_mismatch"):
        _capture(Reader(), receipt_path, "unused")


def test_compact_slot_schema_rejects_field_and_exposure_drift():
    row = {field: 0 for field in SLOT_LEDGER_FIELDS}
    row.update(
        {
            "blockhash": "blockhash",
            "event_time_unix_seconds": None,
            "full_request_json_sha256": "e" * 64,
            "signature_request_json_sha256": "f" * 64,
            "full_receipt_ns": 1,
            "signature_receipt_ns": 2,
            "availability_ns": None,
            "full_response_sha256": "a" * 64,
            "full_stored_sha256": "b" * 64,
            "signature_response_sha256": "c" * 64,
            "signature_stored_sha256": "d" * 64,
            "program_count": 1,
            "program_exposures": [[0, 3]],
        }
    )
    compact = _compact_slot_rows([row])
    assert len(compact) == 1
    assert len(compact[0]) == len(SLOT_LEDGER_FIELDS)
    assert compact[0][SLOT_LEDGER_FIELDS.index("program_exposures")] == [[0, 3]]

    bad_arity = dict(row)
    bad_arity.pop("blockhash")
    with pytest.raises(LedgerFailure, match="slot_ledger_field_schema_mismatch"):
        _compact_slot_rows([bad_arity])

    bad_exposure = dict(row, program_exposures=[["zero", 3]])
    with pytest.raises(LedgerFailure, match="slot_ledger_program_exposure_shape_invalid"):
        _compact_slot_rows([bad_exposure])


def test_instruction_reference_counts_ignore_order_but_reject_duplicate_paths():
    instructions = [
        {"program_id": "program-a", "path": [0], "program_id_source": "static"},
        {"program_id": "program-a", "path": [0, 1], "program_id_source": "loaded_writable"},
        {"program_id": "program-b", "path": [1], "program_id_source": "static"},
        {"program_id": "program-a", "path": [0, 2], "program_id_source": "loaded_readonly"},
    ]
    summaries = set()
    for ordered in itertools.permutations(instructions):
        references, unique_paths = _transaction_instruction_paths(
            list(ordered), dataset_id="synthetic", slot=3, tx_index=0
        )
        summaries.add(
            (
                tuple(sorted(references)),
                tuple(
                    (program, tuple(sorted(paths)))
                    for program, paths in sorted(unique_paths.items())
                ),
            )
        )
    assert summaries == {
        (
            (
                ("program-a", "inner", "loaded_readonly"),
                ("program-a", "inner", "loaded_writable"),
                ("program-a", "outer", "static"),
                ("program-b", "outer", "static"),
            ),
            (("program-a", ("inner", "outer")), ("program-b", ("outer",))),
        )
    }

    with pytest.raises(LedgerFailure, match="duplicate_instruction_identity"):
        _transaction_instruction_paths(
            [
                {"program_id": "program-a", "path": [0], "program_id_source": "static"},
                {"program_id": "program-b", "path": [0], "program_id_source": "static"},
            ],
            dataset_id="synthetic",
            slot=3,
            tx_index=0,
        )


def test_reproducible_content_hash_excludes_timing_and_decision_must_follow_evidence():
    first = {
        "status": "complete_structural_exposure_only",
        "decision_time_ns": 10,
        "decision_time_provenance": {"source": "first"},
        "datasets": [{"counts": {"selected_slots": 1}}],
        "replay_measurements": {"wall_time_ns": 50},
    }
    second = {
        **first,
        "decision_time_ns": 20,
        "decision_time_provenance": {"source": "second"},
        "replay_measurements": {"wall_time_ns": 100},
    }
    assert _reproducible_content_sha256(first) == _reproducible_content_sha256(second)
    _validate_decision_time(20, [10, None])
    with pytest.raises(LedgerFailure, match="decision_time_precedes_evidence"):
        _validate_decision_time(9, [10])


def test_root_unique_counts_are_named_as_sums_of_dataset_observations():
    totals = Counter(
        {
            "selected_slots": 5,
            "unique_program_ids_with_references": 7,
            "unique_referencing_transactions": 11,
        }
    )
    result = _root_aggregate_counts(totals)
    assert result["sum_dataset_program_id_table_entries"] == 7
    assert result["sum_dataset_slot_unique_referencing_transaction_observations"] == 11
    assert "unique_program_ids_with_references" not in result
    assert "unique_referencing_transactions" not in result


def test_raw_outer_v0_guard_inner_loaded_resolution_and_v1_alt_rejection():
    key = "11111111111111111111111111111111"
    transaction = {
        "version": 0,
        "transaction": {
            "signatures": ["synthetic-signature"],
            "message": {
                "accountKeys": [key, key, key],
                "addressTableLookups": [
                    {"accountKey": key, "writableIndexes": [0], "readonlyIndexes": [1]}
                ],
                "instructions": [{"programIdIndex": 2, "accounts": [3], "data": "1"}],
            },
        },
        "meta": {
            "err": None,
            "loadedAddresses": {"writable": [key], "readonly": [key]},
            "innerInstructions": [
                {
                    "index": 0,
                    "instructions": [
                        {
                            "programIdIndex": 4,
                            "accounts": [3],
                            "data": "1",
                            "stackHeight": 2,
                        }
                    ],
                }
            ],
        },
    }
    assert _alt_format_issue(transaction) is None
    assert _v0_outer_program_index_issue(transaction) is None
    normalized = normalize_compiled_transaction(transaction)
    assert normalized["outer_instructions"][0]["program_id"] == key
    assert normalized["accounts"][3]["source"] == "loaded_writable"
    assert normalized["accounts"][4]["source"] == "loaded_readonly"
    assert normalized["outer_instructions"][0]["account_provenance"][0]["source"] == (
        "loaded_writable"
    )
    resolved = [
        {
            **instruction,
            "program_id_source": normalized["accounts"][instruction["program_id_index"]]["source"],
        }
        for instruction in [
            *normalized["outer_instructions"],
            *normalized["inner_instructions"],
        ]
    ]
    references, unique_paths = _transaction_instruction_paths(
        resolved, dataset_id="synthetic", slot=1, tx_index=0
    )
    assert references == [(key, "outer", "static"), (key, "inner", "loaded_readonly")]
    assert unique_paths == {key: {"outer", "inner"}}

    loaded_outer = {
        **transaction,
        "transaction": {
            **transaction["transaction"],
            "message": {
                **transaction["transaction"]["message"],
                "instructions": [{"programIdIndex": 3, "accounts": [], "data": "1"}],
            },
        },
    }
    assert _v0_outer_program_index_issue(loaded_outer) == ("v0_outer_program_id_not_static", 0)

    payer_outer = {
        **transaction,
        "transaction": {
            **transaction["transaction"],
            "message": {
                **transaction["transaction"]["message"],
                "instructions": [{"programIdIndex": 0, "accounts": [], "data": "1"}],
            },
        },
    }
    assert _v0_outer_program_index_issue(payer_outer) == (
        "v0_outer_program_id_is_fee_payer",
        0,
    )

    malformed_v1_alt = {
        **transaction,
        "version": 1,
    }
    assert _alt_format_issue(malformed_v1_alt) == "versioned_address_lookup_state_forbidden"
    legacy_outer = {
        **payer_outer,
        "version": "legacy",
    }
    assert _v0_outer_program_index_issue(legacy_outer) is None


def test_program_source_counts_are_sparse_and_keep_path_dimensions():
    counts = _blank_counts()
    counts["references_by_program_id_source"]["loaded_readonly"] = 2
    counts["references_by_program_id_source_and_path"]["loaded_readonly_inner"] = 2
    frozen = _freeze_counts(counts)
    assert frozen["references_by_program_id_source"] == {"loaded_readonly": 2}
    assert frozen["references_by_program_id_source_and_path"] == {"loaded_readonly_inner": 2}


def test_program_reference_counts_reconcile_every_dimension_and_dataset_total():
    counts = _blank_counts()
    counts.update(
        {
            "instruction_references": 3,
            "success_references": 2,
            "failed_references": 1,
            "outer_references": 2,
            "inner_references": 1,
        }
    )
    counts["references_by_program_id_source"].update({"static": 2, "loaded_readonly": 1})
    counts["references_by_program_id_source_and_path"].update(
        {"static_outer": 2, "loaded_readonly_inner": 1}
    )
    _validate_program_reference_counts({"program-a": counts}, outer_count=2, inner_count=1)

    doubled = {"program-a": {**counts, "instruction_references": 6}}
    with pytest.raises(LedgerFailure, match="program_status_reference_count_mismatch"):
        _validate_program_reference_counts(doubled, outer_count=2, inner_count=1)

    with pytest.raises(LedgerFailure, match="program_reference_total_mismatch"):
        _validate_program_reference_counts({"program-a": counts}, outer_count=3, inner_count=1)


def test_raw_incidence_is_by_message_version_status_and_instruction_path():
    transaction = {
        "version": 0,
        "transaction": {
            "message": {"instructions": [{}, {}]},
        },
        "meta": {
            "err": None,
            "innerInstructions": [{"index": 0, "instructions": [{}, {}, {}]}],
        },
    }
    version, status, counts = _raw_version_status_path_counts(transaction)
    assert (version, status, counts) == ("v0", "success", {"outer": 2, "inner": 3})


def test_invalid_v0_outer_index_fails_dataset_with_raw_incidence():
    tx = {
        "version": 0,
        "transaction": {
            "signatures": ["synthetic-signature"],
            "message": {
                "accountKeys": ["payer", "program"],
                "instructions": [{"programIdIndex": 2, "accounts": [], "data": "1"}],
            },
        },
        "meta": {"err": None, "innerInstructions": []},
    }
    block_row = {
        "slot": 7,
        "full": {"blockhash": "blockhash", "blockTime": None, "transactions": [tx]},
        "full_request": {"method": "getBlock", "params": [7, {}]},
        "signature_request": {"method": "getBlock", "params": [7, {}]},
        "full_receipt": {
            "received_ns": 10,
            "response_sha256": "a" * 64,
            "storage": {"sha256": "b" * 64},
        },
        "signature_receipt": {
            "received_ns": 11,
            "response_sha256": "c" * 64,
            "storage": {"sha256": "d" * 64},
        },
        "signatures": ["synthetic-signature"],
    }
    spec = {
        "dataset_id": "synthetic",
        "campaign_id": "campaign",
        "bucket_id": "bucket",
        "inventory": {"provider_returned_member_count": 1},
        "lineage": {},
    }
    with patch(
        "brain_senses_program_exposure_ledger_v1._paired_blocks",
        return_value=iter([block_row]),
    ):
        result = _scan_dataset(object(), spec)
    assert result["status"] == "failed"
    assert result["failure_categories"] == {"v0_outer_program_id_not_static": 1}
    assert (
        result["counts"]["raw_instruction_incidence_by_version_status_and_path"]["v0_success_outer"]
        == 1
    )
    assert result["counts"]["unsupported_normalization"] == 1
