import copy

import pytest

from memecoin_experience.evaluation import denominator_summary


def _legacy_report():
    return {
        "status": "completed",
        "enrollment": 3,
        "supported_pools": 2,
        "per_pool_records": {"a": 4, "b": 0, "c": 2},
        "per_pool_usable": {"a": 4, "b": 0, "c": 1},
        "population": {"usable": 5},
    }


def _coordinate_report():
    return {
        "status": "completed",
        "pool_coverage": {
            "registered": 4,
            "present_in_snapshot": 3,
            "exact_scored": 2,
            "missing_registered": 1,
            "present_without_exact_scored": ["c"],
        },
        "population": {"test_scored_origins": 5},
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["per_pool_usable"].update(a=5),
        lambda report: report["population"].update(usable=6),
        lambda report: report.update(
            per_pool_records={"synthetic": 1},
            per_pool_usable={"synthetic": 9},
            population={"usable": 999},
            enrollment=1,
            supported_pools=1,
        ),
        lambda report: report["per_pool_usable"].update(a=True),
    ],
)
def test_legacy_summary_rejects_inconsistent_counts(mutate):
    report = _legacy_report()
    mutate(report)

    with pytest.raises(ValueError, match="^experience_evaluation_denominator_summary$"):
        denominator_summary(report)


def test_legacy_summary_preserves_empty_enrollment_and_zero_support():
    report = {
        "status": "completed",
        "enrollment": 0,
        "supported_pools": 0,
        "per_pool_records": {},
        "per_pool_usable": {},
        "population": {"usable": 0},
    }

    assert denominator_summary(report) == {
        "enrolled_pools": 0,
        "present_pools": 0,
        "supported_pools": 0,
        "scored_origins": 0,
        "missing_pools": 0,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["population"].update(test_scored_origins=1),
        lambda report: report["pool_coverage"].update(
            exact_scored=0, present_without_exact_scored=["a", "b", "c"]
        ),
        lambda report: report["population"].update(test_exact_usable_origins=4),
        lambda report: report["population"].update(test=4),
        lambda report: report["population"].update(test_assigned_origins=4),
        lambda report: report["population"].update(test_scored_origins=True),
        lambda report: report["population"].update(test_exact_usable_origins=False),
        lambda report: report["population"].update(test_assigned_origins=-1),
    ],
)
def test_coordinate_summary_rejects_inconsistent_present_counts(mutate):
    report = _coordinate_report()
    mutate(report)

    with pytest.raises(ValueError, match="^experience_evaluation_denominator_summary$"):
        denominator_summary(report)


def test_coordinate_summary_accepts_supported_only_counts_and_optional_zeros():
    report = _coordinate_report()

    assert denominator_summary(report) == {
        "enrolled_pools": 4,
        "present_pools": 3,
        "supported_pools": 2,
        "scored_origins": 5,
        "missing_pools": 1,
    }

    report["population"].update(
        test_exact_usable_origins=5,
        test=5,
        test_assigned_origins=5,
    )
    assert denominator_summary(report)["scored_origins"] == 5


def test_coordinate_summary_preserves_present_unscored_pools_and_zero_support():
    report = {
        "status": "completed",
        "pool_coverage": {
            "registered": 2,
            "present_in_snapshot": 1,
            "exact_scored": 0,
            "missing_registered": 1,
            "present_without_exact_scored": ["present"],
        },
        "population": {"test_scored_origins": 0},
    }

    assert denominator_summary(report) == {
        "enrolled_pools": 2,
        "present_pools": 1,
        "supported_pools": 0,
        "scored_origins": 0,
        "missing_pools": 1,
    }


def test_coordinate_summary_rejects_noninteger_optional_counts():
    for key, value in (
        ("test_exact_usable_origins", 5.0),
        ("test", -1),
        ("test_assigned_origins", True),
    ):
        report = copy.deepcopy(_coordinate_report())
        report["population"][key] = value
        with pytest.raises(ValueError, match="^experience_evaluation_denominator_summary$"):
            denominator_summary(report)
