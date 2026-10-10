from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import brain_senses_pumpswap_structural_novelty_v1 as novelty


def _b58(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    zeroes = len(raw) - len(raw.lstrip(b"\0"))
    value = int.from_bytes(raw, "big")
    encoded = ""
    while value:
        value, remainder = divmod(value, 58)
        encoded = alphabet[remainder] + encoded
    return "1" * zeroes + encoded


def _transaction(*, signature: str = "sig-A", version: str | int = "legacy", meta_err=None):
    token = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    token_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    keys = [_b58(bytes([value]) * 32) for value in range(1, 25)]
    keys[0] = novelty.PROGRAM
    keys[12] = token
    keys[13] = token_2022
    # The first declared IDL instruction is a deterministic known layout; use buy-exact explicitly.
    data = bytes(IDL_DISC) + b"\x01\x02\x03"
    return {
        "version": version,
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": keys,
                "header": {
                    "numRequiredSignatures": 1,
                    "numReadonlySignedAccounts": 0,
                    "numReadonlyUnsignedAccounts": 1,
                },
                "instructions": [
                    {
                        "programIdIndex": 0,
                        "accounts": list(range(1, 24)),
                        "data": _b58(data),
                    }
                ],
                "addressTableLookups": [],
            },
        },
        "meta": {
            "err": meta_err,
            "innerInstructions": [],
            "loadedAddresses": {"writable": [], "readonly": []},
        },
    }


IDL_DISC = bytes(novelty.IDL_INSTRUCTIONS["buy_exact_quote_in"]["discriminator"])


def _sealed_block(root: Path, slot: int, transaction: dict) -> Path:
    zstd = shutil.which("zstd")
    if zstd is None:
        pytest.skip("zstd executable required for sealed capture fixtures")
    capture_dir = root / "captures" / f"{slot:012d}"
    capture_dir.mkdir(parents=True)
    response = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "blockhash": f"block-{slot}",
                "parentSlot": slot - 1,
                "blockTime": 1_800_000_000 + slot,
                "transactions": [transaction],
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    capture = capture_dir / "response.json.zst"
    completed = subprocess.run([zstd, "-q", "-c"], input=response, capture_output=True, check=True)
    capture.write_bytes(completed.stdout)
    receipt = {
        "method": "getBlock",
        "source_alias": "helius-free",
        "received_ns": 1_800_000_000_000_000_000 + slot,
        "request_json": json.dumps(
            {
                "method": "getBlock",
                "params": [
                    slot,
                    {
                        "transactionDetails": "full",
                        "commitment": "finalized",
                        "encoding": "json",
                    },
                ],
            }
        ),
        "response_bytes": len(response),
        "response_sha256": hashlib.sha256(response).hexdigest(),
        "storage": {
            "codec": "zstd",
            "path": capture.name,
            "bytes": capture.stat().st_size,
            "sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        },
    }
    path = capture_dir / "receipt.json"
    path.write_text(json.dumps(receipt))
    return path


def _synthetic_spec(root: Path, slots=(10,)):
    census_frames = {
        "20261010T050000Z": {
            "selected_full_blocks_verified": len(slots),
            "counts": {
                "eligible_successful_sell23_candidates": 0,
                "failed_exact_sell23_structural_refs": 0,
            },
        }
    }
    return {
        "dataset_id": "test-frame",
        "campaign_id": "test-campaign",
        "epoch_id": "20261010T050000Z",
        "capture_root": root,
        "expected_slots": list(slots),
        "plan_sha256": "p" * 64,
        "campaign_plan_sha256": "c" * 64,
        "campaign_code": {"commit": "d" * 40, "sha256": "e" * 64},
        "summary_sha256": "s" * 64,
        "frame_sha256": "f" * 64,
        "stop_sha256": "t" * 64,
        "inventory_sha256": "i" * 64,
        "inventory_denominator": 1,
        "range": [10, 20],
        "census": None,
        "census_path": None,
        "census_sha256": None,
        "sell_census": {"frames": census_frames},
        "sell_census_path": "synthetic.json",
        "sell_census_sha256": "r" * 64,
        "zstd": shutil.which("zstd"),
        "pair64": False,
    }


def test_scans_exact_wire_and_suffix_privileges_without_roles():
    result = novelty._scan_block(
        {"transactions": [_transaction()]},
        10,
        {"received_ns": 1_800_000_000_000_000_010},
    )

    assert result["normalized_transactions"] == 1
    assert len(result["references"]) == 1
    reference = result["references"][0]
    fingerprint = reference["class"]
    assert fingerprint["discriminator_hex"] == IDL_DISC.hex()
    assert fingerprint["wire_data_length"] == 11
    assert fingerprint["trailing_wire_hex"] == "010203"
    assert fingerprint["fixed_account_count"] == 23
    assert fingerprint["observed_account_count"] == 23
    assert fingerprint["suffix"] == []
    assert fingerprint["token_program_pair"] == [
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    ]
    assert fingerprint["transaction_status"] == "success"
    assert reference["pool_key"] == _b58(bytes([2]) * 32)


def test_unknown_discriminator_retains_positional_suffix_and_status():
    tx = _transaction(meta_err={"InstructionError": [0, "failed"]})
    ix = tx["transaction"]["message"]["instructions"][0]
    ix["data"] = _b58(b"unknown!tail")
    ix["accounts"].append(23)
    tx["transaction"]["message"]["accountKeys"].append(_b58(bytes([25]) * 32))
    result = novelty._scan_block({"transactions": [tx]}, 10, {})
    fingerprint = result["references"][0]["class"]
    assert fingerprint["instruction_kind"] == "unknown_discriminator"
    assert fingerprint["fixed_account_count"] is None
    assert fingerprint["suffix"] == [
        {"position": 0, "privilege": {"signer": False, "writable": True}},
        *[
            {"position": index, "privilege": {"signer": False, "writable": True}}
            for index in range(1, 24)
        ],
    ]
    assert fingerprint["transaction_status"] == "failed"


def test_parser_failure_is_fail_closed():
    with pytest.raises(novelty.MonitorFailure, match="transaction_normalization_failed"):
        novelty._scan_block({"transactions": [_transaction(version=7)]}, 10, {})


def test_replay_is_deterministic_and_fails_on_missing_capture(tmp_path: Path):
    spec = _synthetic_spec(tmp_path)
    _sealed_block(tmp_path, 10, _transaction())

    first = novelty._replay_dataset(spec)
    second = novelty._replay_dataset(spec)
    assert novelty._canonical(first) == novelty._canonical(second)
    assert first["counts"]["instruction_references"] == 1
    assert first["counts"]["unique_referencing_transactions"] == 1
    assert len(first["classes"]) <= 256

    missing_spec = _synthetic_spec(tmp_path / "empty")
    (tmp_path / "empty" / "captures").mkdir(parents=True)
    with pytest.raises(novelty.MonitorFailure, match="full_capture_count_mismatch"):
        novelty._replay_dataset(missing_spec)


def test_duplicate_instruction_identity_and_corrupt_hash_fail_closed(tmp_path: Path):
    spec = _synthetic_spec(tmp_path, slots=(10, 11))
    receipt_path = _sealed_block(tmp_path, 10, _transaction())
    _sealed_block(tmp_path, 11, _transaction())
    with pytest.raises(
        novelty.MonitorFailure,
        match="duplicate_referencing_transaction_signature",
    ):
        novelty._replay_dataset(spec)

    receipt = json.loads(receipt_path.read_text())
    capture = receipt_path.parent / receipt["storage"]["path"]
    capture.write_bytes(capture.read_bytes() + b"corruption")
    with pytest.raises(novelty.MonitorFailure, match="stored_capture_hash_mismatch"):
        novelty._read_capture(receipt_path, shutil.which("zstd"))


def test_monitor_failure_receipt_is_written_without_overwrite(tmp_path: Path, monkeypatch, capsys):
    output = tmp_path / "monitor-failure.json"

    def fail():
        raise novelty.MonitorFailure("fixture_corruption", "sealed capture hash changed")

    monkeypatch.setattr(novelty, "build_receipt", fail)
    monkeypatch.setattr("sys.argv", ["novelty", "--output", str(output)])
    assert novelty.main() == 2
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "monitor_failure"
    assert receipt["failure"] == {
        "code": "fixture_corruption",
        "detail": "sealed capture hash changed",
    }
    assert "monitor_failure" in capsys.readouterr().out


def test_unexpected_input_shape_error_writes_bounded_failure_receipt(
    tmp_path: Path, monkeypatch, capsys
):
    output = tmp_path / "unexpected-failure.json"

    def fail():
        raise TypeError("sensitive-or-unbounded-detail" * 100)

    monkeypatch.setattr(novelty, "build_receipt", fail)
    monkeypatch.setattr("sys.argv", ["novelty", "--output", str(output)])
    assert novelty.main() == 2
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "monitor_failure"
    assert receipt["failure"] == {
        "code": "unexpected_replay_error",
        "detail": "unexpected:TypeError",
    }
    assert len(output.read_bytes()) < 2_048
    assert "sensitive-or-unbounded-detail" not in capsys.readouterr().out
