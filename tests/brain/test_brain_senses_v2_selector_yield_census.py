from __future__ import annotations

import random

import pytest

from tools.brain_senses_block_epoch_v2 import _build_selection
from tools.brain_senses_v2_selector_yield_census import _quarter_counts


def test_selection_invariants_over_generated_inventory_domains() -> None:
    generator = random.Random(20261010)
    for case in range(256):
        start = generator.randrange(1, 1_000_000)
        width = generator.randrange(1, 20_001)
        population_size = generator.randrange(1, min(width, 512) + 1)
        inventory = sorted(generator.sample(range(start, start + width), population_size))
        count = generator.randrange(0, population_size + 1)
        plan = {
            "schema_version": 2,
            "epoch_id": f"case-{case}",
            "seed": f"seed-{generator.getrandbits(128):032x}",
            "selection_count": count,
            "selection_rule": "uniform_without_replacement_from_finalized_getblocks_members",
        }
        response_hash = f"{generator.getrandbits(256):064x}"
        selected = _build_selection(
            plan=plan,
            inventory_slots=inventory,
            inventory_sha256=response_hash,
        )
        repeated = _build_selection(
            plan=plan,
            inventory_slots=inventory,
            inventory_sha256=response_hash,
        )
        selected_slots = selected["selected_slots"]
        assert selected == repeated
        assert len(selected_slots) == count
        assert selected_slots == sorted(set(selected_slots))
        assert set(selected_slots) <= set(inventory)
        assert selected["inventory_denominator"] == len(inventory)


def test_frame_quarter_partition_covers_generated_in_frame_slots() -> None:
    generator = random.Random(20261011)
    for _ in range(512):
        start = generator.randrange(1, 1_000_000_000)
        width = generator.randrange(1, 1_000_000)
        end = start + width
        selected = set(generator.sample(range(start, end), min(width, generator.randrange(0, 65))))
        counts = _quarter_counts(start, end, selected)
        assert set(counts) == {"1", "2", "3", "4"}
        assert sum(counts.values()) == len(selected)
        assert all(value >= 0 for value in counts.values())


def test_frame_quarter_partition_rejects_invalid_interval_or_slot() -> None:
    with pytest.raises(ValueError, match="selected_slot_outside_numeric_frame"):
        _quarter_counts(10, 10, set())
    with pytest.raises(ValueError, match="selected_slot_outside_numeric_frame"):
        _quarter_counts(10, 12, {12})
