import hashlib
import json

import pytest

from memecoin_runner import paired_loss_cluster_influence, paired_loss_signflip
from memecoin_runner.paired_loss_sensitivity import analyze_sensitivity


_DEFAULT = object()


def payload(rows=None, clusters=_DEFAULT, signflip=None):
    rows = rows if rows is not None else [
        {"id": "a", "cohort": "new", "loss_a": 4, "loss_b": 0},
        {"id": "b", "cohort": "new", "loss_a": 0, "loss_b": 1},
        {"id": "c", "cohort": "new", "loss_a": 3, "loss_b": None},
    ]
    if clusters is _DEFAULT:
        clusters = [{"id": row["id"], "cluster": f"cluster-{row['id']}"} for row in rows]
    return {
        "model_a": "baseline", "model_b": "candidate", "metric": "loss",
        "loss_upper_bound": 10, "target_cohorts": [{"cohort": "new", "weight": 1}],
        "rows": rows, "sensitivity": {"clusters": clusters, "signflip": signflip},
    }


def test_projections_are_exact_real_producer_envelopes_and_preserve_roster_order():
    candidate = payload(signflip={"assumption": "exchangeable_signs_under_null"})
    result = analyze_sensitivity(candidate)
    complete = candidate["rows"][:2]
    cluster_projection = {
        "model_a": "baseline", "model_b": "candidate", "metric": "loss",
        "rows": [
            {"id": row["id"], "cluster": f"cluster-{row['id']}",
             "loss_a": row["loss_a"], "loss_b": row["loss_b"]}
            for row in complete
        ],
    }
    signflip_projection = {"pairs": [
        {"id": row["id"], "left_loss": row["loss_a"], "right_loss": row["loss_b"]}
        for row in complete
    ]}
    assert result["reports"] == [
        paired_loss_cluster_influence.analyze(cluster_projection),
        paired_loss_signflip.analyze(signflip_projection),
    ]
    for report, projection in zip(result["reports"], [cluster_projection, signflip_projection]):
        expected = hashlib.sha256(json.dumps(
            projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()).hexdigest()
        assert report["input_sha256"] == expected
    metadata = result["metadata"]
    assert metadata["cluster_influence"] == {
        "status": "computed", "reason": None, "roster_count": 3,
        "complete_pair_count": 2, "excluded_incomplete_count": 1,
        "input_sha256": result["reports"][0]["input_sha256"], "cluster_count": 3,
    }
    assert metadata["signflip"] == {
        "status": "computed", "reason": None, "roster_count": 3,
        "complete_pair_count": 2, "excluded_incomplete_count": 1,
        "input_sha256": result["reports"][1]["input_sha256"],
        "assumption": "exchangeable_signs_under_null", "maximum_complete_pairs": 20,
    }


def test_cluster_deletion_can_reverse_sign_and_empty_projection_is_still_computed():
    reversal = payload([
        {"id": "x", "cohort": "new", "loss_a": 4, "loss_b": 0},
        {"id": "y", "cohort": "new", "loss_a": 0, "loss_b": 1},
        {"id": "z", "cohort": "new", "loss_a": 0, "loss_b": 1},
    ], [{"id": "x", "cluster": "x"}, {"id": "y", "cluster": "y"},
        {"id": "z", "cluster": "z"}], None)
    report = analyze_sensitivity(reversal)["reports"][0]
    assert report["summary"]["full_mean_delta"] == -2 / 3
    assert report["details"][0]["sign_reversal"] is True

    empty = payload([], [], None)
    result = analyze_sensitivity(empty)
    assert len(result["reports"]) == 1
    assert result["reports"][0]["summary"]["full_mean_delta"] is None
    assert result["metadata"]["cluster_influence"]["status"] == "computed"
    assert result["metadata"]["cluster_influence"]["cluster_count"] == 0
    assert result["metadata"]["signflip"]["status"] == "not_requested"


def test_signflip_empty_and_all_missing_pairs_are_skipped_without_producer():
    candidate = payload([
        {"id": "a", "cohort": "new", "loss_a": None, "loss_b": 1},
        {"id": "b", "cohort": "new", "loss_a": None, "loss_b": None},
    ], [{"id": "a", "cluster": "g"}, {"id": "b", "cluster": "g"}],
       {"assumption": "exchangeable_signs_under_null"})
    result = analyze_sensitivity(candidate)
    assert result["metadata"]["signflip"]["status"] == "skipped"
    assert result["metadata"]["signflip"]["reason"] == "no_complete_pairs"
    assert result["metadata"]["signflip"]["input_sha256"] is None
    assert result["reports"][0]["summary"]["row_count"] == 0
    assert len(result["reports"]) == 1


def test_exactly_twenty_pairs_calls_producer_but_more_than_twenty_never_calls(monkeypatch):
    rows = [{"id": str(i), "cohort": "new", "loss_a": i, "loss_b": i + 1} for i in range(20)]
    clusters = [{"id": row["id"], "cluster": "all"} for row in rows]
    candidate = payload(rows, clusters, {"assumption": "exchangeable_signs_under_null"})
    result = analyze_sensitivity(candidate)
    assert result["metadata"]["signflip"]["status"] == "computed"
    assert result["reports"][1]["summary"]["sign_flip_assignments"] == 1 << 20

    calls = []
    monkeypatch.setattr(paired_loss_signflip, "analyze", lambda value: calls.append(value))
    rows.append({"id": "20", "cohort": "new", "loss_a": 0, "loss_b": 1})
    clusters.append({"id": "20", "cluster": "all"})
    over = analyze_sensitivity(payload(rows, clusters, {"assumption": "exchangeable_signs_under_null"}))
    assert calls == []
    assert len(over["reports"]) == 1
    assert over["metadata"]["signflip"] == {
        "status": "skipped", "reason": "complete_pair_limit_exceeded",
        "roster_count": 21, "complete_pair_count": 21, "excluded_incomplete_count": 0,
        "input_sha256": None, "assumption": "exchangeable_signs_under_null",
        "maximum_complete_pairs": 20,
    }


@pytest.mark.parametrize("clusters", [
    [{"id": "a", "cluster": "x"}],
    [{"id": "a", "cluster": "x"}, {"id": "a", "cluster": "y"}, {"id": "c", "cluster": "z"}],
    [{"id": "unknown", "cluster": "x"}, {"id": "b", "cluster": "y"}, {"id": "c", "cluster": "z"}],
    [{"id": "a", "cluster": " "}, {"id": "b", "cluster": "y"}, {"id": "c", "cluster": "z"}],
    [{"id": "a", "cluster": "x", "extra": 1}, {"id": "b", "cluster": "y"}, {"id": "c", "cluster": "z"}],
])
def test_invalid_cluster_coverage_labels_duplicates_and_fields(clusters):
    with pytest.raises(ValueError):
        analyze_sensitivity(payload(clusters=clusters))


@pytest.mark.parametrize("sensitivity", [
    {"clusters": None},
    {"clusters": None, "signflip": None, "extra": 1},
    {"clusters": None, "signflip": {"assumption": "inferred"}},
    {"clusters": None, "signflip": {"assumption": "exchangeable_signs_under_null", "extra": True}},
])
def test_invalid_sensitivity_shape_and_assumption(sensitivity):
    candidate = payload()
    candidate["sensitivity"] = sensitivity
    with pytest.raises(ValueError):
        analyze_sensitivity(candidate)


def test_null_declarations_are_explicitly_not_requested():
    result = analyze_sensitivity(payload(clusters=None, signflip=None))
    assert result == {
        "reports": [],
        "metadata": {
            "cluster_influence": {
                "status": "not_requested", "reason": None, "roster_count": 3,
                "complete_pair_count": 2, "excluded_incomplete_count": 1,
                "input_sha256": None, "cluster_count": 0,
            },
            "signflip": {
                "status": "not_requested", "reason": None, "roster_count": 3,
                "complete_pair_count": 2, "excluded_incomplete_count": 1,
                "input_sha256": None, "assumption": None, "maximum_complete_pairs": 20,
            },
        },
    }
