#!/usr/bin/env python3
"""Freeze the one-pass tail2 c4 audit plan from metadata-bound captures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from memecoin_pumpswap.buy_exact_v1 import IDL_SHA256
from tools import brain_senses_buy_exact_tail2_c4_manifest as manifest
from tools import brain_senses_campaign4_recovery as campaign4

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVIATION_PATH = REPO_ROOT / (
    "docs/evidence/pumpswap-buy-exact-tail2-c4-manifest-preplan-hash-only-deviation-2026-10-10.json"
)
TEST_RECEIPT_CONTRACT = "pumpswap_buy_exact_tail2_c4_synthetic_preflight_v2"
TEST_SOURCE_PATHS = (
    "tests/test_brain_senses_buy_exact_tail2_c4_manifest.py",
    "tests/test_brain_senses_buy_exact_tail2_c4_plan.py",
    "tests/test_brain_senses_buy_exact_tail2_c4_audit.py",
)
SOURCE_PATHS = (
    "tools/brain_senses_buy_exact_tail2_c4_manifest.py",
    "tools/brain_senses_buy_exact_tail2_c4_plan.py",
    "tools/brain_senses_buy_exact_tail2_c4_audit.py",
    "tools/brain_senses_buy_exact_v1_census.py",
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_provider_pilot.py",
    "tools/brain_senses_campaign4_recovery.py",
    "tools/brain_senses_program_watch.py",
    "src/memecoin_ai/domain.py",
    "src/memecoin_ai/sources.py",
    "src/memecoin_pumpswap/buy_exact_v1.py",
    "src/memecoin_pumpswap/economic_buy_exact_tail2_v1.py",
    "src/memecoin_pumpswap/compiled_economics.py",
    "src/memecoin_pumpswap/decoder.py",
    "src/memecoin_pumpswap/legacy_token_ledger.py",
    "src/memecoin_pumpswap/normalize.py",
    "src/memecoin_pumpswap/schema.py",
    "pyproject.toml",
    "requirements-dev.lock",
    "tests/test_brain_senses_buy_exact_tail2_c4_manifest.py",
    "tests/test_brain_senses_buy_exact_tail2_c4_plan.py",
    "tests/test_brain_senses_buy_exact_tail2_c4_audit.py",
)


def _stat_identity(row: os.stat_result) -> tuple[int, int, int, int, int]:
    return row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> tuple[dict[str, Any], bytes]:
    before = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError("plan_input_not_regular_file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read()
            after = os.fstat(stream.fileno())
    finally:
        final = path.lstat()
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or final.st_dev != after.st_dev
        or final.st_ino != after.st_ino
        or final.st_size != after.st_size
        or final.st_mtime_ns != after.st_mtime_ns
        or final.st_ctime_ns != after.st_ctime_ns
        or len(raw) != after.st_size
    ):
        raise ValueError("plan_input_changed_while_reading")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("plan_input_object_required")
    return value, raw


def _write_create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _path_hash(path: Path) -> dict[str, Any]:
    before = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError("plan_source_not_regular_file")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    if (
        _stat_identity(before) != _stat_identity(after)
        or _stat_identity(after) != _stat_identity(final)
        or size != after.st_size
    ):
        raise ValueError("plan_source_changed_while_hashing")
    return {"bytes": size, "sha256": digest.hexdigest()}


def _xml_test_counts(path: Path) -> dict[str, int]:
    before = path.lstat()
    if path.is_symlink() or not path.is_file() or before.st_size > 16_000_000:
        raise ValueError("plan_test_result_artifact_invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(16_000_001)
            after = os.fstat(stream.fileno())
    finally:
        final = path.lstat()
    if (
        len(raw) > 16_000_000
        or _stat_identity(before) != _stat_identity(after)
        or _stat_identity(after) != _stat_identity(final)
        or len(raw) != after.st_size
    ):
        raise ValueError("plan_test_result_artifact_changed_or_oversized")
    root = ET.fromstring(raw)
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise ValueError("plan_test_result_suites_missing")
    counts: dict[str, int] = {}
    for key in ("tests", "failures", "errors", "skipped"):
        try:
            counts[key] = sum(int(suite.attrib[key]) for suite in suites)
        except (KeyError, ValueError) as exc:
            raise ValueError("plan_test_result_count_invalid") from exc
    if counts["tests"] <= 0 or counts["failures"] or counts["errors"]:
        raise ValueError("plan_synthetic_test_result_not_clean")
    return counts


def _test_binding(path: Path, repository_root: Path, campaign_root: Path) -> dict[str, Any]:
    value, raw = _json(path)
    if (
        value.get("contract") != TEST_RECEIPT_CONTRACT
        or value.get("status") != "passed_synthetic_only"
        or value.get("rpc_requests") != 0
        or value.get("semantic_campaign_payloads_opened") != 0
        or value.get("test_exit_code") != 0
        or not isinstance(value.get("test_source_hashes"), dict)
        or set(value["test_source_hashes"]) != set(TEST_SOURCE_PATHS)
        or not isinstance(value.get("test_result_artifact"), dict)
    ):
        raise ValueError("plan_synthetic_test_receipt_invalid")
    for name, digest in value["test_source_hashes"].items():
        if name not in SOURCE_PATHS or not isinstance(digest, str):
            raise ValueError("plan_synthetic_test_source_invalid")
        if _path_hash(repository_root / name)["sha256"] != digest:
            raise ValueError("plan_synthetic_test_source_hash_mismatch")
    result_path = Path(value["test_result_artifact"].get("path", ""))
    if result_path.is_symlink() or result_path.resolve().is_relative_to(campaign_root):
        raise ValueError("plan_test_result_artifact_path_invalid")
    result_binding = _path_hash(result_path.resolve(strict=True))
    if result_binding["sha256"] != value["test_result_artifact"].get("sha256"):
        raise ValueError("plan_test_result_artifact_hash_mismatch")
    result_counts = _xml_test_counts(result_path.resolve(strict=True))
    if result_counts != value.get("test_result_counts"):
        raise ValueError("plan_test_result_counts_mismatch")
    return {
        "path": str(path.resolve()),
        "sha256": _sha(raw),
        "receipt": value,
        "result_artifact": {
            **result_binding,
            "path": str(result_path.resolve()),
            "counts": result_counts,
        },
    }


def _selection_is_first_four_successes(
    frames: list[dict[str, Any]], denominator: list[dict[str, Any]]
) -> bool:
    if len(frames) != 4:
        return False
    seen: set[str] = set()
    successes = []
    for row in denominator:
        if not isinstance(row, dict):
            return False
        bucket, state = row.get("bucket"), row.get("state")
        if not isinstance(bucket, str) or bucket in seen:
            return False
        seen.add(bucket)
        if state == "successful":
            successes.append(bucket)
        elif state not in {"missing", "failed_or_partial", "ambiguous_or_partial"}:
            return False
    selected = [row.get("bucket") for row in frames if isinstance(row, dict)]
    return len(selected) == 4 and len(set(selected)) == 4 and successes[:4] == selected


def _scheduled_denominator_is_complete(
    denominator: list[dict[str, Any]], fourth_bucket: str
) -> bool:
    buckets = [row.get("bucket") for row in denominator if isinstance(row, dict)]
    return len(buckets) == len(denominator) and buckets == manifest._scheduled_buckets(
        fourth_bucket
    )


def freeze_plan(
    *,
    root: Path,
    manifest_path: Path,
    test_receipt_path: Path,
    plan_path: Path,
    freeze_receipt_path: Path,
) -> dict[str, Any]:
    if root.is_symlink():
        raise ValueError("plan_campaign_root_symlink")
    root = root.resolve(strict=True)
    if root != campaign4.ROOT.resolve():
        raise ValueError("plan_campaign_root_mismatch")
    if any(path.is_symlink() for path in (plan_path, freeze_receipt_path)):
        raise ValueError("plan_output_symlink")
    if manifest_path.is_symlink() or test_receipt_path.is_symlink():
        raise ValueError("plan_input_symlink")
    manifest_path = manifest_path.resolve(strict=True)
    plan_path = plan_path.resolve()
    freeze_receipt_path = freeze_receipt_path.resolve()
    test_receipt_path = test_receipt_path.resolve(strict=True)
    if (
        plan_path == freeze_receipt_path
        or plan_path.is_relative_to(root)
        or freeze_receipt_path.is_relative_to(root)
        or manifest_path.is_relative_to(root)
        or test_receipt_path.is_relative_to(root)
    ):
        raise ValueError("plan_output_path_invalid")
    if (
        plan_path.exists()
        or plan_path.is_symlink()
        or freeze_receipt_path.exists()
        or freeze_receipt_path.is_symlink()
    ):
        raise ValueError("plan_output_exists_refusing_overwrite")

    manifest_value, manifest_bytes = _json(manifest_path)
    if (
        manifest_value.get("contract") != "pumpswap_buy_exact_tail2_c4_metadata_source_manifest_v1"
        or manifest_value.get("status") != "four_frames_source_bound_payloads_not_decoded"
        or manifest_value.get("preregistration_sha256") != manifest.EXPECTED_PREREG_SHA256
        or Path(manifest_value.get("campaign_root", "")).resolve() != root
        or manifest_value.get("counts", {}).get("selected_successful_frames") != 4
        or manifest_value.get("counts", {}).get("payloads_decoded_by_manifest_builder") != 0
        or manifest_value.get("counts", {}).get("rpc_requests") != 0
    ):
        raise ValueError("plan_metadata_manifest_invalid")
    frames = manifest_value.get("selected_frames")
    denominator = manifest_value.get("scheduled_bucket_denominator")
    if not isinstance(frames, list) or len(frames) != 4 or not isinstance(denominator, list):
        raise ValueError("plan_frame_denominator_shape_invalid")
    statuses = {row.get("bucket"): row.get("state") for row in denominator if isinstance(row, dict)}
    if not _selection_is_first_four_successes(frames, denominator):
        raise ValueError("plan_selection_is_not_first_four_successes")
    frame_buckets = [row["bucket"] for row in frames]
    if not _scheduled_denominator_is_complete(denominator, frame_buckets[-1]):
        raise ValueError("plan_scheduled_bucket_denominator_incomplete")
    if any(statuses.get(bucket) != "successful" for bucket in frame_buckets):
        raise ValueError("plan_selected_frame_not_successful")
    if any(
        row.get("status") != "frame_complete"
        or row.get("request_count") != campaign4.REQUESTS_PER_FRAME
        or len(row.get("selected_slots", [])) != campaign4.SELECTION_COUNT
        or any(type(slot) is not int or slot < 0 for slot in row.get("selected_slots", []))
        or len(row.get("captures", [])) != campaign4.REQUESTS_PER_FRAME
        for row in frames
    ):
        raise ValueError("plan_frame_binding_incomplete")
    active_reservation, _ = _json(root / "active-frame-reservation.json")
    if active_reservation.get("status") != "reconciled":
        raise ValueError("plan_open_or_ambiguous_frame_reservation")
    frontier_binding = manifest_value.get("runtime_metadata_sha256", {}).get("frontier")
    if manifest._sha((root / "frontier-v3.json").read_bytes()) != frontier_binding:
        raise ValueError("plan_campaign_frontier_changed_after_manifest")
    flattened_slots = [slot for row in frames for slot in row["selected_slots"]]
    if len(flattened_slots) != len(set(flattened_slots)):
        raise ValueError("plan_duplicate_selected_slot")

    prereg = manifest._json(manifest.PREREG_PATH)[0]
    if prereg.get("status") != "metadata_only_selection_rule_frozen_payloads_unopened":
        raise ValueError("plan_preregistration_status_invalid")
    deviation_value, deviation_bytes = _json(DEVIATION_PATH)
    if (
        deviation_value.get("frame_id") != "20261010T230000Z"
        or deviation_value.get("result", {}).get("eligible_frame_retained") is not True
        or deviation_value.get("result", {}).get("replacement_frame_selected") is not False
        or deviation_value.get("result", {}).get("audit_independence_status")
        != "retained_with_disclosed_procedural_deviation"
    ):
        raise ValueError("plan_deviation_receipt_invalid")
    test_binding = _test_binding(test_receipt_path, REPO_ROOT, root)
    source_hashes = {name: _path_hash(REPO_ROOT / name) for name in SOURCE_PATHS}
    if any(source_hashes[name]["bytes"] == 0 for name in SOURCE_PATHS):
        raise ValueError("plan_empty_source_pin")
    zstd = os.environ.get("BRAIN_ZSTD_EXECUTABLE") or shutil.which("zstd")
    if not zstd or not Path(zstd).is_absolute() or not Path(zstd).is_file():
        raise ValueError("plan_zstd_unavailable")
    zstd_version = subprocess.run(
        [zstd, "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()
    zstd_binding = {
        "path": str(Path(zstd).resolve()),
        **_path_hash(Path(zstd)),
        "version": zstd_version,
    }
    plan = {
        "schema_version": 1,
        "contract": "pumpswap_buy_exact_tail2_c4_one_pass_audit_plan_v1",
        "status": "frozen_metadata_plan_semantic_replay_not_started",
        "protocol_anchor": {
            "commit": "f7ec8c0",
            "commit_time_utc": "2026-10-10T22:11:37Z",
            "first_eligible_bucket": "20261010T230000Z",
            "selection_rule": "first_four_naturally_successful_scheduled_frames_by_bucket_start",
        },
        "campaign_id": campaign4.CAMPAIGN_ID,
        "campaign_root": str(root),
        "preregistration_sha256": manifest_value["preregistration_sha256"],
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha(manifest_bytes),
        "scheduled_bucket_denominator": denominator,
        "selected_frames": frames,
        "deviation_receipt": {
            "path": str(DEVIATION_PATH),
            "sha256": _sha(deviation_bytes),
            "semantic_payload_access": "none",
        },
        "source_sha256": {name: value["sha256"] for name, value in source_hashes.items()},
        "synthetic_preflight": {
            "path": test_binding["path"],
            "sha256": test_binding["sha256"],
            "status": test_binding["receipt"]["status"],
            "test_source_hashes": test_binding["receipt"]["test_source_hashes"],
            "test_exit_code": test_binding["receipt"]["test_exit_code"],
            "test_result_artifact": test_binding["result_artifact"],
        },
        "idl_and_protocol_constants": {
            "pump_amm_idl_sha256": IDL_SHA256,
            "implementation_runtime": (
                "pure Python standard library; no Solana SDK decoder dependency"
            ),
            "economic_contract": (
                "pumpswap_v1_buy_exact_quote_in_tail2_dual_legacy_atomic_flow_diagnostic_v1"
            ),
            "fee_hypothesis_keys": [
                "pool_credit_less_lp_fee_equals_event_net_user_quote_amount",
                "protocol_recipient_transfer_plus_buyback_equals_event_protocol_fee",
                "protocol_recipient_transfer_equals_protocol_fee_less_buyback",
                "creator_vault_transfer_equals_event_creator_fee",
                "buyback_ata_transfer_equals_event_buyback_fee",
            ],
            "amount_units": "integer token atomic units; no UI conversion or lamport mixing",
        },
        "zstd": zstd_binding,
        "created_ns": time.time_ns(),
        "limits": [
            "No discovery, transaction, process or market completeness claim.",
            "No settlement, wallet-net price, fee law, training or execution admission.",
            (
                "Any semantic disagreement is retained as a negative audit result; no retuning "
                "or replacement frame."
            ),
        ],
    }
    plan_bytes = (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode()
    plan_sha = _sha(plan_bytes)
    _write_create_only(plan_path, plan_bytes)
    freeze_receipt = {
        "schema_version": 1,
        "contract": "pumpswap_buy_exact_tail2_c4_plan_freeze_receipt_v1",
        "status": "plan_frozen_before_semantic_payload_access",
        "plan_path": str(plan_path),
        "plan_sha256": plan_sha,
        "manifest_sha256": _sha(manifest_bytes),
        "created_ns": time.time_ns(),
    }
    freeze_bytes = (
        json.dumps(freeze_receipt, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    _write_create_only(freeze_receipt_path, freeze_bytes)
    return {
        "status": freeze_receipt["status"],
        "plan_path": str(plan_path),
        "plan_sha256": plan_sha,
        "freeze_receipt_path": str(freeze_receipt_path),
        "freeze_receipt_sha256": _sha(freeze_bytes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=campaign4.ROOT)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--test-receipt", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--freeze-receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = freeze_plan(
            root=args.root,
            manifest_path=args.manifest,
            test_receipt_path=args.test_receipt,
            plan_path=args.plan,
            freeze_receipt_path=args.freeze_receipt,
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ET.ParseError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"status": "failed_closed", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
