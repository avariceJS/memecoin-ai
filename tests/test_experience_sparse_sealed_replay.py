"""Synthetic regressions for sealed historical replay and strict PIT isolation.

These cases guard against applying PIT decision-availability rules to sealed replay bases.
"""

import hashlib
import math
import struct

import pytest

from memecoin_experience import observations, sparse
from memecoin_experience.schema import checked_samples
from memecoin_experience.storage import Snapshot

NS = 10**9
ENTITY = "test/market/sealed-replay"
CUTOFF = 50 * NS


def _evidence(identity):
    return hashlib.sha256(identity.encode()).hexdigest()


def _source(tmp_path, events, *, availability="historical_replay", cutoff=CUTOFF, end=40 * NS):
    evidence = [_evidence("support")]
    rows = [
        dict(
            kind="enrollment",
            entity=ENTITY,
            start_ns=0,
            end_ns=end,
            available_ns=0,
            evidence=evidence,
        ),
        *[
            dict(
                kind="event",
                entity=ENTITY,
                stream="ticks",
                id=identity,
                event_ns=event * NS,
                available_ns=available * NS,
                values=[value],
                evidence=[_evidence(identity)],
            )
            for identity, event, available, value in events
        ],
        dict(
            kind="support",
            entity=ENTITY,
            stream="ticks",
            start_ns=0,
            end_ns=end,
            available_ns=cutoff,
            status="complete" if cutoff >= end else "unknown",
            evidence=evidence,
        ),
    ]
    contract = dict(
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
        schema=contract,
        cutoff=cutoff,
        sources=[dict(synthetic=True)],
    )
    return observations.Source(store, pin, role="train")


def _plan(end_horizon=10 * NS):
    return dict(
        stream="ticks",
        context=2,
        max_history_ns=40 * NS,
        lifecycle=[],
        lifecycle_origins=False,
        relative_features=[],
        targets=[
            dict(name="return", kind="endpoint_log_return", horizon_ns=end_horizon, feature="price"),
            dict(name="count", kind="observed_process_count", horizon_ns=end_horizon, feature=None),
        ],
    )


def _read(
    tmp_path,
    events,
    *,
    availability="historical_replay",
    cutoff=CUTOFF,
    end=40 * NS,
    horizon=10 * NS,
):
    source = _source(tmp_path, events, availability=availability, cutoff=cutoff, end=end)
    plan = _plan(horizon)
    index = source.index(tmp_path / "index.sqlite")
    try:
        rows = list(sparse.samples(index, source, plan))
    finally:
        index.close()
    checked = list(checked_samples(rows, sparse.schema(source, plan), cutoff))
    result = sparse.materialize(
        source.store, source.pin, tmp_path / "materialized", plan=plan, chunk=1
    )
    snapshot = Snapshot(tmp_path / "materialized", result["snapshot"])
    persisted = []
    for part in range(len(snapshot.parts)):
        path, metadata = snapshot.partition(part)
        values = struct.unpack(f"<{2 * len(metadata)}d", (path / "y.f64").read_bytes())
        for index, row in enumerate(metadata):
            targets = values[2 * index : 2 * index + 2]
            for value, status in zip(targets, row["target_status"], strict=True):
                assert math.isfinite(value) if status == "observed" else math.isnan(value)
            persisted.append(dict(row, targets=[None if math.isnan(v) else v for v in targets]))
    return source, rows, checked, snapshot, persisted


def _at(rows, decision):
    return [row for row in rows if row["decision_ns"] == decision * NS]


@pytest.mark.parametrize("base_available", [20, 50])
def test_late_exact_base_and_endpoint_replay_only_at_sealed_cutoff(tmp_path, base_available):
    events = [
        ("late-base", 3, base_available, 10.0),
        ("exact-endpoint", 13, 50, 20.0),
    ]
    _, rows, checked, snapshot, persisted = _read(tmp_path, events)
    for collection in (rows, checked, persisted):
        row = next(row for row in _at(collection, 3) if row["observation_origin"]["event_ns"] == 3 * NS)
        assert row["target_status"] == ["observed", "observed"]
        assert row["targets"] == pytest.approx([math.log(2), 1])
        # Complete support is only certified by the support fact sealed at cutoff.
        assert row["target_available_ns"] == [CUTOFF, CUTOFF]
        detail = row["target_details"][0]
        assert detail["reason"] == "exact_observed_endpoint"
        assert detail["price_provenance"] == dict(
            base=dict(
                event_ns=3 * NS,
                available_ns=base_available * NS,
                evidence=[_evidence("late-base")],
            ),
            endpoint=dict(
                event_ns=13 * NS,
                available_ns=CUTOFF,
                evidence=[_evidence("exact-endpoint")],
            ),
        )
        assert row["target_captures"][0] == sorted(
            [_evidence("late-base"), _evidence("exact-endpoint")]
        )
    assert snapshot.manifest["decisions"] == len(rows)
    assert snapshot.schema["availability"] == "historical_replay"
    assert len(rows) == len(checked) == len(persisted) == 2
    assert rows[0]["input_available_ns"] > rows[0]["decision_ns"]
    pit_claim = dict(snapshot.schema, availability="point_in_time")
    with pytest.raises(ValueError, match="experience_sample_contract"):
        list(checked_samples(rows, pit_claim, CUTOFF))


def test_pit_rejects_late_base_and_keeps_input_clocks_point_in_time(tmp_path):
    _, rows, checked, _, _ = _read(
        tmp_path,
        [("old-trigger", 3, 5, 10.0), ("late-exact-base", 5, 6, 20.0), ("endpoint", 15, 16, 40.0)],
        availability="point_in_time",
    )
    for collection in (rows, checked):
        row = next(row for row in _at(collection, 5) if row["observation_origin"]["event_ns"] == 3 * NS)
        assert row["target_status"][0] == "unsupported"
        assert row["targets"][0] is None
        assert row["target_details"][0]["reason"] == "origin_not_available_at_decision"
        assert all(dep["available_ns"] <= row["decision_ns"] for dep in row["input_dependencies"])
        assert row["input_available_ns"] <= row["decision_ns"]
        assert row["target_available_ns"][0] is None


def test_endpoint_at_cutoff_is_included_and_horizon_after_cutoff_is_pending(tmp_path):
    _, rows, checked, _, _ = _read(
        tmp_path,
        [("base", 3, 3, 10.0), ("endpoint", 13, 13, 20.0)],
        cutoff=13 * NS,
    )
    for collection in (rows, checked):
        row = next(row for row in _at(collection, 3) if row["observation_origin"]["event_ns"] == 3 * NS)
        assert row["target_status"] == ["observed", "censored"]
        assert row["target_available_ns"][0] == 13 * NS
        assert row["targets"][0] == pytest.approx(math.log(2))

    _, rows, checked, _, _ = _read(
        tmp_path / "past-cutoff",
        [("base", 3, 3, 10.0), ("endpoint", 13, 13, 20.0)],
        cutoff=13 * NS,
        horizon=11 * NS,
    )
    for collection in (rows, checked):
        row = next(row for row in _at(collection, 3) if row["observation_origin"]["event_ns"] == 3 * NS)
        assert row["target_status"] == ["pending", "pending"]
        assert row["targets"] == [None, None]
        assert row["target_details"][0]["reason"] == "fit_cutoff"


@pytest.mark.parametrize(
    "events,reason",
    [
        ([ ("base-a", 3, 20, 10.0), ("base-b", 3, 25, 11.0), ("end", 13, 50, 20.0)], "ambiguous_origin"),
        ([ ("base", 3, 20, 10.0), ("end-a", 13, 30, 20.0), ("end-b", 13, 50, 21.0)], "ambiguous_endpoint"),
        ([ ("base", 3, 3, 10.0), ("near-before", 12, 12, 19.0), ("near-after", 14, 14, 21.0)], "no_exact_endpoint_unknown_return"),
    ],
)
def test_ambiguous_or_nearby_prices_never_substitute_for_unique_exact_points(tmp_path, events, reason):
    _, rows, checked, _, _ = _read(tmp_path, events)
    for collection in (rows, checked):
        row = next(row for row in _at(collection, 3) if row["observation_origin"]["event_ns"] == 3 * NS)
        expected_status = "missing_future" if reason == "no_exact_endpoint_unknown_return" else "unsupported"
        assert row["target_status"][0] == expected_status
        assert row["targets"][0] is None
        assert row["target_details"][0]["reason"] == reason
        assert row["target_details"][0].get("price_provenance") is None


@pytest.mark.parametrize(
    "base_value,endpoint_value,reason",
    [
        (None, 20.0, "no_origin_value"),
        (0.0, 20.0, "no_origin_value"),
        (-1.0, 20.0, "no_origin_value"),
        (10.0, None, "no_endpoint_value"),
        (10.0, 0.0, "no_endpoint_value"),
        (10.0, -1.0, "no_endpoint_value"),
    ],
)
def test_missing_null_and_nonpositive_exact_prices_remain_masked(
    tmp_path, base_value, endpoint_value, reason
):
    _, rows, checked, _, _ = _read(
        tmp_path,
        [("base", 3, 3, base_value), ("endpoint", 13, 13, endpoint_value)],
    )
    for collection in (rows, checked):
        row = next(row for row in _at(collection, 3) if row["observation_origin"]["event_ns"] == 3 * NS)
        assert row["target_status"][0] == "unsupported"
        assert row["targets"][0] is None
        assert row["target_details"][0]["reason"] == reason


def test_candidate_denominators_count_and_enrollment_censoring_are_retained(tmp_path):
    _, rows, checked, _, _ = _read(
        tmp_path,
        [("base", 3, 3, 10.0), ("one-event", 8, 8, 11.0)],
        end=10 * NS,
    )
    assert len(rows) == len(checked) == 2
    row = next(row for row in _at(rows, 3) if row["observation_origin"]["event_ns"] == 3 * NS)
    assert row["target_status"] == ["censored", "censored"]
    assert row["targets"] == [None, None]
    assert row["target_details"][0]["reason"] == "enrollment_boundary"
    assert row["target_details"][1]["lower_bound"] == 1
