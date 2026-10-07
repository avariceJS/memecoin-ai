import copy

import pytest
from test_experience import bars

from memecoin_experience.records import experiences
from memecoin_experience.representations import ohlcv_samples, ohlcv_schema
from memecoin_experience.schema import checked_samples, membership
from memecoin_experience.storage import Snapshot, build


def test_ohlcv_v2_matches_v1_and_variable_targets(tmp_path):
    cutoff = 10**18
    schema = ohlcv_schema(16, (1, 3, 6))
    rows = list(ohlcv_samples(bars(120), cutoff, schema))
    legacy = list(experiences(bars(120), cutoff, 16, horizons=(1, 3, 6)))
    assert [r["values"] for r in rows] == [r["values"] for r in legacy]
    assert [r["targets"] for r in rows] == [r["targets"] for r in legacy]
    assert list(checked_samples(rows, schema, cutoff)) == rows
    result = build(
        tmp_path, iter(rows), sources=[dict(synthetic=True)], cutoff=cutoff, schema=schema
    )
    snapshot = Snapshot(tmp_path, result["snapshot"])
    assert snapshot.schema == schema
    assert sum(len(snapshot.partition(i)[1]) for i in range(len(snapshot.parts))) == len(rows)


def custom_samples(n=310):
    schema = dict(
        schema="brain-experience-v2",
        layout="dense-sequence-f64-v1",
        context=2,
        features=[
            dict(name="depth", unit="USD", modality="liquidity"),
            dict(name="flow", unit="USD/s", modality="order_flow"),
        ],
        targets=[dict(name="change", unit="ratio", kind="synthetic_regression")],
        representation=dict(name="synthetic-multimodal", version="1", code_sha="c" * 64),
        availability="point_in_time",
    )
    rows = []
    for i in range(n):
        t = (i + 1) * 300 * 10**9
        rows.append(
            dict(
                id=f"entity:{i:06}",
                pool="synthetic",
                decision_ns=t,
                input_start_ns=t - 300 * 10**9,
                input_available_ns=t,
                input_dependencies=[
                    dict(artifact_sha="a" * 64, event_ns=t - 300 * 10**9, available_ns=t)
                ],
                values=[[i * 0.01, None], [i * 0.02, i % 3]],
                targets=[(i % 5) * 0.01],
                target_status=["observed"],
                target_available_ns=[t + 100 * 10**9],
                target_maturity_ns=[t + 100 * 10**9],
                target_captures=[["b" * 64]],
            )
        )
    return schema, rows


def test_generic_producer_uses_actual_availability_and_dependency_purge(tmp_path):
    schema, rows = custom_samples()
    assert len(list(checked_samples(rows, schema, 10**18))) == 310
    plan = dict(train_start_s=0, train_end_s=30000, validation_end_s=60000, test_end_s=90000)
    assert membership(rows[99], plan, schema)[0] == "purged"
    result = build(tmp_path, rows, sources=[dict(synthetic=True)], cutoff=10**18, schema=schema)
    Snapshot(tmp_path, result["snapshot"]).partition(0)
    bad = copy.deepcopy(rows[0])
    bad["input_available_ns"] += 1
    with pytest.raises(ValueError, match="sample_contract"):
        list(checked_samples([bad], schema, 10**18))
    bad = copy.deepcopy(rows[0])
    bad["input_dependencies"][0]["artifact_sha"] = "unbound"
    with pytest.raises(ValueError, match="dependency"):
        list(checked_samples([bad], schema, 10**18))


def test_generic_training_has_no_fixed_feature_or_target_count(tmp_path):
    pytest.importorskip("torch")
    from test_experience_learning import CONFIG, PLAN

    from memecoin_experience.learning import train

    schema, rows = custom_samples()
    result = build(
        tmp_path / "store", rows, sources=[dict(synthetic=True)], cutoff=10**18, schema=schema
    )
    report = train(
        tmp_path / "store", result["snapshot"], tmp_path / "job", plan=PLAN, config=CONFIG
    )
    assert report["status"] == "completed"
    assert len(report["test"]["model"]) == 1
    assert len(report["statistics"]["mean"]) == 2


def test_point_in_time_label_receipt_cannot_cross_training_boundary():
    schema, rows = custom_samples()
    row = rows[90]
    row["target_available_ns"][0] = 70000 * 10**9
    plan = dict(train_start_s=0, train_end_s=30000, validation_end_s=60000, test_end_s=90000)
    assert membership(row, plan, schema) == ("train", [False])
    # Historical event-time replay has a different, explicitly declared validity domain.
    assert membership(row, plan, dict(schema, availability="historical_replay")) == (
        "train",
        [True],
    )
