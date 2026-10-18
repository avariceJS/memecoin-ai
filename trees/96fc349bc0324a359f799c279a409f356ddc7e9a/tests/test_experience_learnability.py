"""Read-only audit of the frozen synthetic learnability result."""

import hashlib
import json
from pathlib import Path

EVIDENCE = Path(__file__).parents[1] / "docs/evidence"
PREREG = EVIDENCE / "brain-controlled-signal-learnability-prereg-2026-10-15.json"
RESULT = EVIDENCE / "brain-controlled-signal-learnability-result-2026-10-15.json"


def test_frozen_controlled_signal_receipt_preserves_denominator_failure():
    declaration = json.loads(PREREG.read_text())
    result = json.loads(RESULT.read_text())

    preregistration_pin = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    assert result["preregistration"] == {
        "path": PREREG.name,
        "sha256": preregistration_pin,
    }
    assert declaration["decision_rule"] == (
        "Run once. Preserve pass or failure. Do not revise the signal, fixture, seed, recipe, "
        "or threshold after fitting."
    )

    expected_recipe = declaration["recipe"]
    for key in (
        "model_kind",
        "width",
        "batch",
        "epochs",
        "seed",
        "device",
        "cpu_threads",
        "objective",
    ):
        assert result["recipe"][key] == expected_recipe[key]

    enrolled = declaration["cohorts"]["registered_research_evaluation"]["exact_examples"]
    cohort = result["cohorts"]["registered_evaluation"]
    assert enrolled == declaration["acceptance"]["required_exact_evaluation_count"] == 64
    assert cohort["enrolled"] == enrolled
    assert cohort["exact_eligible"] == 63
    assert cohort["purged"] == 1
    assert cohort["purged_reason"] == (
        "the first input dependency starts one second before the registered evaluation window"
    )

    acceptance = result["acceptance"]
    assert acceptance["required_exact_evaluation_n"] == enrolled
    assert (
        acceptance["actual_exact_evaluation_n"]
        == result["metrics"]["registered_evaluation_exact_n"]
        == 63
    )
    assert acceptance["denominator_gate"] == "fail"
    assert acceptance["overall_outcome"] == "fail"
    assert result["metrics"]["predictions_finite"] is True
    assert (
        "not accepted because the frozen exact-denominator gate failed"
        in acceptance["loss_thresholds"]
    )
