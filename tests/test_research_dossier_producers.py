"""Integration regressions using every supported baseline diagnostic producer."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from memecoin_experience import (
    availability_profile,
    expected_observation_timeliness,
    expected_slot_coverage,
    group_split_allocation_audit,
    group_split_feasibility,
    group_partition_concordance,
    paired_loss_report,
    provider_roster_coverage,
    source_value_concordance,
    split_assignment_plan_delta,
    split_stratum_coverage,
    split_target_deviation,
    weight_concentration,
    weight_state_delta,
)
from memecoin_experience import research_report
from memecoin_population import attrition_funnel, exposure_shift
from memecoin_experience.research_dossier_registry import CURRENT_KINDS
from memecoin_runner import (
    abstention_frontier,
    paired_loss_cluster_influence,
    paired_loss_comparison,
    paired_loss_missing_bounds,
    paired_loss_signflip,
    paired_loss_target_mix,
    quantile_cohort_coverage,
    research_evidence_gate,
)


def _reports() -> list[dict]:
    """Construct meaningful envelopes only by calling actual accepted APIs."""
    slot_input = {
        "intervals": [{"id": "window-a", "stream": "provider-a", "start_ns": 0,
                       "end_ns": 6, "slot_width_ns": 2}],
        "observations": [
            {"id": "seen-0", "stream": "provider-a", "event_ns": 0},
            {"id": "duplicate-0", "stream": "provider-a", "event_ns": 1},
            {"id": "unknown-time", "stream": "provider-a", "event_ns": None},
        ],
    }
    timely_input = {
        "intervals": [{"id": "window-a", "stream": "feed ```\n<script>alert(1)</script>", "start_ns": 0,
                       "end_ns": 4, "slot_width_ns": 2, "deadline_ns": 5}],
        "observations": [
            {"id": "on-time", "stream": "feed ```\n<script>alert(1)</script>", "event_ns": 0,
             "available_ns": 3, "receipt_ns": 4},
            {"id": "late", "stream": "feed ```\n<script>alert(1)</script>", "event_ns": 2,
             "available_ns": 6, "receipt_ns": 7},
        ],
    }
    concordance_input = {
        "sources": ["alpha", "beta"], "absolute_tolerance": 0.1,
        "relative_tolerance": 0.01,
        "observations": [
            {"source": "alpha", "id": "matched", "values": {"score": 1.0}},
            {"source": "beta", "id": "matched", "values": {"score": 1.05}},
            {"source": "alpha", "id": "null", "values": {"score": None}},
        ],
    }
    roster_input = {
        "expected_ids": ["x", "y", "z"], "providers": ["alpha", "beta"],
        "observations": [
            {"provider": "alpha", "id": "x", "observation_id": "ax"},
            {"provider": "alpha", "id": "x", "observation_id": "ax-copy"},
            {"provider": "beta", "id": "y", "observation_id": "by"},
        ],
    }
    gate_input = {
        "policy": {"max_input_age_ns": 10, "max_uncertainty": 0.5},
        "rows": [
            {"id": "eligible", "decision_ns": 10, "input_available_ns": 4,
             "source_status": "qualified", "forecast_status": "available", "uncertainty": 0.2},
            {"id": "unknown", "decision_ns": 10, "input_available_ns": None,
             "source_status": "unknown", "forecast_status": "missing", "uncertainty": None},
        ],
    }
    frontier_input = {
        "thresholds": [0.25, 0.75],
        "rows": [
            {"id": "accepted", "uncertainty": 0.2, "supported": True, "loss": 2},
            {"id": "unknown", "uncertainty": None, "supported": True, "loss": None},
            {"id": "unsupported", "uncertainty": 0.1, "supported": False, "loss": 1},
        ],
    }

    comparison_rows = [
        {"id": "a", "cohort": "large", "loss_a": 4, "loss_b": 3},
        {"id": "b", "cohort": "large", "loss_a": 4, "loss_b": 5},
        {"id": "c", "cohort": "small", "loss_a": 0, "loss_b": 2},
        {"id": "d", "cohort": "small", "loss_a": None, "loss_b": 1},
    ]
    common = {"model_a": "baseline", "model_b": "candidate", "metric": "loss"}
    comparison = {**common, "rows": comparison_rows}
    missing_bounds = {**common, "loss_upper_bound": 10, "rows": comparison_rows}
    target_mix = {
        **common,
        "target_cohorts": [{"cohort": "large", "weight": 0.75},
                           {"cohort": "small", "weight": 0.25}],
        "rows": [row for row in comparison_rows if row["loss_a"] is not None and row["loss_b"] is not None],
    }
    cluster = {
        **common,
        "rows": [
            {"id": "a", "cluster": "group-a", "loss_a": 4, "loss_b": 3},
            {"id": "b", "cluster": "group-a", "loss_a": 4, "loss_b": 5},
            {"id": "c", "cluster": "group-b", "loss_a": 0, "loss_b": 2},
        ],
    }
    signflip = {"pairs": [
        {"id": "p1", "left_loss": 2, "right_loss": 3},
        {"id": "p2", "left_loss": 4, "right_loss": 2},
    ]}

    split_support_input = {
        "rows": [{"id": "r1"}, {"id": "r2"}],
        "strata": [{"id": "s", "supported_splits": ["train", "test"]}],
        "row_strata": [{"row_id": "r1", "stratum_id": "s"},
                       {"row_id": "r2", "stratum_id": "s"}],
        "row_splits": [{"row_id": "r1", "split": "train"},
                       {"row_id": "r2", "split": "train"}],
    }
    split_plan_input = {
        "row_ids": ["r1", "r2", "r3"],
        "before": [{"row_id": "r1", "split": "train"}, {"row_id": "r2", "split": None}],
        "after": [{"row_id": "r1", "split": "test"}, {"row_id": "r3", "split": "train"}],
    }
    weights_input = {"examples": [
        {"id": "e1", "entity": "token-a", "cohort": "c1", "weight": 2},
        {"id": "e2", "entity": "token-a", "cohort": "c2", "weight": 1},
        {"id": "e3", "entity": "token-b", "cohort": "c2", "weight": 0},
    ]}
    exposure_input = {"entities": [
        {"entity": "token-a", "corpus": 2, "exposure": 1},
        {"entity": "token-b", "corpus": 1, "exposure": 3},
        {"entity": "unmatched", "corpus": None, "exposure": 2},
    ]}

    return [
        availability_profile.analyze({"decision_ns": 3, "rows": [
            {"id": "r", "stream": "s", "event_ns": 1, "available_ns": None,
             "receipt_ns": 2},
        ]}),
        attrition_funnel.analyze({
            "stages": ["start", "end"],
            "cohorts": [{"cohort": "empty", "counts": {"start": 0, "end": 0}}],
        }),
        group_split_allocation_audit.analyze({
            "rows": [{"id": "r1", "group_id": "g1"}],
            "assignments": [{"row_id": "r1", "split": "train"}],
        }),
        group_split_feasibility.analyze({
            "groups": [{"id": "g1", "counts": {"s": 2}}], "holdout_counts": {"s": 1},
            "max_states": 100,
        }),
        group_partition_concordance.analyze({
            "row_ids": ["r1", "r2"],
            "left": [{"row_id": "r1", "group": "g1"}],
            "right": [{"row_id": "r2", "group": None}],
        }),
        quantile_cohort_coverage.analyze({"nominal_levels": [0.5], "rows": [
            {"id": "r1", "target": "loss", "cohort": "empty", "status": "unsupported",
             "actual": None, "quantiles": None},
        ]}),
        expected_slot_coverage.analyze(slot_input),
        expected_observation_timeliness.analyze(timely_input),
        source_value_concordance.analyze(concordance_input),
        provider_roster_coverage.analyze(roster_input),
        exposure_shift.analyze(exposure_input),
        weight_concentration.analyze(weights_input),
        split_stratum_coverage.analyze(split_support_input),
        split_assignment_plan_delta.analyze(split_plan_input),
        research_evidence_gate.analyze(gate_input),
        abstention_frontier.analyze(frontier_input),
        paired_loss_comparison.analyze(comparison),
        paired_loss_missing_bounds.analyze(missing_bounds),
        paired_loss_target_mix.analyze(target_mix),
        paired_loss_cluster_influence.analyze(cluster),
        paired_loss_signflip.analyze(signflip),
    ]


def test_actual_producer_baselines_return_six_key_envelopes_and_keep_truthful_support():
    reports = _reports()
    assert {item["kind"] for item in reports} == CURRENT_KINDS
    assert all(set(item) == {
        "schema", "kind", "input_sha256", "summary", "details", "limitations"
    } for item in reports)
    by_kind = {item["kind"]: item for item in reports}
    assert by_kind["expected_slot_coverage"]["summary"]["missing_event_time_observation_count"] == 1
    assert by_kind["expected_observation_timeliness"]["summary"]["late_receipt_count"] == 1
    assert by_kind["provider_roster_coverage"]["summary"]["duplicate_count"] == 1
    assert by_kind["exposure_shift"]["summary"]["exposure_known_count"] == 3
    assert by_kind["abstention_frontier"]["details"][0]["rejected"]["unsupported_count"] == 1
    assert by_kind["paired_loss_signflip"]["summary"]["sign_flip_assignments"] == 4


def test_paired_loss_report_composes_real_workflow_outputs_without_becoming_an_envelope():
    from memecoin_runner.paired_loss_workflow import analyze as analyze_workflow

    workflow_payload = {
        "model_a": "baseline", "model_b": "candidate", "metric": "loss",
        "loss_upper_bound": 10,
        "target_cohorts": [{"cohort": "c", "weight": 1}],
        "rows": [{"id": "r1", "cohort": "c", "loss_a": 2, "loss_b": 1}],
    }
    workflow = analyze_workflow(workflow_payload)
    assert workflow["schema"] == "brain-paired-loss-workflow.v1"
    assert {report["kind"] for report in workflow["reports"]} == {
        "paired_loss_comparison", "paired_loss_missing_bounds", "paired_loss_target_mix"
    }
    bundle = paired_loss_report.analyze({"reports": workflow["reports"]})
    assert bundle["schema"] == "brain-paired-loss-bundle.v1"
    assert bundle["report_count"] == 3
    assert "schema" not in workflow or workflow["schema"] != bundle["schema"]
    assert workflow["support"]["complete_pair_count"] == 1


def test_paired_loss_report_preserves_the_five_actual_paired_loss_diagnostics():
    kinds = {
        "paired_loss_comparison", "paired_loss_missing_bounds", "paired_loss_target_mix",
        "paired_loss_cluster_influence", "paired_loss_signflip",
    }
    reports = [item for item in _reports() if item["kind"] in kinds]
    assert {item["kind"] for item in reports} == kinds
    bundle = paired_loss_report.analyze({"reports": reports})
    assert bundle["schema"] == "brain-paired-loss-bundle.v1"
    assert bundle["report_count"] == 5
    assert bundle["reports"] == sorted(reports, key=lambda item: (item["kind"], item["input_sha256"]))
    current = research_report.analyze({"reports": bundle["reports"]}, mode="current")
    assert current["reports"] == bundle["reports"]
    with pytest.raises(ValueError, match="^research_report_envelope$"):
        research_report.analyze({"reports": [bundle]}, mode="current")


def test_legacy_baseline_reproduces_real_producer_kind_rejection():
    gate = next(item for item in _reports() if item["kind"] == "research_evidence_gate")
    # This is the independent baseline reproduction requested by the task.
    with pytest.raises(ValueError, match="^research_report_kind$"):
        research_report.analyze({"reports": [gate]})


def test_real_split_outputs_keep_their_distinct_envelope_shapes():
    audit = group_split_allocation_audit.analyze({
        "rows": [{"id": "r1", "group_id": "g1"}],
        "assignments": [{"row_id": "r1", "split": "train"}],
    })
    assert audit["schema"] == "brain-research-diagnostic.v1"
    assert audit["kind"] == "group_split_allocation_audit"
    allocation = split_target_deviation.analyze({
        "observed_counts": {"train": {"mature": 3, "new": 1}},
        "target_counts": {"train": {"mature": 2, "new": 2}},
    })
    assert allocation["kind"] == "split_target_deviation"
    assert set(allocation) == {
        "schema", "kind", "input_sha256", "summary", "details", "limitations", "quota_support"
    }
    # The six-key audit is supported; quota_support remains an incompatible extra.


def test_current_api_accepts_real_mixed_producers_preserves_order_hashes_and_missing_values():
    analyze_current = research_report.analyze
    reports = _reports()
    source_copy = json.loads(json.dumps(reports))
    expected = sorted(reports, key=lambda item: (item["kind"], item["input_sha256"]))
    result = analyze_current({"reports": reports}, mode="current")
    assert result["schema"] == "brain-research-bundle.v1"
    assert result["mode"] == "current"
    assert result["reports"] == expected
    assert all(len(item["input_sha256"]) == 64 for item in result["reports"])
    assert reports == source_copy
    assert result["reports"] == expected
    assert "not verified" in result["hash_semantics"]
    assert any("Missing fields" in item for item in result["limitations"])
    assert any(item["summary"].get("common_known_count") == 2 for item in result["reports"])
    assert any(item["summary"].get("eligible_count") == 1 for item in result["reports"])


def test_current_api_rejects_incompatible_real_outputs_and_held_kinds_without_adapters():
    analyze_current = research_report.analyze
    audit = group_split_allocation_audit.analyze({
        "rows": [{"id": "r1", "group_id": "g1"}],
        "assignments": [{"row_id": "r1", "split": "train"}],
    })
    assert analyze_current({"reports": [audit]}, mode="current")["reports"] == [audit]
    allocation = split_target_deviation.analyze({
        "observed_counts": {"train": {"mature": 3, "new": 1}},
        "target_counts": {"train": {"mature": 2, "new": 2}},
    })
    with pytest.raises(ValueError, match="^research_report_envelope$"):
        analyze_current({"reports": [allocation]}, mode="current")
    states = weight_state_delta.analyze({
        "before": {"state_id": "before", "state_sha256": "a" * 64, "examples": []},
        "after": {"state_id": "after", "state_sha256": "b" * 64, "examples": []},
    })
    with pytest.raises(ValueError, match="^research_report_envelope$"):
        analyze_current({"reports": [states]}, mode="current")
    for kind in ("binary_calibration", "representation_shift"):
        with pytest.raises(ValueError, match="^research_report_kind$"):
            analyze_current({"reports": [{**audit, "kind": kind}]}, mode="current")
    from memecoin_experience import population_inclusion_audit, purged_temporal_split

    population = population_inclusion_audit.analyze(
        {"snapshot_id": "empty", "snapshot_sha256": "a" * 64, "row_ids": []}, [],
    )
    split = purged_temporal_split.analyze({"rows": [], "embargo": 0})
    for unsupported in (population, split):
        with pytest.raises(ValueError, match="^research_report_envelope$"):
            analyze_current({"reports": [unsupported]}, mode="current")


def test_current_cli_json_and_markdown_use_actual_producer_batch():
    reports = _reports()
    payload = {"reports": reports}
    env = {**os.environ, "PYTHONPATH": "src"}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    json_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.research_report", "--mode", "current"],
        input=encoded, text=True, capture_output=True, env=env,
    )
    assert json_run.returncode == 0, json_run.stderr
    expected = research_report.analyze(payload, mode="current")
    assert json.loads(json_run.stdout) == expected
    markdown_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.research_report", "--mode", "current", "--markdown"],
        input=encoded, text=True, capture_output=True, env=env,
    )
    assert markdown_run.returncode == 0, markdown_run.stderr
    assert markdown_run.stdout == research_report.render_markdown(expected)


def test_dossier_round_trip_for_hostile_strings_and_exact_paired_loss_integer_helpers():
    envelope = next(item for item in _reports() if item["kind"] == "paired_loss_signflip")
    hostile = next(item for item in _reports() if item["kind"] == "expected_observation_timeliness")
    bundle = research_report.analyze({"reports": [envelope, hostile]}, mode="current")
    markdown = research_report.render_markdown(bundle)
    assert "<script>alert(1)</script>" in markdown
    assert "````" in markdown
    # Use the accepted paired_loss_report implementation as the integer oracle.
    digit_limit = sys.get_int_max_str_digits() if hasattr(sys, "get_int_max_str_digits") else None
    huge = 2**15000
    encoded = paired_loss_report._json_output({"n_sign_patterns": huge})
    parsed = json.loads(encoded, parse_int=paired_loss_report._parse_integer)
    assert parsed["n_sign_patterns"] == huge
    # Consumer helper must preserve exact parse/print without global limit edits.
    from memecoin_experience import research_dossier_rendering

    assert research_dossier_rendering.dumps_json({"n_sign_patterns": huge}) == encoded
    integer_token = encoded.split(":", 1)[1][:-1]
    assert research_dossier_rendering.parse_integer(integer_token) == huge
    if digit_limit is not None:
        assert sys.get_int_max_str_digits() == digit_limit


def test_current_public_api_cli_and_markdown_preserve_caller_declared_huge_integers():
    from memecoin_experience import research_dossier_rendering as rendering

    # This is a caller declaration, not a fabricated sign-flip producer result:
    # the real baseline producer accepts at most 20 pairs (2**20 assignments).
    envelope = next(item for item in _reports() if item["kind"] == "paired_loss_signflip")
    envelope["summary"]["caller_declared_exact_integer"] = 2**15000
    payload = {"reports": [envelope]}
    cap = sys.get_int_max_str_digits()
    bundle = research_report.analyze(payload, mode="current")
    assert bundle["reports"] == [envelope]
    source = rendering.dumps_json(payload)
    for flags, expected in (
        ([], rendering.dumps_json(bundle) + "\n"),
        (["--markdown"], research_report.render_markdown(bundle)),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "memecoin_experience.research_report", "--mode", "current", *flags],
            input=source, text=True, capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected
    assert sys.get_int_max_str_digits() == cap


@pytest.mark.parametrize("source,error", [
    ('{"reports":[],"reports":[]}', "research_report_duplicate_key"),
    ('{"reports":[],"extra":NaN}', "research_report_nonfinite_json"),
    ('{"reports":[', "research_report_"),
])
def test_current_cli_rejects_malformed_json_with_code_two(source, error):
    result = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.research_report", "--mode", "current"],
        input=source, text=True, capture_output=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert error in result.stderr
