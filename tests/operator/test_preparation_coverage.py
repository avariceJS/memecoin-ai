"""Synthetic preparation coverage reconciles labels excluded by the B13 funnel."""

from memecoin_brain.data import build
from memecoin_ml.data import read
from memecoin_operator import learning
from memecoin_operator.learning import NS, bounded, partitions, prepare


def fixture():
    rows = []
    for pool, start in [("old", 0), ("young", 100)]:
        for i in range(start, 300):
            rows.append(
                dict(
                    pool=pool,
                    open_s=i * 300,
                    available_ns=(i + 1) * 300 * NS + 1,
                    ohlcv=[1.0, 1.0, 1.0, 1.0, 100.0],
                    capture_sha=f"synthetic-{pool}-{i}",
                )
            )
    return rows, dict(as_of_ns=300 * 300 * NS + 1, synthetic=True)


def test_prepare_reports_complete_case_purge_and_cap_cells_without_changing_selection(
    tmp_path, monkeypatch
):
    rows, provenance = fixture()
    original_recipe = dict(learning.RECIPE)
    monkeypatch.setattr(
        learning,
        "RECIPE",
        dict(original_recipe, train_cap=20, evaluation_cap=20),
    )

    full = build(rows, provenance)
    parts, _ = partitions(full, provenance["as_of_ns"])
    expected = {
        name: bounded(parts[name], 20)
        for name in ("train", "validation", "test")
    }
    report = prepare(rows, provenance, tmp_path)
    on_disk = read(tmp_path / "coverage.json")

    assert {name: learning.membership(values) for name, values in expected.items()} == report[
        "retained"
    ]
    assert report["retained"] == on_disk["retained"]
    coverage = on_disk["preparation_coverage"]
    assert coverage["availability_contract"] == {
        "mode": "historical_replay",
        "as_of_ns": provenance["as_of_ns"],
        "cutoff_inclusive": True,
        "knowledge_claim": "sealed as_of cutoff; historical availability does not establish online or point-in-time knowledge",
    }
    assert coverage["rows"]["dropped_by_reason"]["complete_case_exclusion"] > 0
    assert coverage["rows"]["dropped_by_reason"]["temporal_maturity_purge"] > 0
    assert coverage["rows"]["dropped_by_reason"]["cap_exclusion"] > 0
    assert coverage["rows"]["dropped"] == sum(coverage["rows"]["dropped_by_reason"].values())
    assert coverage["rows"]["original"] == (
        coverage["rows"]["complete_case_eligible"]
        + coverage["rows"]["dropped_by_reason"]["complete_case_exclusion"]
    )
    assert coverage["rows"]["complete_case_eligible"] == (
        coverage["rows"]["partition_eligible"]
        + coverage["rows"]["dropped_by_reason"]["temporal_maturity_purge"]
    )
    assert coverage["rows"]["partition_eligible"] == (
        coverage["rows"]["retained"]
        + coverage["rows"]["dropped_by_reason"]["cap_exclusion"]
    )

    cells = coverage["target_cells"]
    for horizon, original in cells["stages"]["original"].items():
        complete = cells["stages"]["complete_case_eligible"][horizon]
        partition = cells["stages"]["partition_eligible"][horizon]
        retained = cells["stages"]["retained"][horizon]
        dropped = cells["dropped_by_reason"]
        for field in ("total", "observed", "missing"):
            assert original[field] == (
                complete[field] + dropped["complete_case_exclusion"][horizon][field]
            )
            assert complete[field] == (
                partition[field] + dropped["temporal_maturity_purge"][horizon][field]
            )
            assert partition[field] == retained[field] + dropped["cap_exclusion"][horizon][field]
        assert original["total"] == original["observed"] + original["missing"]

    assert set(cells["stages"]["original"]) == {"1", "3"}
    assert all(
        cells["stages"]["original"][h]["zero_returns"]
        > cells["stages"]["original"][h]["missing"]
        for h in ("1", "3")
    )
