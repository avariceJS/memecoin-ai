import copy
import math
from collections import defaultdict

import pytest

from memecoin_brain.data import build
from memecoin_curriculum.design import experiments, nested, partition, weighted_quantile, weights


def fixture_bundle():
    rows = [
        dict(
            pool=p,
            open_s=i * 300,
            available_ns=(i + 1) * 300 * 10**9 + 10,
            capture_sha=f"{p}-{i}",
            ohlcv=[math.exp(i * 0.001)] * 4 + [100],
        )
        for p in ("a", "b")
        for i in range(180)
    ]
    return build(rows, dict(as_of_ns=10**18))


BOUNDS = dict(train_start_s=12000, train_end_s=24000, validation_end_s=36000, test_end_s=54000)


def test_common_folds_purge_and_label_independent_nested_membership():
    bundle = fixture_bundle()
    exps = list(experiments(bundle, BOUNDS))
    assert all(e["test"] == exps[0]["test"] for e in exps)
    assert all(e["validation"] == exps[0]["validation"] for e in exps)
    parts = partition(bundle, BOUNDS)
    assert all(s["maturity_s"] < BOUNDS["train_end_s"] for s in parts["train"])
    assert any(s["maturity_s"] == BOUNDS["train_end_s"] for s in parts["purged"])
    changed = copy.deepcopy(bundle["samples"])
    for s in changed:
        for t in s["targets"]:
            t["log_return"] = 1000.0
    assert [s["id"] for s in nested(changed, 25)] == [
        s["id"] for s in nested(bundle["samples"], 25)
    ]
    ids = [{s["id"] for s in nested(bundle["samples"], f)} for f in (25, 50, 100)]
    assert ids[0] < ids[1] < ids[2]


def test_hierarchical_balance_equal_pools_and_days_without_new_support():
    samples = [
        dict(pool=p, event_close_s=d * 86400 + i, id=f"{p}-{d}-{i}")
        for p, days, n in [("old", 20, 100), ("young", 2, 4)]
        for d in range(days)
        for i in range(n)
    ]
    probability = weights(samples, True)
    mass = defaultdict(float)
    for s, w in zip(samples, probability, strict=True):
        mass[s["pool"]] += w
    assert mass == pytest.approx(dict(old=0.5, young=0.5))
    assert sum(probability) == pytest.approx(1)
    assert weighted_quantile([0, 1, 100], [0.1, 0.8, 0.1], 0.5) == 1


def test_future_receipt_and_duplicate_rejected():
    b = fixture_bundle()
    b["samples"][0]["targets"][0]["available_ns"] = 10**19
    with pytest.raises(ValueError, match="future_target"):
        partition(b, BOUNDS)
    b = fixture_bundle()
    b["samples"].append(b["samples"][0])
    with pytest.raises(ValueError, match="duplicate"):
        partition(b, BOUNDS)
