"""Synthetic lifecycle clock and exact-base regressions for sparse samples.

These expectations deliberately describe behavior missing at the task base. They
exercise only the public observation publisher/index and sparse sample producer.
"""

import hashlib
import json
import math

import pytest

from memecoin_experience import observations, sparse
from memecoin_experience.schema import checked_samples

NS = 10**9
ENTITY = "test/market/sparse-lifecycle-clocks"


def _pin(identity):
    return hashlib.sha256(identity.encode()).hexdigest()


def _fact(kind, identity, event_s=None, available_s=0, **fields):
    row = dict(
        kind=kind,
        entity=ENTITY,
        available_ns=available_s * NS,
        evidence=[_pin(identity)],
    )
    if kind == "event":
        row.update(id=identity, stream="ticks", event_ns=event_s * NS, values=[fields.pop("price")])
    elif kind == "lifecycle":
        row.update(id=identity, event_ns=event_s * NS, transition=fields.pop("transition", "listed"))
    else:
        row.update(fields)
    return row


def _materialize(tmp_path, facts, *, availability, cutoff_s=30, context=3):
    rows = [
        _fact("enrollment", "enrollment", available_s=0, start_ns=0, end_ns=25 * NS),
        *facts,
        _fact(
            "support",
            "complete-support",
            available_s=cutoff_s,
            stream="ticks",
            start_ns=0,
            end_ns=min(20, cutoff_s) * NS,
            status="complete",
        ),
    ]
    contract = dict(
        version="market-observations-v1",
        role="train",
        availability=availability,
        streams={
            "ticks": dict(
                process="synthetic exact-time ticks",
                features=[dict(name="price", unit="USD", modality="price")],
            )
        },
    )
    source_store = tmp_path / "observations"
    pin = observations.publish(
        source_store,
        sorted(rows, key=observations.key),
        schema=contract,
        cutoff=cutoff_s * NS,
        sources=[dict(synthetic=True)],
    )
    source = observations.Source(source_store, pin, role="train")
    plan = dict(
        stream="ticks",
        context=context,
        max_history_ns=20 * NS,
        lifecycle=["listed"],
        lifecycle_origins=True,
        relative_features=["price"],
        targets=[
            dict(name="return", kind="endpoint_log_return", horizon_ns=2 * NS, feature="price"),
            dict(name="count", kind="observed_process_count", horizon_ns=2 * NS, feature=None),
        ],
    )
    db = source.index(tmp_path / "index.sqlite")
    try:
        produced = list(sparse.samples(db, source, plan))
        support_shards = sorted({
            json.loads(raw)["_source_shard"]
            for (raw,) in db.execute("select payload from support")
        })
        for row in produced:
            if row["target_status"][1] == "observed":
                assert row["target_captures"][1] == support_shards
    finally:
        db.close()
    # The public checked-sample boundary must accept emitted provenance/statuses.
    checked = list(checked_samples(produced, sparse.schema(source, plan), cutoff_s * NS))
    assert [r["id"] for r in checked] == [r["id"] for r in produced]
    return produced


def _prices(row):
    return [frame[0] for frame in row["values"]]


def _lifecycle(rows):
    return next(r for r in rows if r["id"] == "lifecycle:ticks:listed")


def test_pit_late_lifecycle_uses_availability_decision_exact_base_and_retains_history(tmp_path):
    rows = _materialize(
        tmp_path,
        [
            _fact("event", "before", 4, 10, price=10.0),
            _fact("lifecycle", "listed", 3, 10),
            _fact("event", "decision-base", 10, 10, price=20.0),
            _fact("event", "endpoint", 12, 12, price=40.0),
        ],
        availability="point_in_time",
    )
    row = _lifecycle(rows)

    assert row["decision_ns"] == 10 * NS
    assert row["observation_origin"] == dict(
        kind="lifecycle", stream="ticks", event_ns=3 * NS,
        process="synthetic exact-time ticks",
    )
    # Context=3 allocates two event-history frames and one null-price lifecycle frame.
    assert _prices(row) == [pytest.approx(math.log(0.5)), 0.0, None]
    assert row["values"][-1][2] == 0.0  # lifecycle frame is present but is not an event
    assert row["target_status"] == ["unsupported", "observed"]
    assert row["targets"] == [None, 1]
    assert row["target_details"][0]["reason"] == "no_origin_value"
    assert row["target_details"][1]["reason"] == "complete_observation_process"
    assert row["target_available_ns"][1] == 30 * NS
    assert row["input_start_ns"] == 3 * NS
    assert row["input_available_ns"] == 10 * NS
    assert {d["artifact_sha"] for d in row["input_dependencies"]} >= {
        _pin("listed"), _pin("before"), _pin("decision-base")
    }
    assert _pin("endpoint") not in {d["artifact_sha"] for d in row["input_dependencies"]}


def test_replay_late_lifecycle_uses_event_decision_and_cutoff_eligible_exact_base(tmp_path):
    rows = _materialize(
        tmp_path,
        [
            _fact("event", "prior", 1, 2, price=10.0),
            _fact("lifecycle", "listed", 3, 10),
            _fact("event", "decision-base", 3, 10, price=20.0),
            _fact("event", "after-decision", 4, 10, price=30.0),
            _fact("event", "endpoint", 5, 10, price=40.0),
        ],
        availability="historical_replay",
        cutoff_s=10,
    )
    row = _lifecycle(rows)

    assert row["decision_ns"] == 3 * NS
    assert _prices(row) == [pytest.approx(math.log(0.5)), 0.0, None]
    assert row["target_status"] == ["unsupported", "observed"]
    assert row["targets"] == [None, 2]
    assert row["target_details"][0]["reason"] == "no_origin_value"
    assert row["target_details"][1]["reason"] == "complete_observation_process"
    assert row["target_available_ns"][1] == 10 * NS
    assert row["input_start_ns"] == 1 * NS
    assert row["input_available_ns"] == 10 * NS
    assert _pin("decision-base") in {d["artifact_sha"] for d in row["input_dependencies"]}
    assert _pin("after-decision") not in {d["artifact_sha"] for d in row["input_dependencies"]}


@pytest.mark.parametrize(
    ("base_facts", "expected_reason"),
    [
        ([], "no_decision_price"),
        ([_fact("event", "late-base", 10, 11, price=20.0)],
         "origin_not_available_at_decision"),
        ([_fact("event", "ambiguous-a", 10, 10, price=20.0),
          _fact("event", "ambiguous-b", 10, 10, price=25.0)], "ambiguous_origin"),
        ([_fact("event", "null-base", 10, 10, price=None)], "no_origin_value"),
        ([_fact("event", "zero-base", 10, 10, price=0.0)], "no_origin_value"),
        ([_fact("event", "negative-base", 10, 10, price=-1.0)], "no_origin_value"),
    ],
)
def test_pit_lifecycle_masks_unqualified_exact_decision_base_but_keeps_count(
    tmp_path, base_facts, expected_reason
):
    rows = _materialize(
        tmp_path,
        [
            _fact("event", "old", 4, 10, price=10.0),
            _fact("lifecycle", "listed", 3, 10),
            *base_facts,
            _fact("event", "endpoint", 12, 12, price=40.0),
        ],
        availability="point_in_time",
    )
    row = _lifecycle(rows)
    assert _prices(row) == [None, None, None]
    assert row["target_status"] == ["unsupported", "observed"]
    assert row["targets"] == [None, 1]
    # Input-base eligibility does not turn a lifecycle origin into a price-label origin.
    assert row["target_details"][0]["reason"] == "no_origin_value"
    for event_row in rows:
        if event_row["decision_ns"] == row["decision_ns"] and event_row["id"].startswith("event:"):
            assert event_row["target_details"][0]["reason"] == expected_reason


def test_lifecycle_and_event_origins_at_one_pit_decision_share_relative_history(tmp_path):
    rows = _materialize(
        tmp_path,
        [
            _fact("event", "prior", 8, 10, price=10.0),
            _fact("lifecycle", "listed", 10, 10),
            _fact("event", "decision-base", 10, 10, price=20.0),
            _fact("event", "endpoint", 12, 12, price=40.0),
        ],
        availability="point_in_time",
    )
    at_decision = [r for r in rows if r["decision_ns"] == 10 * NS]
    assert {r["id"] for r in at_decision} == {
        "event:ticks:prior", "event:ticks:decision-base", "lifecycle:ticks:listed"
    }
    lifecycle = _lifecycle(at_decision)
    for event in [r for r in at_decision if r["observation_origin"]["kind"] == "event"]:
        event_history = [frame[0] for frame in event["values"] if frame[2] == 1.0]
        lifecycle_history = [frame[0] for frame in lifecycle["values"] if frame[2] == 1.0]
        assert event_history == lifecycle_history == [pytest.approx(math.log(0.5)), 0.0]
    assert lifecycle["values"][-1][0] is None
    assert lifecycle["target_details"][0]["reason"] == "no_origin_value"


@pytest.mark.parametrize(
    ("decision", "cutoff", "status", "reason"),
    [(9, 10, "pending", "fit_cutoff"), (24, 30, "censored", "enrollment_boundary")],
)
def test_lifecycle_history_preserves_pending_and_enrollment_precedence(
    tmp_path, decision, cutoff, status, reason
):
    rows = _materialize(
        tmp_path,
        [_fact("event", "prior", decision - 1, decision - 1, price=10.0),
         _fact("event", "base", decision, decision, price=20.0),
         _fact("lifecycle", "listed", decision, decision)],
        availability="historical_replay", cutoff_s=cutoff,
    )
    row = _lifecycle(rows)
    assert _prices(row) == [pytest.approx(math.log(0.5)), 0.0, None]
    assert row["target_status"] == [status, status]
    assert [detail["reason"] for detail in row["target_details"]] == [reason, reason]
    assert row["targets"] == [None, None]


def test_replay_refuses_price_receipt_beyond_sealed_cutoff(tmp_path):
    with pytest.raises(ValueError, match="observation_availability"):
        _materialize(
            tmp_path,
            [_fact("event", "late-base", 3, 11, price=20.0),
             _fact("lifecycle", "listed", 3, 10)],
            availability="historical_replay", cutoff_s=10,
        )
