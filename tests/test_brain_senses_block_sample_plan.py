from __future__ import annotations

from tools.brain_senses_block_sample_plan import build_sample


def test_three_day_slot_sample_is_seeded_stratified_and_nonoverlapping() -> None:
    first = build_sample(
        end_slot=454_854_223,
        end_block_time=1_791_545_654,
        slots_per_day=321_736,
        seed="helius-public-senses-wave-20261009",
    )
    second = build_sample(
        end_slot=454_854_223,
        end_block_time=1_791_545_654,
        slots_per_day=321_736,
        seed="helius-public-senses-wave-20261009",
    )

    assert first["slot_ranges"] == second["slot_ranges"]
    assert len(first["windows"]) == 144
    assert first["design"]["selected_slots"] == 2_880
    assert first["frame"]["utc_strata_are_approximate"] is True
    assert all(
        right[0] >= left[1]
        for left, right in zip(first["slot_ranges"], first["slot_ranges"][1:], strict=False)
    )
