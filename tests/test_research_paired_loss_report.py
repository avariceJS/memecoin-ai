from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from memecoin_experience.paired_loss_report import _parse_integer, analyze, render_markdown
from memecoin_experience.research_report import analyze as analyze_legacy
from memecoin_runner.paired_loss_cluster_influence import analyze as cluster_influence
from memecoin_runner.paired_loss_comparison import analyze as comparison
from memecoin_runner.paired_loss_missing_bounds import analyze as missing_bounds
from memecoin_runner.paired_loss_signflip import analyze as signflip
from memecoin_runner.paired_loss_target_mix import analyze as target_mix


def actual_producer_envelopes() -> list[dict]:
    return [
        comparison({
            "model_a": "baseline", "model_b": "candidate", "metric": "loss",
            "rows": [
                {"id": "paired", "cohort": "supported", "loss_a": 1, "loss_b": 0},
                {"id": "missing", "cohort": "unsupported", "loss_a": None, "loss_b": None},
            ],
        }),
        missing_bounds({
            "model_a": "baseline", "model_b": "candidate", "metric": "bounded_loss",
            "loss_upper_bound": 1,
            "rows": [{"id": "partial", "cohort": "c", "loss_a": 0.2, "loss_b": None}],
        }),
        target_mix({
            "model_a": "same label", "model_b": "same label", "metric": "loss",
            "target_cohorts": [{"cohort": "absent", "weight": 1}], "rows": [],
        }),
        cluster_influence({
            "model_a": "a", "model_b": "b", "metric": "loss",
            "rows": [{"id": "only", "cluster": "c", "loss_a": 1, "loss_b": 0}],
        }),
        signflip({"pairs": [{"id": "p", "left_loss": 1, "right_loss": 2}]}),
    ]


def test_composes_all_actual_producers_preserving_full_diagnostics() -> None:
    envelopes = actual_producer_envelopes()
    result = analyze({"reports": envelopes})

    assert result["schema"] == "brain-paired-loss-bundle.v1"
    assert result["report_count"] == 5
    assert {report["kind"] for report in result["reports"]} == {
        "paired_loss_comparison", "paired_loss_missing_bounds", "paired_loss_target_mix",
        "paired_loss_cluster_influence", "paired_loss_signflip",
    }
    assert {report["kind"]: report for report in result["reports"]} == {
        report["kind"]: report for report in envelopes
    }
    target = next(report for report in result["reports"] if report["kind"] == "paired_loss_target_mix")
    assert target["summary"]["target_mean_delta"] is None
    assert target["summary"]["unsupported_positive_weight_cohort_count"] == 1
    assert target["details"][0]["supported"] is False
    signflip_report = next(report for report in result["reports"] if report["kind"] == "paired_loss_signflip")
    assert signflip_report["details"] == []
    assert "raw inputs were not supplied" in result["hash_semantics"]
    assert "different populations" in result["limitations"][1]


def test_preserves_actual_producer_4300_digit_integer_through_api_cli_and_markdown() -> None:
    digit_limit = sys.get_int_max_str_digits()
    produced = target_mix({
        "model_a": "A", "model_b": "B", "metric": "synthetic",
        "target_cohorts": [{"cohort": str(i), "weight": 10**4299} for i in range(10)],
        "rows": [],
    })
    expected = 10**4300
    expected_decimal = "1" + "0" * 4300
    assert type(produced["summary"]["target_weight_total"]) is int
    assert produced["summary"]["target_weight_total"] == expected

    # Feed the actual producer's JSON representation to the dossier CLI so its
    # large-integer parser path is exercised as well as detached Python input.
    from memecoin_runner.paired_loss_target_mix import _canonical_output_json

    produced_json = _canonical_output_json(produced)
    payload_json = '{"reports":[' + produced_json + ']}'
    detached = analyze({"reports": [produced]})
    detached_target = detached["reports"][0]
    assert type(detached_target["summary"]["target_weight_total"]) is int
    assert detached_target["summary"]["target_weight_total"] == expected
    assert produced["summary"]["target_weight_total"] == expected

    json_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report"],
        input=payload_json, text=True, capture_output=True, check=False,
    )
    assert json_run.returncode == 0 and json_run.stderr == ""
    cli_bundle = json.loads(json_run.stdout, parse_int=_parse_integer)
    cli_target = cli_bundle["reports"][0]
    assert type(cli_target["summary"]["target_weight_total"]) is int
    assert cli_target["summary"]["target_weight_total"] == expected

    markdown_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report", "--markdown"],
        input=payload_json, text=True, capture_output=True, check=False,
    )
    assert markdown_run.returncode == 0 and markdown_run.stderr == ""
    assert expected_decimal in markdown_run.stdout
    assert markdown_run.stdout == render_markdown(detached)
    assert sys.get_int_max_str_digits() == digit_limit


def test_stable_sorting_detachment_and_empty_details_and_reports() -> None:
    reports = actual_producer_envelopes()
    reports.reverse()
    original = copy.deepcopy(reports)
    result = analyze({"reports": reports})
    assert [(r["kind"], r["input_sha256"]) for r in result["reports"]] == sorted(
        (r["kind"], r["input_sha256"]) for r in reports
    )
    result["reports"][0]["limitations"].append("changed")
    assert reports == original
    assert analyze({"reports": []})["reports"] == []


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda r: r.update(schema="wrong"), "paired_loss_report_schema"),
        (lambda r: r.update(kind="not_a_paired_loss_kind"), "paired_loss_report_kind"),
        (lambda r: r.update(input_sha256="A" * 64), "paired_loss_report_input_sha256"),
        (lambda r: r.update(summary=[]), "paired_loss_report_summary"),
        (lambda r: r.update(details="not structured"), "paired_loss_report_details"),
        (lambda r: r.update(limitations=[" "]), "paired_loss_report_limitations"),
        (lambda r: r.update(extra=True), "paired_loss_report_envelope"),
    ],
)
def test_rejects_malformed_envelopes(mutate, error: str) -> None:
    report = actual_producer_envelopes()[0]
    mutate(report)
    with pytest.raises(ValueError, match=f"^{error}$"):
        analyze({"reports": [report]})


def test_rejects_duplicates_python_only_values_and_non_string_keys() -> None:
    report = actual_producer_envelopes()[0]
    with pytest.raises(ValueError, match="^paired_loss_report_duplicate_report$"):
        analyze({"reports": [report, report]})
    invalid = copy.deepcopy(report)
    invalid["summary"]["not_json"] = float("nan")
    with pytest.raises(ValueError, match="^paired_loss_report_non_json_value$"):
        analyze({"reports": [invalid]})
    invalid = copy.deepcopy(report)
    invalid["summary"][1] = "integer key must not be coerced"
    with pytest.raises(ValueError, match="^paired_loss_report_non_json_value$"):
        analyze({"reports": [invalid]})
    with pytest.raises(ValueError, match="^paired_loss_report_non_json_value$"):
        analyze({"reports": [{**report, "summary": {"fraction": __import__("fractions").Fraction(1, 2)}}]})


def test_markdown_safely_contains_hostile_caller_text_and_all_support() -> None:
    report = actual_producer_envelopes()[0]
    report["summary"]["caller_label"] = "```\n<script>alert('x')</script>"
    markdown = render_markdown(analyze({"reports": [report]}))
    assert "````json\n" in markdown
    assert "<script>alert('x')</script>" in markdown
    assert "missing" in markdown and "limitations" in markdown
    assert markdown.rstrip().endswith("````")


def test_cli_json_markdown_and_invalid_input() -> None:
    payload = {"reports": actual_producer_envelopes()}
    encoded = json.dumps(payload)
    json_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report"],
        input=encoded, text=True, capture_output=True, check=False,
    )
    assert json_run.returncode == 0 and json_run.stderr == ""
    assert json.loads(json_run.stdout) == analyze(payload)
    markdown_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report", "--markdown"],
        input=encoded, text=True, capture_output=True, check=False,
    )
    assert markdown_run.returncode == 0
    assert markdown_run.stdout == render_markdown(analyze(payload))
    invalid_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report"],
        input='{"reports":[],"extra":NaN}', text=True, capture_output=True, check=False,
    )
    assert invalid_run.returncode == 2
    assert invalid_run.stdout == ""
    assert "paired_loss_report_nonfinite_json" in invalid_run.stderr
    duplicate_key_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.paired_loss_report"],
        input='{"reports":[],"reports":[]}', text=True, capture_output=True, check=False,
    )
    assert duplicate_key_run.returncode == 2
    assert "paired_loss_report_duplicate_key" in duplicate_key_run.stderr


def test_frozen_legacy_research_report_contract_remains_usable() -> None:
    legacy = {
        "schema": "brain-research-diagnostic.v1", "kind": "paired_loss_comparison",
        "input_sha256": "a" * 64, "summary": {}, "details": [], "limitations": ["legacy"],
    }
    assert analyze_legacy({"reports": [legacy]})["schema"] == "brain-research-bundle.v1"
