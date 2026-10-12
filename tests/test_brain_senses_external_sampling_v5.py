from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools.brain_senses_external_sampling_v5 import SamplingError, build_selection


@given(st.lists(st.integers(min_value=0, max_value=2**64 - 1), unique=True, max_size=200))
def test_selection_is_reproducible_sorted_and_bounded(slots: list[int]) -> None:
    inventory = sorted(slots)
    selected = build_selection(
        epoch_id="20261012T110000Z-0123456789ab",
        bucket="20261012T120000Z",
        inventory_slots=inventory,
        inventory_sha256="a" * 64,
    )
    again = build_selection(
        epoch_id="20261012T110000Z-0123456789ab",
        bucket="20261012T120000Z",
        inventory_slots=inventory,
        inventory_sha256="a" * 64,
    )
    assert selected == again
    assert selected["selected_slots"] == sorted(selected["selected_slots"])
    assert len(selected["selected_slots"]) == min(16, len(inventory))
    assert set(selected["selected_slots"]) <= set(inventory)
    assert selected["inclusion_probability"] == (
        f"{min(16, len(inventory))}/{len(inventory)}" if inventory else None
    )


def test_selection_is_pinned_to_exact_inventory_hash() -> None:
    first = build_selection(
        epoch_id="20261012T110000Z-0123456789ab",
        bucket="20261012T120000Z",
        inventory_slots=list(range(100)),
        inventory_sha256="a" * 64,
    )
    second = build_selection(
        epoch_id="20261012T110000Z-0123456789ab",
        bucket="20261012T120000Z",
        inventory_slots=list(range(100)),
        inventory_sha256="b" * 64,
    )
    assert first["seed_sha256"] != second["seed_sha256"]


def test_selection_rejects_duplicates_and_uint64_overflow() -> None:
    with pytest.raises(SamplingError, match="inventory_invalid"):
        build_selection(epoch_id="e", bucket="b", inventory_slots=[1, 1], inventory_sha256="a" * 64)
    with pytest.raises(SamplingError, match="inventory_invalid"):
        build_selection(
            epoch_id="e", bucket="b", inventory_slots=[2**64], inventory_sha256="a" * 64
        )
