"""Fit-free checks for the frozen synthetic lagged-context generator."""

from collections import Counter

from tools.lagged_context_retrieval import ROLE_COUNTS, roster


def test_lagged_context_roles_are_balanced_disjoint_and_temporally_ordered():
    cohorts = {role: roster(role) for role in ROLE_COUNTS}
    entity_sets = {role: {row["entity"] for row in rows} for role, rows in cohorts.items()}

    assert all(len(rows) == ROLE_COUNTS[role] for role, rows in cohorts.items())
    assert all(
        entity_sets[left].isdisjoint(entity_sets[right])
        for i, left in enumerate(ROLE_COUNTS)
        for right in list(ROLE_COUNTS)[i + 1 :]
    )
    for role, rows in cohorts.items():
        patterns = Counter(tuple(row["bits"]) for row in rows)
        assert len(patterns) == 16
        assert set(patterns.values()) == {ROLE_COUNTS[role] // 16}
        assert all(len(row["bits"]) == 4 for row in rows)

    # The target is deliberately independent of the latest input bit.
    assert (
        sum(row["bits"][1] != row["bits"][3] for row in cohorts["research_evaluation"])
        == ROLE_COUNTS["research_evaluation"] // 2
    )
