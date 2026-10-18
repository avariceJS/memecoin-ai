import math

import pytest

from memecoin_brain.data import build
from memecoin_curriculum.archive import assemble, path_labels, rolling, shards


def rows(n=90):
    return [
        dict(
            pool=p,
            open_s=i * 300,
            available_ns=(i + 1) * 300 * 10**9 + 10,
            capture_sha=f"{p}-{i}",
            ohlcv=[
                math.exp(i * 0.001),
                math.exp(i * 0.001),
                math.exp(i * 0.001),
                math.exp(i * 0.001),
                100,
            ],
        )
        for p in ["a", "b"]
        for i in range(n)
    ]


def test_shards_match_unsharded_features_and_targets_even_at_boundaries():
    data = rows()
    prov = dict(as_of_ns=10**18)
    whole = build(data, prov)
    merged = assemble(shards(data, prov, chunk=11))
    assert merged["samples"] == whole["samples"]
    assert all(whole["frames"][k] == v for k, v in merged["frames"].items())
    # A gap near a shard edge resets causal context identically.
    data = [r for r in data if r["open_s"] != 3600]
    whole = build(data, prov)
    merged = assemble(shards(data, prov, chunk=11))
    assert merged["samples"] == whole["samples"]
    assert all(whole["frames"][k] == v for k, v in merged["frames"].items())


def test_auxiliary_labels_do_not_fill_missing_future_or_invent_execution():
    data = rows(12)
    labels = list(path_labels(data, 10**18))
    label = next(
        x for x in labels if x["pool"] == "a" and x["event_close_s"] == 2400 and x["horizon"] == 3
    )
    assert label["values"]["log_return"] == pytest.approx(0.003)
    assert label["values"]["realized_variance"] == pytest.approx(3 * 0.001**2)
    assert label["values"]["downside_from_origin_close"] == 0
    assert not label["execution_target"]
    damaged = [r for r in data if not (r["pool"] == "a" and r["open_s"] == 2700)]
    m = next(
        x
        for x in path_labels(damaged, 10**18)
        if x["pool"] == "a" and x["event_close_s"] == 2400 and x["horizon"] == 3
    )
    assert m["values"] is None and m["target_available_ns"] is None
    assert m["input_captures"] == label["input_captures"]


def test_future_receipt_duplicate_and_archive_overflow_rejected():
    data = rows()
    with pytest.raises(ValueError, match="availability"):
        list(path_labels(data, 1))
    with pytest.raises(ValueError, match="duplicate"):
        list(shards([*data, data[0]], dict(as_of_ns=10**18)))
    with pytest.raises(ValueError, match="materialization_capacity"):
        assemble(shards(data, dict(as_of_ns=10**18)), max_decisions=10)


def test_rolling_view_keeps_all_pools_at_cap_boundary():
    base = [r for r in rows(2030) if r["pool"] == "a"]
    data = [dict(r, pool=str(p)) for p in range(17) for r in base]
    cut = 2030 * 300 * 10**9
    selected = rolling(data, cut)
    assert all(r["open_s"] >= cut // 10**9 - 7 * 86400 for r in selected)
    assert 28000 < len(selected) < 28017  # Never split pools sharing the floor timestamp.
    floor = min(r["open_s"] for r in selected)
    assert {r["pool"] for r in selected if r["open_s"] == floor} == {str(p) for p in range(17)}
