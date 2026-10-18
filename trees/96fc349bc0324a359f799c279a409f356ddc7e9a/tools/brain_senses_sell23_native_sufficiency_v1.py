"""Read-only, diagnostic-only adapter for independently verified sell23 evidence.

The adapter accepts a typed evidence envelope; it does not open captures, fetch
providers, or re-run the upstream frame verifier. It can recognize explicit
ordered source-account instruction facts, but never qualifies CloseAccount
settlement, wallet proceeds, or an economic outcome.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any, cast

INPUT_SCHEMA = "brain-senses-sell23-native-sufficiency-input-v1"
OUTPUT_SCHEMA = "brain-senses-sell23-native-sufficiency-v1"
SCHEMA_VERSION = 1
LEGACY_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL_MINT = "So11111111111111111111111111111111111111112"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
STATUS_NAMES = ("successful", "failed", "unsupported", "unknown")


class SufficiencyError(ValueError):
    """Malformed or inconsistent upstream evidence envelope."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise SufficiencyError(code)


def _keys(value: Any, expected: set[str], code: str) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == expected, code)
    return cast(dict[str, Any], value)


def _hash(value: Any, code: str = "invalid_sha256") -> str:
    _require(type(value) is str and HASH_RE.fullmatch(value) is not None, code)
    return cast(str, value)


def _text(value: Any, code: str = "invalid_text") -> str:
    _require(type(value) is str and bool(value) and "\x00" not in value, code)
    return cast(str, value)


def _uint(value: Any, code: str = "invalid_nonnegative_integer") -> int:
    _require(type(value) is int and value >= 0, code)
    return cast(int, value)


def _time(value: Any, code: str = "invalid_timestamp") -> int:
    timestamp = _uint(value, code)
    _require(timestamp > 0, code)
    return timestamp


def _validate_source_hashes(value: Any) -> dict[str, str]:
    _require(type(value) is dict and bool(value), "source_hashes_invalid")
    checked: dict[str, str] = {}
    for name, digest in value.items():
        _require(
            type(name) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is not None,
            "source_name_invalid",
        )
        checked[name] = _hash(digest, "source_hash_invalid")
    return checked


def _validate_capture(value: Any) -> dict[str, Any]:
    capture = _keys(
        value,
        {
            "capture_id",
            "capture_sha256",
            "request_sha256",
            "slot",
            "blockhash",
            "event_time_ns",
            "receipt_ns",
            "availability_ns",
        },
        "capture_shape_invalid",
    )
    _text(capture["capture_id"], "capture_id_invalid")
    _hash(capture["capture_sha256"], "capture_hash_invalid")
    _hash(capture["request_sha256"], "capture_request_hash_invalid")
    _uint(capture["slot"], "capture_slot_invalid")
    _text(capture["blockhash"], "capture_blockhash_invalid")
    event = _time(capture["event_time_ns"], "capture_event_time_invalid")
    receipt = _time(capture["receipt_ns"], "capture_receipt_time_invalid")
    available = _time(capture["availability_ns"], "capture_availability_invalid")
    _require(event <= receipt <= available, "capture_time_order_invalid")
    return capture


def _validate_path(value: Any) -> tuple[int, int | None]:
    path = _keys(value, {"outer_index", "inner_index"}, "instruction_path_invalid")
    outer = _uint(path["outer_index"], "instruction_path_invalid")
    inner_value = path["inner_index"]
    inner = None if inner_value is None else _uint(inner_value, "instruction_path_invalid")
    return outer, inner


def _validate_effects(value: Any) -> list[dict[str, Any]]:
    _require(type(value) is list, "effects_invalid")
    rows: list[dict[str, Any]] = []
    orders: list[int] = []
    paths: list[tuple[int, int | None]] = []
    for raw in value:
        _require(type(raw) is dict, "effect_shape_invalid")
        kind = raw.get("kind")
        common = {"kind", "order", "path"}
        shapes: dict[str, set[str]] = {
            "system_create_account": common
            | {"payer", "account", "lamports", "space_bytes", "owner_program"},
            "system_transfer": common | {"source", "destination", "lamports"},
            "token_initialize_account": common | {"account", "token_program", "mint", "decimals"},
            "sync_native": common | {"account", "token_program"},
            "transfer_checked": common
            | {"source", "destination", "token_program", "mint", "decimals", "amount_atomic"},
            "close_account": common | {"account", "destination", "token_program"},
            "opaque_touch": common | {"accounts", "scope"},
            "unsupported_instruction": common | {"accounts", "program_id", "instruction_name"},
        }
        _require(kind in shapes, "effect_kind_unsupported")
        row = _keys(raw, shapes[kind], "effect_shape_invalid")
        order = _uint(row["order"], "effect_order_invalid")
        path = _validate_path(row["path"])
        orders.append(order)
        paths.append(path)
        if kind == "system_create_account":
            _text(row["payer"])
            _text(row["account"])
            _uint(row["lamports"])
            _uint(row["space_bytes"])
            _text(row["owner_program"])
        elif kind == "system_transfer":
            _text(row["source"])
            _text(row["destination"])
            _uint(row["lamports"])
        elif kind == "token_initialize_account":
            _text(row["account"])
            _text(row["token_program"])
            _text(row["mint"])
            _uint(row["decimals"])
        elif kind == "sync_native":
            _text(row["account"])
            _text(row["token_program"])
        elif kind == "transfer_checked":
            _text(row["source"])
            _text(row["destination"])
            _text(row["token_program"])
            _text(row["mint"])
            _uint(row["decimals"])
            _uint(row["amount_atomic"])
        elif kind == "close_account":
            _text(row["account"])
            _text(row["destination"])
            _text(row["token_program"])
        elif kind == "opaque_touch":
            _require(
                type(row["accounts"]) is list and bool(row["accounts"]), "opaque_accounts_invalid"
            )
            for account in row["accounts"]:
                _text(account)
            _require(row["scope"] in {"sibling", "parent", "unknown"}, "opaque_scope_invalid")
        else:
            _require(type(row["accounts"]) is list, "unsupported_accounts_invalid")
            for account in row["accounts"]:
                _text(account)
            _text(row["program_id"])
            _text(row["instruction_name"])
        rows.append(dict(row))
    _require(
        orders == sorted(orders) and len(orders) == len(set(orders)),
        "effect_order_duplicate_or_unsorted",
    )
    _require(len(paths) == len(set(paths)), "instruction_path_duplicate")
    path_keys = [(outer, -1 if inner is None else inner) for outer, inner in paths]
    _require(path_keys == sorted(path_keys), "effect_path_order_mismatch")
    return rows


ENDPOINT_KEYS = {
    "source_owner_program",
    "source_token_mint",
    "source_token_pre_atomic",
    "source_token_post_atomic",
    "source_lamports_pre",
    "source_lamports_post",
    "rent_sysvar_lamports",
    "destination_owner_program",
    "destination_lamports_pre",
    "destination_lamports_post",
    "closed_token_pre_atomic",
    "closed_token_post_atomic",
}


def _validate_endpoints(value: Any) -> dict[str, Any]:
    endpoints = _keys(value, ENDPOINT_KEYS, "endpoints_shape_invalid")
    for key, item in endpoints.items():
        if key.endswith("_program") or key.endswith("_mint"):
            _require(
                item is None or (type(item) is str and bool(item)), "endpoint_identity_invalid"
            )
        else:
            _require(item is None or (type(item) is int and item >= 0), "endpoint_value_invalid")
    return endpoints


def _source_flow_is_exact(row: dict[str, Any]) -> bool:
    if not row["ordered_effects_complete"] or row["layout_status"] != "supported_sell23":
        return False
    source = row["source_account"]
    destination = row["destination_account"]
    effects = row["effects"]
    creates = [
        e for e in effects if e["kind"] == "system_create_account" and e["account"] == source
    ]
    inits = [
        e for e in effects if e["kind"] == "token_initialize_account" and e["account"] == source
    ]
    syncs = [e for e in effects if e["kind"] == "sync_native" and e["account"] == source]
    transfers = [
        e
        for e in effects
        if e["kind"] == "transfer_checked" and source in (e["source"], e["destination"])
    ]
    closes = [e for e in effects if e["kind"] == "close_account" and e["account"] == source]
    if (len(creates), len(inits), len(syncs), len(transfers), len(closes)) != (1, 1, 1, 1, 1):
        return False
    create, init, sync, transfer, close = creates[0], inits[0], syncs[0], transfers[0], closes[0]
    if not (
        create["payer"] != source
        and create["space_bytes"] == 165
        and create["owner_program"] == LEGACY_TOKEN_PROGRAM
        and init["token_program"] == LEGACY_TOKEN_PROGRAM
        and init["mint"] == WSOL_MINT
        and init["decimals"] == 9
        and sync["token_program"] == LEGACY_TOKEN_PROGRAM
        and transfer["source"] == source
        and transfer["token_program"] == LEGACY_TOKEN_PROGRAM
        and transfer["mint"] == WSOL_MINT
        and transfer["decimals"] == 9
        and transfer["destination"] == row["target_token_account"]
        and row["target_transfer_amount_atomic"] is not None
        and transfer["amount_atomic"] == row["target_transfer_amount_atomic"]
        and close["destination"] == destination
        and close["token_program"] == LEGACY_TOKEN_PROGRAM
        and create["order"] < init["order"] < sync["order"] < transfer["order"] < close["order"]
    ):
        return False
    for effect in effects:
        if effect["kind"] == "system_transfer" and source in (
            effect["source"],
            effect["destination"],
        ):
            if type(effect["lamports"]) is not int or effect["order"] >= close["order"]:
                return False
        if effect["kind"] == "opaque_touch" and source in effect["accounts"]:
            return False
        if effect["kind"] == "unsupported_instruction" and source in effect["accounts"]:
            return False
    return True


def _blockers(row: dict[str, Any]) -> list[str]:
    endpoints = row["endpoints"]
    blockers: list[str] = []
    for first, second, reason in (
        ("source_lamports_pre", "source_lamports_post", "source_lamport_endpoints_missing"),
        ("source_token_pre_atomic", "source_token_post_atomic", "source_token_endpoints_missing"),
        ("closed_token_pre_atomic", "closed_token_post_atomic", "closed_token_endpoints_missing"),
        (
            "destination_lamports_pre",
            "destination_lamports_post",
            "destination_lamport_endpoints_missing",
        ),
    ):
        if endpoints[first] is None or endpoints[second] is None:
            blockers.append(reason)
    if endpoints["source_owner_program"] is None:
        blockers.append("source_owner_endpoint_missing")
    if endpoints["source_token_mint"] is None:
        blockers.append("source_token_mint_endpoint_missing")
    if endpoints["destination_owner_program"] is None:
        blockers.append("destination_owner_endpoint_missing")
    if endpoints["rent_sysvar_lamports"] is None:
        blockers.append("historical_rent_endpoint_missing")
    return sorted(blockers)


def _disposition(row: dict[str, Any]) -> tuple[str, str, list[str]]:
    blockers = _blockers(row)
    if row["input_status"] == "failed":
        return "failed_attempt", "not_evaluated_failed_attempt", blockers
    if row["layout_status"] == "unsupported_layout":
        return "unsupported_layout", "unqualified", blockers
    if any(e["kind"] == "unsupported_instruction" for e in row["effects"]):
        return "unsupported_instruction", "unqualified", blockers
    if any(
        e["kind"]
        in {"token_initialize_account", "sync_native", "transfer_checked", "close_account"}
        and e.get("token_program") == TOKEN_2022_PROGRAM
        for e in row["effects"]
    ):
        return "unsupported_token_2022", "unqualified", blockers
    if row["input_status"] == "unknown" or row["layout_status"] == "unknown":
        return "evidence_unknown", "unqualified", blockers
    if row["input_status"] == "unsupported":
        return "unsupported_instruction", "unqualified", blockers
    exact = _source_flow_is_exact(row)
    source_transfers = [
        effect
        for effect in row["effects"]
        if effect["kind"] == "transfer_checked" and effect["source"] == row["source_account"]
    ]
    if (
        len(source_transfers) == 1
        and row["target_transfer_amount_atomic"] is not None
        and source_transfers[0]["amount_atomic"] != row["target_transfer_amount_atomic"]
    ):
        blockers.append("target_transfer_amount_mismatch")
        exact = False
    opaque_dest = any(
        e["kind"] == "opaque_touch" and row["destination_account"] in e["accounts"]
        for e in row["effects"]
    )
    dest = "destination_ambiguous" if opaque_dest else "destination_unqualified"
    if opaque_dest:
        blockers.append("opaque_sibling_or_parent_destination_touch")
    if exact:
        return "source_account_flow_exact", dest, sorted(set(blockers))
    return "evidence_insufficient", dest, sorted(set(blockers))


def compile_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a typed upstream envelope and emit conservative dispositions.

    The adapter trusts upstream verification only as a provenance declaration;
    it checks the internal joins and semantics in the supplied envelope but does
    not independently replay raw captures.
    """
    envelope = _keys(
        value,
        {
            "schema",
            "schema_version",
            "verification",
            "lineage",
            "frame",
            "denominators",
            "captures",
            "selection_rows",
        },
        "input_envelope_shape_invalid",
    )
    _require(
        envelope["schema"] == INPUT_SCHEMA and envelope["schema_version"] == SCHEMA_VERSION,
        "input_schema_invalid",
    )
    verification = _keys(
        envelope["verification"],
        {"status", "verifier_sha256", "envelope_sha256"},
        "verification_shape_invalid",
    )
    _require(verification["status"] == "independently_verified", "input_not_independently_verified")
    _hash(verification["verifier_sha256"], "verifier_hash_invalid")
    declared_input_hash = _hash(verification["envelope_sha256"], "input_envelope_hash_invalid")
    raw_input_hash = sha256_bytes(
        canonical_json({k: v for k, v in envelope.items() if k != "verification"})
    )
    _require(declared_input_hash == raw_input_hash, "input_envelope_hash_mismatch")

    lineage = _keys(
        envelope["lineage"],
        {"frame_id", "frame_sha256", "plan_sha256", "request_sha256", "source_sha256"},
        "lineage_shape_invalid",
    )
    _text(lineage["frame_id"], "frame_id_invalid")
    for key in ("frame_sha256", "plan_sha256", "request_sha256"):
        _hash(lineage[key], f"{key}_invalid")
    source_hashes = _validate_source_hashes(lineage["source_sha256"])

    frame = _keys(
        envelope["frame"],
        {
            "status",
            "scheduled_frames",
            "sealed_frames",
            "missed_frames",
            "selected_blocks",
            "produced_blocks",
        },
        "frame_shape_invalid",
    )
    _require(frame["status"] == "sealed", "frame_not_sealed")
    for key in (
        "scheduled_frames",
        "sealed_frames",
        "missed_frames",
        "selected_blocks",
        "produced_blocks",
    ):
        _uint(frame[key], "frame_denominator_invalid")
    _require(
        frame["sealed_frames"] + frame["missed_frames"] <= frame["scheduled_frames"],
        "frame_denominator_inconsistent",
    )
    _require(frame["selected_blocks"] <= frame["produced_blocks"], "block_denominator_inconsistent")

    captures_raw = envelope["captures"]
    _require(type(captures_raw) is list, "captures_invalid")
    captures: dict[str, dict[str, Any]] = {}
    for raw in captures_raw:
        capture = _validate_capture(raw)
        _require(capture["capture_id"] not in captures, "duplicate_capture_id")
        captures[capture["capture_id"]] = capture

    rows_raw = envelope["selection_rows"]
    _require(type(rows_raw) is list, "selection_rows_invalid")
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in rows_raw:
        row = _keys(
            raw,
            {
                "candidate_id",
                "input_status",
                "signature_sha256",
                "slot",
                "blockhash",
                "capture_id",
                "layout_status",
                "source_account",
                "target_token_account",
                "destination_account",
                "target_transfer_amount_atomic",
                "ordered_effects_complete",
                "effects",
                "endpoints",
            },
            "candidate_shape_invalid",
        )
        _text(row["candidate_id"], "candidate_id_invalid")
        _require(row["candidate_id"] not in seen_ids, "duplicate_candidate_id")
        seen_ids.add(row["candidate_id"])
        _require(row["input_status"] in STATUS_NAMES, "candidate_status_invalid")
        counts[row["input_status"]] += 1
        _hash(row["signature_sha256"], "signature_hash_invalid")
        _uint(row["slot"], "candidate_slot_invalid")
        _text(row["blockhash"], "candidate_blockhash_invalid")
        _text(row["capture_id"], "candidate_capture_id_invalid")
        capture_value = captures.get(row["capture_id"])
        if capture_value is None:
            raise SufficiencyError("candidate_capture_missing")
        capture = capture_value
        _require(
            row["slot"] == capture["slot"] and row["blockhash"] == capture["blockhash"],
            "candidate_fork_identity_mismatch",
        )
        _require(
            row["layout_status"] in {"supported_sell23", "unsupported_layout", "unknown"},
            "layout_status_invalid",
        )
        _text(row["source_account"], "source_account_invalid")
        _text(row["target_token_account"], "target_token_account_invalid")
        _text(row["destination_account"], "destination_account_invalid")
        amount = row["target_transfer_amount_atomic"]
        _require(amount is None or (type(amount) is int and amount >= 0), "target_amount_invalid")
        _require(type(row["ordered_effects_complete"]) is bool, "ordered_completeness_invalid")
        row["effects"] = _validate_effects(row["effects"])
        row["endpoints"] = _validate_endpoints(row["endpoints"])
        rows.append(dict(row))

    denominator = _keys(
        envelope["denominators"],
        {"selected_candidates", "status_counts"},
        "selection_denominator_shape_invalid",
    )
    _require(
        type(denominator["selected_candidates"]) is int and denominator["selected_candidates"] >= 0,
        "selection_denominator_invalid",
    )
    status_counts = _keys(
        denominator["status_counts"], set(STATUS_NAMES), "status_denominator_shape_invalid"
    )
    for status in STATUS_NAMES:
        _uint(status_counts[status], "status_denominator_invalid")
    _require(
        denominator["selected_candidates"] == len(rows), "selected_candidate_denominator_mismatch"
    )
    _require(
        dict(counts) == {k: status_counts[k] for k in STATUS_NAMES if status_counts[k] > 0},
        "candidate_status_denominator_mismatch",
    )
    _require(
        sum(status_counts.values()) == denominator["selected_candidates"],
        "status_denominator_total_mismatch",
    )

    dispositions: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    for row in rows:
        disposition, destination_status, blockers = _disposition(row)
        capture = captures[row["capture_id"]]
        outcome_counts[disposition] += 1
        dispositions.append(
            {
                "candidate_id": row["candidate_id"],
                "input_status": row["input_status"],
                "signature_sha256": row["signature_sha256"],
                "layout_status": row["layout_status"],
                "source_account": row["source_account"],
                "target_token_account": row["target_token_account"],
                "close_destination_account": row["destination_account"],
                "target_transfer_amount_atomic": row["target_transfer_amount_atomic"],
                "ordered_effects_complete": row["ordered_effects_complete"],
                "effects": row["effects"],
                "endpoints": row["endpoints"],
                "slot": row["slot"],
                "blockhash": row["blockhash"],
                "capture_id": capture["capture_id"],
                "capture_sha256": capture["capture_sha256"],
                "request_sha256": capture["request_sha256"],
                "event_time_ns": capture["event_time_ns"],
                "receipt_ns": capture["receipt_ns"],
                "availability_ns": capture["availability_ns"],
                "source_account_flow": disposition,
                "destination_status": destination_status,
                "blockers": blockers,
            }
        )
    return {
        "schema": OUTPUT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": "diagnostic_evidence_sufficiency_only",
        "verification_basis": (
            "typed_upstream_verified_envelope_internal_consistency_checked_not_raw_replay"
        ),
        "lineage": {
            "frame_id": lineage["frame_id"],
            "frame_sha256": lineage["frame_sha256"],
            "plan_sha256": lineage["plan_sha256"],
            "request_sha256": lineage["request_sha256"],
            "source_sha256": source_hashes,
            "upstream_verifier_sha256": verification["verifier_sha256"],
            "input_envelope_sha256": declared_input_hash,
        },
        "frame_denominators": dict(frame),
        "selection_denominators": {
            "selected_candidates": denominator["selected_candidates"],
            "status_counts": {status: status_counts[status] for status in STATUS_NAMES},
            "disposition_counts": {
                status: outcome_counts[status] for status in sorted(outcome_counts)
            },
        },
        "captures": [dict(captures[key]) for key in sorted(captures)],
        "candidates": dispositions,
        "decision_ns": None,
        "destination_settlement_qualified": False,
        "native_proceeds_qualified": False,
        "wallet_net_qualified": False,
        "buy_exact25_admission": False,
        "economic_admission": False,
        "training_admission": False,
    }
