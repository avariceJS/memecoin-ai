"""Causal fold protocol and a short synthetic four-fit. Historical test is not promotion."""

import copy
import math
import time
from collections import Counter

import pytest

from memecoin_brain.data import build, load_dataset
from memecoin_ml.data import read, write
from memecoin_ml.data import seal as seal_dataset
from memecoin_operator.learning import NS, RECIPE, bounded, partitions, prepare
from memecoin_registry.contracts import seal


def rows_for(pool, start, stop):
    rows = []
    opened = start
    step = 0
    while opened < stop:
        close = 1.0 + (step % 23) * 0.001
        open_ = close * 0.999
        rows.append(
            dict(
                pool=pool,
                open_s=opened,
                available_ns=(opened + 300) * NS + 1,
                ohlcv=[open_, close * 1.002, min(open_, close) * 0.998, close, 10.0],
                capture_sha=f"synthetic-{pool}-{opened}",
            )
        )
        opened += 300
        step += 1
    return rows


def small():
    rows = []
    for pool, start in [("old", 0), ("young", 100)]:
        for i in range(start, 300):
            close = 1.0 + (i % 17) * 0.002
            open_ = close * 0.998
            rows.append(
                dict(
                    pool=pool,
                    open_s=i * 300,
                    available_ns=(i + 1) * 300 * NS + 1,
                    ohlcv=[open_, close * 1.01, open_ * 0.99, close, 100.0],
                    capture_sha=f"synthetic-{pool}-{i}",
                )
            )
    return rows, dict(as_of_ns=300 * 300 * NS + 1, synthetic=True)


def calendar():
    end = 1_800_000_000
    rows = rows_for("oldpool", end - 60 * 3600, end) + rows_for("youngpool", end - 12000, end)
    return rows, dict(as_of_ns=end * NS + 5, synthetic=True)


def test_recipe_stays_the_preregistered_full_protocol():
    assert RECIPE["version"] == "B13-pool-day-v1"
    assert RECIPE["steps"] == 1000
    assert RECIPE["seeds"] == [7, 19]
    assert RECIPE["kinds"] == ["linear", "gru"]
    assert RECIPE["train_cap"] == 48000
    assert RECIPE["evaluation_cap"] == 6000
    assert RECIPE["validation_hours"] == 3
    assert RECIPE["test_hours"] == 3
    assert RECIPE["selection"] == "validation_pool_macro_1pct_simplicity"
    assert RECIPE["automatic_promotion"] is False


def test_fold_maturity_is_strict_and_future_receipts_fail_closed():
    rows, prov = small()
    parts, bounds = partitions(build(rows, prov), prov["as_of_ns"])
    stops = dict(
        train=bounds["train_end_s"],
        validation=bounds["validation_end_s"],
        test=bounds["test_end_s"],
    )
    for name, stop in stops.items():
        assert parts[name]
        assert all(sample["maturity_s"] < stop for sample in parts[name])
        assert all(sample["event_close_s"] < sample["maturity_s"] for sample in parts[name])
    crossing = [
        sample
        for sample in parts["purged"]
        if sample["event_close_s"] < bounds["train_end_s"] <= sample["maturity_s"]
    ]
    assert crossing
    assert {sample["id"] for sample in crossing}.isdisjoint(
        {sample["id"] for sample in parts["train"]}
    )
    victim = parts["test"][0]["id"]
    leaked = build(rows, prov)
    target = next(sample for sample in leaked["samples"] if sample["id"] == victim)
    target["targets"][0]["available_ns"] = prov["as_of_ns"] + 1
    with pytest.raises(ValueError, match="future_target"):
        partitions(leaked, prov["as_of_ns"])
    framed = build(rows, prov)
    sample = next(
        item
        for item in framed["samples"]
        if all(target["log_return"] is not None for target in item["targets"])
    )
    framed["frames"][sample["history"][0]]["available_ns"] = sample["actual_available_ns"] + 1
    with pytest.raises(ValueError, match="future_frame"):
        partitions(framed, prov["as_of_ns"])
    ahead = build(rows, prov)
    ahead["samples"][0]["actual_available_ns"] = prov["as_of_ns"] + 1
    with pytest.raises(ValueError, match="future_input"):
        partitions(ahead, prov["as_of_ns"])


def test_recent_pool_enters_test_without_entering_train_and_labels_do_not_steer_the_cap(tmp_path):
    rows, prov = calendar()
    report = prepare(rows, prov, tmp_path)
    assert report["historical_test_independent"] is False
    assert report["new_fits"] == 0
    assert report["training_pnl_used"] is False
    assert report["unseen_test_pools"] == ["youngpool"]
    assert report["retained"]["train"]["pools"] == 1
    assert "youngpool" not in report["retained"]["train"]["per_pool"]
    assert report["bounds"]["validation_end_s"] - report["bounds"]["train_end_s"] == 10800
    assert report["bounds"]["test_end_s"] - report["bounds"]["validation_end_s"] == 10800
    samples = []
    for pool in ("a", "b", "c"):
        for day in range(2):
            for index in range(5):
                samples.append(
                    dict(
                        pool=pool,
                        event_close_s=day * 86400 + index * 300,
                        id=f"{pool}-{day}-{index}",
                        targets=[dict(log_return=float(index))],
                    )
                )
    first = bounded(samples, 12)
    second = bounded(samples, 24)
    assert Counter(sample["pool"] for sample in first) == dict(a=4, b=4, c=4)
    assert {sample["id"] for sample in first} < {sample["id"] for sample in second}
    altered = copy.deepcopy(samples)
    for sample in altered:
        sample["targets"] = [dict(log_return=1000.0)]
    assert [sample["id"] for sample in bounded(altered, 12)] == [sample["id"] for sample in first]
    uneven = [dict(pool="big", event_close_s=i * 300, id=f"b{i}") for i in range(10)]
    uneven.append(dict(pool="tiny", event_close_s=0, id="t0"))
    taken = bounded(uneven, 100)
    assert [sample["id"] for sample in taken].count("t0") == 1
    assert len(taken) == 11


def reseal_dataset(snapshot, mutate):
    path = snapshot / "dataset" / "dataset.json"
    payload = read(path)
    mutate(payload)
    path.unlink()
    (snapshot / "dataset" / "manifest.json").unlink()
    write(path, payload)
    seal_dataset(snapshot / "dataset")
    seal(snapshot)


def test_train_rejects_recipe_drift_future_cutoff_and_membership_drift(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from memecoin_operator.learning import train

    rows, prov = small()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    prepare(rows, prov, snapshot)
    seal(snapshot)
    monkeypatch.setitem(RECIPE, "steps", 10)
    with pytest.raises(ValueError, match="operator_recipe_changed"):
        train(snapshot, tmp_path / "model-recipe", tmp_path / "progress-recipe.json")
    monkeypatch.setitem(RECIPE, "steps", 1000)

    future = tmp_path / "future"
    future.mkdir()
    prepare(rows, prov, future)

    def move_cutoff(payload):
        payload["provenance"]["as_of_ns"] = time.time_ns() + 86400 * NS

    reseal_dataset(future, move_cutoff)
    with pytest.raises(ValueError, match="operator_future_snapshot"):
        train(future, tmp_path / "model-future", tmp_path / "progress-future.json")

    shifted = tmp_path / "shifted"
    shifted.mkdir()
    prepare(rows, prov, shifted)

    def rename(payload):
        payload["samples"][0]["id"] = payload["samples"][0]["id"] + "-tampered"

    reseal_dataset(shifted, rename)
    with pytest.raises(ValueError, match="operator_snapshot_membership"):
        train(shifted, tmp_path / "model-shift", tmp_path / "progress-shift.json")


def test_four_short_fits_select_before_test_and_reload_finite_sealed_weights(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from memecoin_brain.model import ensemble_predict
    from memecoin_operator.learning import train
    from memecoin_registry.contracts import verify
    from memecoin_runner.ledger import Ledger

    monkeypatch.setitem(RECIPE, "steps", 10)
    rows, prov = small()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    report = prepare(rows, prov, snapshot)
    assert report["recipe"]["steps"] == 10
    assert report["recipe_sha"]
    seal(snapshot)
    progress = tmp_path / "progress.json"
    model = tmp_path / "model"
    card = train(snapshot, model, progress)
    assert card["production_trade_authorized"] is False
    assert card["confirmatory_F01"] is False
    assert card["prospective_shadow_required"] is True
    assert set(card["test"]) == {"empirical", "linear", "gru"}
    assert read(progress)["status"] == "completed"
    assert read(progress)["completed_fits"] == 4
    verify(model)
    binding = read(model / "binding.json")
    with Ledger(model / "ledger", binding) as ledger:
        events = ledger.audit()
    kinds = [event["kind"] for event in events]
    selected_at = kinds.index("development_selection_before_test")
    tested_at = kinds.index("descriptive_test_read")
    assert selected_at < tested_at
    assert "test" not in events[selected_at]["payload"]
    assert events[tested_at]["payload"]["historical_test_independent"] is False
    bundle = load_dataset(snapshot / "dataset")
    parts, _bounds = partitions(bundle, bundle["provenance"]["as_of_ns"])
    for kind in ("linear", "gru"):
        rendered = ensemble_predict(model, kind, bundle, parts["test"])
        assert rendered
        assert all(math.isfinite(value) for row in rendered for value in row)
        assert all(row == sorted(row) for row in rendered)
    (model / "linear-7" / "weights.pt").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        verify(model)
