"""Synthetic fixtures prove chronological/maturity and outcome-independent membership."""

import copy
from collections import Counter

import pytest

from memecoin_brain.data import build
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


def test_absolute_cut_allows_recent_markets_into_train_and_purges_crossing():
    rows, prov = fixture()
    parts, bounds = partitions(build(rows, prov), prov["as_of_ns"])
    assert {s["pool"] for s in parts["train"]} == {"old", "young"}
    assert bounds["train_end_s"] == 300 * 300 - 21600
    for name in ["train", "validation", "test"]:
        assert all(s["maturity_s"] < bounds[name + "_end_s"] for s in parts[name])
    assert any(s["maturity_s"] == bounds["train_end_s"] for s in parts["purged"])


def test_cap_is_nested_label_independent_and_pool_balanced():
    rows, prov = fixture()
    samples = build(rows, prov)["samples"]
    a = bounded(samples, 60)
    b = bounded(samples, 90)
    assert Counter(s["pool"] for s in a) == dict(old=30, young=30)
    assert {s["id"] for s in a} < {s["id"] for s in b}
    altered = copy.deepcopy(samples)
    for s in altered:
        s["targets"] = [dict(log_return=1000)]
    assert [s["id"] for s in bounded(altered, 60)] == [s["id"] for s in a]


def test_receipt_future_target_and_duplicate_fail_closed():
    rows, prov = fixture()
    b = build(rows, prov)
    b["samples"][0]["targets"][0]["available_ns"] = prov["as_of_ns"] + 1
    with pytest.raises(ValueError, match="future_target"):
        partitions(b, prov["as_of_ns"])
    b = build(rows, prov)
    b["samples"].append(b["samples"][0])
    with pytest.raises(ValueError, match="duplicate"):
        partitions(b, prov["as_of_ns"])


def test_snapshot_retains_exact_membership_and_unknown_outcomes(tmp_path):
    rows, prov = fixture()
    r = prepare(rows, prov, tmp_path)
    assert r["original"]["purged"]["decisions"] > 0
    assert r["retained"]["train"]["pools"] == 2
    assert not r["historical_test_independent"]
    assert r["new_fits"] == 0
    assert r["unseen_test_pools"] == []
