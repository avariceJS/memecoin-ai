import copy
import importlib.util
import json
import subprocess
import sys

import pytest

from memecoin_runner.paired_loss_workflow import analyze, render_markdown


def payload():
    return {
        "model_a": "baseline",
        "model_b": "candidate",
        "metric": "synthetic_loss",
        "loss_upper_bound": 10,
        "target_cohorts": [
            {"cohort": "supported", "weight": 1},
            {"cohort": "unsupported", "weight": 2},
        ],
        "rows": [
            {"id": "paired-a", "cohort": "supported", "loss_a": 2, "loss_b": 1},
            {"id": "paired-b", "cohort": "supported", "loss_a": 1, "loss_b": 3},
            {"id": "a-only", "cohort": "supported", "loss_a": 4, "loss_b": None},
            {"id": "missing", "cohort": "unsupported", "loss_a": None, "loss_b": None},
        ],
        "sensitivity": {
            "clusters": [
                {"id": "paired-a", "cluster": "group-a"},
                {"id": "paired-b", "cluster": "group-b"},
                {"id": "a-only", "cluster": "group-a"},
                {"id": "missing", "cluster": "group-b"},
            ],
            "signflip": {"assumption": "exchangeable_signs_under_null"},
        },
    }


@pytest.mark.parametrize("edit", [
    lambda value: value.update(sensitivity=None),
    lambda value: value["sensitivity"].update(extra=None),
    lambda value: value["sensitivity"].pop("clusters"),
    lambda value: value["sensitivity"].update(clusters="group-a"),
    lambda value: value["sensitivity"].update(clusters=[{"id": "paired-a", "cluster": "x"}]),
    lambda value: value["sensitivity"].update(clusters=[
        {"id": "paired-a", "cluster": "x"}, {"id": "paired-a", "cluster": "y"},
        {"id": "paired-b", "cluster": "x"}, {"id": "a-only", "cluster": "x"},
        {"id": "missing", "cluster": "x"},
    ]),
    lambda value: value["sensitivity"].update(clusters=[
        {"id": "paired-a", "cluster": "x"}, {"id": "paired-b", "cluster": "y"},
        {"id": "a-only", "cluster": "x"}, {"id": "missing", "cluster": ""},
    ]),
    lambda value: value["sensitivity"].update(signflip={"assumption": "assume_it"}),
    lambda value: value["sensitivity"].update(signflip={"assumption": "exchangeable_signs_under_null", "extra": 1}),
])
def test_malformed_sensitivity_declarations_rejected_before_optional_helper(edit):
    value = payload()
    edit(value)
    with pytest.raises(ValueError):
        analyze(value)


def test_both_null_declarations_are_valid_and_default_hash_is_unchanged():
    value = payload()
    value["sensitivity"] = {"clusters": None, "signflip": None}
    original_default = copy.deepcopy(value)
    original_default.pop("sensitivity")
    assert "sensitivity" not in analyze(original_default)
    # The validated opt-in input reaches the real optional helper if it is installed.
    if importlib.util.find_spec("memecoin_runner.paired_loss_sensitivity") is None:
        pytest.skip("combined helper is not available in this checkout")
    result = analyze(value)
    assert result["sensitivity"]["cluster_influence"]["status"] == "not_requested"
    assert result["sensitivity"]["signflip"]["status"] == "not_requested"


def _workflow_from_markdown(markdown):
    lines = markdown.splitlines()
    opener_index = next(index for index, line in enumerate(lines)
                        if line.endswith("json") and line.startswith("```"))
    fence = lines[opener_index][:-4]
    closer_index = lines.index(fence, opener_index + 1)
    return json.loads("\n".join(lines[opener_index + 1:closer_index]))


@pytest.mark.skipif(importlib.util.find_spec("memecoin_runner.paired_loss_sensitivity") is None,
                    reason="combined helper is not available in this checkout")
def test_opt_in_api_and_cli_keep_real_reports_and_missing_loss_projection():
    value = payload()
    before = copy.deepcopy(value)
    workflow = analyze(value)
    assert value == before
    assert [report["kind"] for report in workflow["reports"][:3]] == [
        "paired_loss_comparison", "paired_loss_missing_bounds", "paired_loss_target_mix"
    ]
    assert workflow["support"]["complete_pair_count"] == 2
    assert workflow["reports"][2]["summary"]["unsupported_positive_weight_cohort_count"] == 1
    assert [report["kind"] for report in workflow["reports"][3:]] == [
        "paired_loss_cluster_influence", "paired_loss_signflip"
    ]
    assert workflow["sensitivity"]["cluster_influence"]["complete_pair_count"] == 2
    assert workflow["sensitivity"]["signflip"]["complete_pair_count"] == 2

    cli = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_workflow"],
        input=json.dumps(value), text=True, capture_output=True, check=False,
    )
    assert cli.returncode == 0 and cli.stderr == ""
    assert json.loads(cli.stdout) == workflow
    markdown_cli = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_workflow", "--markdown"],
        input=json.dumps(value), text=True, capture_output=True, check=False,
    )
    assert markdown_cli.returncode == 0 and markdown_cli.stderr == ""
    assert _workflow_from_markdown(markdown_cli.stdout) == workflow
    markdown = render_markdown(value)
    assert "Cluster deletion is descriptive" in markdown
    assert "exchangeable-sign assumption" in markdown
    assert "complete pairs 2/4, excluded incomplete 2/4" in markdown
    assert "assumption `exchangeable_signs_under_null`" in markdown
    assert "exact two-sided tail " in markdown
    assert _workflow_from_markdown(markdown) == workflow
    assert "Cluster influence: status `computed`" in markdown
    assert "Sign-flip: status `computed`" in markdown


@pytest.mark.parametrize("pair_count", [0, 3, 21])
def test_real_workflow_to_dossier_handoff_and_bounded_signflip(pair_count, monkeypatch):
    from memecoin_experience import paired_loss_report
    from memecoin_runner import paired_loss_cluster_influence, paired_loss_signflip

    value = payload()
    value["rows"] = [
        {"id": str(i), "cohort": "supported", "loss_a": 3 if i == 0 else 0,
         "loss_b": 0 if i == 0 else 1} for i in range(pair_count)
    ] + [
        {"id": "a", "cohort": "supported", "loss_a": 1, "loss_b": None},
        {"id": "b", "cohort": "supported", "loss_a": None, "loss_b": 1},
        {"id": "m", "cohort": "unsupported", "loss_a": None, "loss_b": None},
    ]
    value["sensitivity"]["clusters"] = [
        {"id": row["id"], "cluster": row["id"]} for row in value["rows"]
    ]
    if pair_count in (0, 21):
        def forbidden(_):
            raise AssertionError("out-of-domain signflip producer must not be invoked")
        monkeypatch.setattr(paired_loss_signflip, "analyze", forbidden)
    workflow = analyze(value)
    complete = value["rows"][:pair_count]
    expected_cluster = paired_loss_cluster_influence.analyze({
        **{key: value[key] for key in ("model_a", "model_b", "metric")},
        "rows": [{"id": row["id"], "cluster": row["id"],
                  "loss_a": row["loss_a"], "loss_b": row["loss_b"]} for row in complete],
    })
    assert workflow["reports"][3] == expected_cluster
    if pair_count == 3:
        assert expected_cluster["summary"]["sign_reversal_count"] == 1
        assert workflow["reports"][4] == paired_loss_signflip.analyze({"pairs": [
            {"id": row["id"], "left_loss": row["loss_a"], "right_loss": row["loss_b"]}
            for row in complete
        ]})
    else:
        assert workflow["sensitivity"]["signflip"]["status"] == "skipped"
        assert workflow["sensitivity"]["signflip"]["reason"] == (
            "no_complete_pairs" if pair_count == 0 else "complete_pair_limit_exceeded"
        )
    default = analyze({key: item for key, item in value.items() if key != "sensitivity"})
    assert default["reports"] == workflow["reports"][:3]
    assert default["support"] == workflow["support"]
    assert workflow["support"]["target_projection_excluded_count"] == 3
    assert workflow["reports"][2]["summary"]["target_mean_delta"] is None
    bundle = paired_loss_report.analyze(workflow)
    assert bundle["workflow"]["sensitivity"] == workflow["sensitivity"]
    assert bundle["reports"] == sorted(workflow["reports"], key=lambda report: report["kind"])
    assert _workflow_from_markdown(paired_loss_report.render_markdown(bundle)) == bundle
    producer = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_workflow"],
        input=json.dumps(value), text=True, capture_output=True, check=True,
    )
    for args in ([], ["--markdown"]):
        dossier = subprocess.run(
            [sys.executable, "-m", "memecoin_experience.paired_loss_report", *args],
            input=producer.stdout, text=True, capture_output=True, check=True,
        )
        assert (_workflow_from_markdown(dossier.stdout) if args else json.loads(dossier.stdout)) == bundle
