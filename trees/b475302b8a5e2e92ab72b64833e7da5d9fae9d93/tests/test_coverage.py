import copy

import pytest
from test_corpus_quality import sample
from test_prospective import resume, setup

from memecoin_ai.coverage import export_support, support_report, verify_support
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.execution_targets import export_execution_targets
from memecoin_ai.prospective import hashes, read


def test_variants_and_overlap_do_not_inflate_decisions_or_claim_independence():
    rows, labels = sample()
    report = support_report(rows, labels)
    assert report["scenario_rows"] == report["feature_decisions"] * 8
    complete = sum(report["complete_decisions_by_partition"].values())
    thinned = sum(report["all_pool_nonoverlapping_windows_by_partition"].values())
    assert 0 < thinned < complete < len(rows)
    assert not report["training_ready"]
    assert report["excluded_decisions"]["at_least_one_censored_variant"] > 0
    assert report["excluded_decisions"]["purged_or_mixed_partition"] > 0
    # Outcome-independent QA: changing signs/magnitudes does not select winners.
    changed = copy.deepcopy(labels)
    for label in changed:
        label["pnl_before_network_costs"] = 10**18
    assert support_report(rows, changed) == report
    assert support_report(rows, list(reversed(labels))) == report


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "pool", "time"])
def test_partial_grid_and_mismatched_start_are_rejected(mutation):
    rows, labels = sample()
    if mutation == "missing":
        labels.pop()
    elif mutation == "duplicate":
        labels.append(copy.deepcopy(labels[0]))
    elif mutation == "pool":
        labels[0]["pool_id"] = "wrong"
    else:
        labels[0]["decision_ns"] += 1
    with pytest.raises(ValidationError):
        support_report(rows, labels)


def test_cross_pool_simultaneous_labels_are_not_additive_independent_windows():
    rows, labels = sample()
    doubled_rows, doubled_labels = copy.deepcopy(rows), copy.deepcopy(labels)
    for row in doubled_rows:
        row["sample_id"] += "copy"
        row["pool_id"] += "copy"
    for label in doubled_labels:
        label["sample_id"] += "copy"
        label["pool_id"] += "copy"
    old = support_report(rows, labels)
    new = support_report(rows + doubled_rows, labels + doubled_labels)
    assert (
        new["all_pool_nonoverlapping_windows_by_partition"]
        == old["all_pool_nonoverlapping_windows_by_partition"]
    )
    assert sum(new["complete_decisions_by_partition"].values()) == 2 * sum(
        old["complete_decisions_by_partition"].values()
    )


def test_support_bundle_reproduces_and_catches_rehashed_report_tamper(tmp_path):
    campaign = tmp_path / "campaign"
    time, client = setup(campaign)
    resume(campaign, time, client)
    targets, out = tmp_path / "targets", tmp_path / "support"
    export_execution_targets(campaign, targets)
    report = export_support(campaign, targets, out)
    assert verify_support(campaign, targets, out) == report
    report["training_ready"] = True
    (out / "report.json").write_text(canonical(report))
    manifest = read(out / "manifest.json")
    manifest["report.json"] = digest((out / "report.json").read_bytes())
    (out / "manifest.json").write_text(canonical(manifest))
    assert hashes(out)
    with pytest.raises(ValidationError, match="reproduction"):
        verify_support(campaign, targets, out)
