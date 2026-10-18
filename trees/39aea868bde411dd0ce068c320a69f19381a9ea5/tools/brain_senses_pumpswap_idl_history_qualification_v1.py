#!/usr/bin/env python3
"""Qualify c4 Pump AMM discriminator identities and Anchor event-CPI shape only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memecoin_pumpswap.decoder import decode58  # noqa: E402
from tools import brain_senses_c4_captured_class_replay_v1 as c4_replay  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block  # noqa: E402

PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
UNKNOWN_RECEIPT = Path(
    "docs/evidence/pumpswap-c4-idl-unrecognized-discriminators-v1-2026-10-11.json"
)
UNKNOWN_RECEIPT_SHA256 = "c4fdaec0145ab96ed229af33e26aa08d192a02d493c1e7939c648cba3ed4338e"
IDL_CAPTURE_DIR = Path("work/brain-senses-pumpswap-idl-history-2026-10-11")
IDL_CAPTURE_MANIFEST_SHA256 = "57a671e4a2b7bfaf84e30dc1804ce05ff4babb1344481f1dc78a1fa4a7eadf03"
ANCHOR_CAPTURE_DIR = Path("work/brain-senses-anchor-event-source-2026-10-11")
ANCHOR_CAPTURE_MANIFEST_SHA256 = "a888592d17686bd9869d6254d9600ff04524ae80ab0b826270a08413c87047d2"
ANCHOR_EVENT_COMMIT = "23b90bffc023058aec1004dbc0898c233fb6e9b0"
ANCHOR_EVENT_TAG_LE = bytes.fromhex("e445a52e51cb9a1d")
CAMPAIGN_ROOT = ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-4"
MANIFEST_PATH = ROOT / "work/brain-senses-c4-captured-class-census-v1/frozen-manifest.json"
EXPECTED_MANIFEST_SHA256 = "4b5ed2c1432c9af2a899ff3e8e1481bb2db4e8026043e0b1d309740408170d6a"
EXPECTED_INCLUDED = tuple(f"20261010T{hour:02d}0000Z" for hour in range(13, 23))
EXCLUDED_TAIL2 = "20261010T230000Z"
EXPECTED_EVENT_CPI_REFS = 9_598
EXPECTED_UNRECOGNIZED_REFS = 11_623
EXPECTED_UNRECOGNIZED_DISCRIMINATORS = 16
EXPECTED_EVENT_AUTHORITY = "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR"


class QualificationError(ValueError):
    """Fail-closed source binding or structural mismatch."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise QualificationError("expected_json_object")
    return value


def _b58encode(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    zeros = len(raw) - len(raw.lstrip(b"\0"))
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, digit = divmod(number, 58)
        encoded = alphabet[digit] + encoded
    return "1" * zeros + encoded


def _b58decode(text: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    try:
        for char in text:
            number = number * 58 + alphabet.index(char)
    except ValueError:
        raise QualificationError("base58_invalid") from None
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def _ed25519_compressed_point_is_valid(encoded: bytes) -> bool:
    """Return whether bytes decompress as a canonical Edwards25519 point."""
    if len(encoded) != 32:
        return False
    p = 2**255 - 19
    d = (-121665 * pow(121666, p - 2, p)) % p
    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= p:
        return False
    y2 = y * y % p
    x2 = (y2 - 1) * pow((d * y2 + 1) % p, p - 2, p) % p
    x = pow(x2, (p + 3) // 8, p)
    if x * x % p != x2:
        sqrt_m1 = pow(2, (p - 1) // 4, p)
        x = x * sqrt_m1 % p
    if x * x % p != x2 or (x == 0 and sign == 1):
        return False
    return True


def _find_program_address(seed: bytes, program_id: str) -> str:
    program = _b58decode(program_id)
    if len(program) != 32:
        raise QualificationError("program_id_width_invalid")
    for bump in range(255, -1, -1):
        candidate = hashlib.sha256(
            seed + bytes([bump]) + program + b"ProgramDerivedAddress"
        ).digest()
        if not _ed25519_compressed_point_is_valid(candidate):
            return _b58encode(candidate)
    raise QualificationError("pda_bump_not_found")


def _resolved_keys(tx: dict[str, Any]) -> list[str | None]:
    message = tx.get("transaction", {}).get("message", {})
    raw_keys = message.get("accountKeys", [])
    keys = [entry.get("pubkey") if isinstance(entry, dict) else entry for entry in raw_keys]
    loaded = tx.get("meta", {}).get("loadedAddresses", {})
    keys.extend(loaded.get("writable", []))
    keys.extend(loaded.get("readonly", []))
    return [key if isinstance(key, str) else None for key in keys]


def _program(ix: Any, keys: list[str | None]) -> str | None:
    if not isinstance(ix, dict):
        return None
    direct = ix.get("programId")
    if isinstance(direct, str):
        return direct
    index = ix.get("programIdIndex")
    return keys[index] if type(index) is int and 0 <= index < len(keys) else None


def _account_key(value: Any, keys: list[str | None]) -> str | None:
    if isinstance(value, str):
        return value
    if type(value) is int and 0 <= value < len(keys):
        return keys[value]
    return None


def _trace_parent(
    stack: list[tuple[int, str | None]],
    height: Any,
    program_id: str | None,
    outer_parent: str | None,
) -> str | None:
    """Advance a depth-first inner-instruction stack and return the direct parent."""
    if type(height) is not int or height < 2:
        raise QualificationError("inner_instruction_stack_height_missing_or_invalid")
    while stack and stack[-1][0] >= height:
        stack.pop()
    if stack:
        parent_height, parent_program = stack[-1]
        if parent_height != height - 1:
            raise QualificationError("inner_instruction_stack_height_discontinuous")
    elif height == 2:
        parent_program = outer_parent
    else:
        raise QualificationError("inner_instruction_stack_parent_missing")
    stack.append((height, program_id))
    return parent_program


def _load_idl_history() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = ROOT / IDL_CAPTURE_DIR / "capture_manifest.json"
    raw = manifest_path.read_bytes()
    if _sha(raw) != IDL_CAPTURE_MANIFEST_SHA256:
        raise QualificationError("idl_capture_manifest_hash_mismatch")
    manifest = _read_json(manifest_path)
    if manifest.get("history_entry_count") != 18:
        raise QualificationError("idl_history_entry_count_mismatch")
    versions: list[dict[str, Any]] = []
    for item in manifest.get("versions", []):
        if not isinstance(item, dict):
            raise QualificationError("idl_history_row_invalid")
        path = ROOT / item["path"]
        data = path.read_bytes()
        if len(data) != item.get("bytes") or _sha(data) != item.get("sha256"):
            raise QualificationError("idl_history_blob_mismatch")
        idl = json.loads(data)
        if idl.get("address") != PROGRAM_ID:
            raise QualificationError("historical_idl_program_id_mismatch")
        versions.append({"capture": item, "idl": idl})
    if len(versions) != 18:
        raise QualificationError("idl_history_count_mismatch")
    return versions, manifest


def _instruction_identities(
    versions: list[dict[str, Any]], observed: set[str]
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for version in versions:
        capture, idl = version["capture"], version["idl"]
        for instruction in idl.get("instructions", []):
            discriminator = bytes(instruction.get("discriminator", [])).hex()
            if discriminator in observed:
                out[discriminator].append(
                    {
                        "name": instruction.get("name"),
                        "commit": capture["commit"],
                        "commit_time_utc": capture["commit_time_utc"],
                        "idl_sha256": capture["sha256"],
                    }
                )
    return out


def _event_identities(versions: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, str]]]:
    out: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for version in versions:
        capture = version["capture"]
        for event in version["idl"].get("events", []):
            discriminator = bytes(event.get("discriminator", [])).hex()
            name = event.get("name")
            if isinstance(name, str):
                out[discriminator][name] = {
                    "source_commit": capture["commit"],
                    "source_commit_time_utc": capture["commit_time_utc"],
                    "source_idl_sha256": capture["sha256"],
                }
    return out


def _verify_anchor_sources() -> dict[str, Any]:
    manifest_path = ROOT / ANCHOR_CAPTURE_DIR / "capture_manifest.json"
    raw_manifest = manifest_path.read_bytes()
    if _sha(raw_manifest) != ANCHOR_CAPTURE_MANIFEST_SHA256:
        raise QualificationError("anchor_capture_manifest_hash_mismatch")
    manifest = _read_json(manifest_path)
    if manifest.get("source_commit") != ANCHOR_EVENT_COMMIT:
        raise QualificationError("anchor_event_commit_mismatch")
    verified: list[dict[str, Any]] = []
    for row in manifest.get("files", []):
        path = ROOT / row["path"]
        data = path.read_bytes()
        if len(data) != row.get("bytes") or _sha(data) != row.get("sha256"):
            raise QualificationError("anchor_source_file_mismatch")
        verified.append(
            {
                "path": row["path"],
                "url": row["url"],
                "bytes": row["bytes"],
                "sha256": row["sha256"],
            }
        )
    event_rs = next(
        (
            ROOT / row["path"]
            for row in manifest["files"]
            if row["path"].endswith("lang-event-" + ANCHOR_EVENT_COMMIT + ".rs")
        ),
        None,
    )
    macro_rs = next(
        (
            ROOT / row["path"]
            for row in manifest["files"]
            if row["path"].endswith("emit-cpi-" + ANCHOR_EVENT_COMMIT + ".rs")
        ),
        None,
    )
    if event_rs is None or macro_rs is None:
        raise QualificationError("anchor_event_sources_missing")
    event_text, macro_text = event_rs.read_text(), macro_rs.read_text()
    if "EVENT_IX_TAG: u64 = 0x1d9acb512ea545e4" not in event_text:
        raise QualificationError("anchor_event_tag_source_mismatch")
    if not all(
        token in macro_text
        for token in (
            "EVENT_IX_TAG_LE",
            "invoke_signed",
            "[authority_info]",
            'b"__event_authority"',
        )
    ):
        raise QualificationError("anchor_emit_cpi_source_shape_mismatch")
    return {
        "source_commit": manifest["source_commit"],
        "capture_manifest_sha256": ANCHOR_CAPTURE_MANIFEST_SHA256,
        "files": verified,
    }


def _event_reference_scan(
    event_identities: dict[str, dict[str, dict[str, str]]],
    zstd: str,
    frozen_manifest: dict[str, Any],
) -> dict[str, Any]:
    pda = _find_program_address(b"__event_authority", PROGRAM_ID)
    if pda != EXPECTED_EVENT_AUTHORITY:
        raise QualificationError("event_authority_pda_mismatch")
    counts: Counter[str] = Counter()
    by_event: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    by_ancestry: Counter[str] = Counter()
    per_frame: dict[str, Counter[str]] = defaultdict(Counter)
    total_blocks = total_transactions = 0
    for frame in EXPECTED_INCLUDED:
        frame_root = CAMPAIGN_ROOT / "epochs" / frame
        frame_meta = {row["frame_id"]: row for row in frozen_manifest["frame_metadata"]}[frame]
        _root, _plan, _summary, _journal, _plan_sha = c4_replay._verify_frame(
            frozen_manifest, frame_meta, CAMPAIGN_ROOT
        )
        slots: set[int] = set()
        for capture_path, receipt, request in _capture_rows(frame_root, v3_layout=True):
            params = request.get("params", [])
            slot = params[0] if params else None
            if type(slot) is not int or slot in slots:
                raise QualificationError("captured_slot_duplicate_or_invalid")
            slots.add(slot)
            block = _read_block(zstd, capture_path, receipt)
            total_blocks += 1
            for tx in block.get("transactions", []):
                total_transactions += 1
                meta = tx.get("meta")
                if not isinstance(meta, dict):
                    continue
                status = "failed" if meta.get("err") is not None else "success"
                keys = _resolved_keys(tx)
                groups = meta.get("innerInstructions")
                if not isinstance(groups, list):
                    continue
                for group in groups:
                    if not isinstance(group, dict) or not isinstance(
                        group.get("instructions"), list
                    ):
                        continue
                    outer = tx.get("transaction", {}).get("message", {}).get("instructions", [])
                    group_index = group.get("index")
                    outer_parent = (
                        _program(outer[group_index], keys)
                        if isinstance(outer, list)
                        and type(group_index) is int
                        and 0 <= group_index < len(outer)
                        else None
                    )
                    has_event_candidate = False
                    for candidate_ix in group["instructions"]:
                        candidate_pid = _program(candidate_ix, keys)
                        try:
                            candidate_data = (
                                decode58(candidate_ix.get("data", ""))
                                if isinstance(candidate_ix, dict)
                                else b""
                            )
                        except (ValueError, TypeError):
                            candidate_data = b""
                        if (
                            candidate_pid == PROGRAM_ID
                            and candidate_data[:8] == ANCHOR_EVENT_TAG_LE
                        ):
                            has_event_candidate = True
                            break
                    if not has_event_candidate:
                        continue
                    trace_stack: list[tuple[int, str | None]] = []
                    for ix in group["instructions"]:
                        pid = _program(ix, keys)
                        try:
                            data = decode58(ix.get("data", "")) if isinstance(ix, dict) else b""
                        except (ValueError, TypeError):
                            data = b""
                        direct_parent = _trace_parent(
                            trace_stack,
                            ix.get("stackHeight") if isinstance(ix, dict) else None,
                            pid,
                            outer_parent,
                        )
                        if pid == PROGRAM_ID and data[:8] == ANCHOR_EVENT_TAG_LE:
                            counts["references"] += 1
                            per_frame[frame]["references"] += 1
                            by_status[status] += 1
                            per_frame[frame][status] += 1
                            if len(data) < 16:
                                counts["failed_short_data"] += 1
                            else:
                                event_disc = data[8:16].hex()
                                names = event_identities.get(event_disc, {})
                                if len(names) == 1:
                                    name = next(iter(names))
                                    by_event[name] += 1
                                    per_frame[frame][f"event:{name}"] += 1
                                    counts["known_unique_event_discriminator"] += 1
                                else:
                                    counts[
                                        "failed_event_discriminator_unresolved_or_ambiguous"
                                    ] += 1
                            account_values = ix.get("accounts")
                            if not isinstance(account_values, list) or len(account_values) != 1:
                                counts["failed_account_count"] += 1
                            elif _account_key(account_values[0], keys) != pda:
                                counts["failed_event_authority_key"] += 1
                            else:
                                counts["event_authority_key_match"] += 1
                            if direct_parent == PROGRAM_ID:
                                by_ancestry["verified_direct_pump_parent"] += 1
                                per_frame[frame]["ancestry_verified"] += 1
                            else:
                                by_ancestry["not_verified_direct_pump_parent"] += 1
                                per_frame[frame]["ancestry_unverified"] += 1
        if len(slots) != 16 or sorted(slots) != frame_meta["selected_slot_ids"]:
            raise QualificationError("selected_block_count_mismatch")
    if counts["references"] != EXPECTED_EVENT_CPI_REFS:
        raise QualificationError("event_cpi_reference_count_mismatch")
    if counts["known_unique_event_discriminator"] != EXPECTED_EVENT_CPI_REFS:
        raise QualificationError("event_discriminator_denominator_mismatch")
    if counts["event_authority_key_match"] != EXPECTED_EVENT_CPI_REFS:
        raise QualificationError("event_authority_denominator_mismatch")
    if (
        counts["failed_short_data"]
        or counts["failed_account_count"]
        or counts["failed_event_authority_key"]
        or counts["failed_event_discriminator_unresolved_or_ambiguous"]
    ):
        raise QualificationError("event_cpi_structural_mismatch")
    ancestry_verified = by_ancestry["verified_direct_pump_parent"]
    if ancestry_verified != EXPECTED_EVENT_CPI_REFS:
        raise QualificationError(
            "event_cpi_ancestry_denominator_mismatch:"
            f"verified={ancestry_verified},unavailable={by_ancestry['unavailable']},"
            f"not_verified={by_ancestry['not_verified_direct_pump_parent']}"
        )
    if by_ancestry["not_verified_direct_pump_parent"]:
        raise QualificationError("event_cpi_parent_program_mismatch")
    if total_blocks != 160:
        raise QualificationError("total_selected_blocks_mismatch")
    return {
        "references": counts["references"],
        "reference_accounting": {
            "total": counts["references"],
            "fully_structurally_matched": counts["references"],
            "failed": 0,
            "unresolved": 0,
        },
        "transaction_status_references": dict(sorted(by_status.items())),
        "nested_event_discriminator_references": dict(sorted(by_event.items())),
        "nested_event_discriminator_identities": [
            {
                "event_discriminator_hex": discriminator,
                "event_name": name,
                **source,
            }
            for discriminator in sorted({key for key, value in event_identities.items() if value})
            for name, source in sorted(event_identities[discriminator].items())
            if by_event[name]
        ],
        "event_authority_key_matches": counts["event_authority_key_match"],
        "ancestry": dict(sorted(by_ancestry.items())),
        "per_frame": {frame: dict(sorted(row.items())) for frame, row in sorted(per_frame.items())},
        "selected_blocks": total_blocks,
        "transactions_scanned": total_transactions,
        "event_authority_pda": pda,
        "all_references_structural_checks_passed": True,
        "claim_scope": (
            "structural Anchor event-CPI envelope only; no payload, event validity, "
            "settlement, or economic admission"
        ),
    }


def qualify() -> dict[str, Any]:
    frozen_manifest, frozen_manifest_sha = c4_replay._verify_frozen_manifest(MANIFEST_PATH)
    if frozen_manifest_sha != EXPECTED_MANIFEST_SHA256:
        raise QualificationError("frozen_manifest_hash_mismatch")
    unknown_bytes = (ROOT / UNKNOWN_RECEIPT).read_bytes()
    if _sha(unknown_bytes) != UNKNOWN_RECEIPT_SHA256:
        raise QualificationError("unknown_discriminator_receipt_hash_mismatch")
    unknown_receipt = json.loads(unknown_bytes)
    grouped = unknown_receipt.get("discriminators")
    if not isinstance(grouped, list):
        raise QualificationError("unknown_discriminator_inventory_missing")
    refs_by_disc = {
        row.get("discriminator_hex"): row.get("instruction_references")
        for row in grouped
        if isinstance(row, dict)
    }
    if (
        len(refs_by_disc) != EXPECTED_UNRECOGNIZED_DISCRIMINATORS
        or sum(refs_by_disc.values()) != EXPECTED_UNRECOGNIZED_REFS
    ):
        raise QualificationError("unknown_discriminator_denominator_mismatch")
    versions, history_manifest = _load_idl_history()
    anchor_capture = _verify_anchor_sources()
    observed = set(refs_by_disc)
    instruction_ids = _instruction_identities(versions, observed)
    event_id_map = _event_identities(versions)
    event_disc = ANCHOR_EVENT_TAG_LE.hex()
    if event_disc not in observed:
        raise QualificationError("anchor_event_tag_not_in_observed_population")
    if event_disc in instruction_ids:
        raise QualificationError("anchor_event_tag_conflicts_with_instruction_identity")
    identities: list[dict[str, Any]] = []
    for discriminator in sorted(observed - {event_disc}):
        names = instruction_ids.get(discriminator, [])
        unique_names = sorted({row["name"] for row in names if isinstance(row.get("name"), str)})
        if len(unique_names) != 1:
            raise QualificationError("observed_discriminator_instruction_identity_ambiguous")
        latest = next(row for row in reversed(names) if row["name"] == unique_names[0])
        identities.append(
            {
                "discriminator_hex": discriminator,
                "idl_instruction_name": unique_names[0],
                "reference_count": refs_by_disc[discriminator],
                "source_commit": latest["commit"],
                "source_commit_time_utc": latest["commit_time_utc"],
                "source_idl_sha256": latest["idl_sha256"],
                "claim_scope": (
                    "documented IDL discriminator/name identity only; deployed behavior, "
                    "accounts, and economic semantics are unqualified"
                ),
            }
        )
    if len(identities) != 15:
        raise QualificationError("historical_instruction_identity_count_mismatch")
    zstd = shutil.which("zstd")
    if zstd is None:
        raise QualificationError("zstd_unavailable")
    event_scan = _event_reference_scan(event_id_map, zstd, frozen_manifest)
    result = {
        "schema": "pumpswap-c4-idl-history-qualification-v3",
        "status": "complete_selected_c4_structural_source_qualification",
        "frozen_manifest_sha256": frozen_manifest_sha,
        "source_receipts": {
            "qualification_tool_sha256": _sha(Path(__file__).read_bytes()),
            "focused_test_sha256": _sha(
                (
                    ROOT / "tests/brain/test_senses_pumpswap_idl_history_qualification_v1.py"
                ).read_bytes()
            ),
            "unknown_discriminators_receipt_sha256": UNKNOWN_RECEIPT_SHA256,
            "pump_amm_idl_history_manifest_sha256": IDL_CAPTURE_MANIFEST_SHA256,
            "pump_amm_idl_history_source_api": history_manifest["source_api"],
            "pump_amm_idl_history_captured_at_utc": history_manifest["captured_at_utc"],
            "pump_amm_idl_history_scope_limit": history_manifest["scope_limit"],
            "pump_amm_idl_history_snapshots": [
                {
                    "commit": row["commit"],
                    "commit_time_utc": row["commit_time_utc"],
                    "url": row["url"],
                    "bytes": row["bytes"],
                    "sha256": row["sha256"],
                }
                for row in history_manifest["versions"]
            ],
            "anchor_event_source": anchor_capture,
            "pump_amm_idl_history_entries": history_manifest["history_entry_count"],
            "pump_amm_program_id": PROGRAM_ID,
        },
        "included_frames": list(EXPECTED_INCLUDED),
        "excluded": [{"frame": EXCLUDED_TAIL2, "reason": "frozen tail2 body remains unopened"}],
        "historical_instruction_discriminator_identities": identities,
        "unmatched_instruction_discriminator": {
            "discriminator_hex": event_disc,
            "reference_count": refs_by_disc[event_disc],
            "interpretation": (
                "matches Anchor EVENT_IX_TAG_LE and is checked as an event-CPI envelope "
                "in full block captures"
            ),
        },
        "event_cpi_structural_scan": event_scan,
        "economic_admission": False,
        "payload_decode": False,
        "global_completeness": False,
    }
    return result


def write_receipt(path: Path, value: dict[str, Any]) -> str:
    payload = _canonical(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = qualify()
        receipt_hash = write_receipt(args.output, result)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"qualification_failed:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "receipt": str(args.output),
                "sha256": receipt_hash,
                "event_cpi": result["event_cpi_structural_scan"]["references"],
                "instruction_names": len(result["historical_instruction_discriminator_identities"]),
                "economic_admission": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
