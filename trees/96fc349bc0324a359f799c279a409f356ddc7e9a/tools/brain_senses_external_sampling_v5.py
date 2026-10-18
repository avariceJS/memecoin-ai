#!/usr/bin/env python3
"""Deterministic provider-inventory sample selection for external Senses v5."""

from __future__ import annotations

import hashlib
from typing import Any

from tools import brain_senses_external_rolling_policy_v5 as policy
from tools.brain_senses_external_schema_v5 import canonical, sha

SELECTION_RULE = "sha256-rank-v1"


class SamplingError(ValueError):
    """Provider inventory cannot support a bounded sample plan."""


def build_selection(
    *, epoch_id: str, bucket: str, inventory_slots: list[int], inventory_sha256: str
) -> dict[str, Any]:
    """Hash-rank unique inventory members under a seed pinned to exact inventory bytes."""
    if (
        not isinstance(epoch_id, str)
        or not isinstance(bucket, str)
        or not isinstance(inventory_slots, list)
        or not isinstance(inventory_sha256, str)
        or len(inventory_sha256) != 64
        or any(char not in "0123456789abcdef" for char in inventory_sha256)
        or any(type(slot) is not int or slot < 0 or slot >= 2**64 for slot in inventory_slots)
        or inventory_slots != sorted(set(inventory_slots))
        or len(inventory_slots) > policy.MAX_INVENTORY_SLOTS_PER_FRAME
    ):
        raise SamplingError("inventory_invalid")
    seed_material = {
        "epoch_id": epoch_id,
        "bucket": bucket,
        "inventory_sha256": inventory_sha256,
        "rule": SELECTION_RULE,
    }
    seed = hashlib.sha256(canonical(seed_material)).digest()
    chosen = sorted(
        inventory_slots,
        key=lambda slot: (
            hashlib.sha256(seed + slot.to_bytes(8, "big", signed=False)).digest(),
            slot,
        ),
    )[: policy.MAX_SELECTED_SLOTS_PER_FRAME]
    selected_slots = sorted(chosen)
    denominator = len(inventory_slots)
    selection_count = len(selected_slots)
    inclusion_probability = None
    if denominator:
        inclusion_probability = f"{selection_count}/{denominator}"
    return {
        "schema": "brain-senses-external-rolling-v5-selection",
        "schema_version": 5,
        "epoch_id": epoch_id,
        "bucket": bucket,
        "inventory_sha256": inventory_sha256,
        "inventory_denominator": denominator,
        "selection_count": selection_count,
        "selected_slots": selected_slots,
        "inclusion_probability": inclusion_probability,
        "seed_sha256": sha(seed),
        "selection_rule": SELECTION_RULE,
    }
