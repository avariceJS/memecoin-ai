"""Persisted consumer acceptance for sparse decision-time price eligibility.

All inputs here are synthetic and flow through the public observation publisher,
sparse materializer, immutable Snapshot reader, and persisted numeric mask audit.
"""

import hashlib
import math
import struct

import pytest

from memecoin_experience import observations, sparse
from memecoin_experience.schema import membership
from memecoin_experience.storage import Snapshot

NS = 10**9


def evidence(identity):
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def contract(availability="point_in_time"):
    return {
        "version": "market-observations-v1",
        "role": "train",
        "availability": availability,
        "streams": {
            "ticks": {
                "process": "synthetic qualified ticks",
                "features": [{"name": "price", "unit": "USD", "modality": "price"}],
            }
        },
    }


def plan(horizon=10 * NS):
    return {
        "stream": "ticks",
        "context": 3,
        "max_history_ns": 100 * NS,
        "lifecycle": ["birth", "terminal"],
        "lifecycle_origins": True,
        "relative_features": [],
        "targets": [
            {"name": "return", "kind": "endpoint_log_return", "horizon_ns": horizon, "feature": "price"},
            {"name": "count", "kind": "observed_process_count", "horizon_ns": horizon, "feature": None},
        ],
    }


def event(at, available, value, identity):
    return {
        "kind": "event",
        "entity": "test/market/A",
        "stream": "ticks",
        "id": identity,
        "event_ns": at * NS,
        "available_ns": available * NS,
        "values": [value],
        "evidence": [evidence("event:" + identity)],
    }


def common_facts(*, end=50, support_status="complete", support_available=None, include_support=True):
    if support_available is None:
        support_available = end
    rows = [
        {
            "kind": "enrollment",
            "entity": "test/market/A",
            "start_ns": 0,
            "end_ns": end * NS,
            "available_ns": 0,
            "evidence": [evidence("enrollment")],
        }
    ]
    if include_support:
        rows.append(
            {
                "kind": "support",
                "entity": "test/market/A",
                "stream": "ticks",
                "start_ns": 0,
                "end_ns": end * NS,
                "available_ns": support_available * NS,
                "status": support_status,
                "evidence": [evidence("support")],
            }
        )
    return rows


def persisted(
    tmp_path,
    events,
    *,
    availability="point_in_time",
    cutoff=50,
    p=None,
    support_status="complete",
    include_support=True,
    end=50,
    extras=(),
):
    facts = common_facts(
        end=end,
        support_status=support_status if cutoff >= 50 else "unknown",
        support_available=min(end, cutoff),
        include_support=include_support,
    ) + list(events) + list(extras)
    facts.sort(key=observations.key)
    source_store = tmp_path / "observations"
    source_pin = observations.publish(
        source_store,
        facts,
        schema=contract(availability),
        cutoff=cutoff * NS,
        sources=[{"synthetic": True}],
        chunk=4096,
    )
    built = sparse.materialize(
        source_store,
        source_pin,
        tmp_path / "experience",
        plan=p or plan(),
        chunk=4096,
    )
    snapshot = Snapshot(tmp_path / "experience", built["snapshot"])
    partitions = [snapshot.partition(index) for index in range(len(snapshot.parts))]
    rows = [row for _, part_rows in partitions for row in part_rows]
    exact = masked = 0
    for partition, part_rows in partitions:
        numeric_targets = struct.unpack(
            f"<{2 * len(part_rows)}d", (partition / "y.f64").read_bytes()
        )
        for row_index, row in enumerate(part_rows):
            for target_index, status in enumerate(row["target_status"]):
                value = numeric_targets[row_index * 2 + target_index]
                if status == "observed":
                    assert math.isfinite(value)
                    exact += 1
                else:
                    assert math.isnan(value)
                    masked += 1
    audit = {"rows": len(rows), "exact_target_cells": exact, "masked_target_cells": masked}
    assert len(rows) == snapshot.manifest["decisions"]
    split = dict(train_start_s=0, train_end_s=100, validation_end_s=110, test_end_s=120)
    for row in rows:
        role, mask = membership(row, split, snapshot.schema)
        assert role == "train"
        assert mask == [status == "observed" for status in row["target_status"]]
    return snapshot, partitions, rows, audit


def at_decision(rows, decision=10 * NS):
    return [row for row in rows if row["decision_ns"] == decision]


def persisted_target(partitions, row_index, target_index=0):
    for partition, rows in partitions:
        if row_index < len(rows):
            values = struct.unpack(f"<{2 * len(rows)}d", (partition / "y.f64").read_bytes())
            return values[row_index * 2 + target_index]
        row_index -= len(rows)
    raise IndexError("persisted target row")


def test_late_origin_and_current_origin_share_the_unique_decision_base(tmp_path):
    # The t=1 fact arrives at decision 10 alongside the exact t=10 price base.
    rows_in = [event(1, 10, 10.0, "late"), event(10, 10, 20.0, "current"), event(20, 20, 40.0, "endpoint")]
    snapshot, partitions, rows, audit = persisted(tmp_path, rows_in, cutoff=50)

    retained = at_decision(rows)
    assert {row["id"] for row in retained} == {"event:ticks:late", "event:ticks:current"}
    for row in retained:
        assert row["target_status"][0] == "observed"
        assert persisted_target(partitions, rows.index(row)) == pytest.approx(math.log(2.0))
        assert row["target_details"][0]["reason"] == "exact_observed_endpoint"
        assert row["target_available_ns"][0] == 20 * NS
        assert row["target_captures"][0] == sorted(
            [evidence("event:current"), evidence("event:endpoint")]
        )
        assert row["target_maturity_ns"][0] == 20 * NS
        assert {dep["event_ns"] for dep in row["input_dependencies"]} >= {1 * NS, 10 * NS}

    late_index = next(i for i, row in enumerate(rows) if row["id"] == "event:ticks:late")
    current_index = next(i for i, row in enumerate(rows) if row["id"] == "event:ticks:current")
    assert persisted_target(partitions, late_index) == pytest.approx(math.log(2.0))
    assert persisted_target(partitions, current_index) == pytest.approx(math.log(2.0))
    split = dict(train_start_s=0, train_end_s=100, validation_end_s=110, test_end_s=120)
    assert all(membership(row, split, snapshot.schema)[1][0] for row in retained)
    assert audit["exact_target_cells"] >= 2


def test_late_only_origin_without_exact_decision_price_is_retained_and_masked(tmp_path):
    snapshot, partitions, rows, audit = persisted(
        tmp_path,
        [event(1, 10, 10.0, "late"), event(20, 20, 40.0, "endpoint")],
    )
    row = next(row for row in rows if row["id"] == "event:ticks:late")
    assert row["decision_ns"] == 10 * NS
    assert row["target_status"][0] == "unsupported"
    assert row["target_available_ns"][0] is None
    assert row["target_captures"][0] == []
    assert row["target_details"][0]["reason"] == "no_decision_price"
    index = rows.index(row)
    assert math.isnan(persisted_target(partitions, index))
    assert snapshot.manifest["decisions"] == len(rows)
    assert audit["masked_target_cells"] >= 1


@pytest.mark.parametrize("ids", [("a", "b"), ("z", "a")])
def test_conflicting_distinct_decision_prices_fail_closed_independent_of_id_order(tmp_path, ids):
    snapshot, partitions, rows, audit = persisted(
        tmp_path,
        [
            event(1, 10, 10.0, "late"),
            event(10, 10, 20.0, ids[0]),
            event(10, 10, 25.0, ids[1]),
            event(20, 20, 40.0, "endpoint"),
        ],
    )
    retained = at_decision(rows)
    assert len(retained) == 3
    for row in retained:
        assert row["target_status"][0] == "unsupported"
        assert row["target_available_ns"][0] is None
        assert row["target_captures"][0] == []
        assert row["target_details"][0]["reason"] == "ambiguous_origin"
    masked_indices = [rows.index(row) for row in retained]
    assert all(math.isnan(persisted_target(partitions, i)) for i in masked_indices)
    assert snapshot.manifest["decisions"] == len(rows)
    assert audit["masked_target_cells"] >= len(retained)


def test_ambiguous_endpoint_fails_closed_and_preserves_the_origins(tmp_path):
    snapshot, partitions, rows, audit = persisted(
        tmp_path,
        [
            event(1, 10, 10.0, "late"),
            event(10, 10, 20.0, "current"),
            event(20, 20, 40.0, "end-a"),
            event(20, 20, 50.0, "end-b"),
        ],
    )
    retained = at_decision(rows)
    assert len(retained) == 2
    for row in retained:
        assert row["target_status"][0] == "unsupported"
        assert row["target_available_ns"][0] is None
        assert row["target_captures"][0] == []
        assert row["target_details"][0]["reason"] == "ambiguous_endpoint"
    assert all(math.isnan(persisted_target(partitions, rows.index(row))) for row in retained)
    assert snapshot.manifest["decisions"] == len(rows)
    assert audit["masked_target_cells"] >= len(retained)


@pytest.mark.parametrize(
    "bad_origin,bad_endpoint,expected_reason",
    [
        (None, 40.0, "no_origin_value"),
        (0.0, 40.0, "no_origin_value"),
        (20.0, None, "no_endpoint_value"),
        (20.0, 0.0, "no_endpoint_value"),
    ],
)
def test_null_or_nonpositive_price_facts_are_masked_with_captures_empty(
    tmp_path, bad_origin, bad_endpoint, expected_reason
):
    snapshot, partitions, rows, audit = persisted(
        tmp_path,
        [
            event(1, 10, 10.0, "late"),
            event(10, 10, bad_origin, "current"),
            event(20, 20, bad_endpoint, "endpoint"),
        ],
    )
    retained = at_decision(rows)
    assert len(retained) == 2
    for row in retained:
        assert row["target_status"][0] == "unsupported"
        assert row["target_available_ns"][0] is None
        assert row["target_captures"][0] == []
        assert row["target_details"][0]["reason"] == expected_reason
    assert all(math.isnan(persisted_target(partitions, rows.index(row))) for row in retained)
    assert snapshot.manifest["decisions"] == len(rows)
    assert audit["masked_target_cells"] >= len(retained)


def test_decision_base_known_only_after_decision_is_not_used(tmp_path):
    snapshot, partitions, rows, audit = persisted(
        tmp_path,
        [
            event(1, 10, 10.0, "late"),
            event(10, 11, 20.0, "base-arrives-late"),
            event(20, 21, 40.0, "endpoint-arrives-late"),
        ],
        cutoff=21,
    )
    row = next(row for row in rows if row["id"] == "event:ticks:late")
    assert row["decision_ns"] == 10 * NS
    assert row["target_status"][0] == "unsupported"
    assert row["target_available_ns"][0] is None
    assert row["target_captures"][0] == []
    assert row["target_details"][0]["reason"] == "origin_not_available_at_decision"
    assert math.isnan(persisted_target(partitions, rows.index(row)))
    assert snapshot.manifest["decisions"] == len(rows)
    assert audit["masked_target_cells"] >= 1


def test_endpoint_beyond_source_cutoff_is_pending_despite_valid_decision_base(tmp_path):
    # The t=20 endpoint arrives at t=20, after this source's t=19 cutoff. The
    # exact t=10 base is available at the t=10 decision and remains in the source.
    snapshot, partitions, rows, _audit = persisted(
        tmp_path,
        [event(10, 10, 20.0, "current")],
        cutoff=19,
    )
    row = next(row for row in rows if row["id"] == "event:ticks:current")
    assert row["decision_ns"] == 10 * NS
    assert row["target_status"][0] == "pending"
    assert row["target_maturity_ns"][0] == 20 * NS
    assert row["target_details"][0]["reason"] == "fit_cutoff"
    assert row["target_available_ns"][0] is None
    assert row["target_captures"][0] == []
    assert any(
        dependency["artifact_sha"] == evidence("event:current")
        and dependency["event_ns"] == 10 * NS
        and dependency["available_ns"] == 10 * NS
        for dependency in row["input_dependencies"]
    )
    assert math.isnan(persisted_target(partitions, rows.index(row)))
    assert snapshot.manifest["decisions"] == len(rows)


def test_endpoint_arriving_after_cutoff_is_rejected_at_source_publication(tmp_path):
    with pytest.raises(ValueError, match="observation_availability"):
        persisted(
            tmp_path,
            [event(10, 10, 20.0, "current"), event(20, 22, 40.0, "late-endpoint")],
            cutoff=21,
        )


@pytest.mark.parametrize(
    "support_status,expected_count_status,expected_reason",
    [
        ("complete", "observed", "complete_observation_process"),
        ("unknown", "censored", "unknown_support"),
        ("failed", "censored", "source_failed"),
        ("stopped", "censored", "source_stopped"),
        ("unsupported", "censored", "unsupported_process"),
    ],
)
def test_count_support_and_price_mask_are_independent_in_persisted_rows(
    tmp_path, support_status, expected_count_status, expected_reason
):
    snapshot, partitions, rows, audit = persisted(
        tmp_path,
        [event(10, 10, 20.0, "current")],
        p=plan(horizon=5 * NS),
        cutoff=50,
        support_status=support_status,
    )
    row = next(row for row in rows if row["id"] == "event:ticks:current")
    assert row["target_status"][0] == "missing_future"
    assert row["target_captures"][0] == []
    assert row["target_status"][1] == expected_count_status
    if support_status == "complete":
        assert persisted_target(partitions, rows.index(row), target_index=1) == 0
        assert row["target_available_ns"][1] == 50 * NS
        assert row["target_captures"][1]
        assert row["target_details"][1]["reason"] == expected_reason
    else:
        assert math.isnan(persisted_target(partitions, rows.index(row), target_index=1))
        assert row["target_available_ns"][1] is None
        assert row["target_captures"][1] == []
        assert row["target_details"][1]["lower_bound"] == 0
        assert expected_reason in row["target_details"][1]["support_reasons"]
    assert math.isnan(persisted_target(partitions, rows.index(row), target_index=0))
    assert snapshot.manifest["decisions"] == len(rows)
    assert audit["masked_target_cells"] >= 1


def test_unasserted_count_window_remains_a_zero_lower_bound_not_a_zero_count(tmp_path):
    snapshot, partitions, rows, _audit = persisted(
        tmp_path,
        [event(10, 10, 20.0, "current")],
        p=plan(horizon=5 * NS),
        include_support=False,
        extras=[
            {
                "kind": "support",
                "entity": "test/market/A",
                "stream": "ticks",
                "start_ns": 0,
                "end_ns": 10 * NS,
                "available_ns": 10 * NS,
                "status": "complete",
                "evidence": [evidence("support:pre-window")],
            }
        ],
    )
    row = next(row for row in rows if row["id"] == "event:ticks:current")
    assert row["target_status"][1] == "censored"
    assert row["target_details"][1]["lower_bound"] == 0
    assert row["target_details"][1]["support_reasons"] == ["unasserted_gap"]
    assert math.isnan(persisted_target(partitions, rows.index(row), target_index=1))
    assert snapshot.manifest["decisions"] == len(rows)


def test_enrollment_boundary_censoring_keeps_lifecycle_origin_evidence(tmp_path):
    birth = {
        "kind": "lifecycle",
        "entity": "test/market/A",
        "id": "birth",
        "event_ns": 5 * NS,
        "available_ns": 5 * NS,
        "transition": "birth",
        "evidence": [evidence("lifecycle:birth")],
    }
    snapshot, partitions, rows, _audit = persisted(
        tmp_path,
        [event(8, 8, 20.0, "current")],
        p=plan(horizon=5 * NS),
        end=9,
        extras=[birth],
    )
    lifecycle = next(row for row in rows if row["id"] == "lifecycle:ticks:birth")
    assert lifecycle["observation_origin"]["kind"] == "lifecycle"
    assert any(
        dep["artifact_sha"] == evidence("lifecycle:birth")
        for dep in lifecycle["input_dependencies"]
    )
    assert lifecycle["target_details"][0]["reason"] == "enrollment_boundary"
    assert lifecycle["target_status"][1] == "censored"
    assert lifecycle["target_details"][1]["reason"] == "enrollment_boundary"
    current = next(row for row in rows if row["id"] == "event:ticks:current")
    assert current["target_details"][1]["reason"] == "enrollment_boundary"
    assert math.isnan(persisted_target(partitions, rows.index(current), target_index=1))
    assert snapshot.manifest["decisions"] == len(rows)


def test_historical_replay_retains_late_fact_with_separate_event_and_knowledge_clocks(tmp_path):
    snapshot, _partition, rows, _audit = persisted(
        tmp_path,
        [event(1, 10, 10.0, "late"), event(10, 10, 20.0, "current"), event(20, 20, 40.0, "endpoint")],
        availability="historical_replay",
    )
    late = next(row for row in rows if row["id"] == "event:ticks:late")
    assert late["decision_ns"] == 1 * NS
    assert late["observation_origin"] == {
        "kind": "event",
        "stream": "ticks",
        "event_ns": 1 * NS,
        "process": "synthetic qualified ticks",
    }
    assert late["input_available_ns"] == 10 * NS
    assert late["input_available_ns"] > late["decision_ns"]
    assert snapshot.manifest["decisions"] == len(rows)
