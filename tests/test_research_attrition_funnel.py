from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_population.attrition_funnel import analyze


def example_payload() -> dict[str, object]:
    return {
        "stages": ["enrolled", "eligible", "usable"],
        "cohorts": [
            {"cohort": "alpha", "counts": {"enrolled": 100, "eligible": 80, "usable": 20}},
            {"cohort": "beta", "counts": {"enrolled": 100, "eligible": 30, "usable": None}},
        ],
        "completeness": [
            {"from_stage": "enrolled", "to_stage": "eligible", "complete_cohorts": ["alpha", "beta"]},
            {"from_stage": "eligible", "to_stage": "usable", "complete_cohorts": ["alpha"]},
        ],
    }


def test_reports_cohort_losses_missing_and_completeness_gated_globals() -> None:
    result = analyze(example_payload())
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "attrition_funnel"
    details = result["details"]
    assert isinstance(details, dict)
    alpha, beta = details["cohorts"]
    assert alpha["largest_evidenced_attrition"] == {
        "from_stage": "eligible", "to_stage": "usable", "absolute_loss": 60
    }
    assert beta["largest_evidenced_attrition"] == {
        "from_stage": "enrolled", "to_stage": "eligible", "absolute_loss": 70
    }
    assert beta["transitions"][1]["status"] == "missing_support"
    assert beta["transitions"][1]["absolute_loss"] is None
    first, second = details["transitions"]
    assert (first["from_total"], first["to_total"], first["absolute_loss"]) == (200, 110, 90)
    assert first["conditional_retention"] == 0.55
    assert second["complete_cohorts"] == ["alpha"]
    assert second["support"] == 1


def test_unspecified_global_metrics_are_null_and_zero_denominator_is_distinct() -> None:
    result = analyze({
        "stages": ["start", "end"],
        "cohorts": [{"cohort": "empty", "counts": {"start": 0, "end": 0}}],
    })
    details = result["details"]
    transition = details["transitions"][0]
    assert transition["status"] == "completeness_unspecified"
    assert transition["absolute_loss"] is None
    row = details["cohorts"][0]["transitions"][0]
    assert row["status"] == "zero_denominator"
    assert row["absolute_loss"] == 0
    assert row["conditional_retention"] is None
    assert details["cohorts"][0]["largest_evidenced_attrition"]["absolute_loss"] == 0


def test_explicit_empty_completeness_reports_zero_support() -> None:
    result = analyze({
        "stages": ["start", "end"],
        "cohorts": [{"cohort": "a", "counts": {"start": 4, "end": 2}}],
        "completeness": [{"from_stage": "start", "to_stage": "end", "complete_cohorts": []}],
    })
    transition = result["details"]["transitions"][0]
    assert transition["support"] == 0
    assert transition["from_total"] == transition["to_total"] == 0
    assert transition["status"] == "zero_denominator"


def test_digest_uses_frozen_canonical_json() -> None:
    payload = example_payload()
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    assert analyze(payload)["input_sha256"] == expected


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"stages": [], "cohorts": []}, "stages_structure"),
        ({"stages": ["x", "x"], "cohorts": []}, "duplicate_stage"),
        ({"stages": ["x"], "cohorts": [
            {"cohort": "same", "counts": {}}, {"cohort": "same", "counts": {}}
        ]}, "duplicate_cohort"),
        ({"stages": ["x"], "cohorts": [{"cohort": "a", "counts": {"x": True}}]}, "count_value"),
        ({"stages": ["x"], "cohorts": [{"cohort": "a", "counts": {"x": 1.0}}]}, "count_value"),
        ({"stages": ["x"], "cohorts": [{"cohort": "a", "counts": {"x": -1}}]}, "count_value"),
        ({"stages": ["x", "y"], "cohorts": [
            {"cohort": "a", "counts": {"x": 2, "y": 3}}
        ]}, "increasing_counts"),
        ({"stages": ["x", "y"], "cohorts": [
            {"cohort": "a", "counts": {"x": 2, "y": None}}
        ], "completeness": [{
            "from_stage": "x", "to_stage": "y", "complete_cohorts": ["a"]
        }]}, "incomplete_cohort_counts"),
    ],
)
def test_rejects_invalid_inputs_with_stable_value_error(payload: object, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        analyze(payload)  # type: ignore[arg-type]


def test_cli_emits_one_json_envelope_and_rejects_duplicate_json_keys() -> None:
    good = subprocess.run(
        [sys.executable, "-m", "memecoin_population.attrition_funnel"],
        input=json.dumps(example_payload()), text=True, capture_output=True, check=True,
    )
    assert json.loads(good.stdout)["kind"] == "attrition_funnel"
    bad = subprocess.run(
        [sys.executable, "-m", "memecoin_population.attrition_funnel"],
        input='{"stages":["x"],"stages":["y"],"cohorts":[]}',
        text=True, capture_output=True,
    )
    assert bad.returncode != 0
