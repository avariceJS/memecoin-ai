import copy
import math

import pytest
from test_experience import bars

from memecoin_experience.admission import create, decide
from memecoin_experience.composition import compose
from memecoin_experience.representations import ohlcv_samples, ohlcv_schema
from memecoin_experience.schema import feature_names, membership
from memecoin_experience.storage import Snapshot, build, object_path
from memecoin_experience.transfer import replicate

POLICY = dict(
    name="causal_activity_v1", min_positive_volume_fraction=0.25, min_context_volume_usd=100
)
PLAN = dict(train_start_s=0, train_end_s=12000, validation_end_s=24000, test_end_s=36000)


def test_input_policy_has_no_outcome_or_survival_channel():
    names = ["log_volume_usd", "zero_volume"]
    assert decide([[math.log1p(100), 0]], names, POLICY) == 0
    assert decide([[0, 1]] * 32, names, POLICY) == 1
    assert decide([[math.log1p(1), 0]], names, POLICY) == 2
    assert decide([[None, 0]], names, POLICY) == 3
    assert decide([[float("nan"), 0]], names, POLICY) == 3
    assert decide([[1000, 0]], names, POLICY) == 0


def dataset(store):
    schema = ohlcv_schema(8, stable=True)
    raw = list(bars(120))
    for r in raw[:50]:
        r["ohlcv"][4] = 0
    samples = list(ohlcv_samples(raw, 10**18, schema))
    result = build(
        store, samples, sources=[dict(synthetic=True)], cutoff=10**18, schema=schema, chunk=13
    )
    return result, samples, schema


def test_mask_preserves_physical_payloads_denominators_and_replica(tmp_path):
    store = tmp_path / "store"
    result, samples, schema = dataset(store)
    view = create(store, result["snapshot"], POLICY)
    assert view == create(store, result["snapshot"], POLICY)
    assert sum(view["counts"].values()) == len(samples)
    assert 0 < view["counts"]["eligible"] < len(samples)
    parent, child = Snapshot(store, result["snapshot"]), Snapshot(store, view["snapshot"])
    assert [p["pin"] for p in parent.parts] == [p["pin"] for p in child.parts]
    for i in range(len(child.parts)):
        path, rows = child.partition(i)
        original_path, originals = parent.partition(i)
        assert path == original_path
        for row, original in zip(rows, originals, strict=True):
            assert row["target_status"] == original["target_status"]
            if row["_admission"] != "eligible":
                assert not any(membership(row, PLAN, schema)[1])
            else:
                assert membership(row, PLAN, schema) == membership(original, PLAN, schema)
    replicate(store, tmp_path / "replica", view["snapshot"])
    replica = Snapshot(tmp_path / "replica", view["snapshot"])
    assert replica.partition(0)[1] == child.partition(0)[1]
    with pytest.raises(ValueError, match="compose_before_admission"):
        compose(store, [view["snapshot"], result["snapshot"]])
    with pytest.raises(ValueError, match="admission_parent"):
        create(store, view["snapshot"], POLICY)
    mask = object_path(store, child.parts[0]["admission"]) / "codes.u8"
    mask.write_bytes(b"\0" * mask.stat().st_size)
    with pytest.raises(ValueError, match="checksum"):
        child.partition(0)


def test_selection_fixed_by_entity_and_input_time_and_future_invariance(tmp_path):
    result, samples, schema = dataset(tmp_path / "a")
    # Same inputs, arbitrary target values and missing-future status.
    changed = copy.deepcopy(samples)
    for row in changed:
        row["targets"] = [None] * 2
        row["target_status"] = ["missing_future"] * 2
        row["target_captures"] = [[]] * 2
        row["target_available_ns"] = [None] * 2
    other = build(
        tmp_path / "b",
        changed,
        sources=[dict(synthetic=True)],
        cutoff=10**18,
        schema=schema,
        chunk=13,
    )
    selection = dict(before_ns=12000 * 10**9, entities=["different"])
    first = create(tmp_path / "a", result["snapshot"], POLICY, selection=selection)
    second = create(tmp_path / "b", other["snapshot"], POLICY, selection=selection)
    assert first["counts"] == second["counts"]
    assert first["counts"]["not_selected"] > 0
    assert first["counts"]["eligible"] > 0


def test_stable_features_extreme_positive_prices():
    from memecoin_brain.data import features as legacy
    from memecoin_experience.numerics import features

    normal = list(bars(8))
    assert features(normal) == legacy(normal)
    raw = list(bars(40))
    for i, row in enumerate(raw):
        p = 1e300 if i % 2 else 1e-300
        row["ohlcv"] = [p, p, p, p, 1e308]
    schema = ohlcv_schema(8, stable=True)
    samples = list(ohlcv_samples(raw, 10**18, schema))
    assert samples
    assert all(math.isfinite(v) for s in samples for f in s["values"] for v in f if v is not None)
    assert all(math.isfinite(v) for s in samples for v in s["targets"] if v is not None)


def test_constant_feature_normalization_and_masked_training(tmp_path):
    pytest.importorskip("torch")
    from memecoin_experience.learning import prepare

    result, _, schema = dataset(tmp_path / "store")
    view = create(tmp_path / "store", result["snapshot"], POLICY)
    snapshot = Snapshot(tmp_path / "store", view["snapshot"])
    # Use a split whose training interval includes both quiet and active inputs.
    plan = dict(train_start_s=0, train_end_s=24000, validation_end_s=30000, test_end_s=36000)
    stats = prepare(snapshot, plan, tmp_path, 16, "constant_feature_scale_one_v1")
    j = feature_names(schema).index("log_close_open")
    assert stats["mean"][j] == 0
    assert stats["scale"][j] == 1
    assert stats["population"]["train_usable"] < stats["population"]["train"]


def test_admission_paged_catalog_replication_and_cross_partition_binding(tmp_path, monkeypatch):
    from memecoin_experience import catalog
    from memecoin_experience.storage import atomic_json, digest

    monkeypatch.setattr(catalog, "PAGE_SIZE", 2)
    store = tmp_path / "store"
    result, _, _ = dataset(store)
    view = create(store, result["snapshot"], POLICY)
    child = Snapshot(store, view["snapshot"])
    assert "partition_pages" in child.manifest
    replicate(store, tmp_path / "replica", view["snapshot"])
    clone = Snapshot(tmp_path / "replica", view["snapshot"])
    assert clone.partition(len(clone.parts) - 1)[1] == child.partition(len(child.parts) - 1)[1]
    # Even a newly hashed catalog cannot move a valid mask onto a different partition.
    forged = copy.deepcopy(child.manifest)
    forged.pop("partition_pages")
    forged.pop("partition_count")
    forged.pop("partition_index")
    forged["partitions"] = list(child.parts)
    forged["partitions"][0] = dict(forged["partitions"][0], admission=child.parts[1]["admission"])
    pin = digest(forged)
    atomic_json(store / "snapshots" / (pin + ".json"), forged)
    with pytest.raises(ValueError, match="admission_binding"):
        Snapshot(store, pin).partition(0)
