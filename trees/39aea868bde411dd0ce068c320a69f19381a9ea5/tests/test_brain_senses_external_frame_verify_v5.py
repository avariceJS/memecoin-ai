from __future__ import annotations

import pytest

from tools.brain_senses_external_frame_verify_v5 import (
    FrameVerifyError,
    finalized_slot,
    inventory_slots,
    verify_sample_pairs,
)
from tools.brain_senses_external_sampling_v5 import build_selection


def _pair(slot: int, signatures: list[str]) -> dict[str, object]:
    identity = {
        "blockhash": f"hash-{slot}",
        "parentSlot": slot - 1,
        "previousBlockhash": f"prev-{slot}",
    }
    return {
        "slot": slot,
        "signatures": {**identity, "signatures": signatures},
        "full": {
            **identity,
            "transactions": [
                {"transaction": {"signatures": [signature]}} for signature in signatures
            ],
        },
    }


def test_inventory_and_pair_replay_recompute_sample() -> None:
    slots = inventory_slots([101, 105, 110], start=101, end_inclusive=110)
    selection = build_selection(
        epoch_id="20261012T110000Z-0123456789ab",
        bucket="20261012T120000Z",
        inventory_slots=slots,
        inventory_sha256="a" * 64,
    )
    pairs = [_pair(slot, []) for slot in selection["selected_slots"]]
    assert (
        verify_sample_pairs(
            epoch_id="20261012T110000Z-0123456789ab",
            bucket="20261012T120000Z",
            inventory=slots,
            inventory_sha256="a" * 64,
            pairs=pairs,
        )
        == selection
    )


def test_frame_verifier_rejects_signature_order_or_block_identity_change() -> None:
    slots = [101]
    pair = _pair(101, ["sig-a", "sig-b"])
    pair["signatures"] = {**pair["signatures"], "signatures": ["sig-b", "sig-a"]}
    with pytest.raises(FrameVerifyError, match="sample_ordered_signatures_mismatch"):
        verify_sample_pairs(
            epoch_id="20261012T110000Z-0123456789ab",
            bucket="20261012T120000Z",
            inventory=slots,
            inventory_sha256="a" * 64,
            pairs=[pair],
        )


def test_slot_and_inventory_ranges_fail_closed() -> None:
    assert finalized_slot(1) == 1
    with pytest.raises(FrameVerifyError, match="finalized_slot_invalid"):
        finalized_slot(True)
    with pytest.raises(FrameVerifyError, match="inventory_interval_or_shape_invalid"):
        inventory_slots([4, 3], start=3, end_inclusive=4)
