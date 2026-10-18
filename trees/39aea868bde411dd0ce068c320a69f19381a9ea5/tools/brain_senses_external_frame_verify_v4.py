#!/usr/bin/env python3
"""Independent finalized inventory and selected-block frame verification."""

from __future__ import annotations

from typing import Any

from tools import brain_senses_external_rolling_policy_v1 as policy
from tools import brain_senses_external_sampling_v4 as sampling


class FrameVerifyError(ValueError):
    """A provider frame does not satisfy its pinned modality contract."""


def _signature_list(value: Any, *, full: bool) -> list[str] | None:
    if not isinstance(value, dict):
        return None
    if not full:
        signatures = value.get("signatures")
        return (
            signatures
            if isinstance(signatures, list)
            and all(isinstance(signature, str) for signature in signatures)
            else None
        )
    transactions = value.get("transactions")
    if not isinstance(transactions, list):
        return None
    result: list[str] = []
    for row in transactions:
        transaction = row.get("transaction") if isinstance(row, dict) else None
        signatures = transaction.get("signatures") if isinstance(transaction, dict) else None
        if not isinstance(signatures, list) or not signatures or not isinstance(signatures[0], str):
            return None
        result.append(signatures[0])
    return result


def finalized_slot(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise FrameVerifyError("finalized_slot_invalid")
    return value


def inventory_slots(value: Any, *, start: int, end_inclusive: int) -> list[int]:
    if (
        type(start) is not int
        or type(end_inclusive) is not int
        or start < 0
        or end_inclusive < start
        or end_inclusive - start + 1 > policy.MAX_INVENTORY_SLOTS_PER_FRAME
        or not isinstance(value, list)
        or any(type(slot) is not int or slot < start or slot > end_inclusive for slot in value)
        or value != sorted(set(value))
    ):
        raise FrameVerifyError("inventory_interval_or_shape_invalid")
    return value


def verify_sample_pairs(
    *,
    epoch_id: str,
    bucket: str,
    inventory: list[int],
    inventory_sha256: str,
    pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recompute selected identities and compare block hashes and ordered signatures."""
    selection = sampling.build_selection(
        epoch_id=epoch_id,
        bucket=bucket,
        inventory_slots=inventory,
        inventory_sha256=inventory_sha256,
    )
    selected = selection["selected_slots"]
    if len(pairs) != len(selected):
        raise FrameVerifyError("sample_pair_count_mismatch")
    observed: list[int] = []
    for slot, pair in zip(selected, pairs, strict=True):
        if not isinstance(pair, dict) or set(pair) != {"slot", "signatures", "full"}:
            raise FrameVerifyError("sample_pair_shape_invalid")
        signatures, full = pair["signatures"], pair["full"]
        if (
            pair["slot"] != slot
            or not isinstance(signatures, dict)
            or not isinstance(full, dict)
            or signatures.get("slot", slot) != slot
            or full.get("slot", slot) != slot
            or signatures.get("blockhash") != full.get("blockhash")
            or signatures.get("parentSlot") != full.get("parentSlot")
            or signatures.get("previousBlockhash") != full.get("previousBlockhash")
        ):
            raise FrameVerifyError("sample_block_identity_mismatch")
        sig_list = _signature_list(signatures, full=False)
        full_sig_list = _signature_list(full, full=True)
        if sig_list is None or full_sig_list is None or sig_list != full_sig_list:
            raise FrameVerifyError("sample_ordered_signatures_mismatch")
        if not isinstance(full.get("transactions"), list):
            raise FrameVerifyError("sample_full_block_structural_invalid")
        observed.append(slot)
    if observed != selected:
        raise FrameVerifyError("sample_selected_slot_mismatch")
    return selection
