#!/usr/bin/env python3
"""Offline IDL-layout feasibility replay for c4 BuyEvent/SellEvent captures only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tomllib
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import brain_senses_c4_captured_class_replay_v1 as c4_replay  # noqa: E402
from tools import brain_senses_pumpswap_idl_history_qualification_v1 as structural  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block  # noqa: E402

SCHEMA = "pumpswap-c4-event-layout-feasibility-v1"
IDL_HISTORY_RECEIPT = Path("docs/evidence/pumpswap-c4-idl-history-qualification-v3-2026-10-11.json")
IDL_HISTORY_RECEIPT_SHA256 = "3003c80f0ad6291b45a2a8c6c477fe729d8339446eb591a67c3c5c47029bdf46"
IDL_HISTORY_CAPTURE_MANIFEST_SHA256 = (
    "57a671e4a2b7bfaf84e30dc1804ce05ff4babb1344481f1dc78a1fa4a7eadf03"
)
ANCHOR_ENCODING_MANIFEST = Path(
    "work/brain-senses-anchor-borsh-source-2026-10-11/capture_manifest.json"
)
ANCHOR_ENCODING_MANIFEST_SHA256 = "2a7fdd88a1926447ae2d406e06799fcb55c26065746d7e92ce47841282acded7"
ANCHOR_ENCODING_DIR = Path("work/brain-senses-anchor-borsh-source-2026-10-11")
EXPECTED_HISTORY_COMMITS = 18
EXPECTED_EVENT_COUNTS = {"BuyEvent": 4_384, "SellEvent": 4_746}
EXPECTED_EVENT_STATUS = {
    "BuyEvent": {"success": 3_947, "failed": 437},
    "SellEvent": {"success": 4_491, "failed": 255},
}
TARGET_EVENTS = frozenset(EXPECTED_EVENT_COUNTS)
WIRE_PREFIX_BYTES = 16
SUPPORTED_TYPES = {
    "i64": (8, True),
    "u64": (8, False),
    "i128": (16, True),
}


class FeasibilityError(ValueError):
    """Fail-closed source, capture, or layout replay error."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise FeasibilityError("expected_json_object")
    return value


def _safe_source_path(name: str) -> Path:
    path = (ROOT / ANCHOR_ENCODING_DIR / name).resolve()
    if not path.is_relative_to((ROOT / ANCHOR_ENCODING_DIR).resolve()):
        raise FeasibilityError("serialization_source_path_escape")
    return path


def verify_anchor_encoding_sources() -> dict[str, Any]:
    """Verify Anchor's event wrapper and its commit-locked Borsh serialization source."""
    manifest_path = ROOT / ANCHOR_ENCODING_MANIFEST
    raw_manifest = manifest_path.read_bytes()
    if _sha(raw_manifest) != ANCHOR_ENCODING_MANIFEST_SHA256:
        raise FeasibilityError("anchor_encoding_manifest_hash_mismatch")
    manifest = _read_json(manifest_path)
    if manifest.get("anchor_commit") != structural.ANCHOR_EVENT_COMMIT:
        raise FeasibilityError("anchor_encoding_commit_mismatch")

    pinned_files: dict[str, dict[str, Any]] = {}
    for row in manifest.get("files", []):
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise FeasibilityError("anchor_encoding_file_row_invalid")
        name = row["name"]
        raw = _safe_source_path(name).read_bytes()
        if len(raw) != row.get("bytes") or _sha(raw) != row.get("sha256"):
            raise FeasibilityError("anchor_encoding_source_hash_mismatch")
        pinned_files[name] = row

    required = {
        "anchor-Cargo.lock",
        "anchor-lang-Cargo.toml",
        "anchor-lang-src-lib.rs",
        "borsh-0.9.3.crate",
        "borsh-0.9.3-ser-mod.rs",
        "borsh-derive-0.9.3.crate",
        "borsh-derive-internal-0.9.3.crate",
        "borsh-derive-internal-0.9.3-struct-ser.rs",
        "solana-program-1.15.2.crate",
        "solana-program-1.15.2-pubkey.rs",
    }
    if not required <= pinned_files.keys():
        raise FeasibilityError("anchor_encoding_source_set_incomplete")

    lock_raw = _safe_source_path("anchor-Cargo.lock").read_bytes()
    lock = tomllib.loads(lock_raw.decode())
    packages = {(row.get("name"), row.get("version")): row for row in lock.get("package", [])}
    expected_packages = {
        ("borsh", "0.9.3"): "borsh-0.9.3.crate",
        ("borsh-derive", "0.9.3"): "borsh-derive-0.9.3.crate",
        ("borsh-derive-internal", "0.9.3"): "borsh-derive-internal-0.9.3.crate",
        ("solana-program", "1.15.2"): "solana-program-1.15.2.crate",
    }
    for package, filename in expected_packages.items():
        lock_package = packages.get(package)
        archive_sha = pinned_files[filename]["sha256"]
        if (
            not isinstance(lock_package, dict)
            or lock_package.get("checksum") != archive_sha
            or lock_package.get("source") != "registry+https://github.com/rust-lang/crates.io-index"
        ):
            raise FeasibilityError("anchor_locked_serialization_package_mismatch")

    anchor_manifest = tomllib.loads(_safe_source_path("anchor-lang-Cargo.toml").read_text())
    if (
        anchor_manifest.get("package", {}).get("version") != "0.27.0"
        or anchor_manifest.get("dependencies", {}).get("borsh") != "0.9"
    ):
        raise FeasibilityError("anchor_lang_borsh_dependency_mismatch")
    anchor_lib = _safe_source_path("anchor-lang-src-lib.rs").read_text()
    if "BorshSerialize as AnchorSerialize" not in anchor_lib:
        raise FeasibilityError("anchor_serialize_alias_mismatch")

    # Verify the exact package source members extracted from the lock-checked archives.
    archives = {
        "borsh-0.9.3.crate": "borsh-0.9.3/src/ser/mod.rs",
        "borsh-derive-internal-0.9.3.crate": ("borsh-derive-internal-0.9.3/src/struct_ser.rs"),
        "solana-program-1.15.2.crate": "solana-program-1.15.2/src/pubkey.rs",
    }
    for archive_name, member in archives.items():
        archive_raw = _safe_source_path(archive_name).read_bytes()
        with tarfile.open(fileobj=BytesIO(archive_raw), mode="r:gz") as package:
            extracted = package.extractfile(member)
            if extracted is None:
                raise FeasibilityError("anchor_serialization_source_member_missing")
            member_raw = extracted.read()
        extracted_name = {
            "borsh-0.9.3.crate": "borsh-0.9.3-ser-mod.rs",
            "borsh-derive-internal-0.9.3.crate": "borsh-derive-internal-0.9.3-struct-ser.rs",
            "solana-program-1.15.2.crate": "solana-program-1.15.2-pubkey.rs",
        }[archive_name]
        if _sha(member_raw) != pinned_files[extracted_name]["sha256"]:
            raise FeasibilityError("anchor_serialization_extracted_member_mismatch")

    borsh_ser = _safe_source_path("borsh-0.9.3-ser-mod.rs").read_text()
    struct_ser = _safe_source_path("borsh-derive-internal-0.9.3-struct-ser.rs").read_text()
    pubkey_source = _safe_source_path("solana-program-1.15.2-pubkey.rs").read_text()
    emit_cpi = (
        ROOT / structural.ANCHOR_CAPTURE_DIR / f"emit-cpi-{structural.ANCHOR_EVENT_COMMIT}.rs"
    ).read_text()
    if not all(
        (
            "to_le_bytes()" in borsh_ser,
            "impl BorshSerialize for String" in borsh_ser,
            "self.as_bytes().serialize(writer)" in borsh_ser,
            "u32::try_from(self.len())" in borsh_ser,
            "for field in &fields.named" in struct_ser,
            "serialize(&self.#field_name, writer)?" in struct_ser,
            "pub struct Pubkey(pub(crate) [u8; 32]);" in pubkey_source,
            "BorshSerialize" in pubkey_source,
            "d.append(&mut self.try_to_vec().unwrap())" in emit_cpi,
            "EVENT_IX_TAG_LE" in emit_cpi,
        )
    ):
        raise FeasibilityError("anchor_borsh_serialization_contract_unrecognized")
    # The older IDL source receipt independently pins the event trait/macro files.
    source_receipt = _read_json(ROOT / IDL_HISTORY_RECEIPT)
    verified_event_source = structural._verify_anchor_sources()
    if (
        source_receipt.get("source_receipts", {})
        .get("anchor_event_source", {})
        .get("source_commit")
        != structural.ANCHOR_EVENT_COMMIT
        or source_receipt.get("source_receipts", {}).get("anchor_event_source", {}).get("files")
        != verified_event_source.get("files")
        or source_receipt.get("event_cpi_structural_scan", {}).get("claim_scope")
        != (
            "structural Anchor event-CPI envelope only; no payload, event validity, "
            "settlement, or economic admission"
        )
    ):
        raise FeasibilityError("anchor_event_source_receipt_binding_invalid")
    return {
        "anchor_commit": structural.ANCHOR_EVENT_COMMIT,
        "anchor_lang_version": anchor_manifest["package"]["version"],
        "borsh_version": "0.9.3",
        "borsh_crate_sha256": packages[("borsh", "0.9.3")]["checksum"],
        "borsh_derive_version": "0.9.3",
        "borsh_derive_crate_sha256": packages[("borsh-derive", "0.9.3")]["checksum"],
        "borsh_derive_internal_version": "0.9.3",
        "borsh_derive_internal_crate_sha256": packages[("borsh-derive-internal", "0.9.3")][
            "checksum"
        ],
        "solana_program_version": "1.15.2",
        "solana_program_crate_sha256": packages[("solana-program", "1.15.2")]["checksum"],
        "cargo_lock_sha256": pinned_files["anchor-Cargo.lock"]["sha256"],
        "source_capture_manifest_sha256": ANCHOR_ENCODING_MANIFEST_SHA256,
        "encoding_claim": (
            "Anchor Event::data prefixes the event discriminator to Borsh 0.9.3 derived struct "
            "serialization; emit_cpi prefixes EVENT_IX_TAG_LE. The IDL field list/order is the "
            "candidate schema. This pins source serialization mechanics, not deployed program code."
        ),
    }


def _schema_fields(event_type: dict[str, Any]) -> list[dict[str, Any]] | None:
    type_def = event_type.get("type") if isinstance(event_type, dict) else None
    if not isinstance(type_def, dict) or type_def.get("kind") != "struct":
        return None
    fields = type_def.get("fields")
    if not isinstance(fields, list):
        return None
    normalized: list[dict[str, Any]] = []
    for field in fields:
        if (
            not isinstance(field, dict)
            or not isinstance(field.get("name"), str)
            or not isinstance(field.get("type"), str)
        ):
            return None
        normalized.append({"name": field["name"], "type": field["type"]})
    return normalized


def _schema_sha256(fields: list[dict[str, Any]]) -> str:
    return _sha(_canonical(fields))


def load_layouts() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    receipt_path = ROOT / IDL_HISTORY_RECEIPT
    receipt_raw = receipt_path.read_bytes()
    if _sha(receipt_raw) != IDL_HISTORY_RECEIPT_SHA256:
        raise FeasibilityError("idl_history_receipt_hash_mismatch")
    receipt = json.loads(receipt_raw)
    if (
        receipt.get("schema") != "pumpswap-c4-idl-history-qualification-v3"
        or receipt.get("payload_decode") is not False
        or receipt.get("source_receipts", {}).get("pump_amm_idl_history_entries")
        != EXPECTED_HISTORY_COMMITS
        or receipt.get("included_frames") != list(c4_replay.INCLUDED)
        or receipt.get("excluded")
        != [{"frame": c4_replay.EXCLUDED, "reason": "frozen tail2 body remains unopened"}]
        or receipt.get("event_cpi_structural_scan", {})
        .get("nested_event_discriminator_references", {})
        .get("BuyEvent")
        != EXPECTED_EVENT_COUNTS["BuyEvent"]
        or receipt.get("event_cpi_structural_scan", {})
        .get("nested_event_discriminator_references", {})
        .get("SellEvent")
        != EXPECTED_EVENT_COUNTS["SellEvent"]
    ):
        raise FeasibilityError("idl_history_receipt_scope_or_denominator_mismatch")

    versions, _history_manifest = structural._load_idl_history()
    if (
        _sha((ROOT / structural.IDL_CAPTURE_DIR / "capture_manifest.json").read_bytes())
        != IDL_HISTORY_CAPTURE_MANIFEST_SHA256
        or len(versions) != EXPECTED_HISTORY_COMMITS
    ):
        raise FeasibilityError("idl_history_snapshots_invalid")
    receipt_versions = {
        row["commit"]: row for row in receipt["source_receipts"]["pump_amm_idl_history_snapshots"]
    }
    layouts: list[dict[str, Any]] = []
    for version in versions:
        capture = version["capture"]
        receipt_row = receipt_versions.get(capture["commit"])
        if receipt_row is None or receipt_row.get("sha256") != capture["sha256"]:
            raise FeasibilityError("idl_snapshot_receipt_pin_mismatch")
        for event_name in sorted(TARGET_EVENTS):
            event_def = next(
                (row for row in version["idl"].get("events", []) if row.get("name") == event_name),
                None,
            )
            type_def = next(
                (row for row in version["idl"].get("types", []) if row.get("name") == event_name),
                None,
            )
            fields = _schema_fields(type_def) if type_def else None
            discriminator = (
                bytes(event_def.get("discriminator", [])) if event_def is not None else None
            )
            if not isinstance(discriminator, bytes) or len(discriminator) != 8:
                layouts.append(
                    {
                        "event_name": event_name,
                        "source_commit": capture["commit"],
                        "source_commit_time_utc": capture["commit_time_utc"],
                        "source_idl_sha256": capture["sha256"],
                        "discriminator_hex": None,
                        "fields": None,
                        "layout_sha256": None,
                        "available": False,
                    }
                )
                continue
            layouts.append(
                {
                    "event_name": event_name,
                    "source_commit": capture["commit"],
                    "source_commit_time_utc": capture["commit_time_utc"],
                    "source_idl_sha256": capture["sha256"],
                    "discriminator_hex": discriminator.hex(),
                    "fields": fields,
                    "layout_sha256": _schema_sha256(fields) if fields is not None else None,
                    "available": fields is not None,
                }
            )
    if len(layouts) != EXPECTED_HISTORY_COMMITS * len(TARGET_EVENTS):
        raise FeasibilityError("historical_layout_enumeration_incomplete")
    for event_name in TARGET_EVENTS:
        discs = {
            row["discriminator_hex"]
            for row in layouts
            if row["event_name"] == event_name and row["available"]
        }
        if len(discs) != 1:
            raise FeasibilityError("historical_event_discriminator_changed_or_ambiguous")
    return layouts, receipt


def parse_borsh_fields(fields: list[dict[str, Any]], payload: bytes) -> dict[str, Any]:
    """Parse supported Borsh primitives with bounds checks and exact-consumption reporting."""
    cursor = 0
    decoded: dict[str, Any] = {}
    offsets: list[dict[str, Any]] = []
    for field in fields:
        name, kind = field.get("name"), field.get("type")
        if not isinstance(name, str) or not isinstance(kind, str):
            return {"status": "unsupported_type", "reason": "field_schema_invalid"}
        start = cursor
        if kind in SUPPORTED_TYPES:
            width, signed = SUPPORTED_TYPES[kind]
            if len(payload) - cursor < width:
                return {"status": "truncated", "reason": f"field:{name}", "consumed": cursor}
            value = int.from_bytes(payload[cursor : cursor + width], "little", signed=signed)
            cursor += width
        elif kind == "pubkey":
            width = 32
            if len(payload) - cursor < width:
                return {"status": "truncated", "reason": f"field:{name}", "consumed": cursor}
            value = payload[cursor : cursor + width]
            cursor += width
        elif kind == "bool":
            if len(payload) - cursor < 1:
                return {"status": "truncated", "reason": f"field:{name}", "consumed": cursor}
            byte = payload[cursor]
            if byte not in (0, 1):
                return {"status": "invalid_bool", "reason": f"field:{name}", "consumed": cursor}
            value = bool(byte)
            cursor += 1
        elif kind == "string":
            if len(payload) - cursor < 4:
                return {"status": "truncated", "reason": f"field:{name}_length", "consumed": cursor}
            byte_length = int.from_bytes(payload[cursor : cursor + 4], "little", signed=False)
            cursor += 4
            remaining = len(payload) - cursor
            # Compare before slicing/decoding so an attacker-controlled u32 never drives allocation.
            if byte_length > remaining:
                return {
                    "status": "truncated",
                    "reason": f"field:{name}_length_exceeds_remaining",
                    "consumed": cursor,
                    "declared_bytes": byte_length,
                    "remaining_bytes": remaining,
                }
            raw = payload[cursor : cursor + byte_length]
            try:
                value = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return {"status": "invalid_utf8", "reason": f"field:{name}", "consumed": cursor}
            cursor += byte_length
        else:
            return {"status": "unsupported_type", "reason": f"field:{name}:{kind}"}
        decoded[name] = value
        offsets.append(
            {
                "name": name,
                "type": kind,
                "start": start,
                "end": cursor,
                "value_bytes": cursor - start,
            }
        )
    if cursor != len(payload):
        return {
            "status": "trailing_bytes",
            "consumed": cursor,
            "remaining_bytes": len(payload) - cursor,
            "fields": decoded,
            "offsets": offsets,
        }
    return {"status": "exact", "consumed": cursor, "fields": decoded, "offsets": offsets}


def parse_event_data(
    data: bytes, *, event_tag: bytes, event_discriminator: bytes, fields: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(data) < 8 or data[:8] != event_tag:
        return {"status": "bad_event_tag"}
    if len(data) < WIRE_PREFIX_BYTES:
        return {"status": "truncated_event_discriminator"}
    if data[8:16] != event_discriminator:
        return {"status": "event_discriminator_mismatch"}
    parsed = parse_borsh_fields(fields, data[WIRE_PREFIX_BYTES:])
    parsed["wire_bytes"] = len(data)
    parsed["payload_bytes"] = len(data) - WIRE_PREFIX_BYTES
    return parsed


def classify_layouts(
    data: bytes, event_name: str, layouts: list[dict[str, Any]], event_tag: bytes
) -> tuple[str, list[dict[str, Any]]]:
    candidates = [row for row in layouts if row["event_name"] == event_name]
    exact: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    saw_malformed = False
    supported = False
    for layout in candidates:
        if not layout["available"]:
            source_results.append(
                {
                    "source_commit": layout["source_commit"],
                    "source_idl_sha256": layout["source_idl_sha256"],
                    "layout_sha256": None,
                    "parse_status": "unsupported_type",
                }
            )
            continue
        supported = True
        parsed = parse_event_data(
            data,
            event_tag=event_tag,
            event_discriminator=bytes.fromhex(layout["discriminator_hex"]),
            fields=layout["fields"],
        )
        status = parsed["status"]
        if status == "exact":
            exact.append(layout)
            source_status = "exact"
        elif status == "unsupported_type":
            source_status = "unsupported"
        else:
            saw_malformed = True
            source_status = "malformed"
        source_results.append(
            {
                "source_commit": layout["source_commit"],
                "source_idl_sha256": layout["source_idl_sha256"],
                "layout_sha256": layout["layout_sha256"],
                "parse_status": source_status,
                "parse_reason": status if source_status != "exact" else None,
            }
        )
    schema_matches = sorted({row["layout_sha256"] for row in exact})
    if len(schema_matches) > 1:
        return "ambiguous", source_results
    if len(schema_matches) == 1:
        sources = [row for row in exact if row["layout_sha256"] == schema_matches[0]]
        return ("equivalent" if len(sources) > 1 else "unique"), source_results
    if not supported:
        return "unsupported", source_results
    return ("malformed" if saw_malformed else "unsupported"), source_results


def _resolve_event_discriminators(
    layouts: list[dict[str, Any]], receipt: dict[str, Any]
) -> dict[bytes, str]:
    expected = {
        row["event_name"]: bytes.fromhex(row["event_discriminator_hex"])
        for row in receipt["event_cpi_structural_scan"]["nested_event_discriminator_identities"]
        if row["event_name"] in TARGET_EVENTS
    }
    if set(expected) != TARGET_EVENTS or any(len(value) != 8 for value in expected.values()):
        raise FeasibilityError("upstream_target_event_discriminator_missing")
    for event_name, discriminator in expected.items():
        observed = {
            bytes.fromhex(row["discriminator_hex"])
            for row in layouts
            if row["event_name"] == event_name and row["available"]
        }
        if observed != {discriminator}:
            raise FeasibilityError("target_event_discriminator_source_conflict")
    reverse = {disc: name for name, disc in expected.items()}
    if len(reverse) != len(expected):
        raise FeasibilityError("target_event_discriminator_collision")
    return reverse


def replay() -> dict[str, Any]:
    """Recompute target event wire-layout fit over only the frozen ten included frames."""
    encoding = verify_anchor_encoding_sources()
    layouts, upstream_receipt = load_layouts()
    discriminator_names = _resolve_event_discriminators(layouts, upstream_receipt)
    frozen_manifest, frozen_manifest_sha = c4_replay._verify_frozen_manifest(
        c4_replay.MANIFEST_DEFAULT
    )
    zstd = shutil.which("zstd")
    if not zstd:
        raise FeasibilityError("zstd_unavailable")

    counts: Counter[tuple[str, str]] = Counter()
    aggregate_outcomes: Counter[tuple[str, str, str]] = Counter()
    source_outcomes: Counter[tuple[str, str, str, str]] = Counter()
    by_frame: Counter[tuple[str, str, str, str]] = Counter()
    wire_lengths: Counter[tuple[str, int]] = Counter()
    offset_ranges: dict[tuple[str, str, str], dict[str, Any]] = {}
    capture_inputs: list[dict[str, Any]] = []
    frame_rows = {row["frame_id"]: row for row in frozen_manifest["frame_metadata"]}

    for frame_id in c4_replay.INCLUDED:
        frame_meta = frame_rows.get(frame_id)
        if frame_meta is None:
            raise FeasibilityError("included_frame_metadata_missing")
        frame_root, _plan, summary, _journal, plan_sha = c4_replay._verify_frame(
            frozen_manifest, frame_meta, c4_replay.ROOT / c4_replay.EXPECTED_CAMPAIGN_ROOT
        )
        captures = _capture_rows(frame_root, v3_layout=True)
        if len(captures) != 16:
            raise FeasibilityError("included_frame_capture_count_mismatch")
        seen_slots: set[int] = set()
        for capture_path, capture_receipt, request in captures:
            params = request.get("params", [])
            slot = params[0] if params else None
            if type(slot) is not int or slot in seen_slots:
                raise FeasibilityError("included_frame_slot_duplicate_or_invalid")
            seen_slots.add(slot)
            storage = capture_receipt.get("storage", {})
            capture_inputs.append(
                {
                    "frame_id": frame_id,
                    "slot": slot,
                    "response_sha256": capture_receipt.get("response_sha256"),
                    "response_bytes": capture_receipt.get("response_bytes"),
                    "stored_sha256": storage.get("sha256"),
                    "stored_bytes": storage.get("bytes"),
                }
            )
            block = _read_block(zstd, capture_path, capture_receipt)
            for tx in block["transactions"]:
                meta = tx.get("meta")
                if not isinstance(meta, dict):
                    continue
                tx_status = "failed" if meta.get("err") is not None else "success"
                keys = structural._resolved_keys(tx)
                groups = meta.get("innerInstructions")
                if not isinstance(groups, list):
                    continue
                for group in groups:
                    if not isinstance(group, dict) or not isinstance(
                        group.get("instructions"), list
                    ):
                        continue
                    for instruction in group["instructions"]:
                        if structural._program(instruction, keys) != structural.PROGRAM_ID:
                            continue
                        encoded = instruction.get("data") if isinstance(instruction, dict) else None
                        if not isinstance(encoded, str):
                            continue
                        try:
                            data = structural._b58decode(encoded)
                        except (ValueError, TypeError):
                            continue
                        if not data.startswith(structural.ANCHOR_EVENT_TAG_LE) or len(data) < 16:
                            continue
                        event_name = discriminator_names.get(data[8:16])
                        if event_name not in TARGET_EVENTS:
                            # Other 468 event-CPI references remain excluded from payload decoding.
                            continue
                        outcome, source_rows = classify_layouts(
                            data, event_name, layouts, structural.ANCHOR_EVENT_TAG_LE
                        )
                        counts[(event_name, tx_status)] += 1
                        aggregate_outcomes[(event_name, tx_status, outcome)] += 1
                        by_frame[(frame_id, event_name, tx_status, outcome)] += 1
                        wire_lengths[(event_name, len(data))] += 1
                        for source_row in source_rows:
                            source_outcomes[
                                (
                                    event_name,
                                    source_row["source_commit"],
                                    tx_status,
                                    source_row["parse_status"],
                                )
                            ] += 1
                        match_rows_by_schema = {
                            layout["layout_sha256"]: layout
                            for layout, source_row in zip(
                                [row for row in layouts if row["event_name"] == event_name],
                                source_rows,
                                strict=True,
                            )
                            if source_row["parse_status"] == "exact"
                        }
                        match_rows = list(match_rows_by_schema.values())
                        for layout in match_rows:
                            parsed = parse_event_data(
                                data,
                                event_tag=structural.ANCHOR_EVENT_TAG_LE,
                                event_discriminator=bytes.fromhex(layout["discriminator_hex"]),
                                fields=layout["fields"],
                            )
                            for field_offset in parsed["offsets"]:
                                key = (event_name, layout["layout_sha256"], field_offset["name"])
                                profile = offset_ranges.setdefault(
                                    key,
                                    {
                                        "event_name": event_name,
                                        "layout_sha256": layout["layout_sha256"],
                                        "field_name": field_offset["name"],
                                        "type": field_offset["type"],
                                        "start_min": field_offset["start"],
                                        "start_max": field_offset["start"],
                                        "end_min": field_offset["end"],
                                        "end_max": field_offset["end"],
                                        "observations": 0,
                                    },
                                )
                                profile["start_min"] = min(
                                    profile["start_min"], field_offset["start"]
                                )
                                profile["start_max"] = max(
                                    profile["start_max"], field_offset["start"]
                                )
                                profile["end_min"] = min(profile["end_min"], field_offset["end"])
                                profile["end_max"] = max(profile["end_max"], field_offset["end"])
                                profile["observations"] += 1

        if len(seen_slots) != 16 or summary.get("status") != "frame_complete":
            raise FeasibilityError("included_frame_not_complete")

    total_by_event = {
        event: sum(counts[(event, status)] for status in ("success", "failed"))
        for event in TARGET_EVENTS
    }
    if total_by_event != EXPECTED_EVENT_COUNTS:
        raise FeasibilityError("target_event_reference_denominator_mismatch")
    if {
        event: {status: counts[(event, status)] for status in ("success", "failed")}
        for event in TARGET_EVENTS
    } != EXPECTED_EVENT_STATUS:
        raise FeasibilityError("target_event_transaction_status_denominator_mismatch")

    layout_profile = []
    for layout in layouts:
        layout_profile.append(
            {
                "event_name": layout["event_name"],
                "source_commit": layout["source_commit"],
                "source_commit_time_utc": layout["source_commit_time_utc"],
                "source_idl_sha256": layout["source_idl_sha256"],
                "event_discriminator_hex": layout["discriminator_hex"],
                "field_count": len(layout["fields"]) if layout["fields"] is not None else None,
                "layout_sha256": layout["layout_sha256"],
                "fields": layout["fields"],
                "available": layout["available"],
            }
        )
    source_rows = [
        {
            "event_name": event,
            "source_commit": commit,
            "source_idl_sha256": next(
                row["source_idl_sha256"]
                for row in layouts
                if row["event_name"] == event and row["source_commit"] == commit
            ),
            "transaction_status": status,
            "layout_parse_status": parse_status,
            "references": count,
        }
        for (event, commit, status, parse_status), count in sorted(source_outcomes.items())
    ]
    return {
        "schema": SCHEMA,
        "status": "complete_selected_event_layout_feasibility_replay",
        "claim_label": "IDL-consistent development observations",
        "claim_scope": (
            "Offline Borsh full-byte parsing against every pinned historical official IDL layout "
            "for only BuyEvent and SellEvent in the ten included c4 frames; not deployed layout, "
            "event validity, settlement, economic admission, completeness, or zero."
        ),
        "activation_authorized": False,
        "live_rpc_requests": 0,
        "included_frames": list(c4_replay.INCLUDED),
        "excluded": [
            {
                "reference_class": "all other event-CPI references",
                "references": 468,
                "reason": (
                    "not in the requested BuyEvent/SellEvent population; payloads not decoded"
                ),
            },
            {"frame": c4_replay.EXCLUDED, "reason": "frozen tail2 body remains unopened"},
        ],
        "upstream_structural_receipt": {
            "path": str(IDL_HISTORY_RECEIPT),
            "sha256": IDL_HISTORY_RECEIPT_SHA256,
            "claim_used": (
                "source-pinned event discriminator identity and 10-frame structural envelope only"
            ),
        },
        "frozen_manifest_sha256": frozen_manifest_sha,
        "anchor_serialization": encoding,
        "idl_history_manifest_sha256": IDL_HISTORY_CAPTURE_MANIFEST_SHA256,
        "idl_history_snapshot_count": EXPECTED_HISTORY_COMMITS,
        "historical_layouts_enumerated": layout_profile,
        "reference_accounting": {
            "total_target_references": sum(total_by_event.values()),
            "by_event_and_transaction_status": {
                event: {status: counts[(event, status)] for status in ("success", "failed")}
                for event in sorted(TARGET_EVENTS)
            },
            "by_event_and_overall_layout_outcome": [
                {
                    "event_name": event,
                    "transaction_status": status,
                    "layout_outcome": outcome,
                    "references": count,
                }
                for (event, status, outcome), count in sorted(aggregate_outcomes.items())
            ],
            "by_source_commit_hash_and_transaction_status": source_rows,
            "observed_wire_length_bytes_including_anchor_tag_and_discriminator": [
                {"event_name": event, "wire_bytes": wire_len, "references": count}
                for (event, wire_len), count in sorted(wire_lengths.items())
            ],
            "matched_field_byte_offset_ranges": sorted(
                offset_ranges.values(),
                key=lambda row: (row["event_name"], row["start_min"], row["field_name"]),
            ),
        },
        "capture_inputs": capture_inputs,
        "limitations": [
            "all decoded fields are IDL-consistent development observations only; no "
            "event-validity "
            "or deployed-version claim",
            "IDL commit date is not used to select a deployed layout; all 18 captured source "
            "revisions are tested equally",
            "failed transaction status remains failed even when its event-CPI payload is "
            "IDL-consistent",
            "no settlement, economic admission, completeness, exact economic zero, provider truth, "
            "or independent replication claim",
            "Anchor/Borsh serialization mechanics are pinned at Anchor commit "
            "23b90bffc023058aec1004dbc0898c233fb6e9b0 with lock-resolved Borsh 0.9.3 and Solana "
            "Pubkey 1.15.2; this does not establish that the deployed Pump program used "
            "that source "
            "version",
            "only the ten included c4 frames are read; other event references and the frozen "
            "23:00Z tail2 body are excluded",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "work/brain-senses-pumpswap-c4-event-layout-feasibility-v1/report.json",
    )
    args = parser.parse_args()
    report = replay()
    output = args.output.resolve()
    allowed_root = (ROOT / "work/brain-senses-pumpswap-c4-event-layout-feasibility-v1").resolve()
    if not output.is_relative_to(allowed_root):
        raise FeasibilityError("output_must_stay_in_ignored_work_root")
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded_report = json.dumps(report, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded_report)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    report["report_path"] = str(output.relative_to(ROOT))
    report["report_sha256"] = _sha(output.read_bytes())
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
