"""Synthetic acceptance tests for honest historical per-target support."""

import copy

import pytest

from memecoin_experience.evaluation import denominator_summary


def _report():
    return {
        "status": "completed",
        "enrollment": 2,
        "supported_pools": 1,
        "per_pool_records": {"a": 2, "b": 0},
        "per_pool_usable": {"a": 1, "b": 0},
        "population": {"usable": 1, "test_assigned_origins": 2},
        "denominators": {
            "test/fast/exact": 1,
            "test/fast/pending": 1,
            "test/fast/lower_bound": 0,
            "test/fast/masked": 0,
            "test/fast/exact_usable": 1,
            "test/fast/scored_exact_usable": 1,
            "test/slow/exact": 0,
            "test/slow/pending": 0,
            "test/slow/lower_bound": 1,
            "test/slow/masked": 1,
            "test/slow/exact_usable": 0,
            "test/slow/scored_exact_usable": 0,
        },
        "metrics": [{"target": "fast", "n": 1}, {"target": "slow", "n": 0}],
    }


def test_default_shape_is_compatible_and_details_report_per_target_support():
    report = _report()
    assert denominator_summary(report) == {
        "enrolled_pools": 2, "present_pools": 1, "supported_pools": 1,
        "scored_origins": 1, "missing_pools": 1,
    }
    details = denominator_summary(report, include_details=True)["per_target"]["targets"]
    assert details["fast"] == {
        "status_counts": {"exact": 1, "pending": 1, "lower_bound": 0, "masked": 0},
        "status_counts_complete": True,
        "exact_usable_cells": 1,
        "scored_exact_usable_cells": 1,
        "metric_n": 1,
    }
    assert details["slow"]["status_counts"]["lower_bound"] == 1
    assert details["slow"]["exact_usable_cells"] == 0


def test_omitted_evidence_stays_unavailable_while_explicit_zero_is_retained():
    report = _report()
    del report["denominators"]["test/slow/exact_usable"]
    del report["denominators"]["test/slow/scored_exact_usable"]
    result = denominator_summary(report, include_details=True)["per_target"]["targets"]["slow"]
    assert result["exact_usable_cells"] is None
    assert result["scored_exact_usable_cells"] is None
    assert result["metric_n"] == 0

    report["denominators"]["test/slow/exact_usable"] = 0
    result = denominator_summary(report, include_details=True)["per_target"]["targets"]["slow"]
    assert result["exact_usable_cells"] == 0


def test_legacy_and_all_empty_reports_have_explicit_availability():
    legacy = {
        "status": "completed", "enrollment": 0, "supported_pools": 0,
        "per_pool_records": {}, "per_pool_usable": {}, "population": {"usable": 0},
    }
    assert denominator_summary(legacy, include_details=True)["per_target"] == {
        "available": False, "reason": "per_target_denominators_unavailable",
    }
    empty = _report()
    empty["population"]["test_assigned_origins"] = 0
    empty["denominators"] = {key: 0 for key in empty["denominators"]}
    for metric in empty["metrics"]:
        metric["n"] = 0
    empty.update(supported_pools=0, per_pool_records={"a": 0, "b": 0},
                 per_pool_usable={"a": 0, "b": 0},
                 population={"usable": 0, "test_assigned_origins": 0})
    result = denominator_summary(empty, include_details=True)["per_target"]
    assert result["available"] is True
    assert all(item["metric_n"] == 0 for item in result["targets"].values())


@pytest.mark.parametrize("mutation", [
    lambda r: r["denominators"].update({"test/fast/exact": 2}),
    lambda r: r["denominators"].update({"test/fast/scored_exact_usable": 0}),
    lambda r: r["denominators"].update({"test/slow/masked": 0}),
    lambda r: r["denominators"].update({"test/fast/exact_usable": True}),
    lambda r: r["metrics"][0].update(n=float("nan")),
])
def test_rejects_tampered_or_invalid_declared_target_evidence(mutation):
    report = copy.deepcopy(_report())
    mutation(report)
    with pytest.raises(ValueError, match="^experience_evaluation_denominator_summary$"):
        denominator_summary(report, include_details=True)


def _utc_report():
    report = _report()
    report["population"] = {"test": 2, "usable": 1}
    report["denominators"] = {
        "fast/observed": 1, "fast/unsupported": 1, "fast/usable": 1,
        "slow/missing_future": 1, "slow/censored": 1, "slow/usable": 0,
    }
    return report


def test_real_utc_report_format_preserves_recorded_statuses_and_horizons():
    result = denominator_summary(_utc_report(), include_details=True)["per_target"]["targets"]
    assert result["fast"]["status_counts"] == {"observed": 1, "unsupported": 1}
    assert result["fast"]["usable_cells"] == 1
    assert result["fast"]["exact_usable_cells"] is None
    assert result["fast"]["status_counts_complete"] is False
    assert result["slow"]["status_counts"] == {"missing_future": 1, "censored": 1}
    assert result["slow"]["usable_cells"] == result["slow"]["metric_n"] == 0
    assert "pending" not in result["slow"]["status_counts"]


@pytest.mark.parametrize("mutation", [
    lambda r: r["denominators"].update({"fast/usable": 0}),
    lambda r: r["denominators"].update({"fast/observed": 0}),
    lambda r: r["denominators"].update({"slow/missing_future": 3}),
    lambda r: r["metrics"].pop(),
    lambda r: r["population"].update(test=0),
    lambda r: r["metrics"][0].update(n=True),
    lambda r: r.update(denominators=[]),
    lambda r: r.update(metrics={}),
])
def test_real_utc_report_tampering_is_rejected(mutation):
    report = _utc_report()
    mutation(report)
    with pytest.raises(ValueError, match="experience_evaluation_denominator_summary"):
        denominator_summary(report, include_details=True)


def test_omitted_target_denominators_do_not_become_measured_support():
    report = _utc_report()
    report["denominators"] = {}
    result = denominator_summary(report, include_details=True)["per_target"]
    assert result["available"] is False


def test_coordinate_report_uses_cell_counts_and_distinct_origin_counts():
    report = _report()
    report.pop("enrollment")
    report.pop("supported_pools")
    report.pop("per_pool_records")
    report.pop("per_pool_usable")
    report["pool_coverage"] = dict(registered=2, present_in_snapshot=1, exact_scored=1,
                                   missing_registered=1, present_without_exact_scored=[])
    report["population"] = dict(test_assigned_origins=2, test_scored_origins=1,
                                test_exact_usable_origins=1, test=1, test_exact_usable_cells=1)
    assert denominator_summary(report, include_details=True)["per_target"]["available"] is True
    report["population"]["test_exact_usable_cells"] = 2
    with pytest.raises(ValueError, match="experience_evaluation_denominator_summary"):
        denominator_summary(report, include_details=True)


def test_v1_numeric_horizon_identity_is_preserved():
    report = _utc_report()
    report["denominators"] = {
        key.replace("fast/", "1/").replace("slow/", "3/"): value
        for key, value in report["denominators"].items()
    }
    report["metrics"] = [dict(target=1, n=1), dict(target=3, n=0)]
    targets = denominator_summary(report, include_details=True)["per_target"]["targets"]
    assert targets[1]["usable_cells"] == 1
    assert targets[3]["status_counts"] == {"missing_future": 1, "censored": 1}


def test_partial_nonexact_counts_bound_the_remaining_possible_exact_support():
    report = _report()
    del report["denominators"]["test/fast/exact"]
    report["denominators"]["test/fast/pending"] = 2
    with pytest.raises(ValueError, match="experience_evaluation_denominator_summary"):
        denominator_summary(report, include_details=True)
