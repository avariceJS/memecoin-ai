#!/usr/bin/env python3
"""Summarize IDL-unrecognized PumpSwap instruction discriminators from a sealed replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RECEIPT = Path("docs/evidence/pumpswap-c4-structural-novelty-v1-2026-10-11.json")
SCHEMA = "pumpswap-idl-unrecognized-discriminators-v1"
HEX16 = re.compile(r"^[0-9a-f]{16}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_RECEIPT_SHA256 = "56f74e7328cf127cf29161572ee22387cfb845e02daeef3fa01d3eb39abf762a"
EXPECTED_IDL_COMMIT = "8cda1fa30ea658b20909d8aedf002047119388d2"
EXPECTED_IDL_SHA256 = "b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b"
EXPECTED_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
EXPECTED_INSTRUCTION_REFS = 22_319
EXPECTED_CLASS_COUNT = 19_751
INCLUDED_BUCKETS = tuple(f"20261010T{hour:02d}0000Z" for hour in range(13, 23))
EXCLUDED_TAIL2 = "20261010T230000Z"


class InventoryError(ValueError):
    """A source binding or structural inventory failed closed."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _validate_source_receipt(receipt_bytes: bytes, receipt: dict[str, Any]) -> None:
    if _sha(receipt_bytes) != SOURCE_RECEIPT_SHA256:
        raise InventoryError("source_receipt_hash_mismatch")
    pins = receipt.get("source_pins")
    if not isinstance(pins, dict) or (
        pins.get("pump_amm_idl_commit") != EXPECTED_IDL_COMMIT
        or pins.get("pump_amm_idl_sha256") != EXPECTED_IDL_SHA256
        or pins.get("pump_amm_program_id") != EXPECTED_PROGRAM_ID
    ):
        raise InventoryError("source_idl_or_program_pin_mismatch")
    manifest = receipt.get("frozen_manifest")
    if not isinstance(manifest, dict):
        raise InventoryError("frozen_manifest_missing")
    if manifest.get("included_buckets") != list(INCLUDED_BUCKETS):
        raise InventoryError("included_frame_set_mismatch")
    excluded = manifest.get("excluded_buckets")
    if (
        manifest.get("body_read_during_freeze") is not False
        or not isinstance(excluded, list)
        or not any(
            isinstance(row, dict)
            and row.get("bucket") == EXCLUDED_TAIL2
            and "frozen_tail2_control_excluded" in str(row.get("reason", ""))
            for row in excluded
        )
    ):
        raise InventoryError("tail2_exclusion_invalid")
    aggregate = receipt.get("aggregate")
    if (
        not isinstance(aggregate, dict)
        or aggregate.get("pumpswap_instruction_references") != EXPECTED_INSTRUCTION_REFS
        or aggregate.get("unique_structural_classes") != EXPECTED_CLASS_COUNT
    ):
        raise InventoryError("source_aggregate_pin_mismatch")


def _validate_artifact_binding(artifact: dict[str, Any], compressed: bytes, decoded: bytes) -> None:
    if len(compressed) != artifact.get("compressed_bytes") or _sha(compressed) != artifact.get(
        "compressed_sha256"
    ):
        raise InventoryError("compressed_detail_binding_mismatch")
    if len(decoded) != artifact.get("uncompressed_bytes") or _sha(decoded) != artifact.get(
        "uncompressed_sha256"
    ):
        raise InventoryError("uncompressed_detail_binding_mismatch")


def _validate_class_inventory(
    rows: Any,
    *,
    structural_refs: Any,
    expected_classes: Any,
    expected_class_count: int = EXPECTED_CLASS_COUNT,
    expected_instruction_refs: int = EXPECTED_INSTRUCTION_REFS,
) -> tuple[dict[str, list[dict[str, Any]]], Counter[str]]:
    if not isinstance(rows, list) or len(rows) != expected_class_count:
        raise InventoryError("class_inventory_count_mismatch")
    ids: set[str] = set()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    kind_counts: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise InventoryError("class_row_invalid")
        class_id = row.get("class_sha256")
        fingerprint = row.get("fingerprint")
        count = row.get("count")
        if (
            not isinstance(class_id, str)
            or not HEX64.fullmatch(class_id)
            or class_id in ids
            or not isinstance(fingerprint, dict)
            or type(count) is not int
            or count <= 0
            or _sha(_canonical(fingerprint)) != class_id
        ):
            raise InventoryError("class_row_invalid_or_duplicate")
        ids.add(class_id)
        kind = fingerprint.get("instruction_kind")
        if not isinstance(kind, str):
            raise InventoryError("class_instruction_kind_invalid")
        kind_counts[kind] += count
        if kind != "unknown_discriminator":
            continue
        discriminator = fingerprint.get("discriminator_hex")
        length = fingerprint.get("wire_data_length")
        status = fingerprint.get("transaction_status")
        first_seen = row.get("first_seen_in")
        also_seen = row.get("also_seen_in")
        if (
            not isinstance(discriminator, str)
            or not HEX16.fullmatch(discriminator)
            or fingerprint.get("fixed_account_count") is not None
            or type(length) is not int
            or length < 8
            or not isinstance(status, str)
            or status not in {"success", "failed"}
            or first_seen not in INCLUDED_BUCKETS
            or not isinstance(also_seen, list)
            or any(bucket not in INCLUDED_BUCKETS for bucket in also_seen)
        ):
            raise InventoryError("unknown_discriminator_class_invalid")
        grouped[discriminator].append(row)
    if expected_classes != expected_class_count or len(ids) != expected_class_count:
        raise InventoryError("unique_class_count_mismatch")
    if type(structural_refs) is not int or sum(kind_counts.values()) != structural_refs:
        raise InventoryError("instruction_reference_total_mismatch")
    if structural_refs != expected_instruction_refs:
        raise InventoryError("structural_reference_count_mismatch")
    return grouped, kind_counts


def _summarize_discriminators(
    grouped: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    items = []
    for discriminator, classes in sorted(grouped.items()):
        lengths: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        first_seen: set[str] = set()
        observed_in: set[str] = set()
        for row in classes:
            fingerprint = row["fingerprint"]
            count = row["count"]
            lengths[str(fingerprint["wire_data_length"])] += count
            statuses[fingerprint["transaction_status"]] += count
            first_seen.add(row["first_seen_in"])
            observed_in.add(row["first_seen_in"])
            observed_in.update(row.get("also_seen_in", []))
        discriminator_refs = sum(row["count"] for row in classes)
        if (
            sum(lengths.values()) != discriminator_refs
            or sum(statuses.values()) != discriminator_refs
        ):
            raise InventoryError("discriminator_partition_mismatch")
        items.append(
            {
                "discriminator_hex": discriminator,
                "instruction_references": discriminator_refs,
                "unique_structural_classes": len(classes),
                "observed_data_length_counts": {
                    int(length): count
                    for length, count in sorted(lengths.items(), key=lambda x: int(x[0]))
                },
                "transaction_status_reference_counts": dict(sorted(statuses.items())),
                "first_seen_frames": sorted(first_seen),
                "frames_with_any_class_observation": sorted(observed_in),
            }
        )
    if sum(row["instruction_references"] for row in items) != sum(
        row["count"] for group in grouped.values() for row in group
    ):
        raise InventoryError("discriminator_reference_sum_mismatch")
    return items


def _load_receipt() -> tuple[dict[str, Any], bytes]:
    receipt_path = ROOT / SOURCE_RECEIPT
    try:
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError("source_receipt_invalid") from exc
    _validate_source_receipt(receipt_bytes, receipt)
    artifact = receipt.get("detail_artifact")
    if (
        receipt.get("schema_version") != "pumpswap-c4-structural-novelty-v1"
        or receipt.get("status") != "replay_complete_structural_only"
        or receipt.get("offline_replay") is not True
        or not isinstance(artifact, dict)
        or artifact.get("codec") != "zstd"
    ):
        raise InventoryError("source_receipt_not_qualified")
    relative = Path(artifact.get("path", ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise InventoryError("detail_path_invalid")
    compressed = (ROOT / relative).read_bytes()
    zstd = subprocess.run(["/usr/bin/which", "zstd"], capture_output=True, text=True)
    if zstd.returncode != 0 or not zstd.stdout.strip():
        raise InventoryError("zstd_unavailable")
    decoded = subprocess.run(
        [zstd.stdout.strip(), "-dc"], input=compressed, capture_output=True, check=False
    )
    if decoded.returncode != 0:
        raise InventoryError("detail_decompression_failed")
    _validate_artifact_binding(artifact, compressed, decoded.stdout)
    try:
        detail = json.loads(decoded.stdout)
    except json.JSONDecodeError as exc:
        raise InventoryError("detail_json_invalid") from exc
    if (
        detail.get("schema_version") != receipt["schema_version"]
        or detail.get("status") != receipt["status"]
        or detail.get("source_pins") != receipt.get("source_pins")
        or detail.get("aggregate") != receipt.get("aggregate")
        or detail.get("frozen_manifest") != receipt.get("frozen_manifest")
    ):
        raise InventoryError("compact_detail_identity_mismatch")
    return receipt, decoded.stdout


def build_inventory() -> dict[str, Any]:
    receipt, detail_bytes = _load_receipt()
    detail = json.loads(detail_bytes)
    aggregate = receipt["aggregate"]
    grouped, kind_counts = _validate_class_inventory(
        detail.get("classes"),
        structural_refs=aggregate["pumpswap_instruction_references"],
        expected_classes=aggregate["unique_structural_classes"],
    )
    structural_refs = aggregate["pumpswap_instruction_references"]
    refs = kind_counts.get("unknown_discriminator", 0)
    items = _summarize_discriminators(grouped)
    return {
        "schema_version": SCHEMA,
        "status": "offline_idl_unrecognized_inventory_complete",
        "offline_only": True,
        "scope": (
            "Unknown discriminator values in the ten included frozen c4 frames, relative to the "
            "pinned PumpSwap IDL only"
        ),
        "source": {
            "receipt_path": str(SOURCE_RECEIPT),
            "receipt_sha256": _sha((ROOT / SOURCE_RECEIPT).read_bytes()),
            "detail_artifact": receipt["detail_artifact"],
            "replay_repository_head": receipt["source_pins"]["repository_head"],
            "pump_amm_idl_commit": receipt["source_pins"]["pump_amm_idl_commit"],
            "pump_amm_idl_sha256": receipt["source_pins"]["pump_amm_idl_sha256"],
        },
        "generator": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": _file_sha(Path(__file__).resolve()),
            "repository_head": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip(),
        },
        "aggregate": {
            "structural_instruction_references": structural_refs,
            "idl_recognized_references": structural_refs - refs,
            "idl_unrecognized_references": refs,
            "distinct_idl_unrecognized_discriminators": len(items),
            "distinct_idl_unrecognized_structural_classes": sum(
                row["unique_structural_classes"] for row in items
            ),
        },
        "discriminators": items,
        "limitations": [
            (
                "IDL-unrecognized is relative only to the pinned IDL and does not prove a new "
                "protocol instruction or protocol revision."
            ),
            (
                "Counts are instruction references in selected captured blocks, not unique "
                "transactions, economic events, or discovery completeness."
            ),
            "Transaction success is not settlement; failed transactions remain attempts.",
            "The frozen 20261010T230000Z tail2 body was not accessed.",
            (
                "No decoding, account-role inference, economic qualification, admission, or "
                "provider request is performed."
            ),
        ],
    }


def write_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(payload) + b"\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    if output.exists():
        raise SystemExit(f"refusing to overwrite output: {output}")
    try:
        payload = build_inventory()
        write_once(output, payload)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to overwrite output: {output}") from exc
    except InventoryError as exc:
        raise SystemExit(f"inventory failed closed: {exc}") from exc
    print(
        json.dumps(
            {
                "status": payload["status"],
                "aggregate": payload["aggregate"],
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
