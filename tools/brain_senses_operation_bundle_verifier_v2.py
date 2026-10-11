#!/usr/bin/env python3
"""Independent read-only verifier for operation-envelope-v2 child bundles.

Does not import the writer, controller-v1, epoch scheduler, or verifier-v1. It
recomputes the 34-operation child contract from immutable captured evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "brain-senses-prospective-operation-envelope-v2"
ZERO = "0" * 64
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
MAX_RESPONSE_BYTES = 32_000_000
PUMP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class VerificationError(ValueError):
    """Parent or child evidence failed independent verification."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for k, v in pairs:
            if k in result:
                raise VerificationError("duplicate_json_key")
            result[k] = v
        return result

    try:
        return json.loads(data, object_pairs_hook=unique)
    except VerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("json_invalid") from exc


def _jsonrpc_result(body: bytes) -> dict[str, Any]:
    """Independently enforce the captured JSON-RPC 2.0 success envelope."""
    envelope = _json(body)
    if (
        not isinstance(envelope, dict)
        or envelope.get("jsonrpc") != "2.0"
        or type(envelope.get("id")) is not int
        or envelope.get("id") != 1
        or "error" in envelope
        or "result" not in envelope
    ):
        raise VerificationError("captured_jsonrpc_success_envelope_invalid")
    return envelope


_RESULT_JOURNAL_FIELDS = (
    "slot_value",
    "inventory_slots",
    "inventory_count",
    "blockhash",
    "parent_slot",
    "block_time_seconds",
    "transaction_count",
    "signatures",
)


def _validate_result_payload_binding(
    request: dict[str, Any], result: dict[str, Any], body: bytes
) -> None:
    """Recompute the child writer's result status and metadata from captured bytes."""
    if result.get("event_slot") != request.get("slot"):
        raise VerificationError("child_result_event_slot_mismatch")
    try:
        envelope = _jsonrpc_result(body)
    except VerificationError:
        expected_status = "response_error"
        expected_fields: dict[str, Any] = {}
    else:
        value = envelope["result"]
        expected_status = "provider_null" if value is None else "parsed"
        expected_fields = {}
        method = request.get("method")
        if method == "getSlot":
            expected_fields["slot_value"] = value if type(value) is int else None
        elif method == "getBlocks":
            expected_fields["inventory_slots"] = value if isinstance(value, list) else None
            expected_fields["inventory_count"] = len(value) if isinstance(value, list) else None
        elif isinstance(value, dict):
            expected_fields = {
                "blockhash": value.get("blockhash"),
                "parent_slot": value.get("parentSlot"),
                "block_time_seconds": value.get("blockTime"),
                "transaction_count": (
                    len(value["transactions"])
                    if isinstance(value.get("transactions"), list)
                    else None
                ),
            }
            if isinstance(value.get("signatures"), list):
                expected_fields["signatures"] = value["signatures"]
        else:
            expected_status = "result_shape_unknown"
    if result.get("result_status") != expected_status:
        raise VerificationError("child_result_status_payload_mismatch")
    for key in _RESULT_JOURNAL_FIELDS:
        if key in expected_fields:
            if key not in result or result.get(key) != expected_fields[key]:
                raise VerificationError("child_result_payload_field_mismatch")
        elif key in result:
            raise VerificationError("child_result_payload_field_mismatch")


def _expected_frame_artifact(
    plan: dict[str, Any], anchor_slot: int, anchor_receipt_ns: int
) -> dict[str, Any] | None:
    start = plan.get("next_start")
    end = anchor_slot + 1
    if type(start) is not int or end < start:
        return None
    width = end - start
    if width > plan["frame_width_cap"]:
        return {
            "schema_version": 3,
            "epoch_id": plan["epoch_id"],
            "status": "oversized_frame",
            "start": start,
            "end_exclusive": end,
            "width": width,
            "width_cap": plan["frame_width_cap"],
            "anchor_slot": anchor_slot,
            "anchor_receipt_ns": anchor_receipt_ns,
        }
    return {
        "schema_version": 3,
        "epoch_id": plan["epoch_id"],
        "status": "empty_frame" if width == 0 else "captured",
        "start": start,
        "end_exclusive": end,
        "width": width,
        "width_cap": plan["frame_width_cap"],
        "anchor_slot": anchor_slot,
        "anchor_receipt_ns": anchor_receipt_ns,
        "anchor_event_slot": anchor_slot,
        "claim": (
            "numeric frame from persisted frontier and finalized anchor; not an inventory claim"
        ),
    }


def _expected_selection_artifact(
    plan: dict[str, Any], inventory_slots: list[int], inventory_sha256: str
) -> dict[str, Any]:
    denominator = len(inventory_slots)
    count = min(plan["selection_count"], denominator)
    seed = f"{plan['seed']}:{plan['epoch_id']}:{inventory_sha256}"
    selected = sorted(random.Random(seed).sample(inventory_slots, count)) if count else []
    divisor = math.gcd(count, denominator)
    probability = f"{count // divisor}/{denominator // divisor}" if denominator else None
    return {
        "schema_version": 3,
        "epoch_id": plan["epoch_id"],
        "inventory_sha256": inventory_sha256,
        "inventory_denominator": denominator,
        "selection_count": count,
        "selected_slots": selected,
        "inclusion_probability": probability,
        "seed": plan["seed"],
        "selection_rule": "uniform_without_replacement_from_finalized_getblocks_members",
    }


def _read(path: Path, *, owner: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_uid != owner or before.st_nlink != 1:
                raise VerificationError("artifact_type_owner_or_link_invalid")
            body = stream.read()
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except VerificationError:
        raise
    except OSError as exc:
        raise VerificationError("artifact_unavailable") from exc
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        or len(body) != after.st_size
    ):
        raise VerificationError("artifact_changed_during_read")
    return body


def _inside(root: Path, rel: Any) -> Path:
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise VerificationError("relative_artifact_path_invalid")
    p = root
    for part in Path(rel).parts:
        p = p / part
        try:
            entry = p.lstat()
        except OSError as exc:
            raise VerificationError("artifact_unavailable") from exc
        if stat.S_ISLNK(entry.st_mode):
            raise VerificationError("artifact_symlink_forbidden")
        if stat.S_ISDIR(entry.st_mode) and (
            entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700
        ):
            raise VerificationError("artifact_directory_owner_or_mode_invalid")
    p = p.resolve(strict=True)
    try:
        p.relative_to(root)
    except ValueError as exc:
        raise VerificationError("artifact_outside_parent_root") from exc
    return p


def _parent_events(root: Path, owner: int) -> list[dict[str, Any]]:
    raw = _read(root / "events-v2.jsonl", owner=owner)
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise VerificationError("parent_ledger_incomplete")
    rows = []
    prior = ZERO
    for seq, line in enumerate(raw.splitlines()):
        row = _json(line)
        if _canonical(row) + b"\n" != line + b"\n":
            raise VerificationError("parent_event_noncanonical")
        body = dict(row)
        supplied = body.pop("event_sha256", None)
        if (
            row.get("seq") != seq
            or row.get("previous_sha256") != prior
            or supplied != _sha(_canonical(body))
        ):
            raise VerificationError("parent_event_chain_invalid")
        row["event_sha256"] = supplied
        prior = supplied
        rows.append(row)
    return rows


def _decode_response(
    receipt: dict[str, Any],
    stored: bytes,
    *,
    expected_codec: str,
    codec_tool: Any,
    max_response_bytes: int,
) -> bytes:
    meta = receipt.get("storage")
    response_bytes = receipt.get("response_bytes")
    if (
        type(max_response_bytes) is not int
        or not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES
        or type(response_bytes) is not int
        or not 0 <= response_bytes <= max_response_bytes
        or len(stored) > max_response_bytes + 1_048_576
    ):
        raise VerificationError("capture_response_size_invalid")
    if expected_codec == "raw" and meta is None:
        if len(stored) != response_bytes:
            raise VerificationError("raw_capture_size_mismatch")
        return stored
    if (
        expected_codec != "zstd"
        or not isinstance(codec_tool, dict)
        or not isinstance(meta, dict)
        or meta.get("codec") != "zstd"
        or meta.get("path") != "response.json.zst"
        or meta.get("bytes") != len(stored)
        or meta.get("sha256") != _sha(stored)
        or meta.get("codec_version") != codec_tool.get("version")
    ):
        raise VerificationError("compressed_capture_receipt_mismatch")
    executable = Path(str(codec_tool.get("path", "")))
    try:
        executable_stat = executable.lstat()
    except OSError as exc:
        raise VerificationError("zstd_verifier_executable_unavailable") from exc
    if (
        not executable.is_absolute()
        or stat.S_ISLNK(executable_stat.st_mode)
        or not stat.S_ISREG(executable_stat.st_mode)
        or not executable_stat.st_mode & 0o111
        or codec_tool.get("sha256") != _sha(_read(executable, owner=executable_stat.st_uid))
    ):
        raise VerificationError("zstd_verifier_executable_binding_invalid")
    version = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=False
    )
    if version.returncode or version.stdout.strip() != codec_tool.get("version"):
        raise VerificationError("zstd_verifier_version_mismatch")
    with tempfile.TemporaryFile() as compressed:
        compressed.write(stored)
        compressed.seek(0)
        process = subprocess.Popen(
            [str(executable), "--quiet", "--decompress", "--stdout"],
            stdin=compressed,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        decoded = bytearray()
        while len(decoded) <= max_response_bytes:
            chunk = process.stdout.read(min(64 * 1024, max_response_bytes + 1 - len(decoded)))
            if not chunk:
                break
            decoded.extend(chunk)
        if len(decoded) > max_response_bytes:
            process.kill()
            process.wait()
            raise VerificationError("compressed_capture_exceeds_response_bound")
        if process.wait() != 0:
            raise VerificationError("compressed_capture_invalid")
    if len(decoded) != response_bytes:
        raise VerificationError("compressed_capture_raw_size_mismatch")
    if not decoded:
        raise VerificationError("compressed_capture_invalid")
    return bytes(decoded)


def _decode58(value: str) -> bytes:
    number = 0
    for char in value:
        try:
            digit = _BASE58.index(char)
        except ValueError as exc:
            raise ValueError("base58_invalid") from exc
        number = number * 58 + digit
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + body


def _signature_list(value: Any, detail: str) -> list[str] | None:
    if not isinstance(value, dict):
        return None
    if detail == "signatures":
        signatures = value.get("signatures")
        return (
            signatures
            if isinstance(signatures, list) and all(isinstance(x, str) for x in signatures)
            else None
        )
    transactions = value.get("transactions")
    if not isinstance(transactions, list):
        return None
    output = []
    for row in transactions:
        tx = row.get("transaction") if isinstance(row, dict) else None
        signatures = tx.get("signatures") if isinstance(tx, dict) else None
        if not isinstance(signatures, list) or not signatures or not isinstance(signatures[0], str):
            return None
        output.append(signatures[0])
    return output


def _resolve_account_keys(entry: dict[str, Any]) -> list[str | None]:
    tx = entry.get("transaction")
    message = tx.get("message") if isinstance(tx, dict) else None
    meta = entry.get("meta")
    loaded = meta.get("loadedAddresses") if isinstance(meta, dict) else None
    keys = message.get("accountKeys") if isinstance(message, dict) else None
    resolved: list[str | None] = []
    if isinstance(keys, list):
        for value in keys:
            resolved.append(
                value
                if isinstance(value, str)
                else value.get("pubkey")
                if isinstance(value, dict)
                else None
            )
    for name in ("writable", "readonly"):
        values = loaded.get(name) if isinstance(loaded, dict) else None
        if isinstance(values, list):
            resolved.extend(value if isinstance(value, str) else None for value in values)
    return resolved


def _normalization_category(entry: Any) -> str | None:
    """Independent shape check for compiled instruction resolution; no writer imports."""
    if not isinstance(entry, dict):
        return "transaction_shape"
    version = entry.get("version")
    if not (version == "legacy" or (type(version) is int and version in (0, 1))):
        return "transaction_version_unsupported_or_missing"
    tx = entry.get("transaction")
    message = tx.get("message") if isinstance(tx, dict) else None
    meta = entry.get("meta")
    if not isinstance(tx, dict) or not isinstance(message, dict) or not isinstance(meta, dict):
        return "transaction_message_or_meta_missing"
    lookups = message.get("addressTableLookups", [])
    if not isinstance(lookups, list):
        return "address_table_lookup_shape"
    if version == "legacy" and lookups:
        return "legacy_transaction_has_address_table_lookups"
    static = message.get("accountKeys")
    if not isinstance(static, list) or not static:
        return "static_account_keys_missing"

    def valid_key(value: Any) -> bool:
        try:
            return isinstance(value, str) and len(_decode58(value)) == 32
        except ValueError:
            return False

    if any(not valid_key(value) for value in static):
        return "static_account_key_invalid"
    writable_n = readonly_n = 0
    for lookup in lookups:
        if not isinstance(lookup, dict):
            return "address_table_lookup_shape"
        if not valid_key(lookup.get("accountKey")):
            return "address_table_key_invalid"
        for key, label in (
            ("writableIndexes", "lookup_writable_indexes"),
            ("readonlyIndexes", "lookup_readonly_indexes"),
        ):
            values = lookup.get(key)
            if not isinstance(values, list) or any(
                type(v) is not int or not 0 <= v <= 255 for v in values
            ):
                return label
        writable_n += len(lookup["writableIndexes"])
        readonly_n += len(lookup["readonlyIndexes"])
    loaded = meta.get("loadedAddresses")
    if loaded is None:
        if writable_n or readonly_n:
            return "loaded_addresses_missing"
        loaded = {"writable": [], "readonly": []}
    if (
        not isinstance(loaded, dict)
        or not isinstance(loaded.get("writable"), list)
        or not isinstance(loaded.get("readonly"), list)
    ):
        return "loaded_addresses_shape"
    if len(loaded["writable"]) != writable_n or len(loaded["readonly"]) != readonly_n:
        return "loaded_address_count_mismatch"
    if any(not valid_key(value) for value in loaded["writable"] + loaded["readonly"]):
        return "loaded_account_key_invalid"
    account_count = len(static) + writable_n + readonly_n
    outer = message.get("instructions")
    if not isinstance(outer, list):
        return "outer_instructions_missing"
    inner = meta.get("innerInstructions")
    if not isinstance(inner, list):
        return "inner_instruction_trace_unavailable"
    seen: set[int] = set()
    groups: list[list[Any]] = [outer]
    for group in inner:
        if not isinstance(group, dict):
            return "inner_instruction_group_shape"
        index = group.get("index")
        if type(index) is not int or not 0 <= index < len(outer) or index in seen:
            return "inner_group_index_invalid"
        seen.add(index)
        instructions = group.get("instructions")
        if not isinstance(instructions, list):
            return "inner_instruction_list_missing"
        if any(
            type(ix.get("stackHeight")) is not int or ix["stackHeight"] < 2
            for ix in instructions
            if isinstance(ix, dict)
        ):
            return "inner_stack_height_unavailable"
        groups.append(instructions)
    for instructions in groups:
        for ix in instructions:
            if not isinstance(ix, dict):
                return "compiled_instruction_shape"
            index = ix.get("programIdIndex")
            if type(index) is not int or not 0 <= index < account_count:
                return "program_id_index_out_of_range"
            accounts = ix.get("accounts")
            if not isinstance(accounts, list) or any(
                type(v) is not int or not 0 <= v <= 255 for v in accounts
            ):
                return "instruction_account_indexes"
            if any(v >= account_count for v in accounts):
                return "account_index_out_of_range"
            data = ix.get("data")
            if not isinstance(data, str):
                return "instruction_data_missing"
            try:
                _decode58(data)
            except ValueError:
                return "instruction_data_invalid_base58"
    return None


def _structural_failures(block: Any, expected_slot: int) -> list[str]:
    failures: list[str] = []
    if not isinstance(block, dict):
        return ["block_result_not_object"]
    if not isinstance(block.get("blockhash"), str) or not block["blockhash"]:
        failures.append("blockhash_missing")
    if not isinstance(block.get("previousBlockhash"), str) or not block["previousBlockhash"]:
        failures.append("previous_blockhash_missing")
    if type(block.get("parentSlot")) is not int or block["parentSlot"] >= expected_slot:
        failures.append("parent_slot_invalid")
    transactions = block.get("transactions")
    if not isinstance(transactions, list):
        return failures + ["transaction_list_missing"]
    seen: set[str] = set()
    for i, row in enumerate(transactions):
        label = f"transaction_{i}"
        if not isinstance(row, dict):
            failures.append(f"{label}_not_object")
            continue
        tx = row.get("transaction")
        message = tx.get("message") if isinstance(tx, dict) else None
        meta = row.get("meta")
        if not isinstance(tx, dict) or not isinstance(message, dict):
            failures.append(f"{label}_message_missing")
            continue
        version = row.get("version")
        if not (version == "legacy" or (type(version) is int and version in (0, 1))):
            failures.append(f"{label}_unsupported_version")
        sigs = tx.get("signatures")
        if (
            not isinstance(sigs, list)
            or not sigs
            or any(not isinstance(s, str) or not s for s in sigs)
        ):
            failures.append(f"{label}_signatures_invalid")
        elif sigs[0] in seen:
            failures.append(f"{label}_duplicate_signature")
        else:
            seen.add(sigs[0])
        if not isinstance(meta, dict) or "err" not in meta:
            failures.append(f"{label}_meta_status_missing")
            continue
        keys = message.get("accountKeys")
        loaded = meta.get("loadedAddresses")
        if not isinstance(keys, list) or not isinstance(loaded, dict):
            failures.append(f"{label}_account_resolution_missing")
            continue
        if not isinstance(loaded.get("writable"), list) or not isinstance(
            loaded.get("readonly"), list
        ):
            failures.append(f"{label}_loaded_addresses_invalid")
            continue
        resolved = _resolve_account_keys(row)
        if not resolved or any(not isinstance(k, str) or not k for k in resolved):
            failures.append(f"{label}_account_resolution_invalid")
        outer, inner = message.get("instructions"), meta.get("innerInstructions")
        if not isinstance(outer, list) or not isinstance(inner, list):
            failures.append(f"{label}_instruction_coverage_missing")
            continue
        lists = [outer]
        for group in inner:
            if not isinstance(group, dict) or not isinstance(group.get("instructions"), list):
                failures.append(f"{label}_inner_instruction_group_invalid")
                continue
            lists.append(group["instructions"])
        for ins in lists:
            for ix in ins:
                if not isinstance(ix, dict):
                    failures.append(f"{label}_instruction_invalid")
                    continue
                if ix.get("programId") is None:
                    index = ix.get("programIdIndex")
                    if type(index) is not int or not 0 <= index < len(resolved):
                        failures.append(f"{label}_program_id_unresolved")
                accounts = ix.get("accounts")
                if accounts is not None and (
                    not isinstance(accounts, list)
                    or any(type(k) is not int or not 0 <= k < len(resolved) for k in accounts)
                ):
                    failures.append(f"{label}_instruction_accounts_unresolved")
    return failures


def _pump_counts(block: dict[str, Any]) -> dict[str, Any]:
    total = success = failed = unresolved = transactions = 0
    normalization: dict[str, int] = {}
    for row in block.get("transactions") or []:
        category = _normalization_category(row)
        if category:
            normalization[category] = normalization.get(category, 0) + 1
            unresolved += 1
        keys = _resolve_account_keys(row) if isinstance(row, dict) else []
        tx = row.get("transaction") if isinstance(row, dict) else None
        message = tx.get("message") if isinstance(tx, dict) else {}
        meta = row.get("meta") if isinstance(row, dict) else {}
        instructions = list(message.get("instructions") or [])
        for group in meta.get("innerInstructions") or []:
            instructions.extend(group.get("instructions") or [])
        refs = 0
        for ix in instructions:
            if not isinstance(ix, dict):
                unresolved += 1
                continue
            program = ix.get("programId")
            if program is None:
                index = ix.get("programIdIndex")
                if type(index) is int and 0 <= index < len(keys):
                    program = keys[index]
                else:
                    unresolved += 1
            if program == PUMP_PROGRAM:
                refs += 1
        total += refs
        if refs:
            transactions += 1
            if isinstance(meta, dict) and meta.get("err") is None:
                success += refs
            else:
                failed += refs
    return {
        "structural_reference_count": total,
        "transactions_with_reference": transactions,
        "references_in_successful_transactions": success,
        "references_in_failed_transactions": failed,
        "unresolved_instruction_shapes": unresolved,
        "compiled_transaction_normalization_failures": dict(sorted(normalization.items())),
        "economic_trade_count": None,
        "exact_market_activity_claim": False,
    }


def _child(
    root: Path,
    parent_plan: dict[str, Any],
    operation: dict[str, Any],
    reservation: dict[str, Any],
    bundle: dict[str, Any],
    owner: int,
) -> dict[str, int]:
    child = _inside(root, reservation["child_path"])
    child_stat = child.lstat()
    if (
        not stat.S_ISDIR(child_stat.st_mode)
        or child_stat.st_uid != owner
        or stat.S_IMODE(child_stat.st_mode) != 0o700
    ):
        raise VerificationError("child_root_owner_or_mode_invalid")
    child_rel = reservation["child_path"]
    plan_path = child / "epoch-plan.json"
    plan_bytes = _read(plan_path, owner=owner)
    plan = _json(plan_bytes)
    if (
        _sha(plan_bytes) != reservation["child_plan_sha256"]
        or plan.get("epoch_id") != child.name
        or plan.get("schema_version") != 3
        or plan.get("protocol") != "senses-finalized-inventory-first-v3"
        or plan.get("provider") != "helius-free"
        or plan.get("capture_storage_codec") not in {"raw", "zstd"}
        or (
            plan.get("capture_storage_codec") == "raw"
            and plan.get("capture_codec_tool") is not None
        )
        or (
            plan.get("capture_storage_codec") == "zstd"
            and not isinstance(plan.get("capture_codec_tool"), dict)
        )
        or type(plan.get("max_response_bytes")) is not int
        or not 1 <= plan["max_response_bytes"] <= MAX_RESPONSE_BYTES
    ):
        raise VerificationError("child_plan_binding_invalid")
    bucket = next(
        (row for row in parent_plan["buckets"] if row["bucket_id"] == reservation["bucket_id"]),
        None,
    )
    boot = bucket is not None and bucket["kind"] == "anchor"
    if (
        bucket is None
        or plan.get("bucket") != reservation.get("bucket_id")
        or plan.get("epoch_id") != reservation.get("bucket_id")
        or plan.get("bootstrap") is not boot
        or type(plan.get("request_budget")) is not int
        or plan["request_budget"] < reservation.get("requests", 1)
        or plan.get("raw_byte_budget") != reservation.get("raw_bytes")
        or plan.get("raw_byte_budget", parent_plan["caps"]["raw_bytes"] + 1)
        > parent_plan["caps"]["raw_bytes"]
        or plan.get("stored_byte_budget") != reservation.get("stored_bytes")
        or plan.get("stored_byte_budget", parent_plan["caps"]["stored_bytes"] + 1)
        > parent_plan["caps"]["stored_bytes"]
        or type(plan.get("minimum_free_disk_bytes")) is not int
        or plan["minimum_free_disk_bytes"] < parent_plan["caps"]["physical_bytes"]
        or type(plan.get("frame_width_cap")) is not int
        or not 1 <= plan["frame_width_cap"] <= parent_plan["caps"]["width"]
        or plan.get("next_start") != (None if boot else reservation.get("frontier_before"))
        or type(plan.get("selection_count")) is not int
        or not 0 <= plan["selection_count"] <= plan["frame_width_cap"]
        or (not boot and plan.get("selection_count") != 16)
    ):
        raise VerificationError("child_plan_parent_caps_or_schedule_mismatch")
    child_code_pin = plan.get("code_sha256")
    source = parent_plan.get("source_sha256", {})
    source_pins = ("child_v3_runner", "child_v3", "tools/brain_senses_block_epoch_v3.py")
    for key in source_pins:
        if key in source and source[key] != child_code_pin:
            raise VerificationError("parent_child_source_pin_mismatch")
    if not isinstance(child_code_pin, str) or not re.fullmatch(r"[0-9a-f]{64}", child_code_pin):
        raise VerificationError("child_code_source_pin_invalid")
    summary_path = child / "epoch-summary.json"
    summary_bytes = _read(summary_path, owner=owner)
    summary = _json(summary_bytes)
    stop_path = child / "STOP"
    stop_bytes = _read(stop_path, owner=owner)
    stop = _json(stop_bytes)
    if (
        _sha(summary_bytes) != bundle.get("summary_sha256")
        or summary != bundle.get("summary")
        or summary.get("schema_version") != 3
        or summary.get("epoch_id") != child.name
        or summary.get("status") not in {"frame_complete", "bootstrap_complete"}
        or summary.get("stop_sha256") != _sha(stop_bytes)
        or summary.get("sealed_ns") != stop.get("time_ns")
        or summary.get("status") != stop.get("status")
        or summary.get("reason") != stop.get("reason")
        or summary.get("advance_to") != stop.get("advance_to")
        or stop.get("schema_version") != 3
        or stop.get("status") not in {"frame_complete", "bootstrap_complete"}
        or stop.get("reason") != stop.get("status")
    ):
        raise VerificationError("child_stop_summary_binding_or_status_invalid")
    stop_extras = {
        key: value
        for key, value in stop.items()
        if key not in {"schema_version", "status", "reason", "advance_to", "time_ns"}
    }
    if any(summary.get(key) != value for key, value in stop_extras.items()):
        raise VerificationError("child_stop_summary_extra_fields_mismatch")
    if (
        bundle.get("operation_id") != operation["operation_id"]
        or bundle.get("bucket_id") != reservation["bucket_id"]
        or bundle.get("child_plan_sha256") != reservation["child_plan_sha256"]
        or bundle.get("claim_scope")
        != "captured_request_response_bundle_only_child_structure_not_independently_qualified"
        or bundle.get("provider_charge_units") is not None
        or bundle.get("provider_charge_status") != "unknown_unverified"
        or bundle.get("child_plan_path") != f"{child_rel}/epoch-plan.json"
        or bundle.get("summary_path") != f"{child_rel}/epoch-summary.json"
        or bundle.get("frontier_before") != reservation.get("frontier_before")
    ):
        raise VerificationError("bundle_parent_identity_mismatch")
    if boot:
        if bundle.get("frame_path") is not None or bundle.get("selection_path") is not None:
            raise VerificationError("anchor_bundle_has_frame_artifacts")
    else:
        for path_key, hash_key, relname in (
            ("frame_path", "frame_sha256", "frame.json"),
            ("selection_path", "selection_sha256", "selection-plan.json"),
        ):
            artifact_path = _inside(root, bundle.get(path_key))
            if bundle.get(path_key) != f"{child_rel}/{relname}":
                raise VerificationError("bundle_frame_or_selection_path_invalid")
            if _sha(_read(artifact_path, owner=owner)) != bundle.get(hash_key):
                raise VerificationError("bundle_frame_or_selection_digest_invalid")
    expected_n = 1 if boot else 34
    if reservation.get("requests") != expected_n or bundle.get("actual_requests") != expected_n:
        raise VerificationError("parent_child_request_count_invalid")
    requests = []
    results = []
    for seq in range(1, expected_n + 1):
        rq = _inside(child, f"requests/{seq:012d}.json")
        rs = _inside(child, f"request-results/{seq:012d}.json")
        requests.append((_read(rq, owner=owner), rq))
        results.append((_read(rs, owner=owner), rs))
    if (
        len(list((child / "requests").glob("*.json"))) != expected_n
        or len(list((child / "request-results").glob("*.json"))) != expected_n
    ):
        raise VerificationError("unaccounted_child_journal_record")
    parsed: list[tuple[dict[str, Any], dict[str, Any], bytes, bytes, dict[str, Any]]] = []
    raw_total = stored_total = 0
    listed = bundle.get("children")
    if not isinstance(listed, list) or len(listed) != expected_n:
        raise VerificationError("bundle_child_list_count_invalid")
    for idx, ((rq_bytes, _rq_path), (rs_bytes, _rs_path), item) in enumerate(
        zip(requests, results, listed, strict=True), start=1
    ):
        rq, rs = _json(rq_bytes), _json(rs_bytes)
        if (
            rq.get("schema_version") != 3
            or rs.get("schema_version") != 3
            or rq.get("sequence") != idx
            or rs.get("sequence") != idx
            or rq.get("provider") != plan.get("provider")
            or rs.get("provider") != plan.get("provider")
            or rs.get("status") != "captured"
            or rs.get("result_status") != "parsed"
            or rq.get("method") != rs.get("method")
        ):
            raise VerificationError("child_journal_identity_or_status_invalid")
        params = rq.get("params")
        request_body = _canonical(
            {"jsonrpc": "2.0", "id": 1, "method": rq["method"], "params": params}
        )
        if (
            _sha(request_body) != rq.get("request_sha256")
            or _sha(_canonical(params)) != item.get("params_sha256")
            or item.get("request_sha256") != rq.get("request_sha256")
        ):
            raise VerificationError("child_request_digest_invalid")
        if item.get("request_file_sha256") != _sha(rq_bytes) or item.get(
            "result_file_sha256"
        ) != _sha(rs_bytes):
            raise VerificationError("bundle_journal_file_digest_invalid")
        cap_rel = rs.get("capture_path")
        if (
            not isinstance(cap_rel, str)
            or Path(cap_rel).is_absolute()
            or ".." in Path(cap_rel).parts
        ):
            raise VerificationError("child_capture_relpath_invalid")
        receipt_path = _inside(child, f"{cap_rel}/receipt.json")
        receipt_bytes = _read(receipt_path, owner=owner)
        receipt = _json(receipt_bytes)
        stored_rel = receipt.get("storage", {}).get("path", "response.json")
        if Path(stored_rel).is_absolute() or ".." in Path(stored_rel).parts:
            raise VerificationError("response_relpath_invalid")
        stored_path = _inside(child, f"{cap_rel}/{stored_rel}")
        stored_bytes = _read(stored_path, owner=owner)
        body = _decode_response(
            receipt,
            stored_bytes,
            expected_codec=plan.get("capture_storage_codec"),
            codec_tool=plan.get("capture_codec_tool"),
            max_response_bytes=plan.get("max_response_bytes", -1),
        )
        _validate_result_payload_binding(rq, rs, body)
        request_json = _json(receipt.get("request_json", "").encode())
        if (
            request_json.get("method") != rq.get("method")
            or request_json.get("params") != params
            or request_json.get("id") != 1
            or receipt.get("request_started_ns") != rs.get("request_started_ns")
            or receipt.get("received_ns") != rs.get("receipt_ns")
            or receipt.get("source_alias") != plan.get("provider")
            or type(rs.get("availability_ns")) is not int
            or rs.get("availability_ns", -1) < rs.get("receipt_ns", 0)
            or receipt.get("response_sha256") != _sha(body)
            or receipt.get("response_bytes") != len(body)
        ):
            raise VerificationError("capture_receipt_payload_mismatch")
        if (
            rs.get("response_sha256") != _sha(body)
            or rs.get("response_bytes") != len(body)
            or rs.get("stored_response_bytes") != len(stored_bytes)
        ):
            raise VerificationError("child_result_capture_mismatch")
        if type(rq.get("reserved_ns")) is not int:
            raise VerificationError("child_time_coordinate_missing")
        for time_key in ("request_started_ns", "receipt_ns", "availability_ns"):
            if type(rs.get(time_key)) is not int:
                raise VerificationError("child_time_coordinate_missing")
        if (
            not operation["started_at_ns"] <= rq.get("reserved_ns", -1)
            or not rq["reserved_ns"] <= rs["request_started_ns"] <= rs["receipt_ns"]
            or rs["availability_ns"] < rs["receipt_ns"]
            or type(operation.get("started_at_ns")) is not int
            or rs["request_started_ns"] < operation["started_at_ns"]
            or rs["request_started_ns"] >= operation.get("bucket_end_ns", -1)
            or rs["request_started_ns"] >= operation.get("lease_expires_ns", -1)
        ):
            raise VerificationError("child_time_order_invalid")
        if (
            item.get("receipt_sha256") != _sha(receipt_bytes)
            or item.get("response_stored_sha256") != _sha(stored_bytes)
            or item.get("response_raw_sha256") != _sha(body)
            or item.get("raw_bytes") != len(body)
            or item.get("stored_bytes") != len(stored_bytes)
            or item.get("sequence") != idx
            or item.get("method") != rq.get("method")
            or item.get("request_path") != f"{child_rel}/requests/{idx:012d}.json"
            or item.get("result_path") != f"{child_rel}/request-results/{idx:012d}.json"
            or item.get("receipt_path") != f"{child_rel}/{cap_rel}/receipt.json"
            or item.get("response_path") != f"{child_rel}/{cap_rel}/{stored_rel}"
            or item.get("request_started_ns") != rs.get("request_started_ns")
            or item.get("receipt_ns") != rs.get("receipt_ns")
            or item.get("availability_ns") != rs.get("availability_ns")
            or item.get("event_slot") != rq.get("slot")
            or rs.get("event_slot") != rq.get("slot")
        ):
            raise VerificationError("bundle_capture_receipt_or_bytes_mismatch")
        raw_total += len(body)
        stored_total += len(stored_bytes)
        parsed.append((rq, rs, body, stored_bytes, receipt))
    _check_operations(plan, child, parsed, boot, summary, owner)
    _verify_child_structure(plan, parsed, summary, boot)
    latest_availability = max(row[1]["availability_ns"] for row in parsed)
    if type(stop.get("time_ns")) is not int or stop["time_ns"] < latest_availability:
        raise VerificationError("child_stop_precedes_capture_availability")
    if (
        summary.get("request_reservations") != expected_n
        or summary.get("captured_response_bytes") != raw_total
        or summary.get("stored_response_bytes") != stored_total
    ):
        raise VerificationError("child_summary_recomputed_totals_mismatch")
    if (
        bundle.get("actual_raw_bytes") != raw_total
        or bundle.get("actual_stored_bytes") != stored_total
    ):
        raise VerificationError("bundle_recomputed_totals_mismatch")
    if raw_total > reservation.get("raw_bytes", -1) or stored_total > reservation.get(
        "stored_bytes", -1
    ):
        raise VerificationError("parent_envelope_byte_cap_exceeded")
    frontier_after = summary.get("bootstrap_slot", -1) + 1 if boot else summary.get("advance_to")
    if (
        type(frontier_after) is not int
        or bundle.get("frontier_after") != frontier_after
        or (not boot and plan.get("next_start") != reservation.get("frontier_before"))
    ):
        raise VerificationError("parent_child_frontier_binding_invalid")
    return {
        "requests": expected_n,
        "raw_bytes": raw_total,
        "stored_bytes": stored_total,
        "_child_sealed_ns": stop["time_ns"],
        "_latest_availability_ns": latest_availability,
    }


def _validate_v3_prefix(
    child: Path,
    reservation: dict[str, Any],
    owner: int,
    started_at_ns: int | None = None,
    parent_plan: dict[str, Any] | None = None,
) -> tuple[int, int] | None:
    child_stat = child.lstat()
    if (
        not stat.S_ISDIR(child_stat.st_mode)
        or child_stat.st_uid != owner
        or stat.S_IMODE(child_stat.st_mode) != 0o700
    ):
        raise VerificationError("child_root_owner_or_mode_invalid")
    plan_bytes = _read(child / "epoch-plan.json", owner=owner)
    plan = _json(plan_bytes)
    if (
        _sha(plan_bytes) != reservation.get("child_plan_sha256")
        or plan.get("schema_version") != 3
        or plan.get("protocol") != "senses-finalized-inventory-first-v3"
        or plan.get("provider") != "helius-free"
        or plan.get("epoch_id") != child.name
    ):
        raise VerificationError("child_plan_v3_required")
    if (
        not isinstance(plan.get("code_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", plan["code_sha256"])
        or type(plan.get("request_budget")) is not int
        or type(plan.get("raw_byte_budget")) is not int
        or type(plan.get("stored_byte_budget")) is not int
    ):
        raise VerificationError("child_prefix_plan_limits_or_source_invalid")
    if parent_plan is not None:
        bucket = next(
            (
                row
                for row in parent_plan["buckets"]
                if row["bucket_id"] == reservation.get("bucket_id")
            ),
            None,
        )
        expected_requests = 1 if bucket and bucket["kind"] == "anchor" else 34
        if (
            bucket is None
            or plan.get("epoch_id") != reservation.get("bucket_id")
            or plan.get("bucket") != reservation.get("bucket_id")
            or plan.get("bootstrap") is not (bucket["kind"] == "anchor")
            or reservation.get("requests") != expected_requests
            or plan.get("request_budget", 0) < expected_requests
            or plan.get("raw_byte_budget") != reservation.get("raw_bytes")
            or plan.get("raw_byte_budget", parent_plan["caps"]["raw_bytes"] + 1)
            > parent_plan["caps"]["raw_bytes"]
            or plan.get("stored_byte_budget") != reservation.get("stored_bytes")
            or plan.get("stored_byte_budget", parent_plan["caps"]["stored_bytes"] + 1)
            > parent_plan["caps"]["stored_bytes"]
            or type(plan.get("minimum_free_disk_bytes")) is not int
            or plan["minimum_free_disk_bytes"] < parent_plan["caps"]["physical_bytes"]
            or type(plan.get("frame_width_cap")) is not int
            or not 1 <= plan["frame_width_cap"] <= parent_plan["caps"]["width"]
            or type(plan.get("selection_count")) is not int
            or not 0 <= plan["selection_count"] <= plan["frame_width_cap"]
            or (bucket["kind"] == "frame" and plan["selection_count"] != 16)
            or plan.get("next_start")
            != (None if bucket["kind"] == "anchor" else reservation.get("frontier_before"))
        ):
            raise VerificationError("child_prefix_plan_parent_binding_invalid")
        for key in ("child_v3_runner", "child_v3", "tools/brain_senses_block_epoch_v3.py"):
            if key in parent_plan.get("source_sha256", {}) and parent_plan["source_sha256"][
                key
            ] != plan.get("code_sha256"):
                raise VerificationError("child_prefix_source_pin_mismatch")
    request_dir, result_dir = child / "requests", child / "request-results"
    for directory in (request_dir, result_dir):
        if directory.exists():
            _inside(child, directory.name)
    request_paths = sorted(request_dir.glob("*.json")) if request_dir.exists() else []
    result_paths = sorted(result_dir.glob("*.json")) if result_dir.exists() else []
    for directory, paths in ((request_dir, request_paths), (result_dir, result_paths)):
        if directory.exists() and len(list(directory.iterdir())) != len(paths):
            raise VerificationError("child_prefix_orphan_journal_artifact")
        if directory.exists():
            _inside(child, directory.name)
    request_names = {path.name for path in request_paths}
    result_names = {path.name for path in result_paths}
    if result_names - request_names:
        raise VerificationError("child_result_without_reservation")
    result_sequences = sorted(int(name[:-5]) for name in result_names)
    if (
        result_sequences != list(range(1, len(result_sequences) + 1))
        or len(request_paths) > len(result_paths) + 1
    ):
        raise VerificationError("child_result_prefix_not_contiguous")
    expected_count = reservation["requests"]
    if len(request_paths) > expected_count:
        raise VerificationError("child_prefix_exceeds_reserved_request_count")
    if any(path.name != f"{index:012d}.json" for index, path in enumerate(request_paths, 1)):
        raise VerificationError("child_request_prefix_not_contiguous")
    for index, request_path in enumerate(request_paths, 1):
        request = _json(_read(request_path, owner=owner))
        if (
            request.get("schema_version") != 3
            or request.get("sequence") != index
            or request.get("provider") != plan.get("provider")
        ):
            raise VerificationError("child_request_v3_identity_invalid")
        result_path = result_dir / request_path.name
        if not result_path.exists():
            continue
        result = _json(_read(result_path, owner=owner))
        if (
            result.get("schema_version") != 3
            or result.get("sequence") != index
            or result.get("provider") != plan.get("provider")
            or result.get("method") != request.get("method")
            or result.get("phase") != request.get("phase")
            or result.get("slot") != request.get("slot")
        ):
            raise VerificationError("child_result_v3_identity_invalid")
        if result.get("status") == "captured":
            receipt_ns = result.get("receipt_ns")
            availability_ns = result.get("availability_ns")
            if (
                result.get("result_status")
                not in {"parsed", "response_error", "provider_null", "result_shape_unknown"}
                or type(result.get("request_started_ns")) is not int
                or type(receipt_ns) is not int
                or type(availability_ns) is not int
                or not result["request_started_ns"] <= receipt_ns <= availability_ns
            ):
                raise VerificationError("captured_child_time_coordinates_invalid")
        elif result.get("status") == "received_persistence_failed":
            if (
                type(result.get("request_started_ns")) is not int
                or type(result.get("receipt_ns")) is not int
                or result.get("availability_ns") is not None
                or type(result.get("failure_observed_ns")) is not int
            ):
                raise VerificationError("failed_persistence_claims_payload_availability")
        elif result.get("status") == "captured_availability_unknown":
            if (
                type(result.get("request_started_ns")) is not int
                or type(result.get("receipt_ns")) is not int
                or result.get("availability_ns") is not None
                or type(result.get("failure_observed_ns")) is not int
                or result.get("result_status") != "capture_clock_order_invalid"
            ):
                raise VerificationError("clock_regression_claims_payload_availability")
        elif result.get("status") == "request_failed":
            if (
                result.get("receipt_ns") is not None
                or result.get("availability_ns") is not None
                or type(result.get("failure_observed_ns")) is not int
            ):
                raise VerificationError("request_failure_claims_payload_availability")
        else:
            raise VerificationError("child_result_status_unknown")
    return _validate_v3_prefix_artifacts(
        child, plan, reservation, request_paths, result_paths, owner, started_at_ns, parent_plan
    )


def _validate_v3_prefix_artifacts(
    child: Path,
    plan: dict[str, Any],
    reservation: dict[str, Any],
    request_paths: list[Path],
    result_paths: list[Path],
    owner: int,
    started_at_ns: int | None,
    parent_plan: dict[str, Any] | None,
) -> tuple[int, int] | None:
    boot = plan.get("bootstrap") is True
    requests = [_json(_read(path, owner=owner)) for path in request_paths]
    results = {path.name: _json(_read(path, owner=owner)) for path in result_paths}
    expected_n = 1 if boot else 34
    if reservation.get("requests") != expected_n:
        raise VerificationError("child_prefix_parent_request_count_invalid")
    expected_ops: list[tuple[str, list[Any], int | None, str]] = [
        ("getSlot", [{"commitment": "finalized"}], None, "anchor")
    ]
    anchor_result = results.get("000000000001.json")
    anchor_slot = anchor_result.get("slot_value") if isinstance(anchor_result, dict) else None
    if (
        anchor_result is not None
        and anchor_result.get("status") == "captured"
        and anchor_result.get("result_status") == "parsed"
    ):
        anchor_envelope = _prefix_capture_value(child, requests[0], anchor_result, plan, owner)
        captured_anchor_slot = anchor_envelope.get("result")
        if (
            type(captured_anchor_slot) is int
            and captured_anchor_slot >= 1
            and anchor_slot != captured_anchor_slot
        ):
            raise VerificationError("child_prefix_anchor_result_binding_invalid")
    if not boot:
        frame_path = child / "frame.json"
        selection_path = child / "selection-plan.json"
        frame = _json(_read(frame_path, owner=owner)) if frame_path.exists() else None
        selection = _json(_read(selection_path, owner=owner)) if selection_path.exists() else None
        expected_frame: dict[str, Any] | None = None
        if frame is not None or selection is not None or len(requests) >= 2:
            if (
                type(anchor_slot) is not int
                or anchor_result is None
                or anchor_result.get("status") != "captured"
                or anchor_result.get("result_status") != "parsed"
            ):
                raise VerificationError("child_prefix_frame_without_captured_anchor")
            expected_frame = _expected_frame_artifact(
                plan, anchor_slot, anchor_result["receipt_ns"]
            )
            if expected_frame is None or frame != expected_frame:
                raise VerificationError("child_prefix_frame_binding_invalid")
        start = plan.get("next_start")
        end = anchor_slot + 1 if type(anchor_slot) is int else None
        if len(requests) >= 2 and (
            expected_frame is None or expected_frame["status"] != "captured"
        ):
            raise VerificationError("child_prefix_request_after_terminal_frame")
        if len(requests) >= 2:
            expected_ops.append(
                ("getBlocks", [start, end - 1, {"commitment": "finalized"}], None, "inventory")
            )
        inventory_result = results.get("000000000002.json")
        inventory_parsed = (
            inventory_result is not None
            and inventory_result.get("status") == "captured"
            and inventory_result.get("result_status") == "parsed"
        )
        selection_required = selection is not None or len(requests) >= 3
        if selection_required and not inventory_parsed:
            raise VerificationError("child_prefix_selection_without_inventory")
        if inventory_parsed:
            if len(requests) < 2:
                raise VerificationError("child_prefix_selection_without_inventory_request")
            inventory_value = _prefix_capture_value(
                child, requests[1], inventory_result, plan, owner
            )
            inventory_slots = inventory_value.get("result")
            if selection_required:
                if (
                    not isinstance(inventory_slots, list)
                    or any(type(value) is not int for value in inventory_slots)
                    or inventory_slots != sorted(set(inventory_slots))
                    or any(value < start or value >= end for value in inventory_slots)
                ):
                    raise VerificationError("child_prefix_inventory_payload_invalid")
                expected_selection = _expected_selection_artifact(
                    plan, inventory_slots, inventory_result["response_sha256"]
                )
                if selection != expected_selection:
                    raise VerificationError("child_prefix_selection_recomputation_mismatch")
                for slot in expected_selection["selected_slots"]:
                    expected_ops.extend(
                        [
                            (
                                "getBlock",
                                [
                                    slot,
                                    {
                                        "commitment": "finalized",
                                        "encoding": "json",
                                        "transactionDetails": "full",
                                        "rewards": False,
                                        "maxSupportedTransactionVersion": 1,
                                    },
                                ],
                                slot,
                                "full_block",
                            ),
                            (
                                "getBlock",
                                [
                                    slot,
                                    {
                                        "commitment": "finalized",
                                        "encoding": "json",
                                        "transactionDetails": "signatures",
                                        "rewards": False,
                                    },
                                ],
                                slot,
                                "signature_check",
                            ),
                        ]
                    )
    for index, request in enumerate(requests, start=1):
        if index > len(expected_ops):
            raise VerificationError("child_prefix_operation_exceeds_derived_schedule")
        method, params, slot, phase = expected_ops[index - 1]
        if (
            request.get("method") != method
            or request.get("params") != params
            or request.get("phase") != phase
            or request.get("slot") != slot
            or type(request.get("reserved_ns")) is not int
            or (started_at_ns is not None and request["reserved_ns"] < started_at_ns)
            or (parent_plan is not None and request["reserved_ns"] < parent_plan["created_ns"])
            or (
                parent_plan is not None
                and request["reserved_ns"]
                >= min(
                    next(
                        b["end_ns"]
                        for b in parent_plan["buckets"]
                        if b["bucket_id"] == reservation["bucket_id"]
                    ),
                    parent_plan["expires_ns"],
                )
            )
        ):
            raise VerificationError("child_prefix_request_schedule_mismatch")
    expected_capture_dirs: set[str] = set()
    pending_capture_requests: dict[str, dict[str, Any]] = {}
    structural_by_key: dict[tuple[int, str], tuple[dict[str, Any], Any]] = {}
    for request in requests:
        result = results.get(f"{request['sequence']:012d}.json")
        if result is None:
            pending_capture_requests[f"{request['sequence']:012d}"] = request
            continue
        status = result.get("status")
        if status != "captured":
            cap_rel = result.get("capture_path")
            if cap_rel is not None:
                if cap_rel != f"captures/{request['sequence']:012d}":
                    raise VerificationError("failed_child_prefix_has_capture_path")
                expected_capture_dirs.add(cap_rel)
                pending_capture_requests[f"{request['sequence']:012d}"] = request
                _verify_prefix_capture(
                    child,
                    request,
                    result,
                    cap_rel,
                    plan,
                    owner,
                    parent_plan,
                    reservation,
                    started_at_ns,
                )
            continue
        if result.get("result_status") not in {
            "parsed",
            "response_error",
            "provider_null",
            "result_shape_unknown",
        }:
            raise VerificationError("captured_child_prefix_result_status_invalid")
        cap_rel = result.get("capture_path")
        if (
            not isinstance(cap_rel, str)
            or Path(cap_rel).is_absolute()
            or ".." in Path(cap_rel).parts
        ):
            raise VerificationError("child_prefix_capture_path_invalid")
        expected_capture_dirs.add(cap_rel)
        _verify_prefix_capture(
            child, request, result, cap_rel, plan, owner, parent_plan, reservation, started_at_ns
        )
        if result.get("status") == "captured" and request.get("phase") in {
            "full_block",
            "signature_check",
        }:
            response_value = (
                _prefix_capture_value(child, request, result, plan, owner)
                if result.get("result_status") == "parsed"
                else None
            )
            structural_by_key[(request["slot"], request["phase"])] = (
                result,
                response_value.get("result") if isinstance(response_value, dict) else None,
            )
    capture_root = child / "captures"
    actual_capture_dirs = set()
    if capture_root.exists():
        _inside(child, "captures")
        for path in capture_root.iterdir():
            if not path.is_dir() or stat.S_ISLNK(path.lstat().st_mode):
                raise VerificationError("child_prefix_orphan_capture_artifact")
            actual_capture_dirs.add(path.name)
            _inside(child, f"captures/{path.name}")
    expected_capture_names = {Path(value).name for value in expected_capture_dirs}
    orphan_capture_dirs = actual_capture_dirs - expected_capture_names
    if orphan_capture_dirs:
        for dirname in orphan_capture_dirs:
            request = pending_capture_requests.get(dirname)
            if request is None:
                raise VerificationError("child_prefix_orphan_capture_artifact")
            _verify_prefix_capture(
                child,
                request,
                None,
                f"captures/{dirname}",
                plan,
                owner,
                parent_plan,
                reservation,
                started_at_ns,
            )
            expected_capture_dirs.add(f"captures/{dirname}")
    if actual_capture_dirs != expected_capture_names:
        raise VerificationError("child_prefix_orphan_capture_artifact")
    stop_path, summary_path = child / "STOP", child / "epoch-summary.json"
    if summary_path.exists() and not stop_path.exists():
        raise VerificationError("child_prefix_summary_without_stop")
    child_terminal: tuple[int, int] | None = None
    if stop_path.exists():
        stop_bytes = _read(stop_path, owner=owner)
        stop = _json(stop_bytes)
        captured_availability = [
            r.get("availability_ns") for r in results.values() if r.get("status") == "captured"
        ]
        stop_ns = stop.get("time_ns")
        if (
            stop.get("schema_version") != 3
            or not isinstance(stop.get("status"), str)
            or not stop["status"]
            or not isinstance(stop.get("reason"), str)
            or not stop["reason"]
            or (stop.get("advance_to") is not None and type(stop.get("advance_to")) is not int)
            or type(stop_ns) is not int
            or (captured_availability and stop_ns < max(captured_availability))
        ):
            raise VerificationError("child_prefix_stop_without_summary_invalid")
        child_terminal = (stop_ns, max(captured_availability, default=0))
        if not summary_path.exists():
            return child_terminal
        summary = _json(_read(summary_path, owner=owner))
        if (
            summary.get("stop_sha256") != _sha(stop_bytes)
            or summary.get("schema_version") != 3
            or summary.get("epoch_id") != child.name
            or summary.get("sealed_ns") != stop.get("time_ns")
            or summary.get("status") != stop.get("status")
            or summary.get("reason") != stop.get("reason")
            or (captured_availability and stop.get("time_ns", -1) < max(captured_availability))
        ):
            raise VerificationError("child_prefix_stop_summary_binding_invalid")
        if any(
            summary.get(key) != value
            for key, value in stop.items()
            if key not in {"schema_version", "status", "reason", "advance_to", "time_ns"}
        ):
            raise VerificationError("child_prefix_stop_summary_extra_fields_mismatch")
        if not boot and stop.get("status") in {"frame_complete", "sampled_gaps"}:
            selected = summary.get("selected_slots")
            if (
                not isinstance(selected, list)
                or any(type(slot) is not int for slot in selected)
                or selected != sorted(set(selected))
            ):
                raise VerificationError("child_prefix_structural_selection_missing")
            _verify_structural_rows(selected, structural_by_key, summary)
    return child_terminal


def _verify_prefix_capture(
    child: Path,
    request: dict[str, Any],
    result: dict[str, Any] | None,
    cap_rel: str,
    plan: dict[str, Any],
    owner: int,
    parent_plan: dict[str, Any] | None = None,
    reservation: dict[str, Any] | None = None,
    started_at_ns: int | None = None,
) -> None:
    receipt_bytes = _read(_inside(child, f"{cap_rel}/receipt.json"), owner=owner)
    receipt = _json(receipt_bytes)
    storage = receipt.get("storage")
    stored_rel = (
        storage.get("path", "response.json") if isinstance(storage, dict) else "response.json"
    )
    stored_bytes = _read(_inside(child, f"{cap_rel}/{stored_rel}"), owner=owner)
    body = _decode_response(
        receipt,
        stored_bytes,
        expected_codec=plan.get("capture_storage_codec"),
        codec_tool=plan.get("capture_codec_tool"),
        max_response_bytes=plan.get("max_response_bytes", -1),
    )
    if {path.name for path in _inside(child, cap_rel).iterdir()} != {"receipt.json", stored_rel}:
        raise VerificationError("child_prefix_capture_directory_has_unexpected_artifact")
    expected_request = _canonical(
        {"jsonrpc": "2.0", "id": 1, "method": request["method"], "params": request["params"]}
    )
    if (
        _sha(expected_request) != request.get("request_sha256")
        or receipt.get("request_json") != expected_request.decode()
        or receipt.get("source_alias") != plan.get("provider")
        or receipt.get("response_sha256") != _sha(body)
        or receipt.get("response_bytes") != len(body)
        or type(receipt.get("request_started_ns")) is not int
        or type(receipt.get("received_ns")) is not int
        or not request.get("reserved_ns", -1)
        <= receipt["request_started_ns"]
        <= receipt["received_ns"]
    ):
        raise VerificationError("child_prefix_capture_receipt_binding_invalid")
    if parent_plan is not None and reservation is not None:
        bucket = next(
            b for b in parent_plan["buckets"] if b["bucket_id"] == reservation["bucket_id"]
        )
        end_ns = min(bucket["end_ns"], parent_plan["expires_ns"])
        if (
            receipt["request_started_ns"] < parent_plan["created_ns"]
            or receipt["request_started_ns"] >= end_ns
            or (started_at_ns is not None and receipt["request_started_ns"] < started_at_ns)
        ):
            raise VerificationError("child_prefix_capture_outside_parent_window")
    if (
        result is not None
        and result.get("status") == "captured"
        and (
            result.get("response_sha256") != _sha(body)
            or result.get("response_bytes") != len(body)
            or result.get("stored_response_bytes") != len(stored_bytes)
            or receipt.get("request_started_ns") != result.get("request_started_ns")
            or receipt.get("received_ns") != result.get("receipt_ns")
            or result.get("availability_ns", -1) < result.get("receipt_ns", 0)
        )
    ):
        raise VerificationError("child_prefix_capture_receipt_binding_invalid")
    if result is not None and result.get("status") == "captured":
        _validate_result_payload_binding(request, result, body)


def _prefix_capture_value(
    child: Path, request: dict[str, Any], result: dict[str, Any], plan: dict[str, Any], owner: int
) -> Any:
    cap_rel = result.get("capture_path")
    if not isinstance(cap_rel, str):
        raise VerificationError("child_prefix_capture_path_invalid")
    receipt = _json(_read(_inside(child, f"{cap_rel}/receipt.json"), owner=owner))
    storage = receipt.get("storage")
    stored_rel = (
        storage.get("path", "response.json") if isinstance(storage, dict) else "response.json"
    )
    stored = _read(_inside(child, f"{cap_rel}/{stored_rel}"), owner=owner)
    body = _decode_response(
        receipt,
        stored,
        expected_codec=plan.get("capture_storage_codec"),
        codec_tool=plan.get("capture_codec_tool"),
        max_response_bytes=plan.get("max_response_bytes", -1),
    )
    request_body = _canonical(
        {"jsonrpc": "2.0", "id": 1, "method": request["method"], "params": request["params"]}
    )
    if receipt.get("request_json") != request_body.decode() or receipt.get(
        "response_sha256"
    ) != _sha(body):
        raise VerificationError("child_prefix_capture_binding_invalid")
    return _jsonrpc_result(body)


def _check_operations(
    plan: dict[str, Any],
    child: Path,
    parsed: list[tuple[dict[str, Any], dict[str, Any], bytes, bytes, dict[str, Any]]],
    boot: bool,
    summary: dict[str, Any],
    owner: int,
) -> None:
    anchor_result = _jsonrpc_result(parsed[0][2])
    anchor_slot = anchor_result.get("result") if isinstance(anchor_result, dict) else None
    anchor_row = parsed[0][1]
    if (
        type(anchor_slot) is not int
        or anchor_slot < 1
        or anchor_row.get("slot_value") != anchor_slot
    ):
        raise VerificationError("anchor_slot_value_invalid")
    if boot:
        if (
            parsed[0][4].get("received_ns") != anchor_row.get("receipt_ns")
            or anchor_row.get("availability_ns") < anchor_row.get("receipt_ns")
            or summary.get("bootstrap_slot") != anchor_slot
            or summary.get("bootstrap_receipt_ns") != anchor_row.get("receipt_ns")
        ):
            raise VerificationError("anchor_availability_binding_invalid")
    elif anchor_row.get("availability_ns") < anchor_row.get("receipt_ns") or anchor_row.get(
        "receipt_ns"
    ) != parsed[0][4].get("received_ns"):
        raise VerificationError("anchor_availability_binding_invalid")
    expected: list[tuple[str, list[Any], int | None, str | None]] = [
        ("getSlot", [{"commitment": "finalized"}], None, "anchor")
    ]
    if not boot:
        frame = _json(_read(_inside(child, "frame.json"), owner=owner))
        start, end = frame.get("start"), frame.get("end_exclusive")
        if (
            frame != _expected_frame_artifact(plan, anchor_slot, anchor_row.get("receipt_ns"))
            or frame.get("status") != "captured"
            or type(start) is not int
            or type(end) is not int
            or end <= start
            or start != plan.get("next_start")
            or end != anchor_slot + 1
            or frame.get("anchor_slot") != anchor_slot
            or frame.get("anchor_receipt_ns") != anchor_row.get("receipt_ns")
        ):
            raise VerificationError("frame_definition_invalid")
        expected.append(
            ("getBlocks", [start, end - 1, {"commitment": "finalized"}], None, "inventory")
        )
        inventory = _jsonrpc_result(parsed[1][2])
        slots = inventory.get("result") if isinstance(inventory, dict) else None
        if (
            not isinstance(slots, list)
            or any(type(s) is not int for s in slots)
            or slots != sorted(set(slots))
            or any(s < start or s >= end for s in slots)
        ):
            raise VerificationError("inventory_payload_invalid")
        selection = _json(_read(_inside(child, "selection-plan.json"), owner=owner))
        expected_selection = _expected_selection_artifact(
            plan, slots, parsed[1][1]["response_sha256"]
        )
        if selection != expected_selection:
            raise VerificationError("selection_recomputation_mismatch")
        selected = expected_selection["selected_slots"]
        if len(selected) != 16:
            raise VerificationError("frame_selection_not_16")
        if (
            summary.get("start") != start
            or summary.get("end_exclusive") != end
            or summary.get("advance_to") != end
            or summary.get("selected_slots") != selected
            or summary.get("inventory_sha256") != parsed[1][1].get("response_sha256")
        ):
            raise VerificationError("summary_frontier_or_selection_binding_invalid")
        for slot in selected:
            expected.extend(
                [
                    (
                        "getBlock",
                        [
                            slot,
                            {
                                "commitment": "finalized",
                                "encoding": "json",
                                "transactionDetails": "full",
                                "rewards": False,
                                "maxSupportedTransactionVersion": 1,
                            },
                        ],
                        slot,
                        "full_block",
                    ),
                    (
                        "getBlock",
                        [
                            slot,
                            {
                                "commitment": "finalized",
                                "encoding": "json",
                                "transactionDetails": "signatures",
                                "rewards": False,
                            },
                        ],
                        slot,
                        "signature_check",
                    ),
                ]
            )
    if len(expected) != len(parsed):
        raise VerificationError("expected_child_operation_count_mismatch")
    for (method, params, slot, phase), (rq, rs, _, _, _) in zip(expected, parsed, strict=True):
        if (
            rq.get("method") != method
            or rq.get("params") != params
            or rq.get("phase") != phase
            or rs.get("phase") != phase
            or rq.get("slot") != slot
            or rs.get("slot") != slot
        ):
            raise VerificationError("child_operation_order_or_parameters_invalid")


def _verify_child_structure(
    plan: dict[str, Any],
    parsed: list[tuple[dict[str, Any], dict[str, Any], bytes, bytes, dict[str, Any]]],
    summary: dict[str, Any],
    boot: bool,
) -> None:
    if boot:
        if summary.get("status") != "bootstrap_complete":
            raise VerificationError("anchor_child_status_invalid")
        return
    selected = summary.get("selected_slots")
    if (
        not isinstance(selected, list)
        or len(selected) != 16
        or any(type(slot) is not int for slot in selected)
    ):
        raise VerificationError("child_selected_slots_invalid")
    by_key = {
        (rq.get("slot"), rq.get("phase")): (
            rs,
            _jsonrpc_result(body).get("result") if rs.get("result_status") == "parsed" else None,
        )
        for rq, rs, body, _stored, _receipt in parsed
    }
    _verify_structural_rows(selected, by_key, summary)


def _verify_structural_rows(
    selected: list[int],
    by_key: dict[tuple[int, str], tuple[dict[str, Any], Any]],
    summary: dict[str, Any],
) -> None:
    checks: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for slot in selected:
        full = by_key.get((slot, "full_block"))
        signatures = by_key.get((slot, "signature_check"))
        full_rs, full_result = full if full is not None else ({"status": "not_attempted"}, None)
        sig_rs, sig_result = (
            signatures if signatures is not None else ({"status": "not_attempted"}, None)
        )
        failures = _structural_failures(full_result, slot)
        structural = (
            _pump_counts(full_result) if isinstance(full_result, dict) and not failures else None
        )
        full_sigs = _signature_list(full_result, "full")
        sig_sigs = _signature_list(sig_result, "signatures")
        identity_equal = (
            isinstance(full_result, dict)
            and isinstance(sig_result, dict)
            and full_result.get("blockhash") == sig_result.get("blockhash")
            and full_result.get("parentSlot") == sig_result.get("parentSlot")
            and full_result.get("previousBlockhash") == sig_result.get("previousBlockhash")
        )
        qualified = (
            structural is not None
            and structural.get("unresolved_instruction_shapes") == 0
            and not structural.get("compiled_transaction_normalization_failures")
            and full_rs.get("status") == "captured"
            and full_rs.get("result_status") == "parsed"
            and sig_rs.get("status") == "captured"
            and sig_rs.get("result_status") == "parsed"
            and full_sigs is not None
            and sig_sigs is not None
            and full_sigs == sig_sigs
            and identity_equal
        )
        checks.append(
            {
                "slot": slot,
                "status": "qualified_structural_reference_count"
                if qualified
                else "self_check_failed_or_unknown",
                "full_response_validation_failures": failures,
                "structural_references": structural,
                "block_identity_equal": identity_equal,
                "signature_order_equal": full_sigs == sig_sigs
                if full_sigs is not None and sig_sigs is not None
                else None,
                "full_receipt_ns": full_rs.get("receipt_ns"),
                "signature_receipt_ns": sig_rs.get("receipt_ns"),
                "event_slot": slot,
                "block_time_seconds": full_result.get("blockTime")
                if isinstance(full_result, dict)
                else None,
            }
        )
        outcomes.append(
            {
                "slot": slot,
                "full_status": full_rs.get("status"),
                "full_result_status": full_rs.get("result_status"),
                "signature_status": sig_rs.get("status"),
                "signature_result_status": sig_rs.get("result_status"),
            }
        )
    if summary.get("structural_checks") != checks or summary.get("per_slot_outcomes") != outcomes:
        raise VerificationError("child_structural_summary_recomputation_mismatch")
    has_gaps = any(row["status"] != "qualified_structural_reference_count" for row in checks)
    expected_status = "sampled_gaps" if has_gaps else "frame_complete"
    if summary.get("status") != expected_status:
        raise VerificationError("child_structural_frame_status_mismatch")


def verify(root: Path) -> dict[str, Any]:
    """Verify one local envelope tree and return a read-only recovery recommendation."""
    original_root = root
    try:
        if stat.S_ISLNK(original_root.lstat().st_mode):
            raise VerificationError("parent_root_symlink_forbidden")
    except OSError as exc:
        raise VerificationError("parent_root_unavailable") from exc
    root = root.resolve(strict=True)
    st = root.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise VerificationError("parent_root_owner_or_mode_invalid")
    owner = os.getuid()
    plan_bytes = _read(root / "plan-v2.json", owner=owner)
    plan = _json(plan_bytes)
    _validate_plan(plan, plan_bytes)
    rows = _parent_events(root, owner)
    if (
        not rows
        or rows[0].get("kind") != "initialized"
        or rows[0].get("plan_sha256") != _sha(plan_bytes)
    ):
        raise VerificationError("parent_plan_not_bound")
    regressed_frontier_operations = _verify_transitions(plan, rows)
    reservations = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
    starts = {r["operation_id"]: r for r in rows if r["kind"] == "started"}
    bundles = {r["operation_id"]: r for r in rows if r["kind"] == "bundle"}
    seals = {r["operation_id"]: r for r in rows if r["kind"] == "seal"}
    verified_child_times: dict[str, tuple[int, int]] = {}
    counts = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
    for oid, reservation in reservations.items():
        if oid not in starts:
            child = _inside(root, reservation["child_path"])
            _validate_v3_prefix(child, reservation, owner, parent_plan=plan)
            plan_body = _read(child / "epoch-plan.json", owner=owner)
            if _sha(plan_body) != reservation.get("child_plan_sha256"):
                raise VerificationError("unstarted_child_plan_binding_invalid")
            request_dir = child / "requests"
            result_dir = child / "request-results"
            if (request_dir.exists() and list(request_dir.iterdir())) or (
                result_dir.exists() and list(result_dir.iterdir())
            ):
                raise VerificationError("child_io_without_parent_started_barrier")
            counts["requests"] += reservation["requests"]
            continue
        if oid not in bundles:
            child = _inside(root, reservation["child_path"])
            child_terminal = _validate_v3_prefix(
                child, reservation, owner, starts[oid]["at_ns"], plan
            )
            if child_terminal is not None:
                verified_child_times[oid] = child_terminal
            _verify_parent_stop_time(rows, verified_child_times, seals)
            return {
                "schema": f"{SCHEMA}-verifier-v2",
                "valid": True,
                "recovery": "stop_unknown_started_operation_no_replay",
                "activation_authorized": False,
            }
        bundle_event = bundles[oid]
        bundle_path = _inside(root, bundle_event.get("bundle_path"))
        bundle_bytes = _read(bundle_path, owner=owner)
        if _sha(bundle_bytes) != bundle_event.get("bundle_sha256"):
            raise VerificationError("parent_bundle_hash_invalid")
        bundle = _json(bundle_bytes)
        bucket = next(b for b in plan["buckets"] if b["bucket_id"] == reservation["bucket_id"])
        totals = _child(
            root,
            plan,
            {
                "operation_id": oid,
                "started_at_ns": starts[oid]["at_ns"],
                "bucket_end_ns": bucket["end_ns"],
                "lease_expires_ns": plan["expires_ns"],
            },
            reservation,
            bundle,
            owner,
        )
        usage = {key: value for key, value in totals.items() if not key.startswith("_")}
        if any(bundle_event.get("actual_" + key) != value for key, value in usage.items()):
            raise VerificationError("parent_child_totals_mismatch")
        if oid in seals and (
            type(seals[oid].get("at_ns")) is not int
            or seals[oid]["at_ns"] < totals["_child_sealed_ns"]
            or seals[oid]["at_ns"] < totals["_latest_availability_ns"]
        ):
            raise VerificationError("parent_seal_precedes_child_seal")
        verified_child_times[oid] = (totals["_child_sealed_ns"], totals["_latest_availability_ns"])
        if oid not in seals:
            _verify_parent_stop_time(rows, verified_child_times, seals)
            return {
                "schema": f"{SCHEMA}-verifier-v2",
                "valid": True,
                "recovery": "stopped_no_replay"
                if any(row["kind"] == "stop" for row in rows)
                else "frontier_regression_stop_required_no_replay"
                if oid in regressed_frontier_operations
                else "verified_bundle_ready_to_seal_no_replay",
                "verified_child_totals": totals,
                "activation_authorized": False,
            }
        if _sha(bundle_bytes) != seals[oid].get("bundle_sha256") or any(
            seals[oid].get("actual_" + key) != value for key, value in usage.items()
        ):
            raise VerificationError("parent_seal_binding_or_totals_invalid")
        for key, value in usage.items():
            counts[key] += value
    _verify_parent_stop_time(rows, verified_child_times, seals)
    return {
        "schema": f"{SCHEMA}-verifier-v2",
        "valid": True,
        "recovery": "stopped_no_replay"
        if any(row["kind"] == "stop" for row in rows)
        else "sealed"
        if len(seals) == len(reservations)
        else "unstarted_reservation_may_be_repaired_or_gapped",
        "usage": counts,
        "operation_count": len(reservations),
        "activation_authorized": False,
    }


def _verify_parent_stop_time(
    rows: list[dict[str, Any]],
    child_times: dict[str, tuple[int, int]],
    seals: dict[str, dict[str, Any]],
) -> None:
    stop = next((row for row in rows if row.get("kind") == "stop"), None)
    if stop is None:
        return
    if type(stop.get("at_ns")) is not int:
        raise VerificationError("parent_stop_time_missing")
    for oid, (child_stop_ns, latest_availability_ns) in child_times.items():
        minimum_stop_ns = max(child_stop_ns, latest_availability_ns)
        if oid in seals:
            seal_ns = seals[oid].get("at_ns")
            if type(seal_ns) is not int:
                raise VerificationError("parent_seal_time_missing")
            minimum_stop_ns = max(minimum_stop_ns, seal_ns)
        if stop["at_ns"] < minimum_stop_ns:
            raise VerificationError("parent_stop_precedes_completed_child")


def _validate_plan(plan: Any, encoded: bytes) -> None:
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise VerificationError("parent_plan_schema_invalid")
    if _canonical(plan) + b"\n" != encoded:
        raise VerificationError("parent_plan_noncanonical")
    if (
        not isinstance(plan.get("lease_id"), str)
        or not plan["lease_id"]
        or type(plan.get("created_ns")) is not int
        or type(plan.get("expires_ns")) is not int
        or plan["expires_ns"] <= plan["created_ns"]
    ):
        raise VerificationError("parent_plan_identity_or_clock_invalid")
    caps = plan.get("caps")
    cap_names = (
        "requests",
        "raw_bytes",
        "stored_bytes",
        "physical_bytes",
        "width",
        "daily_requests",
        "daily_raw_bytes",
        "daily_stored_bytes",
    )
    if not isinstance(caps, dict) or any(
        type(caps.get(key)) is not int or caps[key] <= 0 for key in cap_names
    ):
        raise VerificationError("parent_plan_caps_invalid")
    if caps["physical_bytes"] < 50 * 1024**3:
        raise VerificationError("parent_plan_disk_floor_below_minimum")
    source = plan.get("source_sha256")
    if (
        not isinstance(source, dict)
        or not source
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)
            for key, value in source.items()
        )
    ):
        raise VerificationError("parent_plan_source_binding_invalid")
    buckets = plan.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        raise VerificationError("parent_plan_buckets_invalid")
    seen: set[str] = set()
    previous_end = plan["created_ns"]
    for bucket in buckets:
        if (
            not isinstance(bucket, dict)
            or not isinstance(bucket.get("bucket_id"), str)
            or not bucket["bucket_id"]
            or bucket["bucket_id"] in seen
            or bucket.get("kind") not in {"anchor", "frame"}
            or type(bucket.get("start_ns")) is not int
            or type(bucket.get("end_ns")) is not int
            or bucket["start_ns"] < previous_end
            or bucket["end_ns"] <= bucket["start_ns"]
            or bucket["end_ns"] > plan["expires_ns"]
        ):
            raise VerificationError("parent_plan_bucket_schedule_invalid")
        seen.add(bucket["bucket_id"])
        previous_end = bucket["end_ns"]
    if buckets[0]["kind"] != "anchor":
        raise VerificationError("parent_plan_first_bucket_not_anchor")
    predecessor = plan.get("predecessor")
    if (
        not isinstance(predecessor, dict)
        or not isinstance(predecessor.get("terminal_sha256"), str)
        or len(predecessor["terminal_sha256"]) != 64
        or any(ch not in "0123456789abcdef" for ch in predecessor["terminal_sha256"])
        or type(predecessor.get("frontier_slot")) is not int
        or predecessor["frontier_slot"] < 0
        or not isinstance(predecessor.get("gaps"), list)
        or any(not isinstance(gap, str) or not gap for gap in predecessor["gaps"])
    ):
        raise VerificationError("parent_plan_predecessor_invalid")


def _verify_transitions(plan: dict[str, Any], rows: list[dict[str, Any]]) -> set[str]:
    if not rows or rows[0].get("kind") != "initialized":
        raise VerificationError("parent_initialization_missing")
    if any(row.get("kind") == "initialized" for row in rows[1:]):
        raise VerificationError("parent_duplicate_initialization")
    buckets = {row["bucket_id"]: row for row in plan["buckets"]}
    reservations: dict[str, dict[str, Any]] = {}
    starts: set[str] = set()
    bundles: dict[str, dict[str, Any]] = {}
    seals: dict[str, dict[str, Any]] = {}
    regressed_frontier_operations: set[str] = set()
    gapped: set[str] = set()
    child_paths: set[str] = set()
    frontier = plan["predecessor"]["frontier_slot"]
    reset_required = bool(plan["predecessor"]["gaps"])
    stopped = False
    for row in rows[1:]:
        kind = row.get("kind")
        oid = row.get("operation_id")
        if stopped:
            raise VerificationError("parent_event_after_stop")
        if kind == "reservation":
            bucket = buckets.get(row.get("bucket_id"))
            count = 1 if bucket and bucket["kind"] == "anchor" else 34
            if (
                not isinstance(oid, str)
                or not _SAFE_ID.fullmatch(oid)
                or oid in reservations
                or bucket is None
                or bucket["kind"] != row.get("bucket_kind")
                or row["bucket_id"] in gapped
                or any(r["bucket_id"] == row["bucket_id"] for r in reservations.values())
                or row.get("requests") != count
                or row.get("method") != ("v3-anchor" if count == 1 else "v3-frame")
                or type(row.get("at_ns")) is not int
                or not bucket["start_ns"]
                <= row["at_ns"]
                < min(bucket["end_ns"], plan["expires_ns"])
                or row.get("utc_day_index") != row["at_ns"] // 86_400_000_000_000
                or row.get("physical_free_bytes", 0) < plan["caps"]["physical_bytes"]
                or not isinstance(row.get("child_path"), str)
                or row.get("child_path") in child_paths
                or not isinstance(row.get("child_plan_sha256"), str)
                or len(row["child_plan_sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in row["child_plan_sha256"])
                or row.get("request_sha256")
                != _sha(
                    _canonical(
                        {
                            "bucket_id": row["bucket_id"],
                            "child_plan_sha256": row["child_plan_sha256"],
                            "request_count": count,
                        }
                    )
                )
            ):
                raise VerificationError("parent_reservation_invalid")
            outstanding = set(reservations) - set(seals)
            if outstanding:
                raise VerificationError("parent_multiple_outstanding_operations")
            known = set(gapped) | {r["bucket_id"] for r in reservations.values()}
            if any(
                b["end_ns"] <= row["at_ns"]
                and b["bucket_id"] not in known
                and b["bucket_id"] != row["bucket_id"]
                for b in plan["buckets"]
            ):
                raise VerificationError("parent_missed_bucket_not_gapped")
            latest_gap = max(
                (r["seq"] for r in rows[: row["seq"]] if r["kind"] == "gap"), default=-1
            )
            reservation_map = {r["operation_id"]: r for r in reservations.values()}
            latest_anchor = max(
                (
                    r["seq"]
                    for r in rows[: row["seq"]]
                    if r["kind"] == "seal"
                    and reservation_map[r["operation_id"]]["bucket_kind"] == "anchor"
                    and r.get("status") == "captured_bundle"
                ),
                default=-1,
            )
            if latest_gap > latest_anchor and bucket["kind"] != "anchor":
                raise VerificationError("parent_gap_requires_anchor")
            if bucket["kind"] == "frame" and latest_anchor < 0:
                raise VerificationError("parent_frame_without_anchor")
            if row.get("frontier_before") != frontier:
                raise VerificationError("parent_reservation_frontier_mismatch")
            if bucket["kind"] == "frame" and reset_required:
                raise VerificationError("parent_gap_requires_anchor")
            child_paths.add(row["child_path"])
            reservations[oid] = row
        elif kind == "started":
            reservation = reservations.get(oid)
            if (
                not reservation
                or oid in starts
                or row.get("child_plan_sha256") != reservation.get("child_plan_sha256")
                or type(row.get("at_ns")) is not int
                or row["at_ns"] < reservation["at_ns"]
                or row["at_ns"]
                >= min(buckets[reservation["bucket_id"]]["end_ns"], plan["expires_ns"])
                or row.get("physical_free_bytes", 0) < plan["caps"]["physical_bytes"]
            ):
                raise VerificationError("parent_start_invalid")
            starts.add(oid)
        elif kind == "bundle":
            reservation = reservations.get(oid)
            if (
                reservation is None
                or oid not in starts
                or oid in bundles
                or row.get("child_plan_sha256") != reservation.get("child_plan_sha256")
                or row.get("actual_requests", 0) > reservation["requests"]
                or row.get("actual_raw_bytes", 0) > reservation["raw_bytes"]
                or row.get("actual_stored_bytes", 0) > reservation["stored_bytes"]
                or not isinstance(row.get("bundle_sha256"), str)
                or len(row["bundle_sha256"]) != 64
                or row.get("frontier_before") != reservation.get("frontier_before")
                or type(row.get("frontier_after")) is not int
                or row["frontier_after"] <= 0
                or row.get("bundle_path") != f"bundles/{oid}.json"
            ):
                raise VerificationError("parent_bundle_transition_invalid")
            if row["frontier_after"] < reservation["frontier_before"]:
                regressed_frontier_operations.add(oid)
            bundles[oid] = row
        elif kind == "seal":
            reservation = reservations.get(oid)
            bundle = bundles.get(oid)
            if (
                not reservation
                or not bundle
                or oid in seals
                or row.get("status") != "captured_bundle"
                or row.get("bundle_sha256") != bundle.get("bundle_sha256")
                or row.get("actual_requests", 0) > reservation["requests"]
                or row.get("actual_raw_bytes", 0) > reservation["raw_bytes"]
                or row.get("actual_stored_bytes", 0) > reservation["stored_bytes"]
            ):
                raise VerificationError("parent_seal_invalid")
            if oid in regressed_frontier_operations:
                raise VerificationError("parent_bundle_frontier_regression_seal_forbidden")
            if reservation["bucket_kind"] == "anchor":
                if bundle["frontier_after"] < frontier:
                    raise VerificationError("parent_anchor_frontier_regression")
                frontier = bundle["frontier_after"]
                reset_required = False
            else:
                if reset_required or bundle.get("frontier_before") != frontier:
                    raise VerificationError("parent_frame_frontier_discontinuity")
                if bundle["frontier_after"] < frontier:
                    raise VerificationError("parent_frame_frontier_regression")
                frontier = bundle["frontier_after"]
            seals[oid] = row
        elif kind == "gap":
            bucket = buckets.get(row.get("bucket_id"))
            if (
                bucket is None
                or row["bucket_id"] in gapped
                or any(r["bucket_id"] == row["bucket_id"] for r in reservations.values())
                or type(row.get("at_ns")) is not int
                or row["at_ns"] < bucket["end_ns"]
                or not isinstance(row.get("reason"), str)
                or not row["reason"]
                or any(operation_id not in seals for operation_id in reservations)
            ):
                raise VerificationError("parent_gap_invalid")
            gapped.add(row["bucket_id"])
            reset_required = True
        elif kind == "stop":
            if not isinstance(row.get("reason"), str) or not row["reason"]:
                raise VerificationError("parent_stop_reason_invalid")
            if any(
                operation_id not in seals and operation_id in starts
                for operation_id in reservations
            ):
                if (
                    row.get("unknown_interval") is not True
                    or row.get("retry_started_child") is not False
                ):
                    raise VerificationError("parent_stop_unknown_interval_contract_invalid")
            stopped = True
        else:
            raise VerificationError("parent_transition_kind_invalid")
    usage = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
    daily: dict[int, dict[str, int]] = {}
    for oid, reservation in reservations.items():
        seal = seals.get(oid)
        actual = {
            key: seal.get("actual_" + key, reservation[key])
            if seal and seal.get("status") == "captured_bundle"
            else reservation[key]
            for key in usage
        }
        for key, value in actual.items():
            usage[key] += value
        day = reservation["utc_day_index"]
        day_total = daily.setdefault(day, {key: 0 for key in usage})
        for key, value in actual.items():
            day_total[key] += value
    if any(usage[key] > plan["caps"][key] for key in usage):
        raise VerificationError("parent_lease_budget_exceeded")
    daily_caps = {
        "requests": plan["caps"]["daily_requests"],
        "raw_bytes": plan["caps"]["daily_raw_bytes"],
        "stored_bytes": plan["caps"]["daily_stored_bytes"],
    }
    if any(amount[key] > daily_caps[key] for amount in daily.values() for key in usage):
        raise VerificationError("parent_utc_day_budget_exceeded")
    return regressed_frontier_operations
