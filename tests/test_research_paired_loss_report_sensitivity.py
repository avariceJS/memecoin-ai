from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from memecoin_experience.paired_loss_report import analyze, render_markdown
from memecoin_runner.paired_loss_cluster_influence import analyze as cluster_influence
from memecoin_runner.paired_loss_signflip import analyze as signflip
from memecoin_runner.paired_loss_workflow import _output_json, analyze as workflow_analyze


def workflow_result() -> dict:
    return workflow_analyze({
        "model_a": "baseline", "model_b": "candidate", "metric": "loss",
        "loss_upper_bound": 10, "target_cohorts": [{"cohort": "all", "weight": 1}],
        "rows": [
            {"id": "p1", "cohort": "all", "loss_a": 4, "loss_b": 2},
            {"id": "p2", "cohort": "all", "loss_a": 3, "loss_b": 1},
            {"id": "missing", "cohort": "all", "loss_a": None, "loss_b": 2},
        ],
    })


def test_direct_workflow_handoff_preserves_reports_and_provenance() -> None:
    workflow = workflow_result()
    before = copy.deepcopy(workflow)
    bundle = analyze(workflow)
    assert bundle["schema"] == "brain-paired-loss-bundle.v1"
    assert bundle["reports"] == workflow["reports"]
    assert bundle["report_count"] == 3
    assert bundle["workflow"] == {
        "input_sha256": workflow["input_sha256"], "support": workflow["support"],
        "limitations": workflow["limitations"],
    }
    assert workflow == before
    markdown = render_markdown(bundle)
    assert "Workflow provenance" in markdown
    assert "raw workflow input was not supplied" in markdown
    assert '"schema": "brain-paired-loss-bundle.v1"' in markdown
    assert markdown.rstrip().endswith("```")


def test_workflow_cli_json_and_markdown() -> None:
    workflow = workflow_result()
    encoded = json.dumps(workflow)
    json_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report"],
        input=encoded, text=True, capture_output=True, check=False,
    )
    assert json_run.returncode == 0 and json_run.stderr == ""
    bundle = json.loads(json_run.stdout)
    assert bundle == analyze(workflow)
    markdown_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report", "--markdown"],
        input=encoded, text=True, capture_output=True, check=False,
    )
    assert markdown_run.returncode == 0 and markdown_run.stderr == ""
    assert markdown_run.stdout == render_markdown(bundle)


@pytest.mark.parametrize("mutation", [
    lambda w: w.update(schema="wrong"),
    lambda w: w.update(input_sha256="z" * 64),
    lambda w: w["support"].update(roster_count=100),
    lambda w: w["limitations"].append(" "),
])
def test_rejects_bad_workflow_provenance(mutation) -> None:
    value = workflow_result()
    mutation(value)
    with pytest.raises(ValueError, match="^paired_loss_report_(workflow|payload)"):
        analyze(value)


def test_rejects_extra_workflow_fields() -> None:
    value = workflow_result()
    value["extra"] = True
    with pytest.raises(ValueError, match="^paired_loss_report_payload$"):
        analyze(value)


def test_workflow_markdown_fence_is_safe_for_caller_text() -> None:
    value = workflow_result()
    value["reports"][0]["summary"]["hostile"] = "```\n<script>caller</script>"
    rendered = render_markdown(analyze(value))
    assert "````json\n" in rendered
    assert "<script>caller</script>" in rendered
    assert rendered.rstrip().endswith("````")


def test_workflow_sensitivity_handoff_preserves_actual_optional_producers() -> None:
    workflow = workflow_result()
    common = {"model_a": "baseline", "model_b": "candidate", "metric": "loss"}
    complete = [
        {"id": "p1", "cluster": "group-1", "loss_a": 4, "loss_b": 2},
        {"id": "p2", "cluster": "group-1", "loss_a": 3, "loss_b": 1},
    ]
    cluster_report = cluster_influence({**common, "rows": complete})
    flip_report = signflip({"pairs": [
        {"id": row["id"], "left_loss": row["loss_a"], "right_loss": row["loss_b"]}
        for row in complete
    ]})
    workflow["reports"].extend([cluster_report, flip_report])
    workflow["sensitivity"] = {
        "cluster_influence": {
            "status": "computed", "reason": None, "roster_count": 3,
            "complete_pair_count": 2, "excluded_incomplete_count": 1,
            "input_sha256": cluster_report["input_sha256"], "cluster_count": 1,
        },
        "signflip": {
            "status": "computed", "reason": None, "roster_count": 3,
            "complete_pair_count": 2, "excluded_incomplete_count": 1,
            "input_sha256": flip_report["input_sha256"],
            "assumption": "exchangeable_signs_under_null", "maximum_complete_pairs": 20,
        },
    }
    bundle = analyze(workflow)
    assert bundle["report_count"] == 5
    assert {report["kind"] for report in bundle["reports"]} == {
        "paired_loss_comparison", "paired_loss_missing_bounds", "paired_loss_target_mix",
        "paired_loss_cluster_influence", "paired_loss_signflip",
    }
    assert bundle["workflow"]["sensitivity"] == workflow["sensitivity"]
    markdown = render_markdown(bundle)
    assert "cluster_influence: computed" in markdown
    assert "signflip: computed" in markdown
    assert "exchangeable_signs_under_null" in markdown
    assert "does not correct cluster dependence" in markdown


def test_large_workflow_integer_survives_direct_dossier_api_and_cli() -> None:
    digit_limit = sys.get_int_max_str_digits()
    workflow = workflow_analyze({
        "model_a": "A", "model_b": "B", "metric": "synthetic", "loss_upper_bound": 1,
        "target_cohorts": [{"cohort": str(i), "weight": 10**4299} for i in range(10)],
        "rows": [],
    })
    total = 10**4300
    assert workflow["reports"][2]["summary"]["target_weight_total"] == total
    bundle = analyze(workflow)
    assert bundle["reports"][2]["summary"]["target_weight_total"] == total
    assert sys.get_int_max_str_digits() == digit_limit
    run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report"],
        input=_output_json(workflow), text=True, capture_output=True, check=False,
    )
    assert run.returncode == 0 and run.stderr == ""
    assert '"target_weight_total":' + "1" + "0" * 4300 in run.stdout
    assert sys.get_int_max_str_digits() == digit_limit


def test_reports_only_contract_remains_unchanged() -> None:
    report = {
        "schema": "brain-research-diagnostic.v1", "kind": "paired_loss_comparison",
        "input_sha256": "a" * 64, "summary": {}, "details": [], "limitations": ["test"],
    }
    result = analyze({"reports": [report]})
    assert result["report_count"] == 1
    assert "workflow" not in result


@pytest.mark.parametrize("diagnostic,changes", [
    ("cluster_influence", {"cluster_count": -1}),
    ("cluster_influence", {"cluster_count": 4}),
    ("cluster_influence", {"status": "skipped", "reason": "no_complete_pairs", "input_sha256": None}),
    ("signflip", {"status": "skipped", "reason": "no_complete_pairs", "input_sha256": None}),
    ("signflip", {"status": "skipped", "reason": "complete_pair_limit_exceeded", "input_sha256": None}),
    ("signflip", {"maximum_complete_pairs": 20.0}),
    ("signflip", {"status": []}),
    ("signflip", {"reason": {}}),
])
def test_rejects_contradictory_sensitivity_provenance(diagnostic, changes):
    # Start from actual integrated workflow metadata, then corrupt one declaration.
    request = {
        "model_a": "A", "model_b": "B", "metric": "loss", "loss_upper_bound": 10,
        "target_cohorts": [{"cohort": "all", "weight": 1}],
        "rows": [{"id": "p", "cohort": "all", "loss_a": 1, "loss_b": 2}],
        "sensitivity": {"clusters": [{"id": "p", "cluster": "g"}],
                        "signflip": {"assumption": "exchangeable_signs_under_null"}},
    }
    value = workflow_analyze(request)
    value["sensitivity"][diagnostic].update(changes)
    with pytest.raises(ValueError, match="^paired_loss_report_workflow_sensitivity$"):
        analyze(value)
