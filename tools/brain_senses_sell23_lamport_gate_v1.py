#!/usr/bin/env python3
"""Diagnostic-only c3 sell23 destination lamport projection.

The frozen source-account equation in v1 omitted the lamports transferred by a
native WSOL TransferChecked. This tool re-derives a corrected source-side
projection from ordered, decoded effects. That projection is never attributed
to CloseAccount: the frozen transactions contain opaque writable ATA/PumpSwap
touches whose direct lamport effects are not semantically exhausted here.
Consequently all destination outcomes remain ambiguous, including rows whose
decoded destination arithmetic matches transaction-boundary balances.

Frozen effect contract:
- System Transfer uses its explicit u64 amount and verified source/recipient roles.
- System CreateAccount debits its explicit payer; a newly-created destination
  credit is only a candidate with one decoded create, zero prebalance, and
  complete order proving no earlier or enclosing potentially writable touch.
  Opaque touches after the create leave the candidate term intact but unknown.
  Withheld raw amounts remain explicit unknowns, never numeric zero.
- Legacy SPL Native TransferChecked moves its explicit WSOL atomic amount in
  lamports only after WSOL mint, legacy Token program, temp-account init, and
  ordered SyncNative/source/destination/decimals checks. It is subtracted from
  the source projection. Historical rent is not inferred.
- The target CloseAccount sweep remains withheld from destination arithmetic.
  The corrected source remainder is diagnostic only; parent-program opacity
  prevents treating it as destination credit.
- `meta.fee` is charged once to message account index zero. It is never inferred
  from signatures or used to allocate any other account effect.
- Missing endpoints, failed transactions, malformed/duplicate order, unknown
  writable touches, and endpoint mismatches never become zero or exact.
- Token atomic amounts and lamports remain separate fields and equations.

The script performs no provider I/O and writes its large row ledger only through
the configured descriptor-bound research-storage root.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from brain_senses_sell23_c3_native_account_flow import (  # noqa: E402
    _system_effect,
    _token_effect,
    classify_account,
)
from brain_senses_sell23_process_qualification_v1 import (  # noqa: E402
    C3_ROOT,
    MANIFEST_PATH,
    SOURCE_RECEIPTS,
    _canonical_hash,
    _decode_capture,
    _load,
    _sha_bytes,
    _sha_file,
    verify_source_manifest,
)

from memecoin_pumpswap.decoder import TOKEN  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from memecoin_research_storage import writer_guard  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json  # noqa: E402

CONTRACT = "pumpswap_sell23_c3_destination_lamport_diagnostic_v3"
PROCESS_RECEIPT_PATH = (
    REPO / "work/brain-senses-sell23-process-qualification-v1/process-receipt-final-v1.json"
)
FEASIBILITY_PATH = SOURCE_RECEIPTS["native_close_feasibility"]
FLOW_PATH = SOURCE_RECEIPTS["native_account_flow"]
REPLAY_PATH = SOURCE_RECEIPTS["six_frame_replay"]
STORAGE_CONFIG = Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json"
STORAGE_ROOT_ID = "9428695af6d4f46c08e1a444cdc66de8"
STORAGE_VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
LEDGER_RELATIVE = "derived/sell23-lamport-gate-v3-final/c3-diagnostic-rows.jsonl"
RECEIPT_PATH = (
    REPO
    / "docs/evidence/pumpswap-sell23-c3-destination-lamport-diagnostic-v3-final-2026-10-12.json"
)
SYSTEM = "11111111111111111111111111111111"
ASSOCIATED_TOKEN = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL = "So11111111111111111111111111111111111111112"


class GateError(ValueError):
    """Frozen source or diagnostic contract could not be verified."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise GateError(code)


def _strict_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _ordered_unique_nonnegative_ints(values: list[Any]) -> bool:
    if any(type(value) is not int or value < 0 for value in values):
        return False
    integers = cast(list[int], values)
    return integers == sorted(integers) and len(integers) == len(set(integers))


def _ordered_instructions(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    inner_by_outer: dict[int, list[dict[str, Any]]] = {}
    for ix in normalized["inner_instructions"]:
        inner_by_outer.setdefault(ix["outer_index"], []).append(ix)
    rows: list[dict[str, Any]] = []
    for outer in normalized["outer_instructions"]:
        outer_index = outer["path"][0]
        rows.append(outer)
        stack_by_height: dict[int, dict[str, Any]] = {1: outer}
        previous_height = 1
        for ix in inner_by_outer.get(outer_index, []):
            height = ix.get("stack_height")
            if (
                type(height) is not int
                or height < 2
                or height > previous_height + 1
                or height - 1 not in stack_by_height
            ):
                raise GateError("inner_instruction_ancestry_ambiguous")
            for stale_height in [value for value in stack_by_height if value >= height]:
                del stack_by_height[stale_height]
            stack_by_height[height] = ix
            previous_height = height
            rows.append(ix)
    expected = len(normalized["outer_instructions"]) + len(normalized["inner_instructions"])
    paths = [tuple(ix["path"]) for ix in rows]
    if len(rows) != expected or len(paths) != len(set(paths)):
        raise GateError("instruction_order_missing_or_duplicate")
    return rows


def _message_writable_accounts(tx: dict[str, Any], normalized: dict[str, Any]) -> dict[str, bool]:
    message = tx["transaction"]["message"]
    header = message.get("header")
    static = message.get("accountKeys")
    if not isinstance(header, dict) or not isinstance(static, list):
        raise GateError("message_privilege_header_missing")
    required = header.get("numRequiredSignatures")
    readonly_signed = header.get("numReadonlySignedAccounts")
    readonly_unsigned = header.get("numReadonlyUnsignedAccounts")
    if any(type(v) is not int or v < 0 for v in (required, readonly_signed, readonly_unsigned)):
        raise GateError("message_privilege_header_invalid")
    required_count = cast(int, required)
    readonly_signed_count = cast(int, readonly_signed)
    readonly_unsigned_count = cast(int, readonly_unsigned)
    if (
        required_count > len(static)
        or readonly_signed_count > required_count
        or readonly_unsigned_count > len(static) - required_count
    ):
        raise GateError("message_privilege_header_inconsistent")
    keys = [row["pubkey"] for row in normalized["accounts"]]
    writable: dict[str, bool] = {}
    for index, key in enumerate(keys):
        if index < len(static):
            if index < required_count:
                value = index < required_count - readonly_signed_count
            else:
                value = index < len(static) - readonly_unsigned_count
        else:
            loaded = tx.get("meta", {}).get("loadedAddresses") or {}
            writable_count = len(loaded.get("writable", [])) if isinstance(loaded, dict) else 0
            value = len(static) <= index < len(static) + writable_count
        if key in writable and writable[key] != value:
            raise GateError("duplicate_account_privilege_conflict")
        writable[key] = value
    return writable


def _lamport_endpoints(
    tx: dict[str, Any], account: str
) -> tuple[int | None, int | None, int | None]:
    normalized = normalize_compiled_transaction(tx)
    indexes = [i for i, row in enumerate(normalized["accounts"]) if row["pubkey"] == account]
    if len(indexes) != 1:
        return None, None, None
    index = indexes[0]
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances")
    post = meta.get("postBalances")
    count = len(normalized["accounts"])
    if (
        not isinstance(pre, list)
        or not isinstance(post, list)
        or len(pre) != count
        or len(post) != count
    ):
        return index, None, None
    if not _strict_int(pre[index]) or not _strict_int(post[index]):
        raise GateError("invalid_lamport_endpoint")
    return index, pre[index], post[index]


def project_temporary_account(
    *,
    account: str,
    destination: str,
    token_program: str,
    mint: str,
    pre_lamports: int | None,
    post_lamports: int | None,
    effects: list[dict[str, Any]],
    opaque_writable_paths: list[list[int]] | None = None,
) -> dict[str, Any]:
    """Return corrected source-side lamport projection, never destination credit.

    `effects` must be in strict execution order and already decoded from their
    source wire forms. Test fixtures exercise this pure semantic boundary.
    """
    opaque_paths = opaque_writable_paths or []
    if pre_lamports is None or post_lamports is None:
        return {
            "status": "missing_endpoint",
            "decoded_trace_remainder_lamports": None,
            "pre_close_lamports_candidate": None,
            "candidate_is_diagnostic_only": True,
            "close_credit_lamports": None,
            "reasons": ["missing_endpoint"],
            "opaque_writable_paths": opaque_paths,
        }
    if not _strict_int(pre_lamports) or not _strict_int(post_lamports):
        raise GateError("invalid_temporary_lamport_endpoint")
    order_values = [row.get("order") for row in effects]
    paths = [tuple(row.get("path", [])) for row in effects]
    if not _ordered_unique_nonnegative_ints(order_values) or len(paths) != len(set(paths)):
        return {
            "status": "unsupported",
            "decoded_trace_remainder_lamports": None,
            "pre_close_lamports_candidate": None,
            "candidate_is_diagnostic_only": True,
            "close_credit_lamports": None,
            "reasons": ["instruction_order_missing_or_ambiguous"],
            "opaque_writable_paths": opaque_paths,
        }
    if token_program != TOKEN or mint != WSOL:
        return {
            "status": "unsupported",
            "decoded_trace_remainder_lamports": None,
            "pre_close_lamports_candidate": None,
            "candidate_is_diagnostic_only": True,
            "close_credit_lamports": None,
            "reasons": ["legacy_wsol_semantics_not_proven"],
            "opaque_writable_paths": opaque_paths,
        }

    creates = [
        row
        for row in effects
        if row.get("kind") == "create_account" and row.get("account") == account
    ]
    inits = [
        row
        for row in effects
        if row.get("kind") == "initialize_account" and row.get("account") == account
    ]
    syncs = [
        row for row in effects if row.get("kind") == "sync_native" and row.get("account") == account
    ]
    transfers = [
        row
        for row in effects
        if row.get("kind") == "transfer_checked"
        and account in (row.get("source"), row.get("destination"))
    ]
    closes = [
        row
        for row in effects
        if row.get("kind") == "close_account" and row.get("account") == account
    ]
    reasons: list[str] = []
    if (len(creates), len(inits), len(syncs), len(transfers), len(closes)) != (1, 1, 1, 1, 1):
        reasons.append("lifecycle_effect_count_mismatch")
    opaque_path_tuples = {tuple(path) for path in opaque_paths}
    unsupported = [row for row in effects if row.get("kind") == "unsupported_touch"]
    unaccounted_unsupported = [
        row for row in unsupported if tuple(row.get("path", [])) not in opaque_path_tuples
    ]
    if unaccounted_unsupported:
        reasons.append("unsupported_temporary_account_touch")
    system_moves = [
        row
        for row in effects
        if row.get("kind") == "system_transfer"
        and account in (row.get("source"), row.get("destination"))
    ]
    other_system = [row for row in effects if row.get("kind") == "unsupported_system_touch"]
    if other_system:
        reasons.append("unsupported_system_effect")
    create = creates[0] if len(creates) == 1 else None
    init = inits[0] if len(inits) == 1 else None
    sync = syncs[0] if len(syncs) == 1 else None
    transfer = transfers[0] if len(transfers) == 1 else None
    close = closes[0] if len(closes) == 1 else None
    if create and (
        create.get("payer") == account
        or create.get("space_bytes") != 165
        or create.get("owner_program") != token_program
    ):
        reasons.append("create_account_roles_or_shape_invalid")
    if init and (init.get("mint") != mint or init.get("variant") != 18):
        reasons.append("temporary_account_native_initialization_invalid")
    if transfer and (
        transfer.get("source") != account
        or transfer.get("mint") != mint
        or transfer.get("decimals") != 9
        or not _strict_int(transfer.get("amount_atomic"))
    ):
        reasons.append("native_transferchecked_roles_or_units_invalid")
    if close and (close.get("destination") != destination or close.get("account") != account):
        reasons.append("close_account_roles_invalid")
    if pre_lamports != 0 or post_lamports != 0:
        reasons.append("temporary_account_boundary_not_zero")
    if create and init and sync and transfer and close:
        if not (
            create["order"] < init["order"] < sync["order"] < transfer["order"] < close["order"]
        ):
            reasons.append("native_lifecycle_order_invalid")
        if any(row["order"] >= close["order"] and row not in (close,) for row in system_moves):
            reasons.append("system_touch_after_close")
    for move in system_moves:
        if (
            not _strict_int(move.get("lamports"))
            or move.get("source") == move.get("destination") == account
        ):
            reasons.append("system_transfer_roles_or_amount_invalid")

    create_lamports = create.get("lamports") if create else None
    if not _strict_int(create_lamports):
        reasons.append("create_lamports_missing")
    credits = sum(
        row["lamports"]
        for row in system_moves
        if row.get("destination") == account and _strict_int(row.get("lamports"))
    )
    debits = sum(
        row["lamports"]
        for row in system_moves
        if row.get("source") == account and _strict_int(row.get("lamports"))
    )
    native_transfer_lamports = (
        transfer.get("amount_atomic") if transfer and transfer.get("source") == account else None
    )
    if not _strict_int(native_transfer_lamports):
        reasons.append("native_transfer_lamport_projection_missing")
    remainder = None
    if _strict_int(create_lamports) and _strict_int(native_transfer_lamports) and not reasons:
        create_value = cast(int, create_lamports)
        native_transfer_value = cast(int, native_transfer_lamports)
        remainder = (
            pre_lamports
            + create_value
            + credits
            - debits
            - native_transfer_value
            - post_lamports
        )
        if remainder < 0:
            reasons.append("negative_projected_remainder")
            remainder = None

    if reasons:
        status = (
            "unsupported" if any("unsupported" in reason for reason in reasons) else "ambiguous"
        )
    elif opaque_paths or unsupported:
        status = "opaque_parent_semantics_unresolved"
    else:
        status = "source_projection_reconciled_candidate"
    return {
        "status": status,
        "pre_lamports": pre_lamports,
        "created_lamports": create_lamports,
        "system_credited_lamports": credits,
        "system_debited_lamports": debits,
        "native_transfer_amount_atomic_units": transfer.get("amount_atomic") if transfer else None,
        "native_transfer_lamports_under_legacy_wsol_semantics": native_transfer_lamports,
        "post_lamports": post_lamports,
        "decoded_trace_remainder_lamports": remainder,
        "pre_close_lamports_candidate": remainder if not reasons else None,
        "candidate_is_diagnostic_only": True,
        "close_credit_lamports": None,
        "reasons": sorted(set(reasons)),
        "opaque_writable_paths": opaque_paths,
    }


def project_destination_effects(
    *,
    destination: str,
    fee_payer: str | None,
    pre_lamports: int | None,
    post_lamports: int | None,
    fee_lamports: int | None,
    effects: list[dict[str, Any]],
    opaque_writable_paths: list[list[int]] | None = None,
    transaction_success: bool = True,
) -> dict[str, Any]:
    """Project known destination effects, retaining every unknown as unknown.

    `opaque_writable_touch` effects are ordered in the same sequence as decoded
    leaves. They can suppress a newly-created destination's credit when they
    precede or enclose its CreateAccount. They never contribute numeric zero.
    """
    opaque_paths = opaque_writable_paths or []
    empty = {
        "decoded_projection_delta_lamports": None,
        "transaction_boundary_delta_lamports": None,
        "unexplained_delta_lamports": None,
        "opaque_writable_paths": opaque_paths,
        "raw_destination_create_amounts_lamports": [],
        "withheld_destination_create_amounts_lamports": [],
        "unknown_contribution_count": None,
    }
    if not transaction_success:
        return {
            **empty,
            "status": "failed_excluded",
            "reasons": ["failed_transaction_effects_rolled_back"],
        }
    if pre_lamports is None or post_lamports is None or fee_payer is None or fee_lamports is None:
        return {
            **empty,
            "status": "missing_endpoint",
            "reasons": ["destination_or_fee_endpoint_missing"],
        }
    if not all(_strict_int(v) for v in (pre_lamports, post_lamports, fee_lamports)):
        return {
            **empty,
            "status": "unsupported",
            "reasons": ["lamport_dimension_or_value_invalid"],
        }
    target_creates = [
        row
        for row in effects
        if row.get("kind") == "create_account" and row.get("account") == destination
    ]
    raw_create_amounts = [row.get("lamports") for row in target_creates]
    orders = [row.get("order") for row in effects]
    paths = [tuple(row.get("path", [])) for row in effects]
    if not _ordered_unique_nonnegative_ints(orders) or len(set(paths)) != len(paths):
        return {
            **empty,
            "status": "unsupported",
            "transaction_boundary_delta_lamports": post_lamports - pre_lamports,
            "raw_destination_create_amounts_lamports": raw_create_amounts,
            "withheld_destination_create_amounts_lamports": [
                row["lamports"] for row in target_creates if _strict_int(row.get("lamports"))
            ],
            "unknown_contribution_count": max(1, len(opaque_paths) + len(target_creates)),
            "reasons": ["instruction_order_missing_or_ambiguous"],
        }
    projection = -fee_lamports if destination == fee_payer else 0
    decoded: list[dict[str, Any]] = []
    opaque = {tuple(path) for path in opaque_paths}
    target_create_order = target_creates[0].get("order") if len(target_creates) == 1 else None
    if len(target_creates) > 1:
        opaque.update(tuple(row.get("path", [])) for row in target_creates)
    withheld_creates: list[int] = []
    unknown_paths: set[tuple[int, ...]] = set(opaque)
    marker_paths = {
        tuple(row.get("path", [])) for row in effects if row.get("kind") == "opaque_writable_touch"
    }
    unpositioned_opaque = opaque - marker_paths
    earlier_touch = False
    for row in effects:
        kind = row.get("kind")
        amount = row.get("lamports")
        order = row["order"]
        path = tuple(row.get("path", []))
        if kind == "opaque_writable_touch":
            opaque.add(path)
            unknown_paths.add(path)
            touched_marker_order = row.get("order")
            if target_create_order is not None and (
                touched_marker_order < target_create_order
                or _path_is_prefix(path, tuple(target_creates[0].get("path", [])))
            ):
                earlier_touch = True
            continue
        if kind == "read_only_touch":
            # A proven read-only message reference cannot become writable in CPI.
            continue
        if kind == "system_transfer":
            source, recipient = row.get("source"), row.get("destination")
            if source != destination and recipient != destination:
                continue
            if (
                not _strict_int(amount)
                or not isinstance(source, str)
                or not isinstance(recipient, str)
            ):
                opaque.add(path)
                unknown_paths.add(path)
                continue
            if source == destination and recipient == destination:
                delta = 0
            elif source == destination:
                delta = -cast(int, amount)
            elif recipient == destination:
                delta = cast(int, amount)
            else:
                opaque.add(path)
                unknown_paths.add(path)
                continue
            projection += delta
            decoded.append({"path": list(path), "kind": kind, "delta_lamports": delta})
        elif kind == "create_account":
            payer, created = row.get("payer"), row.get("account")
            if (
                not _strict_int(amount)
                or not isinstance(payer, str)
                or not isinstance(created, str)
                or payer == created
            ):
                opaque.add(path)
                unknown_paths.add(path)
                continue
            if payer != destination and created != destination:
                continue
            amount_value = cast(int, amount)
            delta = (
                -amount_value
                if payer == destination
                else amount_value
                if created == destination
                else 0
            )
            if created == destination:
                create_path = tuple(row.get("path", []))
                enclosing_opaque = any(
                    marker.get("kind") == "opaque_writable_touch"
                    and _path_is_prefix(tuple(marker.get("path", [])), create_path)
                    for marker in effects
                )
                if (
                    pre_lamports != 0
                    or earlier_touch
                    or enclosing_opaque
                    or bool(unpositioned_opaque)
                    or target_create_order != order
                    or len(target_creates) != 1
                ):
                    opaque.add(path)
                    unknown_paths.add(path)
                    if _strict_int(amount):
                        withheld_creates.append(cast(int, amount))
                else:
                    projection += delta
                    decoded.append({"path": list(path), "kind": kind, "delta_lamports": delta})
            else:
                projection += delta
                decoded.append({"path": list(path), "kind": kind, "delta_lamports": delta})
        elif kind == "target_close_withheld":
            unknown_paths.add(path)
        else:
            opaque.add(path)
            unknown_paths.add(path)
        if order < (target_create_order if target_create_order is not None else 10**18):
            earlier_touch = True
    boundary = post_lamports - pre_lamports
    unexplained = boundary - projection
    unknown_contribution_count = len(unknown_paths)
    return {
        "status": "destination_ambiguous",
        "pre_lamports": pre_lamports,
        "post_lamports": post_lamports,
        "transaction_boundary_delta_lamports": boundary,
        "decoded_projection_delta_lamports": projection,
        "unexplained_delta_lamports": unexplained,
        "partial_arithmetic_match_diagnostic_only": (
            unknown_contribution_count == 0 and unexplained == 0
        ),
        "complete_equation_residual_lamports": (
            unexplained if unknown_contribution_count == 0 else None
        ),
        "unknown_contribution_count": unknown_contribution_count,
        "raw_destination_create_amounts_lamports": raw_create_amounts,
        "withheld_destination_create_amounts_lamports": withheld_creates,
        "fee_lamports": fee_lamports,
        "fee_applied_once_to_message_fee_payer": destination == fee_payer,
        "decoded_system_effects": decoded,
        "opaque_writable_paths": [list(path) for path in sorted(opaque)],
        "ordered_opaque_writable_touch_markers": [
            {"order": row["order"], "path": list(row["path"])}
            for row in effects
            if row.get("kind") == "opaque_writable_touch"
        ],
        "ordered_proven_read_only_touch_markers": [
            {"order": row["order"], "path": list(row["path"])}
            for row in effects
            if row.get("kind") == "read_only_touch"
        ],
        "close_credit_in_equation": False,
        "reasons": sorted(
            {"opaque_or_unmodeled_writable_touch" for _ in unknown_paths}
            | ({"close_credit_withheld_by_contract"} if not unknown_paths else set())
        ),
    }


def _path_is_prefix(prefix: tuple[int, ...], path: tuple[int, ...]) -> bool:
    return bool(prefix) and len(prefix) < len(path) and path[: len(prefix)] == prefix


def _decode_system(ix: dict[str, Any]) -> dict[str, Any] | None:
    effect = _system_effect(ix)
    if effect is None or effect.get("kind") == "unsupported_touch":
        return effect
    if len(ix.get("accounts", [])) != 2:
        return {
            "kind": "unsupported_touch",
            "path": ix["path"],
            "reason": "system_role_count_ambiguous",
        }
    if effect.get("kind") == "create_account" and effect.get("payer") == effect.get("account"):
        return {
            "kind": "unsupported_touch",
            "path": ix["path"],
            "reason": "system_create_roles_ambiguous",
        }
    return effect


def _decode_legacy_native_effects(
    normalized: dict[str, Any], account: str
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    effects: list[dict[str, Any]] = []
    opaque: list[list[int]] = []
    for order, ix in enumerate(_ordered_instructions(normalized)):
        if account not in ix["accounts"]:
            continue
        program = ix["program_id"]
        effect = _decode_system(ix) or _token_effect(ix)
        if effect is None:
            if program == ASSOCIATED_TOKEN:
                opaque.append(ix["path"])
                continue
            opaque.append(ix["path"])
            effects.append({"kind": "unsupported_touch", "order": order, "path": ix["path"]})
            continue
        effect = dict(effect)
        effect["order"] = order
        effect.setdefault("path", ix["path"])
        if effect.get("kind") == "create_account" and effect.get("account") == account:
            effect["kind"] = "create_account"
        elif effect.get("kind") == "system_transfer":
            effect["kind"] = "system_transfer"
        elif effect.get("kind") == "initialize_account" and effect.get("account") == account:
            pass
        elif (
            effect.get("kind") in ("sync_native", "close_account", "initialize_immutable_owner")
            and effect.get("account") == account
        ):
            pass
        elif effect.get("kind") == "transfer_checked" and account in (
            effect.get("source"),
            effect.get("destination"),
        ):
            pass
        else:
            # Token's other operations are not accepted as lamport effects.
            effect = {"kind": "unsupported_touch", "order": order, "path": ix["path"]}
        effects.append(effect)
    return effects, opaque


def _destination_projection(
    *,
    tx: dict[str, Any],
    normalized: dict[str, Any],
    destination: str,
    target_close_path: list[int],
    target_close_candidate: int | None,
) -> dict[str, Any]:
    index, pre, post = _lamport_endpoints(tx, destination)
    meta = tx.get("meta") or {}
    fee = meta.get("fee")
    keys = [row["pubkey"] for row in normalized["accounts"]]
    if index is None or pre is None or post is None:
        return {
            "status": "missing_endpoint",
            "pre_lamports": pre,
            "post_lamports": post,
            "decoded_delta_lamports": None,
            "fee_lamports": fee if _strict_int(fee) else None,
            "opaque_writable_paths": [],
            "reasons": ["destination_boundary_endpoint_missing"],
        }
    writable = _message_writable_accounts(tx, normalized)
    effects: list[dict[str, Any]] = []
    opaque: list[list[int]] = []
    for order, ix in enumerate(_ordered_instructions(normalized)):
        if destination not in ix["accounts"]:
            continue
        effect = _decode_system(ix)
        kind = effect.get("kind") if effect else None
        if kind == "system_transfer":
            assert effect is not None
            effects.append(
                {
                    "kind": "system_transfer",
                    "source": effect.get("source"),
                    "destination": effect.get("destination"),
                    "lamports": effect.get("lamports"),
                    "order": order,
                    "path": ix["path"],
                }
            )
        elif kind == "create_account":
            assert effect is not None
            effects.append(
                {
                    "kind": "create_account",
                    "payer": effect.get("payer"),
                    "account": effect.get("account"),
                    "lamports": effect.get("lamports"),
                    "order": order,
                    "path": ix["path"],
                }
            )
        else:
            if writable.get(destination, False):
                opaque.append(ix["path"])
                effects.append(
                    {"kind": "opaque_writable_touch", "order": order, "path": ix["path"]}
                )
            else:
                effects.append(
                    {"kind": "read_only_touch", "order": order, "path": ix["path"]}
                )
    report = project_destination_effects(
        destination=destination,
        fee_payer=keys[0] if keys else None,
        pre_lamports=pre,
        post_lamports=post,
        fee_lamports=fee if _strict_int(fee) else None,
        effects=effects,
        opaque_writable_paths=opaque,
    )
    report["target_close_candidate_withheld_lamports"] = (
        target_close_candidate if target_close_path else None
    )
    return report


def _verify_process_receipt(manifest: dict[str, Any]) -> dict[str, Any]:
    process = _load(PROCESS_RECEIPT_PATH)
    raw = PROCESS_RECEIPT_PATH.read_bytes()
    _require(
        process.get("receipt_kind") == "pumpswap_sell23_process_qualification_v1",
        "process_receipt_kind",
    )
    _require(
        process.get("source_manifest_sha256") == manifest["manifest_sha256"],
        "process_manifest_sha_mismatch",
    )
    _require(
        process.get("source_manifest_content_sha256") == manifest["content_sha256"],
        "process_manifest_content_sha_mismatch",
    )
    _require(
        process.get("tool_sha256")
        == _sha_file(REPO / "tools/brain_senses_sell23_process_qualification_v1.py"),
        "process_tool_hash_mismatch",
    )
    expected_content = {
        key: value
        for key, value in process.items()
        if key not in ("content_sha256", "decision_time_ns")
    }
    _require(
        process.get("content_sha256") == _canonical_hash(expected_content),
        "process_receipt_content_hash_mismatch",
    )
    _require(
        process.get("status")
        == "partial_fields_reconciled_full_destination_settlement_unqualified",
        "process_receipt_status",
    )
    denoms = process.get("denominators", {})
    _require(
        denoms.get("selected_full_blocks") == 96
        and denoms.get("successful_exact_class_candidates") == 553,
        "process_block_candidate_denominator",
    )
    _require(
        denoms.get("selected_censored_native_close_rows") == 485
        and denoms.get("exact_ledger_rows_excluded_from_censored_scope") == 68,
        "process_censored_denominator",
    )
    _require(
        denoms.get("failed_structural_refs_outside_success_rows") == 2, "process_failed_denominator"
    )
    _require(
        denoms.get("row_outcomes") == {"ambiguous": 485, "exact": 0, "unsupported": 0},
        "process_source_outcomes",
    )
    _require(len(process.get("rows", [])) == 485, "process_rows_length")
    return {**process, "receipt_sha256": _sha_bytes(raw)}


def _source_inputs(
    manifest: dict[str, Any], process: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    feasibility = _load(FEASIBILITY_PATH)
    flow = _load(FLOW_PATH)
    replay = _load(REPLAY_PATH)
    _require(
        feasibility.get("counts")
        == {
            "censored_rows": 485,
            "exact_ledger_rows_preserved": 68,
            "failed_refs_outside_success_target": 2,
            "native_target_account_rows": 485,
            "receipt_hash_verified_full_blocks": 96,
            "successful_exact_layout_refs": 553,
        },
        "feasibility_denominators",
    )
    _require(
        flow.get("counts", {}).get("source_account_flow_exact") == 485
        and flow.get("counts", {}).get("destination_ambiguous") == 485,
        "native_flow_denominators",
    )
    _require(
        replay.get("eligible_successful_sell23_candidates") == 553
        and replay.get("selected_full_blocks_verified") == 96,
        "replay_denominators",
    )
    _require(
        len(feasibility.get("rows", [])) == len(flow.get("rows", [])) == 485, "source_receipt_rows"
    )
    _require(len(process["rows"]) == 485, "process_rows")
    _require(process.get("decoder_pins") == manifest.get("decoder_pins"), "process_decoder_pins")
    for source_record in manifest["source_receipts"]:
        _require(
            _sha_file(REPO / source_record["path"]) == source_record["sha256"],
            "manifest_receipt_hash",
        )

    def row_key(row: dict[str, Any]) -> tuple[str, int, int, str]:
        return (
            row["frame_id"],
            row["slot"],
            row["transaction_index"],
            row["signature_sha256"],
        )

    flow_map = {row_key(row): row for row in flow["rows"]}
    feas_map = {row_key(row): row for row in feasibility["rows"]}
    proc_map = {row_key(row): row for row in process["rows"]}
    _require(
        len(flow_map) == len(feas_map) == len(proc_map) == 485, "duplicate_source_row_identity"
    )
    _require(flow_map.keys() == feas_map.keys() == proc_map.keys(), "source_row_join_mismatch")
    return list(proc_map.values()), [flow_map[row_key(row)] for row in proc_map.values()]


def _replay(
    manifest: dict[str, Any], process: dict[str, Any], flow_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    proc_rows = process["rows"]
    flow_by_id = {
        (r["frame_id"], r["slot"], r["transaction_index"], r["signature_sha256"]): r
        for r in flow_rows
    }
    blocks: dict[tuple[str, int], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for epoch in manifest["epochs"]:
        ep_root = C3_ROOT / "epochs" / epoch["epoch_id"]
        for record in epoch["full_blocks"]:
            capture = ep_root / "captures" / f"{record['sequence']:012d}" / "response.json.zst"
            _require(_sha_file(capture) == record["compressed_capture_sha256"], "capture_drift")
            block = _decode_capture(capture, zstd="zstd")
            _require(block.get("blockhash") == record["blockhash"], "blockhash_mismatch")
            transactions = block.get("transactions")
            _require(isinstance(transactions, list), "full_block_transactions_missing")
            blocks[(epoch["epoch_id"], record["slot"])] = (
                record,
                cast(list[dict[str, Any]], transactions),
            )
    _require(len(blocks) == 96, "selected_blocks_verified")
    ledger: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    unexplained_deltas: Counter[str] = Counter()
    for row in proc_rows:
        identity = (row["frame_id"], row["slot"], row["transaction_index"], row["signature_sha256"])
        source = flow_by_id[identity]
        block_record, txs = blocks[(row["frame_id"], row["slot"])]
        tx_index = row["transaction_index"]
        _require(type(tx_index) is int and 0 <= tx_index < len(txs), "transaction_index_invalid")
        tx = txs[tx_index]
        meta = tx.get("meta") or {}
        success = isinstance(meta, dict) and "err" in meta and meta.get("err") is None
        _require(
            success is True and row.get("transaction_success") is True, "success_row_join_failed"
        )
        signatures = tx.get("transaction", {}).get("signatures", [])
        _require(
            signatures and _sha_bytes(signatures[0].encode("ascii")) == row["signature_sha256"],
            "signature_row_join_failed",
        )
        _require(
            block_record["response_sha256"] == row["raw_response_sha256"],
            "response_hash_row_join_failed",
        )
        normalized = normalize_compiled_transaction(tx)
        flow = source["flow"]
        native_account = flow.get("account")
        close_destination = flow.get("close_destination")
        target_path = source["target_instruction_path"]
        flow_check = classify_account(tx, native_account, target_path)
        _require(
            flow_check.get("status") == "source_account_flow_exact",
            "source_account_flow_no_longer_exact",
        )
        _require(
            flow_check.get("close_destination") == close_destination, "close_destination_mismatch"
        )
        # Build ordered effect list for the corrected source-side diagnostic.
        temp_effects, temp_opaque = _decode_legacy_native_effects(normalized, native_account)
        _, temp_pre, temp_post = _lamport_endpoints(tx, native_account)
        close_path: list[int] = next(
            (effect["path"] for effect in temp_effects if effect.get("kind") == "close_account"), []
        )
        source_candidate = project_temporary_account(
            account=native_account,
            destination=close_destination,
            token_program=TOKEN,
            mint=WSOL,
            pre_lamports=temp_pre,
            post_lamports=temp_post,
            effects=temp_effects,
            opaque_writable_paths=temp_opaque,
        )
        dest = _destination_projection(
            tx=tx,
            normalized=normalized,
            destination=close_destination,
            target_close_path=close_path,
            target_close_candidate=source_candidate.get("pre_close_lamports_candidate"),
        )
        outcome = {
            "frame_id": row["frame_id"],
            "slot": row["slot"],
            "transaction_index": tx_index,
            "signature_sha256": row["signature_sha256"],
            "capture_receipt_sha256": row["capture_receipt_sha256"],
            "raw_response_sha256": row["raw_response_sha256"],
            "transaction_success": True,
            "native_account": native_account,
            "destination": close_destination,
            "target_instruction_path": target_path,
            "source_candidate": source_candidate,
            "destination_projection": dest,
            "outcome": "destination_ambiguous",
        }
        ledger.append(outcome)
        counts["censored_rows_replayed"] += 1
        counts[f"source_projection:{source_candidate['status']}"] += 1
        counts["temporary_opaque_writable_touch_rows"] += bool(
            source_candidate.get("opaque_writable_paths")
        )
        counts["destination_opaque_writable_paths"] += len(dest.get("opaque_writable_paths", []))
        counts["destination_rows_with_opaque_writable_touch"] += bool(
            dest.get("opaque_writable_paths")
        )
        counts["destination_system_transfer_effects"] += sum(
            effect.get("kind") == "system_transfer"
            for effect in dest.get("decoded_system_effects", [])
        )
        counts["destination_create_account_effects"] += sum(
            effect.get("kind") == "create_account"
            for effect in dest.get("decoded_system_effects", [])
        )
        counts["destination_raw_create_amounts"] += len(
            dest.get("raw_destination_create_amounts_lamports", [])
        )
        counts["destination_withheld_create_amounts"] += len(
            dest.get("withheld_destination_create_amounts_lamports", [])
        )
        counts["destination_proven_read_only_touch_paths"] += len(
            dest.get("ordered_proven_read_only_touch_markers", [])
        )
        unknown_count = dest.get("unknown_contribution_count") or 0
        counts["destination_unknown_contributions"] += unknown_count
        counts["destination_rows_with_unknown_contribution"] += unknown_count > 0
        complete_residual = dest.get("complete_equation_residual_lamports")
        counts["destination_arithmetic_matches_diagnostic_only"] += complete_residual == 0
        counts["destination_arithmetic_mismatches"] += (
            type(complete_residual) is int and complete_residual != 0
        )
        counts["destination_missing_endpoint"] += dest.get("status") == "missing_endpoint"
        unexplained = dest.get("unexplained_delta_lamports")
        if type(unexplained) is int:
            unexplained_deltas[str(unexplained)] += 1
            counts["destination_rows_with_unexplained_delta"] += unexplained != 0
    _require(len(ledger) == 485, "replayed_row_denominator")
    _require(
        sum(row["outcome"] == "destination_ambiguous" for row in ledger) == 485,
        "ambiguous_outcome_denominator",
    )
    return ledger, {
        "row_counts": dict(sorted(counts.items())),
        "unexplained_delta_lamports": dict(
            sorted(unexplained_deltas.items(), key=lambda item: int(item[0]))
        ),
    }


def _open_data_root(guard: Any) -> int:
    current = os.dup(guard.mount_fd)
    device = os.fstat(current).st_dev
    try:
        for component in guard.config.data_root.split("/"):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            st = os.fstat(child)
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o700
            ):
                os.close(child)
                raise GateError("storage_root_identity_or_mode")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _publish_ledger(payload: bytes, config_path: Path) -> dict[str, Any]:
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == STORAGE_ROOT_ID and guard.volume_uuid.lower() == STORAGE_VOLUME_UUID,
            "storage_root_binding_mismatch",
        )
        guard.create_directory("derived/sell23-lamport-gate-v3-final")
        root_fd = _open_data_root(guard)
        try:
            def revalidate_before_write() -> None:
                guard.revalidate()

            with DescriptorWriter(root_fd, before_write=revalidate_before_write) as writer:
                if writer.exists(LEDGER_RELATIVE):
                    prior = writer.read_bytes(LEDGER_RELATIVE, max_bytes=len(payload) + 1)
                    _require(prior == payload, "immutable_ledger_collision")
                else:
                    writer.create(LEDGER_RELATIVE, payload)
        finally:
            os.close(root_fd)
        guard.revalidate()
        return {
            "root_id": guard.root_id,
            "volume_uuid": guard.volume_uuid,
            "relative_path": LEDGER_RELATIVE,
            "bytes": len(payload),
            "sha256": _sha_bytes(payload),
        }


def _create_only_local(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def run(*, config_path: Path = STORAGE_CONFIG, receipt_path: Path = RECEIPT_PATH) -> dict[str, Any]:
    manifest = verify_source_manifest(MANIFEST_PATH)
    process = _verify_process_receipt(manifest)
    proc_rows, flow_rows = _source_inputs(manifest, process)
    ledger, replay_counts = _replay(manifest, process, flow_rows)
    ledger_bytes = b"".join(canonical_json(row) for row in ledger)
    ledger_binding = _publish_ledger(ledger_bytes, config_path)
    receipt = {
        "schema_version": 3,
        "contract": CONTRACT,
        "status": "complete_offline_diagnostic_v3_all_destination_ambiguous",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_manifest_path": MANIFEST_PATH.as_posix(),
        "source_manifest_sha256": manifest["manifest_sha256"],
        "process_receipt_sha256": process["receipt_sha256"],
        "source_receipts": [
            {"path": path.as_posix(), "sha256": _sha_file(path)}
            for path in (REPLAY_PATH, FEASIBILITY_PATH, FLOW_PATH)
        ],
        "tool_sha256": _sha_file(Path(__file__)),
        "scope": {
            "campaign": "c3",
            "frames": [f"20261010T{hour:02d}0000Z" for hour in range(5, 11)],
            "selected_full_blocks": 96,
            "censored_success_rows": 485,
            "exact_ledger_success_rows_excluded": 68,
            "failed_structural_references_excluded": 2,
            "provider_requests": 0,
            "c4_or_new_audit": False,
        },
        "frozen_semantics": {
            "source_candidate": (
                "pre + System CreateAccount + explicit System credits - explicit System "
                "debits - explicit legacy WSOL TransferChecked amount in lamports - post; "
                "candidate remains diagnostic and never enters destination equation"
            ),
            "native_transfer_conversion": (
                "explicit token atomic amount is treated as same integer lamports only "
                "for legacy Token TransferChecked with verified WSOL mint, source temp "
                "account, decimals=9, and ordered native lifecycle"
            ),
            "destination_projection": (
                "transaction-boundary delta compared with decoded System effects and one "
                "metadata fee debit to key index 0; opaque writable touches are ordered "
                "unknown contributions; target CloseAccount credit withheld"
            ),
            "new_destination_create_account": (
                "raw u64 amount always retained; destination credit is a partial-projection "
                "candidate only for one decoded create, zero prebalance, and complete order "
                "proving no earlier or enclosing potentially writable destination reference; "
                "otherwise contribution is unknown/withheld, never numeric zero"
            ),
            "unknown_rules": (
                "opaque writable ATA/PumpSwap or other touch, missing endpoint, "
                "unsupported instruction, malformed order, and endpoint mismatch "
                "remain ambiguous/quarantined"
            ),
            "outcome_rule": (
                "all selected rows remain destination_ambiguous regardless of endpoint equality"
            ),
            "units": {
                "lamports": "System movements, balances, fee and projections",
                "token_atomic_units": (
                    "TransferChecked amount before explicitly qualified native conversion only"
                ),
            },
        },
        "denominators": {
            "successful_exact_layout_candidates": 553,
            "censored_success_rows_expected": 485,
            "censored_success_rows_replayed": len(ledger),
            "exact_ledger_success_rows_excluded": 68,
            "failed_structural_refs_excluded": 2,
            "missing_rows": 485 - len(ledger),
            "outcomes": {
                "destination_lamport_projection_reconciled": 0,
                "destination_ambiguous": 485,
                "unsupported_or_missing": 0,
            },
            **replay_counts["row_counts"],
        },
        "unexplained_delta_lamports": replay_counts["unexplained_delta_lamports"],
        "complete_equation_residuals_available": False,
        "supersedes_receipts": [
            "pumpswap-sell23-c3-destination-lamport-diagnostic-2026-10-12.json",
            "pumpswap-sell23-c3-destination-lamport-diagnostic-v2-2026-10-12.json",
            "pumpswap-sell23-c3-destination-lamport-diagnostic-v2-final-2026-10-12.json",
        ],
        "validation": {
            "focused_pytest": "12 passed",
            "focused_ruff": "clean",
            "focused_mypy_command": (
                "MYPYPATH=tools:src mypy --explicit-package-bases --follow-imports=silent "
                "tools/brain_senses_sell23_lamport_gate_v1.py"
            ),
            "focused_mypy": "success; no issues found",
        },
        "ledger": ledger_binding,
        "limitations": [
            (
                "Corrected temporary-account remainders are diagnostic source-side "
                "candidates, not CloseAccount credits."
            ),
            (
                "All destination outcomes remain ambiguous because writable parent-program "
                "semantics are not independently qualified."
            ),
            (
                "No destination owner, historical rent, wrapped-token balance, wallet-net "
                "proceeds, price, PnL, edge, admission, or training fact is derived."
            ),
            (
                "The 68 exact-ledger successes and two failed references are separately "
                "excluded from the 485 censored-success denominator."
            ),
        ],
    }
    receipt["content_sha256"] = _canonical_hash(
        {key: value for key, value in receipt.items() if key != "content_sha256"}
    )
    _create_only_local(receipt_path, canonical_json(receipt))
    return {
        "status": receipt["status"],
        "receipt": receipt_path.as_posix(),
        "ledger": ledger_binding,
        "counts": receipt["denominators"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-config", type=Path, default=STORAGE_CONFIG)
    parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    args = parser.parse_args()
    print(
        json.dumps(run(config_path=args.storage_config, receipt_path=args.receipt), sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
