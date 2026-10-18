import copy
import json

import pytest
from test_execution_targets import sequence, targets
from test_prospective import resume, setup

from memecoin_ai.corpus_quality import export_quality, summarize_quality, verify_quality
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.execution_targets import export_execution_targets


def sample():
    rows, observations = sequence()
    labels = targets(rows, observations)
    for label in labels:
        label["campaign_segment"] = 0
    return rows, labels


def test_quality_preserves_unknowns_and_reports_maturity_not_execution_latency():
    rows, labels = sample()
    result = summarize_quality([rows], labels, cutoff_ns=60 * 10**9)
    assert result["temporal_and_accounting_checks_passed"]
    assert result["feature_rows"] == 22 and not result["training_ready"]
    assert result["target_partitions"]["purged"] > 0
    assert result["distinct_mature_decisions_by_partition"]["engineering_evaluation"] > 0
    assert result["conditional_entry_observation_delay_seconds"]["minimum"] > 0
    assert result["chain_clock_age_seconds"]["count"] == 21


@pytest.mark.parametrize("mutation", ["net", "maturity", "boundary", "provenance", "inventory"])
def test_independent_quality_guards_reject_bad_target_contract(mutation):
    rows, labels = sample()
    label = next(t for t in labels if t["status"] == "conditional_roundtrip")
    if mutation == "net":
        label["net_pnl"] = 0
    elif mutation == "maturity":
        label["target_available_ns"] -= 1
    elif mutation == "boundary":
        label["campaign_segment"] = 1
    elif mutation == "provenance":
        label["legs"][-1]["response_sha256"] = "wrong"
    else:
        label["residual_token_units"] = 1
    with pytest.raises(ValidationError):
        summarize_quality([rows], labels, cutoff_ns=60 * 10**9)


def test_segment_overlap_and_duplicate_variants_rejected():
    rows, labels = sample()
    with pytest.raises(ValidationError, match="overlapping"):
        summarize_quality([rows, rows], labels, cutoff_ns=60 * 10**9)
    labels.append(copy.deepcopy(labels[0]))
    with pytest.raises(ValidationError, match="duplicate execution"):
        summarize_quality([rows], labels, cutoff_ns=60 * 10**9)


def test_sealed_quality_report_reproduces_and_detects_rehashed_tamper(tmp_path):
    campaign = tmp_path / "campaign"
    t, c = setup(campaign)
    resume(campaign, t, c)
    targets_path = tmp_path / "targets"
    export_execution_targets(campaign, targets_path)
    output = tmp_path / "quality"
    report = export_quality(campaign, targets_path, output)
    assert verify_quality(campaign, targets_path, output) == report
    report["training_ready"] = True
    (output / "report.json").write_text(canonical(report))
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["report.json"] = digest((output / "report.json").read_bytes())
    (output / "manifest.json").write_text(canonical(manifest))
    with pytest.raises(ValidationError, match="reproduction"):
        verify_quality(campaign, targets_path, output)
