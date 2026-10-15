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


def test_admission_rejection_masks_censored_count_objective(tmp_path):
    pytest.importorskip("torch")
    import hashlib

    from memecoin_experience.learning import batches, prepare
    from memecoin_experience.schema import membership

    schema = dict(
        schema="brain-experience-v2",
        layout="dense-sequence-f64-v1",
        context=1,
        features=[
            dict(name="log_volume_usd", unit="log1p_USD", modality="ohlcv"),
            dict(name="zero_volume", unit="indicator", modality="ohlcv"),
        ],
        targets=[
            dict(
                name="observed_count",
                unit="events",
                kind="observed_process_count@1000000000ns",
            )
        ],
        representation=dict(
            name="admission-censored-objective",
            version="1",
            code_sha=hashlib.sha256(b"admission-censored-objective-v1").hexdigest(),
        ),
        availability="point_in_time",
    )
    rows = []
    for role, decisions in (
        ("train", (1, 2, 3, 4)),
        ("validation", (101, 102, 103, 104)),
        ("test", (201, 202, 203, 204)),
    ):
        for offset, decision_s in enumerate(decisions):
            decision_ns = decision_s * 10**9
            censored = offset < 2
            rejected = offset in (1, 2)
            rows.append(
                dict(
                    id=f"{role}:{offset}",
                    pool="synthetic/admission-censored",
                    decision_ns=decision_ns,
                    input_start_ns=decision_ns - 1,
                    input_available_ns=decision_ns,
                    input_dependencies=[
                        dict(
                            artifact_sha="a" * 64,
                            event_ns=decision_ns - 1,
                            available_ns=decision_ns,
                        )
                    ],
                    values=[[math.log1p(1), 0.0] if rejected else [math.log1p(100), 0.0]],
                    targets=[None if censored else 9.0],
                    target_status=["censored" if censored else "observed"],
                    target_available_ns=[None if censored else decision_ns + 10**9],
                    target_maturity_ns=[decision_ns + 10**9],
                    target_captures=[[] if censored else ["b" * 64]],
                    target_details=[
                        dict(lower_bound=2, lower_bound_available_ns=decision_ns + 10**9)
                        if censored
                        else {}
                    ],
                )
            )
    store = tmp_path / "censored-admission-store"
    built = build(
        store,
        sorted(rows, key=lambda row: (row["pool"], row["decision_ns"], row["id"])),
        sources=[dict(kind="synthetic-admission-censored", pin="c" * 64)],
        schema=schema,
        cutoff=300 * 10**9,
        role="train",
    )
    view = create(store, built["snapshot"], POLICY)
    snapshot = Snapshot(store, view["snapshot"])
    plan = dict(train_start_s=0, train_end_s=100, validation_end_s=200, test_end_s=300)

    train_rows = [row for i in range(len(snapshot.parts)) for row in snapshot.partition(i)[1]]
    rejected_censored = next(row for row in train_rows if row["id"] == "train:1")
    rejected_exact = next(row for row in train_rows if row["id"] == "train:2")
    assert rejected_censored["_admission"] == "insufficient_volume"
    assert rejected_exact["_admission"] == "insufficient_volume"
    assert membership(rejected_exact, plan, schema) == ("train", [False])

    observed = {}
    for role in ("train", "validation", "test"):
        packed = list(
            batches(
                snapshot,
                plan,
                role,
                8,
                with_censoring=True,
            )
        )
        observed[role] = [row_id for batch in packed for row_id in batch[5]]
        assert observed[role] == [f"{role}:0", f"{role}:3"]
        assert sum(int(batch[2].sum()) for batch in packed) == 1
        assert sum(int(batch[4].sum()) for batch in packed) == 1

    prepare_path = tmp_path / "prepare"
    prepare_path.mkdir()
    stats = prepare(
        snapshot,
        plan,
        prepare_path,
        8,
        objective="partial_count_lower_bound_pinball_v1",
    )
    assert stats["population"]["train"] == 4
    assert stats["population"]["train_usable"] == 1
    assert stats["population"]["train_partial_count_usable"] == 1
    assert stats["population"]["train_objective_usable"] == 2
    assert stats["denominators"]["train/0/informative_lower_bound"] == 1
    assert stats["denominators"]["train/0/input_admission_rejected"] == 1
    assert stats["denominators"].get("train/0/lower_bound_unavailable_or_invalid", 0) == 0


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
