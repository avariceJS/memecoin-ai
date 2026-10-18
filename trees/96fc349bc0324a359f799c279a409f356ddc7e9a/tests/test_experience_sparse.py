import copy
import math
import sqlite3

import pytest

from memecoin_experience import observations, sparse
from memecoin_experience.schema import checked_samples, membership
from memecoin_experience.storage import Snapshot, object_path

NS = 10**9


def contract(*, availability="historical_replay", role="train"):
    return dict(
        version="market-observations-v1",
        role=role,
        availability=availability,
        streams={
            "ticks": dict(
                process="synthetic qualified ticks",
                features=[dict(name="price", unit="USD", modality="price")],
            )
        },
    )


def plan():
    return dict(
        stream="ticks",
        context=4,
        max_history_ns=100 * NS,
        lifecycle=["birth", "terminal"],
        lifecycle_origins=True,
        relative_features=["price"],
        targets=[
            dict(name="return", kind="endpoint_log_return", horizon_ns=10 * NS, feature="price"),
            dict(name="count", kind="observed_process_count", horizon_ns=10 * NS, feature=None),
        ],
    )


def facts(*, complete=True):
    e = "test/market/A"
    base = dict(entity=e, evidence=["a" * 64])
    rows = [dict(base, kind="enrollment", start_ns=0, end_ns=51 * NS, available_ns=0)]
    for i, t in enumerate((1, 11, 40)):
        rows.append(
            dict(
                base,
                kind="event",
                stream="ticks",
                id=str(i),
                event_ns=t * NS,
                available_ns=t * NS,
                values=[10.0 + i],
            )
        )
    rows.append(
        dict(base, kind="lifecycle", id="birth", event_ns=0, available_ns=0, transition="birth")
    )
    rows.append(
        dict(
            base,
            kind="support",
            stream="ticks",
            start_ns=0,
            end_ns=51 * NS,
            available_ns=51 * NS,
            status="complete" if complete else "unknown",
        )
    )
    return sorted(rows, key=observations.key)


def source(tmp_path, rows=None, **kwargs):
    store = tmp_path / "events"
    pin = observations.publish(
        store,
        facts() if rows is None else rows,
        schema=contract(**kwargs),
        cutoff=100 * NS,
        sources=[dict(synthetic=True)],
        chunk=2,
    )
    return observations.Source(store, pin, role=kwargs.get("role", "train"))


def materialized(tmp_path, rows=None, *, projection=None, **kwargs):
    s = source(tmp_path, rows, **kwargs)
    p = projection or plan()
    db = s.index(tmp_path / "index.sqlite")
    try:
        r = list(sparse.samples(db, s, p))
    finally:
        db.close()
    return s, r, list(checked_samples(r, sparse.schema(s, p), 100 * NS))


def test_short_sparse_lifecycle_and_true_zero(tmp_path):
    s, rows, _ = materialized(tmp_path)
    assert len(rows) == 4  # Includes birth with no price, first event, and a long gap.
    birth, first, second, last = rows
    assert birth["target_status"] == ["unsupported", "observed"]
    assert birth["targets"] == [None, 1]
    assert first["targets"] == [pytest.approx(math.log(1.1)), 1]
    assert second["targets"] == [None, 0]  # Complete window, real zero event count; no zero return.
    assert second["target_status"] == ["missing_future", "observed"]
    assert last["targets"] == [None, 0]
    assert first["values"][0][0] is None
    assert first["values"][-1][0] == 0  # Log price relative to the known origin, not imputation.
    assert last["values"][-1][4] == pytest.approx(math.log1p(29))
    assert all(row["values"][-1][-1] is None for row in rows)  # No invented death.
    assert all(row["values"][-1][5] is None for row in rows)  # Future-ending support not a feature.
    result = sparse.materialize(s.store, s.pin, tmp_path / "store", plan=plan(), chunk=2)
    snap = Snapshot(tmp_path / "store", result["snapshot"])
    assert snap.manifest["decisions"] == 4
    assert (
        sparse.materialize(s.store, s.pin, tmp_path / "store", plan=plan(), chunk=2)["snapshot"]
        == snap.pin
    )
    for i in range(len(snap.parts)):
        snap.partition(i)


@pytest.mark.parametrize("status", ["unknown", "failed", "stopped", "unsupported"])
def test_unknown_support_is_censoring_not_zero(tmp_path, status):
    rows = facts()
    rows[-1]["status"] = status
    _, samples, _ = materialized(tmp_path, rows)
    second = samples[2]
    assert second["target_status"] == ["missing_future", "censored"]
    assert second["targets"] == [None, None]
    assert second["target_details"][1]["lower_bound"] == 0
    first = samples[1]
    assert first["target_details"][1]["lower_bound"] == 1
    assert first["target_details"][1]["lower_bound_available_ns"] == 11 * NS
    assert (
        first["target_details"][1]["reason"]
        == {
            "unknown": "unknown_support",
            "failed": "source_failed",
            "stopped": "source_stopped",
            "unsupported": "unsupported_process",
        }[status]
    )
    split = dict(train_start_s=0, train_end_s=50, validation_end_s=60, test_end_s=70)
    assert membership(second, split, sparse.schema(source(tmp_path / "extra"), plan())) == (
        "train",
        [False, False],
    )
    assert samples[1]["target_status"][0] == "observed"  # Exact endpoints need no intervening bars.


def test_endpoint_across_gap_and_administrative_censoring(tmp_path):
    p = plan()
    p["targets"][0]["horizon_ns"] = 39 * NS
    _, rows, _ = materialized(tmp_path, projection=p)
    assert rows[1]["targets"][0] == pytest.approx(math.log(1.2))
    assert rows[2]["target_status"][0] == "missing_future"
    assert rows[3]["target_status"][0] == "censored"
    assert rows[3]["target_details"][0]["reason"] == "enrollment_boundary"
    assert rows[3]["target_status"][1] == "observed"
    p["targets"][0]["horizon_ns"] = 101 * NS
    _, rows, _ = materialized(tmp_path / "pending", projection=p)
    assert all(r["target_status"][0] == "pending" for r in rows)


def test_future_changes_cannot_change_input_frames(tmp_path):
    raw = facts()
    _, left, _ = materialized(tmp_path / "a", raw)
    changed = copy.deepcopy(raw)
    next(r for r in changed if r["kind"] == "event" and r["event_ns"] == 40 * NS)["values"] = [999]
    changed.append(
        dict(
            kind="lifecycle",
            entity="test/market/A",
            event_ns=45 * NS,
            available_ns=45 * NS,
            evidence=["b" * 64],
            id="terminal",
            transition="terminal",
        )
    )
    _, right, _ = materialized(tmp_path / "b", sorted(changed, key=observations.key))
    assert [r["values"] for r in left[:3]] == [r["values"] for r in right[:3]]
    assert left[2]["input_dependencies"] == right[2]["input_dependencies"]


def test_point_in_time_filters_late_events_and_lifecycle(tmp_path):
    rows = facts()
    next(r for r in rows if r["kind"] == "lifecycle")["available_ns"] = 12 * NS
    next(r for r in rows if r["kind"] == "event" and r["event_ns"] == 11 * NS)["available_ns"] = (
        13 * NS
    )
    _, samples, _ = materialized(tmp_path, rows, availability="point_in_time")
    assert [r["decision_ns"] for r in samples] == [NS, 12 * NS, 13 * NS, 40 * NS]
    assert samples[0]["values"][-1][-2] is None  # Birth not known yet.
    assert samples[-1]["values"][-1][-2] == pytest.approx(math.log1p(40))
    assert all(r["input_available_ns"] <= r["decision_ns"] for r in samples)


def test_integrity_role_and_conflicting_support(tmp_path):
    s = source(tmp_path)
    with pytest.raises(ValueError, match="reserved_role"):
        observations.Source(s.store, s.pin, role="research_evaluation")
    part = object_path(s.store, s.parts[0]["pin"]) / "facts.jsonl"
    part.write_text(part.read_text() + "\n")
    with pytest.raises(ValueError):
        list(s.rows())
    rows = facts()
    rows.append(dict(rows[-1], start_ns=40 * NS, end_ns=50 * NS))
    s = source(tmp_path / "overlap", sorted(rows, key=observations.key))
    with pytest.raises(ValueError, match="overlapping_support"):
        s.index(tmp_path / "bad.sqlite")


def test_duplicate_event_ids_and_premature_completeness(tmp_path):
    rows = facts()
    events = [r for r in rows if r["kind"] == "event"]
    events[1]["id"] = events[0]["id"]
    s = source(tmp_path, rows)
    with pytest.raises(sqlite3.IntegrityError):
        s.index(tmp_path / "duplicate.sqlite")
    rows = facts()
    rows[1]["available_ns"] = 60 * NS
    s = source(tmp_path / "late", rows)
    with pytest.raises(ValueError, match="premature_completeness"):
        s.index(tmp_path / "late.sqlite")


def test_paged_catalog_and_empty_enrollment(tmp_path, monkeypatch):
    from memecoin_experience import catalog

    monkeypatch.setattr(catalog, "PAGE_SIZE", 2)
    rows = facts() + [
        dict(
            kind="enrollment",
            entity="test/market/Z",
            start_ns=0,
            end_ns=50 * NS,
            available_ns=0,
            evidence=["b" * 64],
        )
    ]
    s = source(tmp_path, sorted(rows, key=observations.key))
    assert "partition_pages" in s.manifest
    assert len(list(s.rows())) == 7
    db = s.index(tmp_path / "index.sqlite")
    assert db.execute("select count(*) from members").fetchone()[0] == 2
    assert sparse.event_count(db, "test/market/A", "ticks", 11 * NS, 40 * NS) == 1
    db.close()


def test_continuous_adapter_preserves_missingness(tmp_path):
    from test_experience_continuous import fixture

    from memecoin_experience.continuous import export
    from memecoin_experience.observation_adapters import from_continuous

    index, _, p = fixture(tmp_path)
    result = export(index, tmp_path / "source", p)
    ref = dict(kind="continuous", path=str(tmp_path / "source"), pin=result["pin"])
    pin = from_continuous(ref, tmp_path / "observations")
    s = observations.Source(tmp_path / "observations", pin, role="train")
    rows = list(s.rows())
    assert sum(r["kind"] == "event" for r in rows) == 60
    assert all(r["status"] == "unknown" for r in rows if r["kind"] == "support")
    assert not any(r["kind"] == "lifecycle" for r in rows)
    assert from_continuous(ref, tmp_path / "observations") == pin
    # Reserved role rejection must precede even the sealed payload hash read.
    (tmp_path / "source/meta/rows.jsonl").unlink()
    with pytest.raises(ValueError, match="reserved_role"):
        from_continuous(ref, tmp_path / "forbidden", role="research_evaluation")


def test_replication_restart_and_namespace_guard(tmp_path):
    s = source(tmp_path)
    cloned = observations.replicate(s.store, tmp_path / "replica", s.pin, role="train")
    assert cloned["copied"] > 0
    assert observations.replicate(s.store, tmp_path / "replica", s.pin, role="train")["copied"] == 0
    assert list(s.rows()) == list(
        observations.Source(tmp_path / "replica", s.pin, role="train").rows()
    )
    from memecoin_brain.source import EXCLUDED

    bad = facts()
    bad[0]["entity"] = "solana/pool/" + next(iter(EXCLUDED))
    with pytest.raises(ValueError, match="protected"):
        source(tmp_path / "protected", bad)

    def interrupted():
        yield from facts()[:3]
        raise RuntimeError("injected interruption")

    store = tmp_path / "recovery"
    with pytest.raises(RuntimeError):
        observations.publish(
            store,
            interrupted(),
            schema=contract(),
            cutoff=100 * NS,
            sources=[dict(synthetic=True)],
            chunk=1,
        )
    assert not list((store / "observation-sources").glob("*.json"))
    pin = observations.publish(
        store, facts(), schema=contract(), cutoff=100 * NS, sources=[dict(synthetic=True)], chunk=1
    )
    assert len(list(observations.Source(store, pin, role="train").rows())) == 6


def test_source_shards_are_stable_across_other_entity_growth(tmp_path):
    rows = facts() + [
        dict(
            kind="enrollment",
            entity="test/market/Z",
            start_ns=0,
            end_ns=50 * NS,
            available_ns=0,
            evidence=["b" * 64],
        )
    ]
    first = source(tmp_path, sorted(rows, key=observations.key))
    rows.append(dict(rows[1], id="added", event_ns=20 * NS, available_ns=20 * NS))
    second = source(tmp_path, sorted(rows, key=observations.key))
    assert first.pin != second.pin
    # Final entity's enrollment object does not shift when the earlier market gets an event.
    assert first.parts[-1] == second.parts[-1]


def test_arrival_ties_follow_sample_id_order(tmp_path):
    raw = facts()
    events = [r for r in raw if r["kind"] == "event"]
    events[0].update(id="z", event_ns=3 * NS, available_ns=10 * NS)
    events[1].update(id="a", event_ns=5 * NS, available_ns=10 * NS)
    next(r for r in raw if r["kind"] == "lifecycle")["available_ns"] = 10 * NS
    _, rows, _ = materialized(
        tmp_path, sorted(raw, key=observations.key), availability="point_in_time"
    )
    assert [r["id"] for r in rows[:3]] == [
        "event:ticks:a",
        "event:ticks:z",
        "lifecycle:ticks:birth",
    ]
    assert [r["observation_origin"]["event_ns"] for r in rows[:2]] == [5 * NS, 3 * NS]
    assert rows[2]["values"][-1][3] == pytest.approx(math.log1p(10))


def test_open_gap_completeness_can_be_known_at_anchor(tmp_path):
    raw = facts()
    support_row = next(r for r in raw if r["kind"] == "support")
    support_row.update(end_ns=11 * NS, available_ns=11 * NS)
    _, rows, _ = materialized(tmp_path, raw)
    assert rows[2]["values"][-1][5] == 1.0
    assert rows[3]["values"][-1][5] is None  # Later gap has no complete assertion.


def test_unrelated_growth_reuses_materialized_history(tmp_path):
    s = source(tmp_path)
    first = sparse.materialize(s.store, s.pin, tmp_path / "snapshots", plan=plan(), chunk=2)
    extra = [dict(r, entity="test/market/Z") for r in facts()]
    grown = source(tmp_path, facts() + extra)
    second = sparse.materialize(
        grown.store, grown.pin, tmp_path / "snapshots", plan=plan(), chunk=2
    )
    a = Snapshot(tmp_path / "snapshots", first["snapshot"])
    b = Snapshot(tmp_path / "snapshots", second["snapshot"])
    assert first["snapshot"] != second["snapshot"]
    assert list(a.parts) == list(b.parts)[: len(a.parts)]


def test_reenrollment_intervals_remain_distinct_and_do_not_join_history(tmp_path):
    entity = "test/market/reentered"
    base = dict(entity=entity, evidence=["a" * 64])
    rows = [
        dict(base, kind="enrollment", start_ns=0, end_ns=4 * NS, available_ns=0),
        dict(base, kind="enrollment", start_ns=10 * NS, end_ns=20 * NS, available_ns=10 * NS),
        dict(
            base,
            kind="event",
            stream="ticks",
            id="first-life",
            event_ns=1 * NS,
            available_ns=1 * NS,
            values=[10.0],
        ),
        dict(
            base,
            kind="event",
            stream="ticks",
            id="second-life",
            event_ns=11 * NS,
            available_ns=11 * NS,
            values=[20.0],
        ),
        dict(
            base,
            kind="support",
            stream="ticks",
            start_ns=0,
            end_ns=4 * NS,
            available_ns=4 * NS,
            status="complete",
        ),
        dict(
            base,
            kind="support",
            stream="ticks",
            start_ns=10 * NS,
            end_ns=20 * NS,
            available_ns=20 * NS,
            status="complete",
        ),
    ]
    s = source(tmp_path, sorted(rows, key=observations.key))
    db = s.index(tmp_path / "reentered-index.sqlite")
    try:
        assert db.execute("select count(*) from members").fetchone()[0] == 2
        samples = list(sparse.samples(db, s, plan()))
    finally:
        db.close()
    assert [row["decision_ns"] for row in samples] == [1 * NS, 11 * NS]
    assert all(row["target_status"][1] == "censored" for row in samples)
    assert [row["target_details"][1]["lower_bound"] for row in samples] == [0, 0]


@pytest.mark.parametrize("invalid", ["event_in_gap", "overlap", "support_crosses_gap"])
def test_reenrollment_rejects_unowned_or_overlapping_facts(tmp_path, invalid):
    entity = "test/market/reentered"
    base = dict(entity=entity, evidence=["a" * 64])
    rows = [
        dict(base, kind="enrollment", start_ns=0, end_ns=4 * NS, available_ns=0),
        dict(base, kind="enrollment", start_ns=10 * NS, end_ns=20 * NS, available_ns=10 * NS),
    ]
    if invalid == "event_in_gap":
        rows.append(
            dict(
                base,
                kind="event",
                stream="ticks",
                id="gap-event",
                event_ns=5 * NS,
                available_ns=5 * NS,
                values=[10.0],
            )
        )
    elif invalid == "overlap":
        rows.append(
            dict(base, kind="enrollment", start_ns=3 * NS, end_ns=12 * NS, available_ns=3 * NS)
        )
    else:
        rows.append(
            dict(
                base,
                kind="support",
                stream="ticks",
                start_ns=2 * NS,
                end_ns=11 * NS,
                available_ns=11 * NS,
                status="complete",
            )
        )
    s = source(tmp_path, sorted(rows, key=observations.key))
    with pytest.raises(ValueError, match="observation_(outside_enrollment|overlapping_enrollment)"):
        s.index(tmp_path / "invalid-reentered-index.sqlite")
