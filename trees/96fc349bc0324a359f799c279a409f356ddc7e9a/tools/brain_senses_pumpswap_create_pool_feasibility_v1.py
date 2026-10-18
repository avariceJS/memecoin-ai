#!/usr/bin/env python3
"""Read-only feasibility census for Pump AMM create_pool in frozen Pair64/c3 blocks.

The census reports structural candidates and parser boundaries only. It never emits births,
coverage, economic, admission, or settlement facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from memecoin_pumpswap.birth_schema import PROFILES  # noqa: E402
from memecoin_pumpswap.normalize import (  # noqa: E402
    NormalizationError,
    normalize_compiled_transaction,
)
from memecoin_pumpswap.schema import SCHEMA as PUMP_AMM_SCHEMA  # noqa: E402

LEDGER_PATH = Path("docs/evidence/senses-program-exposure-ledger-v1-pair64-c3-2026-10-10.json")
NOVELTY_PATH = Path("docs/evidence/pumpswap-structural-novelty-v1-2026-10-10.json")
PROJECTION_PATH = Path(
    "docs/evidence/senses-program-exposure-projection-v1-pair64-c3-2026-10-11.json"
)
PAIR_ROOT = Path("work/brain-senses-live-helius-alchemy-512-pair64-20261009/20261009T150000Z")
C3_ROOT = Path("work/brain-senses-live-helius-inventory-v3-20261010-3")
FRAME_IDS = tuple(f"20261010T{hour:02d}0000Z" for hour in range(5, 11))
NETWORK = "solana-mainnet"
PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
CREATE_POOL_IX = bytes.fromhex("e992d18ecf6840bc")
EVENT_TAG = bytes.fromhex("e445a52e51cb9a1d")
CREATE_POOL_EVENT = bytes.fromhex("b1310cd2a076a774")
EVENT_AUTHORITY = "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR"
GLOBAL_CONFIG = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"
EXPECTED_DATASETS = ("pair64", *(f"c3:{frame}" for frame in FRAME_IDS))
STAGE1_SCHEMA = "pumpswap_create_pool_feasibility_stage1_v1"
RESULT_SCHEMA = "pumpswap_create_pool_feasibility_result_v1"
RESULT_CAP = 8 * 1024 * 1024


class FeasibilityError(ValueError):
    """Fail-closed input or trace error."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise FeasibilityError("input_missing_or_unreadable", str(path)) from exc


def _json(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    try:
        return json.loads(raw), raw
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FeasibilityError("input_json_invalid", str(path)) from exc


def _write_create_only(path: Path, payload: bytes, *, root: Path) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root / "work"):
        raise FeasibilityError("output_must_be_under_work")
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(resolved_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        dfd = os.open(resolved_path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            resolved_path.unlink()
        except OSError:
            pass
        raise


def _verify_source_commit(root: Path, commit: str, source_path: str, expected_sha: str) -> None:
    proc = subprocess.run(
        ["git", "show", f"{commit}:{source_path}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if proc.returncode or _sha(proc.stdout) != expected_sha:
        raise FeasibilityError("pinned_source_commit_mismatch", f"{commit}:{source_path}")


def _capture_index(
    root: Path, provider_root: Path, selected_slots: set[int]
) -> dict[int, dict[str, dict[str, Any]]]:
    """Index receipt metadata only; never open a stored block/signature response body here."""
    found: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for receipt_path in sorted((root / provider_root).glob("*/receipt.json")):
        receipt, raw = _json(receipt_path)
        try:
            request = json.loads(receipt["request_json"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise FeasibilityError("capture_receipt_request_invalid", str(receipt_path)) from exc
        if request.get("method") != "getBlock":
            continue
        try:
            params = request["params"]
            details = params[1]["transactionDetails"]
            slot = params[0]
        except (KeyError, IndexError, TypeError) as exc:
            raise FeasibilityError("capture_receipt_request_invalid", str(receipt_path)) from exc
        if details not in {"full", "signatures"}:
            continue
        if type(slot) is not int or slot not in selected_slots:
            continue
        if receipt.get("source_alias") != "helius-free":
            raise FeasibilityError("selected_capture_source_mismatch", str(receipt_path))
        if details in found[slot]:
            raise FeasibilityError("duplicate_selected_capture_receipt", f"{slot}:{details}")
        storage = receipt.get("storage")
        if not isinstance(storage, dict) or storage.get("codec") != "zstd":
            raise FeasibilityError("selected_capture_storage_invalid", str(receipt_path))
        request_sha = _sha(_canonical(request))
        found[slot][details] = {
            "receipt_path": str(receipt_path.relative_to(root)),
            "receipt_sha256": _sha(raw),
            "request_json_sha256": request_sha,
            "response_sha256": receipt.get("response_sha256"),
            "response_bytes": receipt.get("response_bytes"),
            "stored_sha256": storage.get("sha256"),
            "stored_bytes": storage.get("bytes"),
            "storage_path": storage.get("path"),
            "receipt_ns": receipt.get("received_ns"),
            "availability_ns": receipt.get("availability_ns"),
            "request_started_ns": receipt.get("request_started_ns"),
            "details": details,
            "slot": slot,
        }
    return found


def _verify_manifest_pins(root: Path, projection: dict[str, Any]) -> list[dict[str, str]]:
    pins = projection.get("metadata_manifest_pins")
    if not isinstance(pins, list) or len(pins) != 22:
        raise FeasibilityError(
            "metadata_manifest_pin_count", str(len(pins) if isinstance(pins, list) else None)
        )
    result = []
    for pin in pins:
        path = root / pin["path"]
        actual = _file_sha(path)
        if actual != pin.get("sha256"):
            raise FeasibilityError("metadata_manifest_hash_mismatch", pin.get("path", ""))
        result.append({"dataset_id": pin["dataset_id"], "path": pin["path"], "sha256": actual})
    return result


def _selected_from_metadata(
    root: Path, dataset_id: str, ledger_dataset: dict[str, Any]
) -> tuple[list[int], list[dict[str, str]]]:
    metadata_pins: list[dict[str, str]] = []
    if dataset_id == "pair64":
        base = root / PAIR_ROOT / "paired-block-capture"
        plan, plan_raw = _json(base / "plan.json")
        summary, summary_raw = _json(base / "summary.json")
        epoch_plan, epoch_plan_raw = _json(root / PAIR_ROOT / "epoch-plan.json")
        epoch_summary, epoch_summary_raw = _json(root / PAIR_ROOT / "epoch-summary.json")
        selected = plan.get("full_sample_slots")
        if (
            not isinstance(selected, list)
            or len(selected) != 512
            or selected != sorted(set(selected))
            or plan.get("signature_sample_slots") != selected
            or summary.get("plan_sha256") != _sha(plan_raw)
            or epoch_plan.get("window_slots") != 512
            or epoch_summary.get("status") != "provider_window_attempted"
            or epoch_summary.get("block_capture", {}).get("qualified_sample_slot_count") != 512
        ):
            raise FeasibilityError("pair64_selection_metadata_invalid")
        metadata_pins = [
            {"path": str((base / "plan.json").relative_to(root)), "sha256": _sha(plan_raw)},
            {"path": str((base / "summary.json").relative_to(root)), "sha256": _sha(summary_raw)},
            {
                "path": str((root / PAIR_ROOT / "epoch-plan.json").relative_to(root)),
                "sha256": _sha(epoch_plan_raw),
            },
            {
                "path": str((root / PAIR_ROOT / "epoch-summary.json").relative_to(root)),
                "sha256": _sha(epoch_summary_raw),
            },
        ]
        return selected, metadata_pins

    frame_id = dataset_id.split(":", 1)[1]
    epoch = root / C3_ROOT / "epochs" / frame_id
    summary, summary_raw = _json(epoch / "epoch-summary.json")
    plan, plan_raw = _json(epoch / "epoch-plan.json")
    selected = summary.get("selected_slots")
    outcomes = summary.get("per_slot_outcomes")
    if (
        summary.get("epoch_id") != frame_id
        or summary.get("status") != "frame_complete"
        or not isinstance(selected, list)
        or len(selected) != 16
        or selected != sorted(set(selected))
        or not isinstance(outcomes, list)
        or [x.get("slot") for x in outcomes] != selected
        or any(
            x.get("full_status") != "captured"
            or x.get("full_result_status") != "parsed"
            or x.get("signature_status") != "captured"
            or x.get("signature_result_status") != "parsed"
            for x in outcomes
        )
        or plan.get("epoch_id") != frame_id
    ):
        raise FeasibilityError("c3_selection_metadata_invalid", frame_id)
    metadata_pins = [
        {"path": str((epoch / "epoch-plan.json").relative_to(root)), "sha256": _sha(plan_raw)},
        {
            "path": str((epoch / "epoch-summary.json").relative_to(root)),
            "sha256": _sha(summary_raw),
        },
    ]
    return selected, metadata_pins


def build_stage1(root: Path = ROOT) -> dict[str, Any]:
    """Freeze selection and raw capture receipt identities without reading block bodies."""
    if (
        PUMP_AMM_SCHEMA.get("address") != PROGRAM_ID
        or hashlib.sha256(b"global:create_pool").digest()[:8] != CREATE_POOL_IX
        or hashlib.sha256(b"event:CreatePoolEvent").digest()[:8] != CREATE_POOL_EVENT
        or any(bytes(profile["discriminator"]) != CREATE_POOL_EVENT for profile in PROFILES)
    ):
        raise FeasibilityError("create_pool_discriminator_source_mismatch")
    ledger_path, novelty_path, projection_path = (
        root / p for p in (LEDGER_PATH, NOVELTY_PATH, PROJECTION_PATH)
    )
    ledger, ledger_raw = _json(ledger_path)
    novelty, novelty_raw = _json(novelty_path)
    projection, projection_raw = _json(projection_path)
    ledger_sha, novelty_sha, projection_sha = map(_sha, (ledger_raw, novelty_raw, projection_raw))
    if (
        ledger.get("schema") != "senses_program_exposure_ledger_v1"
        or novelty.get("schema_version") != "pumpswap-structural-novelty-v1"
        or projection.get("schema") != "pair64_c3_pump_amm_slot_exposure_projection_receipt_v1"
        or projection.get("input_ledger_path") != str(LEDGER_PATH)
        or projection.get("input_ledger_sha256") != ledger_sha
        or ledger.get("source_pins", {}).get("pump_amm_program_id") != PROGRAM_ID
        or ledger.get("counts", {}).get("selected_slots") != 608
        or ledger.get("counts", {}).get("full_block_transactions") != 741305
    ):
        raise FeasibilityError("source_receipt_binding_invalid")
    manifest_pins = _verify_manifest_pins(root, projection)
    datasets = ledger.get("datasets")
    if not isinstance(datasets, list) or [d.get("dataset_id") for d in datasets] != list(
        EXPECTED_DATASETS
    ):
        raise FeasibilityError("dataset_set_mismatch")
    program = next(
        (p for p in ledger.get("programs", []) if p.get("program_id") == PROGRAM_ID), None
    )
    if program is None:
        raise FeasibilityError("pump_amm_program_ledger_row_missing")

    source_pins = dict(ledger["source_pins"])
    _verify_source_commit(
        root,
        source_pins["repository_head"],
        "tools/brain_senses_program_exposure_ledger_v1.py",
        source_pins["tool_sha256"],
    )
    source_files = {}
    for rel in (
        "tools/brain_senses_pumpswap_create_pool_feasibility_v1.py",
        "tools/brain_senses_program_exposure_ledger_v1.py",
        "src/memecoin_pumpswap/normalize.py",
        "src/memecoin_pumpswap/decoder.py",
        "src/memecoin_pumpswap/schema.py",
        "src/memecoin_pumpswap/births.py",
        "src/memecoin_pumpswap/birth_schema.py",
    ):
        source_files[rel] = _file_sha(root / rel)

    rows_out = []
    dataset_totals = {}
    novelty_by_dataset = {
        d.get("dataset_id"): d
        for d in novelty.get("datasets", [])
        if isinstance(d, dict) and isinstance(d.get("dataset_id"), str)
    }
    if set(novelty_by_dataset) != set(EXPECTED_DATASETS):
        raise FeasibilityError("structural_novelty_dataset_set_mismatch")
    for dataset in datasets:
        dataset_id = dataset["dataset_id"]
        fields = dataset.get("selected_slot_ledger_fields")
        rows = dataset.get("selected_slot_ledger")
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise FeasibilityError("slot_ledger_shape_invalid", dataset_id)
        fields_index = {name: i for i, name in enumerate(fields)}
        selected_from_manifest, dataset_meta_pins = _selected_from_metadata(
            root, dataset_id, dataset
        )
        if len(rows) != len(selected_from_manifest):
            raise FeasibilityError("selected_slot_count_mismatch", dataset_id)
        ledger_slots = [row[fields_index["slot"]] for row in rows]
        if ledger_slots != selected_from_manifest:
            raise FeasibilityError("selected_slot_member_mismatch", dataset_id)
        provider_root = Path(dataset["inventory"]["capture_path"]).parent.parent
        selected_set = set(ledger_slots)
        receipts = _capture_index(root, provider_root, selected_set)
        if set(receipts) != selected_set:
            raise FeasibilityError("selected_capture_slot_set_mismatch", dataset_id)
        per_program = next(
            (p for p in program["per_dataset"] if p["dataset_id"] == dataset_id), None
        )
        if per_program is None:
            raise FeasibilityError("program_dataset_row_missing", dataset_id)
        total = {
            "full_block_transactions": dataset["counts"]["full_block_transactions"],
            "success_transactions": dataset["counts"]["success_transactions"],
            "failed_transactions": dataset["counts"]["failed_transactions"],
            "unknown_status_transactions": dataset["counts"]["unknown_status_transactions"],
            "outer_instruction_references": dataset["counts"]["outer_instruction_references"],
            "inner_instruction_references": dataset["counts"]["inner_instruction_references"],
            "unsupported_normalization": dataset["counts"]["unsupported_normalization"],
            "program_instruction_references": per_program["instruction_references"],
            "program_unique_transactions": per_program["unique_transactions"],
            "structural_novelty_instruction_references": sum(
                item["pumpswap_instruction_references"]
                for item in novelty_by_dataset[dataset_id]["block_bindings"]
            ),
        }
        dataset_totals[dataset_id] = total
        novelty_rows = {
            row.get("slot"): row
            for row in novelty_by_dataset[dataset_id].get("block_bindings", [])
            if isinstance(row, dict) and type(row.get("slot")) is int
        }
        if len(novelty_rows) != len(rows):
            raise FeasibilityError("novelty_selected_slot_count_mismatch", dataset_id)
        for row in rows:
            raw_row = dict(zip(fields, row, strict=True))
            slot = raw_row["slot"]
            found = receipts[slot]
            if set(found) != {"full", "signatures"}:
                raise FeasibilityError(
                    "selected_full_signature_receipt_pair_missing", f"{dataset_id}:{slot}"
                )
            captures = {}
            for detail, ledger_name in (("full", "full"), ("signatures", "signature")):
                capture = found[detail]
                expected = {
                    "response_sha256": raw_row[f"{ledger_name}_response_sha256"],
                    "stored_sha256": raw_row[f"{ledger_name}_stored_sha256"],
                    "receipt_ns": raw_row[f"{ledger_name}_receipt_ns"],
                    "request_json_sha256": raw_row[f"{ledger_name}_request_json_sha256"],
                }
                if any(capture.get(k) != v for k, v in expected.items()):
                    raise FeasibilityError(
                        "selected_capture_receipt_ledger_mismatch", f"{dataset_id}:{slot}:{detail}"
                    )
                captures[detail] = capture
            target_index = dataset["program_id_table"].index(PROGRAM_ID)
            exposures = raw_row["program_exposures"]
            if not isinstance(exposures, list):
                raise FeasibilityError("slot_program_exposures_invalid", f"{dataset_id}:{slot}")
            target_count = next((v[1] for v in exposures if v[0] == target_index), 0)
            novelty_row = novelty_rows.get(slot)
            if (
                novelty_row is None
                or novelty_row.get("blockhash") != raw_row["blockhash"]
                or novelty_row.get("transaction_count")
                != raw_row["provider_full_transaction_count"]
                or novelty_row.get("raw_response_sha256") != raw_row["full_response_sha256"]
                or novelty_row.get("receipt_time_ns") != raw_row["full_receipt_ns"]
            ):
                raise FeasibilityError("novelty_block_binding_mismatch", f"{dataset_id}:{slot}")
            rows_out.append(
                {
                    "dataset_id": dataset_id,
                    "slot": slot,
                    "blockhash": raw_row["blockhash"],
                    "event_time_unix_seconds": raw_row["event_time_unix_seconds"],
                    "full_receipt_ns": raw_row["full_receipt_ns"],
                    "signature_receipt_ns": raw_row["signature_receipt_ns"],
                    "availability_ns": raw_row["availability_ns"],
                    "full_transaction_count": raw_row["provider_full_transaction_count"],
                    "signature_count": raw_row["provider_signature_count"],
                    "expected_outer_instruction_references": raw_row[
                        "outer_instruction_references"
                    ],
                    "expected_inner_instruction_references": raw_row[
                        "inner_instruction_references"
                    ],
                    "expected_program_unique_transaction_count": target_count,
                    "structural_novelty_instruction_references": novelty_row.get(
                        "pumpswap_instruction_references"
                    ),
                    "full_capture": captures["full"],
                    "signature_capture": captures["signatures"],
                }
            )
    if len(rows_out) != 608:
        raise FeasibilityError("selected_slot_total_mismatch", str(len(rows_out)))
    rows_out.sort(key=lambda r: (r["dataset_id"], r["slot"]))
    plan_pin_set = {(p["path"], p["sha256"]) for p in manifest_pins}
    if not set(
        (p["path"], p["sha256"])
        for d in datasets
        for p in _selected_from_metadata(root, d["dataset_id"], d)[1]
    ).issubset(plan_pin_set):
        raise FeasibilityError("selection_metadata_not_in_projection_pins")
    stage = {
        "schema": STAGE1_SCHEMA,
        "stage": "frozen_before_selected_block_body_scan",
        "network": NETWORK,
        "target_program_id": PROGRAM_ID,
        "create_pool_instruction_discriminator_hex": CREATE_POOL_IX.hex(),
        "create_pool_event_discriminator_hex": CREATE_POOL_EVENT.hex(),
        "selection": {
            "pair64_slots": 512,
            "c3_frames": list(FRAME_IDS),
            "c3_slots_per_frame": 16,
            "selected_capture_rows": 608,
        },
        "inputs": {
            "ledger": {
                "path": str(LEDGER_PATH),
                "sha256": ledger_sha,
                "reproducible_content_sha256": ledger["reproducible_content_sha256"],
            },
            "structural_novelty": {"path": str(NOVELTY_PATH), "sha256": novelty_sha},
            "exposure_projection": {
                "path": str(PROJECTION_PATH),
                "sha256": projection_sha,
                "input_ledger_sha256": projection["input_ledger_sha256"],
            },
            "metadata_manifest_pins": manifest_pins,
            "selection_metadata_pins": [
                p for d in datasets for p in _selected_from_metadata(root, d["dataset_id"], d)[1]
            ],
        },
        "historical_source_pins": source_pins,
        "implementation_baseline_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "historical_idl_profile_provenance": [
            {
                "profile_sha256": p["profile_sha"],
                "event_discriminator_hex": bytes(p["discriminator"]).hex(),
                "idl_commits": [v["commit"] for v in p["provenance"]],
                "idl_sha256": [v["idl_sha"] for v in p["provenance"]],
            }
            for p in PROFILES
        ],
        "implementation_source_hashes": source_files,
        "dataset_expected_totals": dataset_totals,
        "selected_rows": rows_out,
        "decision_time_ns": None,
        "claim_boundary": (
            "attempt_and_structural_feasibility_only; "
            "no birth_population_admission_or_economic_claim"
        ),
    }
    stage["content_sha256"] = _sha(_canonical(stage))
    return stage


def freeze_stage1(output: Path, root: Path = ROOT) -> dict[str, Any]:
    output = output if output.is_absolute() else root / output
    stage = build_stage1(root)
    encoded = _canonical(stage) + b"\n"
    _write_create_only(output, encoded, root=root)
    return stage


def load_stage1(path: Path, root: Path = ROOT) -> dict[str, Any]:
    stage, raw = _json(path if path.is_absolute() else root / path)
    if stage.get("schema") != STAGE1_SCHEMA:
        raise FeasibilityError("stage1_schema_invalid")
    claimed = stage.get("content_sha256")
    body = dict(stage)
    body.pop("content_sha256", None)
    if (
        claimed != _sha(_canonical(body))
        or stage.get("stage") != "frozen_before_selected_block_body_scan"
    ):
        raise FeasibilityError("stage1_content_hash_or_order_invalid")
    if (
        stage.get("network") != NETWORK
        or stage.get("selection", {}).get("selected_capture_rows") != 608
    ):
        raise FeasibilityError("stage1_scope_invalid")
    for rel, expected in stage.get("implementation_source_hashes", {}).items():
        if _file_sha(root / rel) != expected:
            raise FeasibilityError("stage1_source_hash_changed", rel)
    return stage


def _read_capture(root: Path, capture: dict[str, Any], zstd: str) -> Any:
    receipt_path = root / capture["receipt_path"]
    receipt, raw_receipt = _json(receipt_path)
    if (
        _sha(raw_receipt) != capture["receipt_sha256"]
        or receipt.get("response_sha256") != capture["response_sha256"]
        or receipt.get("received_ns") != capture["receipt_ns"]
        or receipt.get("availability_ns") != capture["availability_ns"]
    ):
        raise FeasibilityError("capture_receipt_changed", capture["receipt_path"])
    try:
        request = json.loads(receipt["request_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise FeasibilityError("capture_request_json_invalid", capture["receipt_path"]) from exc
    params = request.get("params")
    if (
        not isinstance(params, list)
        or len(params) < 2
        or _sha(_canonical(request)) != capture["request_json_sha256"]
        or request.get("method") != "getBlock"
        or params[0] != capture["slot"]
        or not isinstance(params[1], dict)
        or params[1].get("transactionDetails") != capture["details"]
        or receipt.get("source_alias") != "helius-free"
    ):
        raise FeasibilityError("capture_request_binding_mismatch", capture["receipt_path"])
    stored_path = receipt_path.parent / receipt["storage"]["path"]
    if not stored_path.resolve().is_relative_to((root / "work").resolve()):
        raise FeasibilityError("stored_capture_outside_work", capture["receipt_path"])
    compressed = stored_path.read_bytes()
    if len(compressed) != capture["stored_bytes"] or _sha(compressed) != capture["stored_sha256"]:
        raise FeasibilityError("stored_capture_changed", capture["receipt_path"])
    proc = subprocess.run([zstd, "-dc"], input=compressed, capture_output=True, check=False)
    if proc.returncode:
        raise FeasibilityError("zstd_decompression_failed", capture["receipt_path"])
    if (
        len(proc.stdout) != capture["response_bytes"]
        or _sha(proc.stdout) != capture["response_sha256"]
    ):
        raise FeasibilityError("raw_capture_changed", capture["receipt_path"])
    try:
        wrapper = json.loads(proc.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FeasibilityError("raw_capture_json_invalid", capture["receipt_path"]) from exc
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("result"), (dict, list)):
        raise FeasibilityError("raw_capture_result_shape", capture["receipt_path"])
    return wrapper["result"]


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _independent_b58(value: Any) -> bytes:
    if not isinstance(value, str) or len(value) > 12000:
        raise FeasibilityError("independent_base58_shape")
    if not value:
        return b""
    number = 0
    for char in value:
        try:
            digit = _B58.index(char)
        except ValueError as exc:
            raise FeasibilityError("independent_base58_character") from exc
        number = number * 58 + digit
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + body


def _independent_resolve_instruction(
    raw_ix: Any, keys: list[str], path: list[int], depth: int
) -> dict[str, Any]:
    if not isinstance(raw_ix, dict):
        raise FeasibilityError("independent_instruction_shape")
    program_index, account_indices, data = (
        raw_ix.get("programIdIndex"),
        raw_ix.get("accounts"),
        raw_ix.get("data"),
    )
    if type(program_index) is not int or not 0 <= program_index < len(keys):
        raise FeasibilityError("independent_program_index")
    if not isinstance(account_indices, list) or any(
        type(i) is not int or i < 0 or i >= len(keys) for i in account_indices
    ):
        raise FeasibilityError("independent_account_index")
    decoded = _independent_b58(data)
    return {
        "path": path,
        "stack_height": depth,
        "program_id": keys[program_index],
        "account_indexes": account_indices,
        "accounts": [keys[i] for i in account_indices],
        "data_hex": decoded.hex(),
    }


def _independent_resolve_transaction(tx: Any) -> dict[str, Any]:
    """Independent raw JSON account/index walker used by the verifier, not the ledger normalizer."""
    if not isinstance(tx, dict):
        raise FeasibilityError("independent_transaction_shape")
    version = tx.get("version")
    if version != "legacy" and (type(version) is not int or version not in (0, 1)):
        raise FeasibilityError("independent_transaction_version")
    raw_tx, meta = tx.get("transaction"), tx.get("meta")
    if (
        not isinstance(raw_tx, dict)
        or not isinstance(raw_tx.get("message"), dict)
        or not isinstance(meta, dict)
    ):
        raise FeasibilityError("independent_message_or_meta_missing")
    message = raw_tx["message"]
    raw_keys = message.get("accountKeys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise FeasibilityError("independent_static_keys_missing")
    keys = []
    for item in raw_keys:
        key = item.get("pubkey") if isinstance(item, dict) else item
        if not isinstance(key, str) or len(_independent_b58(key)) != 32:
            raise FeasibilityError("independent_static_key_invalid")
        keys.append(key)
    lookups = message.get("addressTableLookups", [])
    if not isinstance(lookups, list):
        raise FeasibilityError("independent_lookup_shape")
    writable_indexes, readonly_indexes = [], []
    for lookup in lookups:
        if not isinstance(lookup, dict):
            raise FeasibilityError("independent_lookup_shape")
        table = lookup.get("accountKey")
        if not isinstance(table, str) or len(_independent_b58(table)) != 32:
            raise FeasibilityError("independent_lookup_key_invalid")
        writable = lookup.get("writableIndexes")
        readonly = lookup.get("readonlyIndexes")
        if (
            not isinstance(writable, list)
            or not isinstance(readonly, list)
            or any(type(i) is not int or not 0 <= i <= 255 for i in writable + readonly)
        ):
            raise FeasibilityError("independent_lookup_indexes_invalid")
        writable_indexes.extend(writable)
        readonly_indexes.extend(readonly)
    loaded = meta.get("loadedAddresses")
    if loaded is None:
        if writable_indexes or readonly_indexes:
            raise FeasibilityError("independent_loaded_addresses_missing")
        loaded = {"writable": [], "readonly": []}
    if not isinstance(loaded, dict):
        raise FeasibilityError("independent_loaded_addresses_shape")
    writable, readonly = loaded.get("writable"), loaded.get("readonly")
    if (
        not isinstance(writable, list)
        or not isinstance(readonly, list)
        or len(writable) != len(writable_indexes)
        or len(readonly) != len(readonly_indexes)
    ):
        raise FeasibilityError("independent_loaded_address_count")
    for key in writable + readonly:
        if not isinstance(key, str) or len(_independent_b58(key)) != 32:
            raise FeasibilityError("independent_loaded_address_invalid")
    keys.extend(writable)
    keys.extend(readonly)
    outer_raw = message.get("instructions")
    if not isinstance(outer_raw, list):
        raise FeasibilityError("independent_outer_instructions_missing")
    outer = [_independent_resolve_instruction(ix, keys, [i], 1) for i, ix in enumerate(outer_raw)]
    groups = meta.get("innerInstructions")
    if not isinstance(groups, list):
        raise FeasibilityError("independent_inner_trace_missing")
    seen: set[int] = set()
    inner = []
    for group in groups:
        if not isinstance(group, dict):
            raise FeasibilityError("independent_inner_group_shape")
        outer_index, instructions = group.get("index"), group.get("instructions")
        if (
            type(outer_index) is not int
            or not 0 <= outer_index < len(outer)
            or outer_index in seen
            or not isinstance(instructions, list)
        ):
            raise FeasibilityError("independent_inner_group_invalid")
        seen.add(outer_index)
        stack = [1]
        for ordinal, raw_ix in enumerate(instructions):
            depth = raw_ix.get("stackHeight") if isinstance(raw_ix, dict) else None
            if type(depth) is not int or depth < 2:
                raise FeasibilityError("independent_inner_stack_height_missing")
            while stack and stack[-1] >= depth:
                stack.pop()
            if not stack or depth != stack[-1] + 1:
                raise FeasibilityError("independent_inner_stack_height_gap")
            stack.append(depth)
            inner.append(
                _independent_resolve_instruction(raw_ix, keys, [outer_index, ordinal], depth)
            )
    return {
        "outer_instructions": outer,
        "inner_instructions": inner,
        "accounts": [{"index": i, "pubkey": key} for i, key in enumerate(keys)],
        "transaction_signatures": raw_tx.get("signatures"),
    }


def _independent_reenumerate(stage1: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    zstd = shutil.which("zstd")
    if not zstd:
        raise FeasibilityError("zstd_executable_missing")
    model = {
        "candidates_by_dataset": defaultdict(list),
        "slot_rows": {},
        "dataset_totals": {},
        "outcomes": Counter(),
        "cross_dataset": [],
    }
    all_candidates = []
    for row in stage1["selected_rows"]:
        dataset_id, slot = row["dataset_id"], row["slot"]
        full = _read_capture(root, row["full_capture"], zstd)
        signatures = _read_capture(root, row["signature_capture"], zstd)
        txs, sigs = full.get("transactions"), signatures.get("signatures")
        if (
            full.get("blockhash") != row["blockhash"]
            or signatures.get("blockhash") != row["blockhash"]
            or not isinstance(txs, list)
            or not isinstance(sigs, list)
        ):
            raise FeasibilityError("independent_block_binding_mismatch", f"{dataset_id}:{slot}")
        if full.get("blockTime") != row["event_time_unix_seconds"]:
            raise FeasibilityError("independent_block_time_mismatch", f"{dataset_id}:{slot}")
        if (
            len(txs) != len(sigs)
            or len(txs) != row["full_transaction_count"]
            or len(sigs) != row["signature_count"]
        ):
            raise FeasibilityError(
                "independent_transaction_denominator_mismatch", f"{dataset_id}:{slot}"
            )
        counts = Counter()
        signature_set: set[str] = set()
        pamm_signatures: set[str] = set()
        candidate_items: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        counts["transactions"] = len(txs)
        counts["missing_block_time"] = int(full.get("blockTime") is None)
        for position, tx in enumerate(txs):
            try:
                sigs_raw = tx["transaction"]["signatures"]
            except (TypeError, KeyError) as exc:
                raise FeasibilityError(
                    "independent_full_signature_missing", f"{dataset_id}:{slot}:{position}"
                ) from exc
            if not isinstance(sigs_raw, list) or not sigs_raw or sigs_raw[0] != sigs[position]:
                raise FeasibilityError(
                    "independent_ordered_signature_mismatch", f"{dataset_id}:{slot}:{position}"
                )
            signature = sigs_raw[0]
            if signature in signature_set:
                raise FeasibilityError("independent_duplicate_signature", f"{dataset_id}:{slot}")
            signature_set.add(signature)
            meta = tx.get("meta") if isinstance(tx, dict) else None
            if not isinstance(meta, dict) or "err" not in meta:
                status = "unknown"
                counts["unknown_transaction_status"] += 1
            elif meta.get("err") is None:
                status = "success"
                counts["successful_transactions"] += 1
            else:
                status = "failed"
                counts["failed_transactions"] += 1
            try:
                normalized = _independent_resolve_transaction(tx)
            except FeasibilityError as exc:
                counts["normalization_failures"] += 1
                counts[f"normalization_failure:{exc.code}"] += 1
                continue
            outer, inner = normalized["outer_instructions"], normalized["inner_instructions"]
            counts["outer_instruction_references"] += len(outer)
            counts["inner_instruction_references"] += len(inner)
            if normalized["transaction_signatures"] != sigs_raw:
                raise FeasibilityError("independent_signature_projection_mismatch")
            for ix, path_kind in [(i, "outer") for i in outer] + [(i, "inner") for i in inner]:
                if ix["program_id"] != PROGRAM_ID:
                    continue
                counts["program_instruction_references"] += 1
                pamm_signatures.add(signature)
                data = bytes.fromhex(ix["data_hex"])
                if len(data) < 8:
                    counts["program_data_short"] += 1
                    continue
                if data[:8] != CREATE_POOL_IX:
                    continue
                identity = (NETWORK, slot, row["blockhash"], signature, tuple(ix["path"]))
                candidate = {
                    "network": NETWORK,
                    "dataset_id": dataset_id,
                    "slot": slot,
                    "blockhash": row["blockhash"],
                    "signature": signature,
                    "path": ix["path"],
                    "path_kind": path_kind,
                    "transaction_status": status,
                    "instruction_data_length": len(data),
                    "instruction_data_sha256": _sha(data),
                    "account_count": len(ix["accounts"]),
                    "accounts": ix["accounts"],
                }
                child_map = _children_by_parent(normalized)
                candidate["assessment"] = _classify_candidate(
                    tx, normalized, {"path": ix["path"], "instruction": ix}, child_map, status
                )
                candidate_items.append((candidate, tx, normalized))
                counts["create_pool_discriminator_references"] += 1
                if status == "failed":
                    counts["create_pool_failed_attempts"] += 1
        if len(signature_set) != len(sigs):
            raise FeasibilityError("independent_signature_set_mismatch", f"{dataset_id}:{slot}")
        counts["program_unique_transaction_count"] = len(pamm_signatures)
        for candidate, _, _ in candidate_items:
            model["candidates_by_dataset"][dataset_id].append(candidate)
            all_candidates.append(candidate)
            class_name = candidate["assessment"]["feasibility_class"]
            model["outcomes"][class_name] += 1
            counts[f"candidate_outcome:{class_name}"] += 1
            counts[f"candidate_profile:{candidate['assessment']['historical_profile_status']}"] += 1
            counts[f"candidate_event_cpi:{candidate['assessment']['event_cpi_status']}"] += 1
        model["slot_rows"][(dataset_id, slot)] = {
            "slot": slot,
            "blockhash": row["blockhash"],
            "block_time_unix_seconds": full.get("blockTime"),
            "receipt_ns": row["full_receipt_ns"],
            "signature_receipt_ns": row["signature_receipt_ns"],
            "availability_ns": row["availability_ns"],
            "decision_time_ns": None,
            "full_capture_receipt_path": row["full_capture"]["receipt_path"],
            "full_capture_receipt_sha256": row["full_capture"]["receipt_sha256"],
            "full_response_sha256": row["full_capture"]["response_sha256"],
            "full_stored_sha256": row["full_capture"]["stored_sha256"],
            "signature_capture_receipt_path": row["signature_capture"]["receipt_path"],
            "signature_capture_receipt_sha256": row["signature_capture"]["receipt_sha256"],
            "signature_response_sha256": row["signature_capture"]["response_sha256"],
            "signature_stored_sha256": row["signature_capture"]["stored_sha256"],
            "transaction_count": len(txs),
            "signature_count": len(sigs),
            "selected_capture_status": "full_and_ordered_signatures_verified",
            "expected_program_unique_transaction_count": row[
                "expected_program_unique_transaction_count"
            ],
            "program_unique_transaction_count": len(pamm_signatures),
            "program_unique_transaction_count_matches_ledger": len(pamm_signatures)
            == row["expected_program_unique_transaction_count"],
            "structural_novelty_instruction_references": row[
                "structural_novelty_instruction_references"
            ],
            "program_references_match_structural_novelty": (
                counts["program_instruction_references"]
                == row["structural_novelty_instruction_references"]
            ),
            "counts": dict(sorted(counts.items())),
            "selected_discriminator_zero_permitted": (
                counts["create_pool_discriminator_references"] == 0
                and counts["normalization_failures"] == 0
                and counts["unknown_transaction_status"] == 0
                and len(pamm_signatures) == row["expected_program_unique_transaction_count"]
                and counts["program_instruction_references"]
                == row["structural_novelty_instruction_references"]
            ),
        }
    for dataset_id in EXPECTED_DATASETS:
        slots = [
            model["slot_rows"][(dataset_id, row["slot"])]
            for row in stage1["selected_rows"]
            if row["dataset_id"] == dataset_id
        ]
        total = Counter()
        for slot_row in slots:
            total.update(slot_row["counts"])
        total["structural_novelty_instruction_references"] = stage1["dataset_expected_totals"][
            dataset_id
        ]["structural_novelty_instruction_references"]
        model["dataset_totals"][dataset_id] = dict(sorted(total.items()))
    identity_map: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for candidate in all_candidates:
        identity = (
            NETWORK,
            candidate["slot"],
            candidate["blockhash"],
            candidate["signature"],
            tuple(candidate["path"]),
        )
        identity_map[identity].add(candidate["dataset_id"])
    model["cross_dataset"] = [
        {
            "identity": [*identity[:4], list(identity[4])],
            "dataset_ids": sorted(datasets),
            "observation_count": len(datasets),
        }
        for identity, datasets in sorted(identity_map.items())
        if len(datasets) > 1
    ]
    model["duplicate_identity_count_within_capture"] = 0
    return model


def _parse_fixed_fields(data: bytes, layout: list[dict[str, Any]]) -> dict[str, Any]:
    sizes = {"u8": 1, "u16": 2, "u64": 8, "i64": 8, "bool": 1, "pubkey": 32}
    offset, values = 0, {}
    for field in layout:
        kind = field["type"]
        size = sizes.get(kind)
        if size is None or offset + size > len(data):
            raise ValueError("truncated_or_unsupported_field")
        part = data[offset : offset + size]
        if kind == "pubkey":
            value = base58_encode(part)
        else:
            value = int.from_bytes(part, "little", signed=(kind == "i64"))
            if kind == "bool" and value not in (0, 1):
                raise ValueError("invalid_boolean")
        values[field["name"]] = value
        offset += size
    if offset != len(data):
        raise ValueError("unknown_suffix")
    return values


def base58_encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, digit = divmod(n, 58)
        out = alphabet[digit] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def _matching_profiles(ix_data: bytes, accounts: list[str]) -> list[dict[str, Any]]:
    if not ix_data.startswith(CREATE_POOL_IX):
        return []
    matches = []
    for profile in PROFILES:
        if len(accounts) != len(profile["accounts"]):
            continue
        try:
            args = _parse_fixed_fields(ix_data[len(CREATE_POOL_IX) :], profile["args"])
        except ValueError:
            continue
        matches.append({"profile": profile, "args": args})
    return matches


def _event_fields(event_data: bytes, profile: dict[str, Any]) -> dict[str, Any]:
    prefix = EVENT_TAG + CREATE_POOL_EVENT
    if not event_data.startswith(prefix):
        raise ValueError("event_discriminator_mismatch")
    return _parse_fixed_fields(event_data[len(prefix) :], profile["fields"])


def _classify_candidate(
    tx: dict[str, Any],
    normalized: dict[str, Any],
    candidate: dict[str, Any],
    children_by_parent: dict[tuple[int, ...], list[dict[str, Any]]],
    status: str,
) -> dict[str, Any]:
    ix = candidate["instruction"]
    accounts = ix["accounts"]
    ix_data = bytes.fromhex(ix["data_hex"])
    profiles = _matching_profiles(ix_data, accounts)
    assessment: dict[str, Any] = {
        "historical_profile_status": "no_exact_profile",
        "historical_profile_sha256": None,
        "account_role_count": len(accounts),
        "expected_account_role_count": len(PROFILES[0]["accounts"]),
        "event_cpi_status": "not_assessed_without_unique_profile",
        "pool_mint_vault_tuple_status": "unknown",
        "transaction_boundary_status": (
            "slot_blockhash_signature_and_status_bound"
            if status != "unknown"
            else "transaction_status_unknown"
        ),
        "feasibility_class": "unsupported_or_malformed_layout",
    }
    if len(profiles) == 1:
        profile = profiles[0]["profile"]
        args = profiles[0]["args"]
        assessment["historical_profile_status"] = "exact_historical_profile"
        assessment["historical_profile_sha256"] = profile["profile_sha"]
        roles = dict(zip(profile["accounts"], accounts, strict=True))
        assessment["roles"] = roles
        event_candidates = []
        for child in children_by_parent.get(tuple(candidate["path"]), []):
            child_ix = child["instruction"]
            if child_ix["program_id"] == PROGRAM_ID and bytes.fromhex(
                child_ix["data_hex"]
            ).startswith(EVENT_TAG + CREATE_POOL_EVENT):
                event_candidates.append(child_ix)
        assessment["event_cpi_count"] = len(event_candidates)
        if len(event_candidates) != 1:
            assessment["event_cpi_status"] = "missing" if not event_candidates else "duplicate"
        elif event_candidates[0]["accounts"] != [EVENT_AUTHORITY]:
            assessment["event_cpi_status"] = "event_authority_role_mismatch"
        else:
            try:
                event = _event_fields(bytes.fromhex(event_candidates[0]["data_hex"]), profile)
            except ValueError as exc:
                assessment["event_cpi_status"] = f"malformed:{exc}"
            else:
                assessment["event_cpi_status"] = "uniquely_bound_event_cpi"
                assessment["event_role_checks"] = {
                    name: event.get(name) == roles.get(name)
                    for name in (
                        "creator",
                        "base_mint",
                        "quote_mint",
                        "lp_mint",
                        "user_base_token_account",
                        "user_quote_token_account",
                        "pool",
                    )
                }
                if "coin_creator" in args and "coin_creator" in event:
                    assessment["event_role_checks"]["coin_creator"] = (
                        args["coin_creator"] == event["coin_creator"]
                    )
                fixed_roles_ok = (
                    roles.get("program") == PROGRAM_ID
                    and roles.get("event_authority") == EVENT_AUTHORITY
                    and roles.get("global_config") == GLOBAL_CONFIG
                )
                event_roles_ok = fixed_roles_ok and all(assessment["event_role_checks"].values())
                assessment["event_roles_consistent"] = event_roles_ok
                tuple_status = _vault_tuple_status(tx, normalized, roles, event)
                assessment["pool_mint_vault_tuple_status"] = tuple_status
                assessment["pool_mint_vault_tuple_consistent"] = (
                    tuple_status == "consistent"
                    and len({roles.get("base_mint"), roles.get("quote_mint")}) == 2
                    and len(
                        {
                            roles.get("pool_base_token_account"),
                            roles.get("pool_quote_token_account"),
                        }
                    )
                    == 2
                )
                assessment["event_fields"] = {
                    key: event.get(key)
                    for key in (
                        "pool",
                        "creator",
                        "base_mint",
                        "quote_mint",
                        "lp_mint",
                        "base_mint_decimals",
                        "quote_mint_decimals",
                    )
                }
                assessment["instruction_args"] = {
                    k: v for k, v in args.items() if k in {"index", "coin_creator"}
                }
                if status == "failed":
                    assessment["feasibility_class"] = "failed_transaction_attempt_only"
                elif (
                    status == "success"
                    and event_roles_ok
                    and assessment["pool_mint_vault_tuple_consistent"]
                ):
                    assessment["feasibility_class"] = "successful_structural_feasibility_candidate"
                elif status == "unknown":
                    assessment["feasibility_class"] = "transaction_status_unknown"
                else:
                    assessment["feasibility_class"] = "role_or_tuple_inconsistent_or_unknown"
    elif len(profiles) > 1:
        assessment["historical_profile_status"] = "ambiguous_profiles"
    else:
        if len(accounts) != assessment["expected_account_role_count"]:
            assessment["historical_profile_status"] = "account_role_count_unsupported"
        elif len(ix_data) < len(CREATE_POOL_IX) + 1:
            assessment["historical_profile_status"] = "truncated_instruction_data"
        else:
            assessment["historical_profile_status"] = "unknown_historical_layout_or_suffix"
    if status == "failed":
        assessment["feasibility_class"] = "failed_transaction_attempt_only"
    elif (
        status == "unknown" and assessment["feasibility_class"] != "failed_transaction_attempt_only"
    ):
        assessment["feasibility_class"] = "transaction_status_unknown"
    return assessment


def _vault_tuple_status(
    tx: dict[str, Any], normalized: dict[str, Any], roles: dict[str, str], event: dict[str, Any]
) -> str:
    """Compare only tuple identity in transaction metadata; never infer token movement or birth."""
    if (
        roles.get("pool") is None
        or roles.get("base_mint") != event.get("base_mint")
        or roles.get("quote_mint") != event.get("quote_mint")
    ):
        return "role_mint_mismatch"
    accounts = normalized.get("accounts")
    meta = tx.get("meta")
    if not isinstance(accounts, list) or not isinstance(meta, dict):
        return "missing_account_context"
    key_to_index = {x.get("pubkey"): x.get("index") for x in accounts if isinstance(x, dict)}
    post = meta.get("postTokenBalances")
    if not isinstance(post, list):
        return "post_token_balances_missing"
    by_index = {}
    for balance in post:
        if not isinstance(balance, dict) or type(balance.get("accountIndex")) is not int:
            return "post_token_balance_malformed"
        idx = balance["accountIndex"]
        if idx in by_index:
            return "post_token_balance_duplicate_index"
        by_index[idx] = balance
    for side in ("base", "quote"):
        vault = roles.get(f"pool_{side}_token_account")
        mint = roles.get(f"{side}_mint")
        idx = key_to_index.get(vault)
        balance = by_index.get(idx)
        if idx is None or not isinstance(balance, dict):
            return "vault_balance_missing"
        if balance.get("mint") != mint:
            return "vault_mint_mismatch"
        amount = balance.get("uiTokenAmount")
        if (
            not isinstance(amount, dict)
            or type(amount.get("decimals")) is not int
            or not 0 <= amount["decimals"] <= 255
        ):
            return "vault_decimals_missing_or_invalid"
        if amount["decimals"] != event.get(f"{side}_mint_decimals"):
            return "vault_decimals_mismatch"
    return "consistent"


def scan_stage1(stage1: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    if build_stage1(root) != stage1:
        raise FeasibilityError("stage1_input_receipts_changed")
    zstd = shutil.which("zstd")
    if not zstd:
        raise FeasibilityError("zstd_executable_missing")
    rows_by_dataset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    slot_output_rows: dict[tuple[str, int], dict[str, Any]] = {}
    candidates_by_dataset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dataset_counts: dict[str, Counter[str]] = defaultdict(Counter)
    dataset_refs: dict[str, Counter[str]] = defaultdict(Counter)
    identity_seen: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    duplicate_candidate_ids: dict[str, int] = Counter()
    for row in stage1["selected_rows"]:
        dataset_id, slot = row["dataset_id"], row["slot"]
        full = _read_capture(root, row["full_capture"], zstd)
        signatures = _read_capture(root, row["signature_capture"], zstd)
        if not isinstance(full, dict) or not isinstance(signatures, dict):
            raise FeasibilityError("block_capture_result_not_object", f"{dataset_id}:{slot}")
        if (
            full.get("blockhash") != row["blockhash"]
            or signatures.get("blockhash") != row["blockhash"]
        ):
            raise FeasibilityError("blockhash_receipt_mismatch", f"{dataset_id}:{slot}")
        txs, sigs = full.get("transactions"), signatures.get("signatures")
        if not isinstance(txs, list) or not isinstance(sigs, list):
            raise FeasibilityError(
                "block_transaction_or_signature_list_missing", f"{dataset_id}:{slot}"
            )
        if len(txs) != row["full_transaction_count"] or len(sigs) != row["signature_count"]:
            raise FeasibilityError("block_signature_denominator_mismatch", f"{dataset_id}:{slot}")
        if len(txs) != len(sigs):
            raise FeasibilityError("block_signature_count_mismatch", f"{dataset_id}:{slot}")
        slot_count = Counter()
        per_sig: set[str] = set()
        slot_program_signatures: set[str] = set()
        block_time = full.get("blockTime")
        if block_time != row["event_time_unix_seconds"]:
            raise FeasibilityError("block_time_receipt_mismatch", f"{dataset_id}:{slot}")
        slot_count["missing_block_time"] += int(block_time is None)
        for tx_position, tx in enumerate(txs):
            slot_count["transactions"] += 1
            try:
                raw_signatures = tx["transaction"]["signatures"]
            except (KeyError, TypeError) as exc:
                raise FeasibilityError(
                    "full_transaction_signature_missing", f"{dataset_id}:{slot}:{tx_position}"
                ) from exc
            if (
                not isinstance(raw_signatures, list)
                or not raw_signatures
                or raw_signatures[0] != sigs[tx_position]
            ):
                raise FeasibilityError(
                    "ordered_signature_mismatch", f"{dataset_id}:{slot}:{tx_position}"
                )
            signature = raw_signatures[0]
            if signature in per_sig:
                raise FeasibilityError(
                    "duplicate_signature_within_selected_block", f"{dataset_id}:{slot}"
                )
            per_sig.add(signature)
            meta = tx.get("meta") if isinstance(tx, dict) else None
            if not isinstance(meta, dict) or "err" not in meta:
                status = "unknown"
                slot_count["unknown_transaction_status"] += 1
            elif meta.get("err") is None:
                status = "success"
                slot_count["successful_transactions"] += 1
            else:
                status = "failed"
                slot_count["failed_transactions"] += 1
            try:
                normalized = normalize_compiled_transaction(tx)
            except NormalizationError as exc:
                slot_count["normalization_failures"] += 1
                slot_count[f"normalization_failure:{exc.category}"] += 1
                continue
            outer, inner = normalized["outer_instructions"], normalized["inner_instructions"]
            slot_count["outer_instruction_references"] += len(outer)
            slot_count["inner_instruction_references"] += len(inner)
            normalized_signatures = normalized.get("transaction_signatures")
            if normalized_signatures != raw_signatures:
                raise FeasibilityError(
                    "normalizer_signature_projection_mismatch", f"{dataset_id}:{slot}:{tx_position}"
                )
            all_ixs = [(ix, "outer") for ix in outer] + [(ix, "inner") for ix in inner]
            for ix, path_kind in all_ixs:
                if ix.get("program_id") != PROGRAM_ID:
                    continue
                slot_count["program_instruction_references"] += 1
                dataset_refs[dataset_id][signature] += 1
                slot_program_signatures.add(signature)
                data = bytes.fromhex(ix["data_hex"])
                if len(data) < 8:
                    slot_count["program_data_short"] += 1
                    continue
                if data[:8] != CREATE_POOL_IX:
                    continue
                identity = (NETWORK, slot, row["blockhash"], signature, tuple(ix["path"]))
                if identity in identity_seen[dataset_id]:
                    duplicate_candidate_ids[dataset_id] += 1
                identity_seen[dataset_id].add(identity)
                candidate = {
                    "network": NETWORK,
                    "dataset_id": dataset_id,
                    "slot": slot,
                    "blockhash": row["blockhash"],
                    "signature": signature,
                    "path": ix["path"],
                    "path_kind": path_kind,
                    "transaction_status": status,
                    "instruction_data_length": len(data),
                    "instruction_data_sha256": _sha(data),
                    "account_count": len(ix["accounts"]),
                    "accounts": ix["accounts"],
                }
                candidates_by_dataset[dataset_id].append((candidate, tx, normalized, ix))
                slot_count["create_pool_discriminator_references"] += 1
                if status == "failed":
                    slot_count["create_pool_failed_attempts"] += 1
        if len(per_sig) != len(sigs):
            raise FeasibilityError("full_signature_set_count_mismatch", f"{dataset_id}:{slot}")
        slot_count["program_unique_transaction_count"] = len(slot_program_signatures)
        slot_program_unique_matches = (
            len(slot_program_signatures) == row["expected_program_unique_transaction_count"]
        )
        slot_novelty_reference_match = (
            slot_count["program_instruction_references"]
            == row["structural_novelty_instruction_references"]
        )
        for key, value in slot_count.items():
            dataset_counts[dataset_id][key] += value
        rows_by_dataset[dataset_id].append(
            {
                "slot": slot,
                "blockhash": row["blockhash"],
                "block_time_unix_seconds": block_time,
                "receipt_ns": row["full_receipt_ns"],
                "signature_receipt_ns": row["signature_receipt_ns"],
                "availability_ns": row["availability_ns"],
                "decision_time_ns": None,
                "full_capture_receipt_path": row["full_capture"]["receipt_path"],
                "full_capture_receipt_sha256": row["full_capture"]["receipt_sha256"],
                "full_response_sha256": row["full_capture"]["response_sha256"],
                "full_stored_sha256": row["full_capture"]["stored_sha256"],
                "signature_capture_receipt_path": row["signature_capture"]["receipt_path"],
                "signature_capture_receipt_sha256": row["signature_capture"]["receipt_sha256"],
                "signature_response_sha256": row["signature_capture"]["response_sha256"],
                "signature_stored_sha256": row["signature_capture"]["stored_sha256"],
                "transaction_count": len(txs),
                "signature_count": len(sigs),
                "selected_capture_status": "full_and_ordered_signatures_verified",
                "expected_program_unique_transaction_count": row[
                    "expected_program_unique_transaction_count"
                ],
                "program_unique_transaction_count": len(slot_program_signatures),
                "program_unique_transaction_count_matches_ledger": slot_program_unique_matches,
                "structural_novelty_instruction_references": row[
                    "structural_novelty_instruction_references"
                ],
                "program_references_match_structural_novelty": slot_novelty_reference_match,
                "counts": dict(sorted(slot_count.items())),
                "selected_discriminator_zero_permitted": (
                    slot_count["create_pool_discriminator_references"] == 0
                    and slot_count["normalization_failures"] == 0
                    and slot_count["unknown_transaction_status"] == 0
                    and slot_program_unique_matches
                ),
            }
        )
        slot_output_rows[(dataset_id, slot)] = rows_by_dataset[dataset_id][-1]
    all_candidates = []
    for dataset_id, items in candidates_by_dataset.items():
        for candidate, tx, normalized, ix in items:
            # Reconstruct inner stack parent links from runtime stack heights for event binding.
            children = _children_by_parent(normalized)
            candidate["assessment"] = _classify_candidate(
                tx,
                normalized,
                {"path": candidate["path"], "instruction": ix},
                children,
                candidate["transaction_status"],
            )
            all_candidates.append(candidate)
            per_slot_counts = slot_output_rows[(dataset_id, candidate["slot"])]["counts"]
            class_name = candidate["assessment"]["feasibility_class"]
            profile_name = candidate["assessment"]["historical_profile_status"]
            event_name = candidate["assessment"]["event_cpi_status"]
            per_slot_counts[f"candidate_outcome:{class_name}"] = (
                per_slot_counts.get(f"candidate_outcome:{class_name}", 0) + 1
            )
            per_slot_counts[f"candidate_profile:{profile_name}"] = (
                per_slot_counts.get(f"candidate_profile:{profile_name}", 0) + 1
            )
            per_slot_counts[f"candidate_event_cpi:{event_name}"] = (
                per_slot_counts.get(f"candidate_event_cpi:{event_name}", 0) + 1
            )
            dataset_counts[dataset_id][f"candidate_outcome:{class_name}"] += 1
            dataset_counts[dataset_id][f"candidate_profile:{profile_name}"] += 1
            dataset_counts[dataset_id][f"candidate_event_cpi:{event_name}"] += 1
    duplicate_identity_counts: Counter[tuple[Any, ...]] = Counter()
    dataset_by_identity: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for item in all_candidates:
        identity = (
            item["network"],
            item["slot"],
            item["blockhash"],
            item["signature"],
            tuple(item["path"]),
        )
        duplicate_identity_counts[identity] += 1
        dataset_by_identity[identity].add(item["dataset_id"])
    cross_dataset = [
        {
            "identity": [*identity[:4], list(identity[4])],
            "dataset_ids": sorted(dataset_by_identity[identity]),
            "observation_count": duplicate_identity_counts[identity],
        }
        for identity in sorted(duplicate_identity_counts)
        if len(dataset_by_identity[identity]) > 1
    ]
    outcomes = Counter(candidate["assessment"]["feasibility_class"] for candidate in all_candidates)
    reconciliation = _reconcile_stage1(stage1, dataset_counts, dataset_refs)
    result = {
        "schema": RESULT_SCHEMA,
        "stage1_sha256": stage1["content_sha256"],
        "status": "verified_candidate_feasibility_only"
        if reconciliation["denominator_match"]
        else "partial_denominator_mismatch",
        "network": NETWORK,
        "target_program_id": PROGRAM_ID,
        "instruction_discriminator_hex": CREATE_POOL_IX.hex(),
        "event_discriminator_hex": CREATE_POOL_EVENT.hex(),
        "scope": "exactly_608_existing_selected_full_blocks_and_ordered_signature_lists",
        "dataset_order": list(EXPECTED_DATASETS),
        "zero_claim_scope": (
            "per-selected-block zero only for this fixed create_pool instruction discriminator "
            "when full capture/signature verification and every transaction normalization/status "
            "are complete"
        ),
        "decision_time_ns": None,
        "time_fields": {
            "receipt_time": "capture receipt received_ns",
            "availability_time": "sealed receipt availability_ns or null",
            "event_time": "blockTime, kept separate",
            "decision_time_ns": None,
        },
        "stage1_provenance": {
            "inputs": stage1["inputs"],
            "historical_source_pins": stage1["historical_source_pins"],
            "implementation_source_hashes": stage1["implementation_source_hashes"],
            "historical_idl_profile_provenance": stage1["historical_idl_profile_provenance"],
            "implementation_baseline_head": stage1["implementation_baseline_head"],
        },
        "datasets": {
            dataset_id: {
                "totals": {
                    **dict(sorted(dataset_counts[dataset_id].items())),
                    "structural_novelty_instruction_references": stage1["dataset_expected_totals"][
                        dataset_id
                    ]["structural_novelty_instruction_references"],
                },
                "selected_slots": sorted(rows_by_dataset[dataset_id], key=lambda r: r["slot"]),
                "candidates": [c for c in all_candidates if c["dataset_id"] == dataset_id],
            }
            for dataset_id in EXPECTED_DATASETS
        },
        "candidate_outcome_partition": dict(sorted(outcomes.items())),
        "cross_dataset_candidate_overlaps": cross_dataset,
        "duplicate_candidate_identity_count_within_capture": sum(duplicate_candidate_ids.values()),
        "reconciliation": reconciliation,
        "claims": {
            "births": False,
            "population_completeness": False,
            "economic_validity": False,
            "admission": False,
            "settlement": False,
            "global_chain_zero": False,
        },
    }
    body = dict(result)
    result["reproducible_content_sha256"] = _sha(_canonical(body))
    encoded = _canonical(result) + b"\n"
    if len(encoded) > RESULT_CAP:
        raise FeasibilityError("result_cap_exceeded", str(len(encoded)))
    return result


def _children_by_parent(normalized: dict[str, Any]) -> dict[tuple[int, ...], list[dict[str, Any]]]:
    children: dict[tuple[int, ...], list[dict[str, Any]]] = defaultdict(list)
    outer = normalized["outer_instructions"]
    nodes = [{"path": ix["path"], "instruction": ix, "depth": 1} for ix in outer]
    for node in nodes:
        children.setdefault(tuple(node["path"]), [])
    raw_inner = normalized.get("inner_instructions", [])
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for ix in raw_inner:
        grouped[ix["path"][0]].append(ix)
    for outer_index, instructions in grouped.items():
        stack = [nodes[outer_index]]
        for ix in instructions:
            depth = ix["stack_height"]
            while stack and stack[-1]["depth"] >= depth:
                stack.pop()
            if not stack or depth != stack[-1]["depth"] + 1:
                continue
            node = {"path": ix["path"], "instruction": ix, "depth": depth}
            children[tuple(stack[-1]["path"])].append(node)
            children.setdefault(tuple(ix["path"]), [])
            stack.append(node)
    return children


def _reconcile_stage1(
    stage1: dict[str, Any], counts: dict[str, Counter[str]], refs: dict[str, Counter[str]]
) -> dict[str, Any]:
    expected = stage1["dataset_expected_totals"]
    observed = {}
    for dataset_id in EXPECTED_DATASETS:
        item = counts[dataset_id]
        expected_row = expected[dataset_id]
        observed[dataset_id] = {
            "transactions": item["transactions"],
            "outer_instruction_references": item["outer_instruction_references"],
            "inner_instruction_references": item["inner_instruction_references"],
            "program_instruction_references": item["program_instruction_references"],
            "program_unique_transactions": len(refs[dataset_id]),
            "normalization_failures": item["normalization_failures"],
            "structural_novelty_instruction_references": expected_row[
                "structural_novelty_instruction_references"
            ],
            "success_transactions": item["successful_transactions"],
            "failed_transactions": item["failed_transactions"],
            "unknown_status_transactions": item["unknown_transaction_status"],
            "matches_ledger_transactions": item["transactions"]
            == expected_row["full_block_transactions"],
            "matches_ledger_outer_refs": item["outer_instruction_references"]
            == expected_row["outer_instruction_references"],
            "matches_ledger_inner_refs": item["inner_instruction_references"]
            == expected_row["inner_instruction_references"],
            "matches_ledger_program_refs": item["program_instruction_references"]
            == expected_row["program_instruction_references"],
            "matches_ledger_program_unique_transactions": len(refs[dataset_id])
            == expected_row["program_unique_transactions"],
            "matches_ledger_status_partition": (
                item["successful_transactions"] == expected_row["success_transactions"]
                and item["failed_transactions"] == expected_row["failed_transactions"]
                and item["unknown_transaction_status"]
                == expected_row["unknown_status_transactions"]
            ),
            "matches_ledger_normalization_failures": item["normalization_failures"]
            == expected_row["unsupported_normalization"],
            "matches_structural_novelty_program_references": (
                item["program_instruction_references"]
                == expected_row["structural_novelty_instruction_references"]
            ),
        }
    all_rows = list(observed.values())
    fully_reconciled = (
        all(
            row[k]
            for row in all_rows
            for k in (
                "matches_ledger_transactions",
                "matches_ledger_outer_refs",
                "matches_ledger_inner_refs",
                "matches_ledger_program_refs",
                "matches_ledger_program_unique_transactions",
                "matches_ledger_status_partition",
                "matches_ledger_normalization_failures",
                "matches_structural_novelty_program_references",
            )
        )
        and sum(row["transactions"] for row in all_rows) == 741305
    )
    return {
        "per_dataset": observed,
        "aggregate_transactions": sum(row["transactions"] for row in all_rows),
        "aggregate_outer_instruction_references": sum(
            row["outer_instruction_references"] for row in all_rows
        ),
        "aggregate_inner_instruction_references": sum(
            row["inner_instruction_references"] for row in all_rows
        ),
        "aggregate_program_instruction_references": sum(
            row["program_instruction_references"] for row in all_rows
        ),
        "aggregate_program_unique_transactions_per_dataset": sum(
            row["program_unique_transactions"] for row in all_rows
        ),
        "aggregate_structural_novelty_instruction_references_per_dataset": sum(
            row["structural_novelty_instruction_references"] for row in all_rows
        ),
        "denominator_match": fully_reconciled,
        "structural_novelty_relation": (
            "same pinned Pump AMM program; per-slot and per-dataset instruction-reference "
            "totals are cross-checked, not added"
        ),
    }


def verify_projection_model(
    stage1: dict[str, Any], model: dict[str, Any], supplied: dict[str, Any]
) -> None:
    body = dict(supplied)
    claimed = body.pop("reproducible_content_sha256", None)
    if claimed != _sha(_canonical(body)):
        raise FeasibilityError("result_content_hash_mismatch")
    if (
        supplied.get("schema") != RESULT_SCHEMA
        or supplied.get("stage1_sha256") != stage1["content_sha256"]
        or supplied.get("scope")
        != "exactly_608_existing_selected_full_blocks_and_ordered_signature_lists"
        or supplied.get("network") != NETWORK
        or supplied.get("target_program_id") != PROGRAM_ID
        or supplied.get("instruction_discriminator_hex") != CREATE_POOL_IX.hex()
        or supplied.get("event_discriminator_hex") != CREATE_POOL_EVENT.hex()
        or supplied.get("zero_claim_scope")
        != (
            "per-selected-block zero only for this fixed create_pool instruction discriminator "
            "when full capture/signature verification and every transaction normalization/status "
            "are complete"
        )
        or supplied.get("time_fields")
        != {
            "receipt_time": "capture receipt received_ns",
            "availability_time": "sealed receipt availability_ns or null",
            "event_time": "blockTime, kept separate",
            "decision_time_ns": None,
        }
        or supplied.get("decision_time_ns") is not None
        or supplied.get("stage1_provenance")
        != {
            "inputs": stage1.get("inputs"),
            "historical_source_pins": stage1.get("historical_source_pins"),
            "implementation_source_hashes": stage1.get("implementation_source_hashes"),
            "historical_idl_profile_provenance": stage1.get("historical_idl_profile_provenance"),
            "implementation_baseline_head": stage1.get("implementation_baseline_head"),
        }
        or supplied.get("claims")
        != {
            "births": False,
            "population_completeness": False,
            "economic_validity": False,
            "admission": False,
            "settlement": False,
            "global_chain_zero": False,
        }
    ):
        raise FeasibilityError("result_scope_or_claim_policy_mismatch")
    datasets = supplied.get("datasets")
    if (
        not isinstance(datasets, dict)
        or set(datasets) != set(EXPECTED_DATASETS)
        or supplied.get("dataset_order") != list(EXPECTED_DATASETS)
    ):
        raise FeasibilityError("result_dataset_set_or_order_mismatch")
    expected_by_dataset = model["candidates_by_dataset"]
    expected_candidates = []
    for dataset_id in EXPECTED_DATASETS:
        dataset = datasets.get(dataset_id)
        if (
            not isinstance(dataset, dict)
            or not isinstance(dataset.get("candidates"), list)
            or not isinstance(dataset.get("selected_slots"), list)
        ):
            raise FeasibilityError("result_dataset_shape_invalid", dataset_id)
        candidates = expected_by_dataset.get(dataset_id, [])
        if dataset.get("candidates") != candidates:
            raise FeasibilityError("candidate_identity_or_outcome_mismatch", dataset_id)
        expected_candidates.extend(candidates)
        if dataset.get("totals") != model["dataset_totals"][dataset_id]:
            raise FeasibilityError("dataset_denominator_mismatch", dataset_id)
        expected_slot_rows = [
            model["slot_rows"][(dataset_id, row["slot"])]
            for row in stage1["selected_rows"]
            if row["dataset_id"] == dataset_id
        ]
        if dataset.get("selected_slots") != expected_slot_rows:
            raise FeasibilityError("selected_slot_time_or_denominator_mismatch", dataset_id)
    identities = []
    for item in expected_candidates:
        identities.append(
            (
                item["network"],
                item["slot"],
                item["blockhash"],
                item["signature"],
                tuple(item["path"]),
                item["dataset_id"],
            )
        )
    if len(identities) != len(set(identities)):
        raise FeasibilityError("duplicate_candidate_identity_within_dataset")
    outcomes = Counter(item["assessment"]["feasibility_class"] for item in expected_candidates)
    if supplied.get("candidate_outcome_partition") != dict(sorted(outcomes.items())):
        raise FeasibilityError("candidate_outcome_partition_mismatch")
    if supplied.get("cross_dataset_candidate_overlaps") != model["cross_dataset"]:
        raise FeasibilityError("cross_dataset_overlap_mismatch")
    if (
        supplied.get("duplicate_candidate_identity_count_within_capture")
        != model["duplicate_identity_count_within_capture"]
    ):
        raise FeasibilityError("duplicate_candidate_count_mismatch")
    expected_reconciliation = _reconcile_model(stage1, model)
    if supplied.get("reconciliation") != expected_reconciliation:
        raise FeasibilityError("ledger_reconciliation_mismatch")
    expected_status = (
        "verified_candidate_feasibility_only"
        if expected_reconciliation["denominator_match"]
        else "partial_denominator_mismatch"
    )
    if supplied.get("status") != expected_status:
        raise FeasibilityError("result_status_mismatch")


def _reconcile_model(stage1: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    expected = stage1["dataset_expected_totals"]
    observed = {}
    for dataset_id in EXPECTED_DATASETS:
        item = model["dataset_totals"][dataset_id]
        exp = expected[dataset_id]
        status_success = item.get("successful_transactions", 0)
        status_failed = item.get("failed_transactions", 0)
        status_unknown = item.get("unknown_transaction_status", 0)
        unique_program_tx = sum(
            row["program_unique_transaction_count"]
            for (did, _), row in model["slot_rows"].items()
            if did == dataset_id
        )
        observed[dataset_id] = {
            "transactions": item.get("transactions", 0),
            "outer_instruction_references": item.get("outer_instruction_references", 0),
            "inner_instruction_references": item.get("inner_instruction_references", 0),
            "program_instruction_references": item.get("program_instruction_references", 0),
            "program_unique_transactions": unique_program_tx,
            "normalization_failures": item.get("normalization_failures", 0),
            "structural_novelty_instruction_references": exp[
                "structural_novelty_instruction_references"
            ],
            "success_transactions": status_success,
            "failed_transactions": status_failed,
            "unknown_status_transactions": status_unknown,
            "matches_ledger_transactions": item.get("transactions", 0)
            == exp["full_block_transactions"],
            "matches_ledger_outer_refs": item.get("outer_instruction_references", 0)
            == exp["outer_instruction_references"],
            "matches_ledger_inner_refs": item.get("inner_instruction_references", 0)
            == exp["inner_instruction_references"],
            "matches_ledger_program_refs": item.get("program_instruction_references", 0)
            == exp["program_instruction_references"],
            "matches_ledger_program_unique_transactions": unique_program_tx
            == exp["program_unique_transactions"],
            "matches_ledger_status_partition": (
                status_success == exp["success_transactions"]
                and status_failed == exp["failed_transactions"]
                and status_unknown == exp["unknown_status_transactions"]
            ),
            "matches_ledger_normalization_failures": item.get("normalization_failures", 0)
            == exp["unsupported_normalization"],
            "matches_structural_novelty_program_references": (
                item.get("program_instruction_references", 0)
                == exp["structural_novelty_instruction_references"]
            ),
        }
    rows = list(observed.values())
    denominator_match = (
        all(
            row[k]
            for row in rows
            for k in (
                "matches_ledger_transactions",
                "matches_ledger_outer_refs",
                "matches_ledger_inner_refs",
                "matches_ledger_program_refs",
                "matches_ledger_program_unique_transactions",
                "matches_ledger_status_partition",
                "matches_ledger_normalization_failures",
                "matches_structural_novelty_program_references",
            )
        )
        and sum(row["transactions"] for row in rows) == 741305
    )
    return {
        "per_dataset": observed,
        "aggregate_transactions": sum(row["transactions"] for row in rows),
        "aggregate_outer_instruction_references": sum(
            row["outer_instruction_references"] for row in rows
        ),
        "aggregate_inner_instruction_references": sum(
            row["inner_instruction_references"] for row in rows
        ),
        "aggregate_program_instruction_references": sum(
            row["program_instruction_references"] for row in rows
        ),
        "aggregate_program_unique_transactions_per_dataset": sum(
            row["program_unique_transactions"] for row in rows
        ),
        "aggregate_structural_novelty_instruction_references_per_dataset": sum(
            row["structural_novelty_instruction_references"] for row in rows
        ),
        "denominator_match": denominator_match,
        "structural_novelty_relation": (
            "same pinned Pump AMM program; per-slot and per-dataset instruction-reference "
            "totals are cross-checked, not added"
        ),
    }


def verify_document(stage1: dict[str, Any], supplied: dict[str, Any], root: Path = ROOT) -> None:
    if build_stage1(root) != stage1:
        raise FeasibilityError("stage1_input_receipts_changed")
    model = _independent_reenumerate(stage1, root)
    verify_projection_model(stage1, model, supplied)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-stage1")
    freeze.add_argument("--output", type=Path, required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--stage1", type=Path, required=True)
    scan.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--stage1", type=Path, required=True)
    verify.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze-stage1":
            stage = freeze_stage1(args.output)
            print(
                _canonical(
                    {
                        "status": "stage1_frozen",
                        "content_sha256": stage["content_sha256"],
                        "rows": len(stage["selected_rows"]),
                    }
                ).decode()
            )
        elif args.command == "scan":
            stage = load_stage1(args.stage1)
            result = scan_stage1(stage)
            output = args.output if args.output.is_absolute() else ROOT / args.output
            _write_create_only(output, _canonical(result) + b"\n", root=ROOT)
            print(
                _canonical(
                    {
                        "status": result["status"],
                        "output": str(output),
                        "candidates": sum(
                            len(v["candidates"]) for v in result["datasets"].values()
                        ),
                        "denominator_match": result["reconciliation"]["denominator_match"],
                    }
                ).decode()
            )
        else:
            stage = load_stage1(args.stage1)
            result, _ = _json(args.result if args.result.is_absolute() else ROOT / args.result)
            verify_document(stage, result)
            print(
                _canonical(
                    {
                        "status": "independent_replay_match",
                        "stage1_sha256": stage["content_sha256"],
                        "candidate_count": sum(
                            len(v["candidates"]) for v in result["datasets"].values()
                        ),
                    }
                ).decode()
            )
        return 0
    except FeasibilityError as exc:
        print(
            _canonical(
                {"status": "failed_closed", "code": exc.code, "detail": exc.detail}
            ).decode(),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
