import hashlib
import math

import pytest

from memecoin_experience import observations, sparse
from memecoin_experience.schema import checked_samples

NS = 10**9


def make_source(tmp_path, events, *, availability="historical_replay", cutoff=50 * NS):
    entity = "test/market/decision-price"
    evidence = ["a" * 64]
    rows = [
        dict(
            kind="enrollment",
            entity=entity,
            start_ns=0,
            end_ns=40 * NS,
            available_ns=0,
            evidence=evidence,
        ),
        *[
            dict(
                kind="event",
                entity=entity,
                stream="ticks",
                id=identity,
                event_ns=t * NS,
                available_ns=available * NS,
                values=[value],
                evidence=[hashlib.sha256(identity.encode()).hexdigest()],
            )
            for identity, t, available, value in events
        ],
        dict(
            kind="support",
            entity=entity,
            stream="ticks",
            start_ns=0,
            end_ns=40 * NS,
            available_ns=cutoff,
            status="complete" if cutoff >= 40 * NS else "unknown",
            evidence=evidence,
        ),
    ]
    schema = dict(
        version="market-observations-v1",
        role="train",
        availability=availability,
        streams={
            "ticks": dict(
                process="synthetic ticks",
                features=[dict(name="price", unit="USD", modality="price")],
            )
        },
    )
    store = tmp_path / "observations"
    pin = observations.publish(
        store,
        sorted(rows, key=observations.key),
        schema=schema,
        cutoff=cutoff,
        sources=[dict(synthetic=True)],
    )
    return observations.Source(store, pin, role="train")


def materialize(tmp_path, events, *, availability="historical_replay", cutoff=50 * NS):
    source = make_source(tmp_path, events, availability=availability, cutoff=cutoff)
    plan = dict(
        stream="ticks",
        context=2,
        max_history_ns=40 * NS,
        lifecycle=[],
        lifecycle_origins=False,
        relative_features=[],
        targets=[
            dict(name="return", kind="endpoint_log_return", horizon_ns=10 * NS, feature="price"),
            dict(name="count", kind="observed_process_count", horizon_ns=10 * NS, feature=None),
        ],
    )
    db = source.index(tmp_path / "index.sqlite")
    try:
        rows = list(sparse.samples(db, source, plan))
    finally:
        db.close()
    checked = list(checked_samples(rows, sparse.schema(source, plan), cutoff))
    return rows, checked


@pytest.mark.parametrize(
    "ids",
    [("late-old", "current"), ("current", "late-old")],
)
@pytest.mark.parametrize("availability", ["historical_replay", "point_in_time"])
def test_decision_price_is_independent_of_arrival_anchor_and_id_order(
    tmp_path, ids, availability
):
    old_id, current_id = ids
    rows, _ = materialize(
        tmp_path,
        [
            (old_id, 1, 10, 10.0),
            (current_id, 10, 10, 20.0),
                ("endpoint", 20, 25, 40.0),
        ],
        availability=availability,
    )
    decision_rows = [row for row in rows if row["decision_ns"] == 10 * NS]
    assert len(decision_rows) == (2 if availability == "point_in_time" else 1)
    assert all(row["targets"] == [pytest.approx(math.log(2)), 1] for row in decision_rows), decision_rows
    assert all(row["target_status"] == ["observed", "observed"] for row in decision_rows)
    for row in decision_rows:
        detail = row["target_details"][0]["price_provenance"]
        assert detail["base"] == dict(
            event_ns=10 * NS,
            available_ns=10 * NS,
            evidence=[hashlib.sha256(current_id.encode()).hexdigest()],
        )
        assert detail["endpoint"]["event_ns"] == 20 * NS
        assert detail["endpoint"]["evidence"] == [
            hashlib.sha256(b"endpoint").hexdigest()
        ]
        assert row["target_available_ns"][0] == 25 * NS
        assert row["target_captures"][0] == sorted(
            [hashlib.sha256(current_id.encode()).hexdigest(), hashlib.sha256(b"endpoint").hexdigest()]
        )


def test_origin_after_decision_is_masked_but_count_is_retained(tmp_path):
    rows, _ = materialize(
        tmp_path,
        [
            ("trigger", 1, 3, 10.0),
            ("late-decision-price", 3, 4, 20.0),
            ("endpoint", 13, 13, 30.0),
        ],
        availability="point_in_time",
    )
    row = next(row for row in rows if row["decision_ns"] == 3 * NS)
    assert row["target_status"] == ["unsupported", "observed"]
    assert row["targets"] == [None, 1]
    assert row["target_details"][0]["reason"] == "origin_not_available_at_decision"


@pytest.mark.parametrize("value", [None, 0.0, -1.0])
def test_null_and_nonpositive_decision_price_is_masked(tmp_path, value):
    rows, _ = materialize(
        tmp_path,
        [("trigger", 1, 3, 10.0), ("decision", 3, 3, value), ("endpoint", 13, 13, 20.0)],
        availability="point_in_time",
    )
    decision_rows = [row for row in rows if row["decision_ns"] == 3 * NS]
    assert decision_rows
    assert all(row["target_status"] == ["unsupported", "observed"] for row in decision_rows)
    assert all(row["target_details"][0]["reason"] == "no_origin_value" for row in decision_rows)


def test_ambiguous_origin_and_endpoint_fail_closed_for_price_only(tmp_path):
    rows, _ = materialize(
        tmp_path,
        [
            ("trigger", 3, 3, 10.0),
            ("origin-b", 3, 3, 12.0),
            ("endpoint-a", 13, 13, 20.0),
            ("endpoint-b", 13, 13, 22.0),
        ],
        availability="point_in_time",
    )
    row = next(row for row in rows if row["decision_ns"] == 3 * NS)
    assert row["target_status"] == ["unsupported", "observed"]
    assert row["target_details"][0]["reason"] == "ambiguous_origin"

    rows, _ = materialize(
        tmp_path / "endpoint",
        [
            ("origin", 3, 3, 10.0),
            ("endpoint-a", 13, 13, 20.0),
            ("endpoint-b", 13, 13, 22.0),
        ],
        availability="point_in_time",
    )
    row = next(row for row in rows if row["decision_ns"] == 3 * NS)
    assert row["target_status"] == ["unsupported", "observed"]
    assert row["target_details"][0]["reason"] == "ambiguous_endpoint"


def test_nonpositive_endpoint_and_horizon_past_cutoff_are_masked(tmp_path):
    rows, _ = materialize(
        tmp_path,
        [("origin", 3, 3, 10.0), ("endpoint", 13, 13, 0.0)],
        availability="point_in_time",
    )
    row = next(row for row in rows if row["decision_ns"] == 3 * NS)
    assert row["target_status"][0] == "unsupported"
    assert row["target_details"][0]["reason"] == "no_endpoint_value"

    rows, _ = materialize(
        tmp_path / "cutoff",
        [("origin", 3, 3, 10.0)],
        availability="point_in_time",
        cutoff=12 * NS,
    )
    row = next(row for row in rows if row["decision_ns"] == 3 * NS)
    assert row["target_status"][0] == "pending"
    assert row["target_details"][0]["reason"] == "fit_cutoff"
