"""Synthetic producer contract for decision-relative sparse features."""

import hashlib
import math

import pytest

from memecoin_experience import observations, sparse

NS = 10**9
ENTITY = "test/market/sparse-relative-origin"


def _pin(label):
    return hashlib.sha256(label.encode()).hexdigest()


def _materialize(
    tmp_path, events, *, lifecycle=(), availability="point_in_time", cutoff=40, context=2
):
    rows = [
        dict(
            kind="enrollment",
            entity=ENTITY,
            start_ns=0,
            end_ns=35 * NS,
            available_ns=0,
            evidence=[_pin("enrollment")],
        ),
        *[
            dict(
                kind="event",
                entity=ENTITY,
                stream="ticks",
                id=identity,
                event_ns=event_s * NS,
                available_ns=available_s * NS,
                values=[price],
                evidence=[_pin(identity)],
            )
            for identity, event_s, available_s, price in events
        ],
        *[
            dict(
                kind="lifecycle",
                entity=ENTITY,
                id=identity,
                event_ns=event_s * NS,
                available_ns=available_s * NS,
                transition="listed",
                evidence=[_pin(identity)],
            )
            for identity, event_s, available_s in lifecycle
        ],
        dict(
            kind="support",
            entity=ENTITY,
            stream="ticks",
            start_ns=0,
            end_ns=25 * NS,
            available_ns=cutoff * NS,
            status="complete",
            evidence=[_pin("support")],
        ),
    ]
    source_schema = dict(
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
    store = tmp_path / "observations"
    pin = observations.publish(
        store,
        sorted(rows, key=observations.key),
        schema=source_schema,
        cutoff=cutoff * NS,
        sources=[dict(synthetic=True)],
    )
    source = observations.Source(store, pin, role="train")
    plan = dict(
        stream="ticks",
        context=context,
        max_history_ns=30 * NS,
        lifecycle=["listed"],
        lifecycle_origins=bool(lifecycle),
        relative_features=["price"],
        targets=[
            dict(name="return", kind="endpoint_log_return", horizon_ns=5 * NS, feature="price"),
        ],
    )
    db = source.index(tmp_path / "index.sqlite")
    try:
        samples = list(sparse.samples(db, source, plan))
    finally:
        db.close()
    return samples, plan, source


def _relative_prices(row):
    # sparse.schema orders value/price before the observation-process columns.
    return [frame[0] for frame in row["values"]]


@pytest.mark.parametrize("ids", [("late-old", "current"), ("current", "late-old")])
def test_pit_late_old_trigger_uses_unique_exact_decision_price_independent_of_id_order(
    tmp_path, ids
):
    old_id, current_id = ids
    rows, plan, source = _materialize(
        tmp_path,
        [(old_id, 1, 10, 10.0), (current_id, 10, 10, 20.0), ("endpoint", 15, 15, 30.0)],
    )
    decision_rows = [row for row in rows if row["decision_ns"] == 10 * NS]
    assert {row["id"] for row in decision_rows} == {
        f"event:ticks:{old_id}", f"event:ticks:{current_id}"
    }
    for row in decision_rows:
        assert _relative_prices(row) == [pytest.approx(math.log(10 / 20)), 0.0]
        assert row["target_status"] == ["observed"]
        assert row["targets"] == [pytest.approx(math.log(30 / 20))]
        assert row["target_details"][0]["price_provenance"]["base"]["evidence"] == [
            _pin(current_id)
        ]
    representation = sparse.schema(source, plan)["representation"]
    assert representation["name"] == "sparse-event-sequence"
    assert representation["version"] == "3"


@pytest.mark.parametrize(
    ("decision_events", "reason"),
    [([], "no_decision_price"), ([("hidden", 10, 11, 20.0)], "origin_not_available_at_decision")],
)
def test_pit_does_not_invent_a_relative_basis_without_a_qualified_exact_event(
    tmp_path, decision_events, reason
):
    rows, _, _ = _materialize(
        tmp_path,
        [("trigger", 1, 10, 10.0), *decision_events, ("endpoint", 15, 15, 30.0)],
    )
    trigger = next(row for row in rows if row["id"] == "event:ticks:trigger")
    assert _relative_prices(trigger) == [None, None]
    assert trigger["target_status"] == ["unsupported"]
    assert trigger["target_details"][0]["reason"] == reason


def test_ambiguous_exact_base_masks_relative_features_and_price_label(tmp_path):
    rows, _, _ = _materialize(
        tmp_path,
        [
            ("trigger", 1, 10, 10.0),
            ("base-a", 10, 10, 20.0),
            ("base-b", 10, 10, 25.0),
            ("endpoint", 15, 15, 30.0),
        ],
    )
    ambiguous = next(row for row in rows if row["id"] == "event:ticks:trigger")
    assert _relative_prices(ambiguous) == [None, None]
    assert ambiguous["target_status"] == ["unsupported"]
    assert ambiguous["target_details"][0]["reason"] == "ambiguous_origin"



@pytest.mark.parametrize("base_value", [None, 0.0, -1.0])
def test_missing_or_nonpositive_exact_base_masks_relative_features_and_price_label(
    tmp_path, base_value
):
    rows, _, _ = _materialize(
        tmp_path,
        [("trigger", 1, 10, 10.0), ("invalid-base", 10, 10, base_value), ("endpoint", 15, 15, 30.0)],
    )
    invalid = next(row for row in rows if row["id"] == "event:ticks:trigger")
    assert _relative_prices(invalid) == [None, None]
    assert invalid["target_status"] == ["unsupported"]
    assert invalid["target_details"][0]["reason"] == "no_origin_value"


def test_lifecycle_only_frame_keeps_price_mask_and_does_not_claim_unused_price_lineage(tmp_path):
    rows, _, _ = _materialize(
        tmp_path,
        [("before", 8, 8, 10.0), ("decision-price", 10, 10, 20.0)],
        lifecycle=[("listed", 10, 10)],
        context=1,
    )
    lifecycle = next(row for row in rows if row["id"] == "lifecycle:ticks:listed")
    assert lifecycle["observation_origin"]["kind"] == "lifecycle"
    # A lifecycle-only frame contains no price value and uses no normalization basis.
    assert _relative_prices(lifecycle) == [None]
    assert lifecycle["target_status"] == ["unsupported"]
    assert lifecycle["target_details"][0]["reason"] == "no_origin_value"
    assert _pin("decision-price") not in {
        dependency["artifact_sha"] for dependency in lifecycle["input_dependencies"]
    }


def test_historical_replay_can_use_late_exact_base_known_by_cutoff_with_lineage(tmp_path):
    rows, _, _ = _materialize(
        tmp_path,
        [("trigger", 1, 2, 10.0), ("late-base", 10, 20, 20.0), ("endpoint", 15, 25, 30.0)],
        availability="historical_replay",
        cutoff=30,
    )
    trigger = next(row for row in rows if row["id"] == "event:ticks:late-base")
    assert _relative_prices(trigger) == [pytest.approx(math.log(10 / 20)), 0.0]
    assert trigger["target_status"] == ["observed"]
    assert trigger["target_details"][0]["price_provenance"]["base"]["evidence"] == [
        _pin("late-base")
    ]
    assert _pin("late-base") in {
        dependency["artifact_sha"] for dependency in trigger["input_dependencies"]
    }
