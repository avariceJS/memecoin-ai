"""Synthetic regression for relative event history on lifecycle origins."""

import hashlib
import math

import pytest

from memecoin_experience import observations, sparse

NS = 10**9
ENTITY = "test/market/sparse-lifecycle-relative-history"


def _pin(label):
    return hashlib.sha256(label.encode()).hexdigest()


def _materialize(
    tmp_path, *, availability="point_in_time", late_base=False, history=True, context=3
):
    base_available = 20 if late_base else 10
    rows = [
        dict(kind="enrollment", entity=ENTITY, start_ns=0, end_ns=35 * NS,
             available_ns=0, evidence=[_pin("enrollment")]),
        *[
            dict(kind="event", entity=ENTITY, stream="ticks", id=identity,
                 event_ns=event_s * NS, available_ns=available_s * NS,
                 values=[price, size], evidence=[_pin(identity)])
            for identity, event_s, available_s, price, size in (
                [("before", 8, 8, 10.0, 80.0)] if history else []
            ) + [("decision", 10, base_available, 20.0, 100.0),
                 ("endpoint", 15, 25, 30.0, 150.0)]
        ],
        dict(kind="lifecycle", entity=ENTITY, id="listed", event_ns=10 * NS,
             available_ns=10 * NS, transition="listed", evidence=[_pin("listed")]),
        dict(kind="support", entity=ENTITY, stream="ticks", start_ns=0,
             end_ns=25 * NS, available_ns=30 * NS, status="complete",
             evidence=[_pin("support")]),
    ]
    schema = dict(
        version="market-observations-v1", role="train", availability=availability,
        streams={"ticks": dict(process="synthetic exact-time ticks", features=[
            dict(name="price", unit="USD", modality="price"),
            dict(name="size", unit="units", modality="raw"),
        ])},
    )
    store = tmp_path / "observations"
    pin = observations.publish(
        store, sorted(rows, key=observations.key), schema=schema,
        cutoff=30 * NS, sources=[dict(synthetic=True)],
    )
    source = observations.Source(store, pin, role="train")
    plan = dict(
        stream="ticks", context=context, max_history_ns=30 * NS,
        lifecycle=["listed"], lifecycle_origins=True, relative_features=["price"],
        targets=[
            dict(name="return", kind="endpoint_log_return",
                 horizon_ns=5 * NS, feature="price"),
            dict(name="count", kind="observed_process_count",
                 horizon_ns=5 * NS, feature=None),
        ],
    )
    db = source.index(tmp_path / "index.sqlite")
    try:
        samples = list(sparse.samples(db, source, plan))
    finally:
        db.close()
    return samples, plan, source


def _relative(row):
    return [frame[0] for frame in row["values"]]


def test_lifecycle_history_uses_exact_decision_base_but_keeps_origin_frame_null(tmp_path):
    rows, plan, source = _materialize(tmp_path)
    lifecycle = next(row for row in rows if row["observation_origin"]["kind"] == "lifecycle")
    event = next(row for row in rows if row["id"] == "event:ticks:decision")

    assert _relative(lifecycle) == [pytest.approx(math.log(10 / 20)), 0.0, None]
    assert [frame[1] for frame in lifecycle["values"]] == [80.0, 100.0, None]
    assert lifecycle["target_status"] == ["unsupported", "observed"]
    assert lifecycle["targets"] == [None, 1]
    assert lifecycle["target_details"][0]["reason"] == "no_origin_value"
    assert _pin("decision") in {d["artifact_sha"] for d in lifecycle["input_dependencies"]}

    assert _relative(event) == [None, pytest.approx(math.log(10 / 20)), 0.0]
    assert event["values"][-2:] == lifecycle["values"][:2]
    assert event["target_status"] == ["observed", "observed"]
    assert event["targets"] == [pytest.approx(math.log(30 / 20)), 1]
    assert sparse.schema(source, plan)["representation"]["version"] == "4"


def test_historical_replay_lifecycle_history_can_use_late_exact_base(tmp_path):
    rows, _, _ = _materialize(
        tmp_path, availability="historical_replay", late_base=True
    )
    lifecycle = next(row for row in rows if row["observation_origin"]["kind"] == "lifecycle")
    assert _relative(lifecycle) == [pytest.approx(math.log(10 / 20)), 0.0, None]
    assert _pin("decision") in {d["artifact_sha"] for d in lifecycle["input_dependencies"]}
    dep = next(d for d in lifecycle["input_dependencies"] if d["artifact_sha"] == _pin("decision"))
    assert dep["event_ns"] == 10 * NS
    assert dep["available_ns"] == 20 * NS


def test_lifecycle_only_all_null_tensor_does_not_depend_on_price_base(tmp_path):
    rows, _, _ = _materialize(tmp_path, history=False, context=1)
    lifecycle = next(row for row in rows if row["observation_origin"]["kind"] == "lifecycle")
    assert _relative(lifecycle) == [None]
    assert _pin("decision") not in {d["artifact_sha"] for d in lifecycle["input_dependencies"]}
