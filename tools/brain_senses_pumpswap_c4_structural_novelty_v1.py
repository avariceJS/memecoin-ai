#!/usr/bin/env python3
"""Offline structural novelty replay over the ten included c4 frames only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import brain_senses_pumpswap_structural_novelty_v1 as novelty  # noqa: E402

C4_REPLAY = Path("docs/evidence/pumpswap-c4-captured-class-replay-2026-10-11.json")
DETAIL_OUTPUT = Path("work/brain-senses-c4-structural-novelty-v1-2026-10-11/detail.json.zst")
SCHEMA_VERSION = "pumpswap-c4-structural-novelty-v1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise novelty.MonitorFailure("input_json_invalid", str(path)) from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise novelty.MonitorFailure(code, detail)


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    _write_bytes_once(path, _canonical(payload) + b"\n", mode=0o644)


def _write_bytes_once(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if mode == 0o600 else 0o755)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        temporary_path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def build_receipt() -> dict[str, Any]:
    replay_path = REPO_ROOT / C4_REPLAY
    replay = _json(replay_path)
    _require(
        replay.get("schema") == "brain-senses-c4-captured-class-replay-v1"
        and replay.get("status") == "complete_selected_block_structural_replay",
        "c4_replay_not_qualified",
        str(C4_REPLAY),
    )
    root = Path(replay["campaign_root"])
    manifest_path = REPO_ROOT / replay["freeze_manifest"]
    manifest_bytes = manifest_path.read_bytes()
    manifest = _json(manifest_path)
    manifest_sha = _sha(manifest_bytes)
    _require(
        manifest_sha == replay.get("freeze_manifest_sha256")
        and manifest.get("freeze_sha256") == replay.get("freeze_manifest_internal_sha256"),
        "c4_freeze_manifest_hash_mismatch",
        str(replay["freeze_manifest"]),
    )
    included = replay.get("included_buckets")
    frames = replay.get("frame_receipts")
    _require(
        isinstance(included, list)
        and isinstance(frames, list)
        and len(included) == 10
        and [row.get("bucket") for row in frames] == included,
        "c4_included_frame_set_invalid",
        "expected the ten frozen included buckets in order",
    )
    excluded = replay.get("excluded_buckets", [])
    _require(
        all(row.get("bucket") != "20261010T230000Z" for row in frames)
        and any(row.get("bucket") == "20261010T230000Z" for row in excluded)
        and "frozen_tail2_control_excluded" in str(excluded),
        "frozen_tail2_boundary_invalid",
        "the excluded 23:00Z tail2 must remain outside the replay",
    )
    metadata = {row.get("frame_id"): row for row in manifest.get("frame_metadata", [])}
    _require(set(metadata) == set(included), "c4_manifest_frame_set_invalid", "frame ids differ")
    zstd = novelty.shutil.which("zstd")
    _require(zstd is not None, "zstd_cli_unavailable", "zstd is required")

    class_index: dict[str, str] = {}
    class_payloads: dict[str, dict[str, Any]] = {}
    class_counts: Counter[str] = Counter()
    class_frames: dict[str, list[str]] = {}
    class_examples: dict[str, set[str]] = {}
    novelty_rows = []
    block_bindings = []
    frame_rows = []
    global_transactions: set[str] = set()
    total_transactions = 0
    total_refs = 0
    prior_classes: set[str] = set()
    prior_families: set[str] = set()

    for frame in frames:
        bucket = frame["bucket"]
        frame_meta = metadata[bucket]
        expected_slots = frame_meta.get("selected_slot_ids")
        expected_check_slots = [row.get("slot") for row in frame_meta.get("structural_checks", [])]
        _require(
            isinstance(expected_slots, list)
            and len(expected_slots) == 16
            and len(set(expected_slots)) == 16
            and len(expected_check_slots) == 16
            and set(expected_check_slots) == set(expected_slots)
            and frame_meta.get("status") == "frame_complete",
            "c4_selected_slot_set_invalid",
            bucket,
        )
        epoch_root = root / "epochs" / bucket
        captures = novelty._capture_receipts(epoch_root, pair64=False)
        _require(
            len(captures) == len(expected_slots),
            "c4_full_capture_count_mismatch",
            bucket,
        )
        expected_slot_set = set(expected_slots)
        seen_slots: set[int] = set()
        observed_checks = {}
        frame_transactions = 0
        frame_refs = 0
        frame_buy_refs = 0
        frame_buy_signatures: set[str] = set()
        frame_classes: set[str] = set()

        for capture_path in captures:
            slot, block, capture_receipt = novelty._read_capture(capture_path, zstd)
            _require(
                slot in expected_slot_set and slot not in seen_slots,
                "c4_selected_slot_set_mismatch_or_duplicate",
                f"{bucket}:{slot}",
            )
            seen_slots.add(slot)
            summary = novelty._scan_block(block, slot, capture_receipt)
            refs = summary["references"]
            signatures = {row["identity"]["signature_sha256"] for row in refs}
            _require(
                not global_transactions.intersection(signatures),
                "c4_duplicate_referencing_transaction",
                f"{bucket}:{slot}",
            )
            global_transactions.update(signatures)
            success_refs = sum(row["class"]["transaction_status"] == "success" for row in refs)
            failed_refs = len(refs) - success_refs
            observed = {
                "structural_reference_count": len(refs),
                "references_in_successful_transactions": success_refs,
                "references_in_failed_transactions": failed_refs,
                "transactions_with_reference": len(signatures),
            }
            expected_check = next(
                (row for row in frame_meta["structural_checks"] if row.get("slot") == slot), None
            )
            _require(expected_check is not None, "c4_slot_check_missing", f"{bucket}:{slot}")
            _require(
                expected_check.get("status") == "qualified_structural_reference_count"
                and expected_check.get("block_identity_equal") is True
                and expected_check.get("signature_order_equal") is True
                and expected_check.get("full_response_validation_failures") == [],
                "c4_pair_qualification_invalid",
                f"{bucket}:{slot}",
            )
            expected_counts = expected_check["structural_references"]
            for field, value in observed.items():
                _require(
                    expected_counts.get(field) == value,
                    "c4_structural_count_mismatch",
                    f"{bucket}:{slot}:{field}",
                )
            observed_checks[str(slot)] = observed
            block_bindings.append(
                {
                    "bucket": bucket,
                    "slot": slot,
                    "blockhash": block["blockhash"],
                    "parent_slot": block.get("parentSlot"),
                    "event_time_unix_seconds": block.get("blockTime"),
                    "receipt_time_ns": capture_receipt["received_ns"],
                    "capture_receipt": str(capture_path.relative_to(epoch_root)),
                    "raw_response_sha256": capture_receipt["response_sha256"],
                    "raw_response_bytes": capture_receipt["response_bytes"],
                    "stored_response_sha256": capture_receipt["storage"]["sha256"],
                    "stored_response_bytes": capture_receipt["storage"]["bytes"],
                    "same_slot_block_identity_qualified": expected_check["block_identity_equal"],
                    "same_slot_signature_order_qualified": expected_check["signature_order_equal"],
                    "transactions": summary["transaction_count"],
                    **observed,
                }
            )
            frame_transactions += summary["transaction_count"]
            frame_refs += len(refs)
            for ref in refs:
                signature_hash = ref["identity"]["signature_sha256"]
                kind = ref["class"]["instruction_kind"]
                if kind == "buy_exact_quote_in":
                    frame_buy_refs += 1
                    frame_buy_signatures.add(signature_hash)
                class_bytes = _canonical(ref["class"])
                class_id = _sha(class_bytes)
                class_index[class_id] = ref["class"]["wire_family_sha256"]
                class_payloads[class_id] = ref["class"]
                class_counts[class_id] += 1
                if class_id not in frame_classes:
                    frame_classes.add(class_id)
                    class_frames.setdefault(class_id, []).append(bucket)
                class_examples.setdefault(class_id, set()).add(signature_hash)

        _require(seen_slots == expected_slot_set, "c4_selected_slot_set_mismatch", bucket)
        buy_counts = frame.get("buy_exact_counts", {})
        observed_buy = {
            "full_block_transactions": frame_transactions,
            "candidate_transactions": len(frame_buy_signatures),
            "instruction_references": frame_buy_refs,
            "block_requests": len(expected_slots),
        }
        expected_buy = {key: buy_counts.get(key) for key in observed_buy}
        _require(
            observed_buy == expected_buy,
            "c4_buyexact_census_mismatch",
            bucket,
        )
        frame_rows.append(
            {
                "bucket": bucket,
                "selected_slots": len(expected_slots),
                "transactions": frame_transactions,
                "pumpswap_instruction_references": frame_refs,
                "buy_exact_census_reconciliation": {
                    "expected": expected_buy,
                    "observed": observed_buy,
                    "status": "matched",
                },
                "slot_structural_reconciliation": observed_checks,
                "unique_structural_classes": len(frame_classes),
                "no_pumpswap_reference_slots": sorted(
                    slot
                    for slot, counts in ((int(k), v) for k, v in observed_checks.items())
                    if counts["structural_reference_count"] == 0
                ),
            }
        )
        current = frame_classes
        new = current - prior_classes
        new_families = {class_index[item] for item in new} - prior_families
        changed_variants = {item for item in new if class_index[item] in prior_families}
        novelty_rows.append(
            {
                "bucket": bucket,
                "classes_seen_before": len(prior_classes),
                "new_class_count": len(new),
                "first_wire_family_count": len(new_families),
                "changed_wire_variant_count": len(changed_variants),
                "new_class_sha256": sorted(new)[:256],
                "new_class_list_truncated": len(new) > 256,
                "no_longer_observed_class_count": len(prior_classes - current),
            }
        )
        prior_classes.update(current)
        prior_families.update(class_index[item] for item in current)
        total_transactions += frame_transactions
        total_refs += frame_refs

    aggregate = replay["aggregate"]
    _require(
        total_transactions == aggregate.get("transactions")
        and total_refs == aggregate.get("pumpswap_instruction_references")
        and sum(row["selected_slots"] for row in frame_rows)
        == aggregate.get("selected_full_blocks"),
        "c4_aggregate_reconciliation_failed",
        "captured transaction/reference/block totals differ",
    )
    repository_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "replay_complete_structural_only",
        "offline_replay": True,
        "scope": "PumpSwap structural novelty in the ten included frozen c4 frames only",
        "source_pins": {
            "repository_head": repository_head,
            "tool_sha256": _file_sha(Path(__file__)),
            "novelty_classifier_sha256": _file_sha(
                REPO_ROOT / "tools/brain_senses_pumpswap_structural_novelty_v1.py"
            ),
            "normalizer_sha256": _file_sha(REPO_ROOT / "src/memecoin_pumpswap/normalize.py"),
            "schema_module_sha256": _file_sha(REPO_ROOT / "src/memecoin_pumpswap/schema.py"),
            "pump_amm_idl_commit": novelty.SCHEMA["provenance"]["commit"],
            "pump_amm_idl_sha256": novelty.SCHEMA["provenance"]["sha256"],
            "pump_amm_program_id": novelty.PUMP_PROGRAM,
        },
        "c4_replay_receipt": {"path": str(C4_REPLAY), "sha256": _file_sha(replay_path)},
        "frozen_manifest": {
            "path": replay["freeze_manifest"],
            "sha256": manifest_sha,
            "internal_freeze_sha256": manifest["freeze_sha256"],
            "included_buckets": included,
            "excluded_buckets": excluded,
            "body_read_during_freeze": manifest.get("body_read_during_freeze"),
        },
        "aggregate": {
            "selected_full_blocks": len(block_bindings),
            "transactions": total_transactions,
            "pumpswap_instruction_references": total_refs,
            "unique_referencing_transactions": len(global_transactions),
            "unique_structural_classes": len(prior_classes),
            "wire_families": len(prior_families),
            "raw_full_block_response_bytes": sum(
                row["raw_response_bytes"] for row in block_bindings
            ),
            "stored_full_block_response_bytes": sum(
                row["stored_response_bytes"] for row in block_bindings
            ),
        },
        "novelty_sequence": novelty_rows,
        "frames": frame_rows,
        "block_bindings": block_bindings,
        "classes": [
            {
                "class_sha256": class_id,
                "wire_family_sha256": class_index[class_id],
                "count": class_counts[class_id],
                "first_seen_in": class_frames[class_id][0],
                "also_seen_in": class_frames[class_id][1:],
                "fingerprint": class_payloads[class_id],
                "signature_hash_examples": sorted(class_examples[class_id])[:3],
            }
            for class_id in sorted(class_counts)
        ],
        "limitations": [
            "Selected captured blocks only; no global discovery or market-process completeness.",
            "Structural instruction references are not economic swaps or exact market activity.",
            "Failed transactions are attempts; successful status does not prove settlement.",
            "Unknown account suffixes remain positional; no account role is inferred.",
            (
                "Event time and local capture receipt time are distinct; no historical "
                "availability is inferred."
            ),
            "Frozen 20261010T230000Z tail2 response body was not accessed.",
            "No admission, economic zero, training, execution, or provider billing claim is made.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    detail_path = REPO_ROOT / DETAIL_OUTPUT
    if output.exists() or detail_path.exists():
        raise SystemExit(f"refusing to overwrite output or detail artifact: {output}")
    try:
        detail = build_receipt()
        detail_bytes = _canonical(detail) + b"\n"
        zstd = novelty.shutil.which("zstd")
        if not zstd:
            raise novelty.MonitorFailure("zstd_cli_unavailable", "zstd is required")
        compressed = subprocess.run(
            [zstd, "-q", "-3", "-c"], input=detail_bytes, capture_output=True, check=False
        )
        if compressed.returncode != 0:
            raise novelty.MonitorFailure("detail_compression_failed", "zstd returned nonzero")
        artifact = {
            "path": str(detail_path.relative_to(REPO_ROOT)),
            "codec": "zstd",
            "uncompressed_bytes": len(detail_bytes),
            "uncompressed_sha256": _sha(detail_bytes),
            "compressed_bytes": len(compressed.stdout),
            "compressed_sha256": _sha(compressed.stdout),
        }
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": detail["status"],
            "offline_replay": True,
            "scope": detail["scope"],
            "source_pins": detail["source_pins"],
            "c4_replay_receipt": detail["c4_replay_receipt"],
            "frozen_manifest": detail["frozen_manifest"],
            "aggregate": detail["aggregate"],
            "novelty_sequence": detail["novelty_sequence"],
            "frames": [
                {
                    key: value
                    for key, value in frame.items()
                    if key != "slot_structural_reconciliation"
                }
                for frame in detail["frames"]
            ],
            "detail_artifact": artifact,
            "limitations": detail["limitations"],
        }
        _write_bytes_once(detail_path, compressed.stdout, mode=0o600)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to overwrite output or detail artifact: {output}") from exc
    except Exception as exc:
        code = exc.code if isinstance(exc, novelty.MonitorFailure) else "unexpected_replay_error"
        detail = exc.detail[:160] if isinstance(exc, novelty.MonitorFailure) else type(exc).__name__
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "monitor_failure",
            "offline_replay": True,
            "failure": {"code": code, "detail": detail},
            "source_pins": {"tool_sha256": _file_sha(Path(__file__))},
        }
        try:
            _write_once(output, payload)
        except FileExistsError:
            raise SystemExit(f"refusing to overwrite existing receipt: {output}") from exc
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 2
    try:
        _write_once(output, payload)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to overwrite existing receipt: {output}") from exc
    print(
        json.dumps(
            {
                "status": payload["status"],
                "selected_full_blocks": payload["aggregate"]["selected_full_blocks"],
                "transactions": payload["aggregate"]["transactions"],
                "pumpswap_instruction_references": payload["aggregate"][
                    "pumpswap_instruction_references"
                ],
                "detail_artifact": artifact["path"],
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
