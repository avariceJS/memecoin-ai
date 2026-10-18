import pytest
from test_experience_schema import custom_samples

from memecoin_experience.storage import build, file_hash, object_path
from memecoin_population import catalog, diversity, planning


def member(name="A", start=0, end=100, known=1, **attrs):
    return dict(
        entity="chain/market/" + name,
        start_ns=start,
        end_ns=end,
        known_ns=known,
        attributes=attrs,
        evidence=["a" * 64],
    )


def definition(role="train"):
    return dict(role=role, selection="fixed synthetic population including unsupported entities")


def config(**changes):
    return dict(
        entity_prefix="chain/market/",
        knowledge="at_decision",
        axes=[["@entity"], ["venue"], ["@period"], ["venue", "regime"]],
        time_bucket_ns=20000 * 10**9,
        selection=dict(salt="fixed", numerator=1, denominator=1),
        enforcement="require",
        min_train_entities=1,
        max_entity_share=1.0,
        max_unknown_fraction=1.0,
        objective=diversity.OBJECTIVE,
        **changes,
    )


def test_paged_append_history_compaction_replica_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "SHARD_ROWS", 2)
    monkeypatch.setattr(catalog, "PAGE_SHARDS", 2)
    store = tmp_path / "store"
    first = catalog.publish(
        store,
        "population",
        [member(str(i), venue="old") for i in range(11)],
        cutoff_ns=1,
        definition=definition(),
    )
    later = catalog.publish(
        store,
        "population",
        [member("0", known=20, venue="new")],
        cutoff_ns=20,
        definition=definition(),
        parents=[first],
    )
    view = catalog.View(tmp_path / "view.sqlite")
    view.ingest(store, later)
    assert view.attributes("chain/market/0", 50, 1) == dict(venue="old")
    assert view.attributes("chain/market/0", 50, 20) == dict(venue="new")
    assert view.attributes("chain/market/0", 50, 0) == {}
    assert len(list(view.intervals(0, 100, 20))) == 11
    view.close()
    compact = catalog.compact(store, later)
    assert len(list(catalog.records(store, compact))) == 12
    replica = tmp_path / "replica"
    assert catalog.replicate(store, replica, later)["copied"] > 0
    assert catalog.replicate(store, replica, later)["copied"] == 0
    assert list(catalog.records(replica, later)) == list(catalog.records(store, later))
    m = catalog.manifest(store, first)
    path = object_path(store, m["pages"][0]["pin"]) / "page.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="checksum"):
        list(catalog.records(store, later))
    assert len(list(catalog.records(replica, later))) == 12


def test_temporal_conflicts_and_unknown_counts_fail_closed(tmp_path):
    store = tmp_path / "store"
    pin = catalog.publish(
        store,
        "population",
        [member(venue="X"), member(venue="Y")],
        cutoff_ns=1,
        definition=definition(),
    )
    view = catalog.View(tmp_path / "v.sqlite")
    view.ingest(store, pin)
    with pytest.raises(ValueError, match="ambiguous_metadata"):
        view.attributes("chain/market/A", 50, 1)
    view.close()
    bad = dict(
        entity="chain/market/A",
        start_ns=0,
        end_ns=100,
        known_ns=1,
        evidence=["a" * 64],
        stream="events",
        contract="c" * 64,
        unit="events",
        stage="capture",
        status="failed",
        expected=None,
        observed=0,
    )
    with pytest.raises(ValueError, match="unknown_is_not_zero"):
        catalog.publish(store, "coverage", [bad], cutoff_ns=1, definition=definition())
    with pytest.raises(ValueError, match="record_time"):
        catalog.publish(
            store, "population", [member(known=2)], cutoff_ns=1, definition=definition()
        )


def test_generic_coverage_planning_no_implicit_retries_or_fixed_cadence(tmp_path):
    store = tmp_path / "store"
    pin = catalog.publish(
        store,
        "population",
        [member("A"), member("B", start=17)],
        cutoff_ns=100,
        definition=definition(),
    )
    stream = dict(
        name="bars",
        source="arbitrary",
        modality="price",
        representation="external-v1",
        unit="observations",
        tile_ns=50,
        cadence_ns=5,
    )
    plan = dict(
        start_ns=0,
        end_ns=100,
        cutoff_ns=100,
        streams=[stream, dict(stream, name="flow", modality="swaps", cadence_ns=None)],
        selection=dict(salt="x", numerator=1, denominator=1),
        partitions=3,
    )
    first = planning.create(store, pin, tmp_path / "initial", plan)
    assert first["cells"] == 8 and first["enrolled_entities"] == 2
    work = [
        r
        for p in range(3)
        for r in planning.work(
            tmp_path / "initial", file_hash(tmp_path / "initial/manifest.json"), p
        )
    ]
    assert len({r["id"] for r in work}) == 8
    target = next(r for r in work if r["stream"] == "flow")
    row = {
        k: target[k]
        for k in ("entity", "stream", "contract", "start_ns", "end_ns", "unit", "expected")
    }
    cov = catalog.publish(
        store,
        "coverage",
        [
            dict(
                row,
                stage="capture",
                status="failed",
                observed=None,
                known_ns=90,
                evidence=["b" * 64],
            )
        ],
        cutoff_ns=100,
        definition=definition(),
    )
    revised = planning.create(store, pin, tmp_path / "revised", plan, coverage=cov)
    assert revised["actions"] == dict(acquire_unobserved=7, review_failure=1)
    assert revised == planning.create(store, pin, tmp_path / "revised", plan, coverage=cov)
    assert all(r["expected"] is None for r in work if r["stream"] == "flow")
    assert (
        next(
            r
            for r in work
            if r["entity"].endswith("/B") and r["stream"] == "bars" and r["start_ns"] == 17
        )["expected"]
        == 6
    )


def test_diversity_audit_preserves_distribution_and_unknown_denominators(tmp_path):
    PLAN = dict(train_start_s=0, train_end_s=30000, validation_end_s=60000, test_end_s=90000)

    schema, rows = custom_samples()
    store = tmp_path / "data"
    snap = build(store, rows, schema=schema, sources=[dict(synthetic=True)], cutoff=10**18)[
        "snapshot"
    ]
    facts = tmp_path / "facts"
    pin = catalog.publish(
        facts,
        "population",
        [
            member("synthetic", end=10**18, known=10**17, venue="X"),
            member("empty", end=10**18, known=1, venue="Y"),
        ],
        cutoff_ns=10**18,
        definition=definition(),
    )
    cfg = config()
    cfg["max_unknown_fraction"] = 0
    result = diversity.audit(store, snap, facts, pin, tmp_path / "audit", split=PLAN, config=cfg)
    assert result["enrolled_entities"] == 2
    assert result["train_distribution"]['["@entity"]']["supported_values"] == 1
    assert not result["training_allowed"] and any(
        v.startswith("unknown_metadata") for v in result["violations"]
    )
    assert result["population"]["train_usable"] > 0
    assert (
        diversity.audit(store, snap, facts, pin, tmp_path / "audit", split=PLAN, config=cfg)
        == result
    )
    cfg = dict(cfg, knowledge="at_cutoff", axes=[["@entity"], ["venue"]])
    retrospective = diversity.audit(
        store, snap, facts, pin, tmp_path / "retrospective", split=PLAN, config=cfg
    )
    assert retrospective["training_allowed"]
    assert retrospective["train_distribution"]['["venue"]']["max_share"] == 1.0
    cfg = dict(cfg, max_entity_share=0.5)
    blocked = diversity.audit(store, snap, facts, pin, tmp_path / "blocked", split=PLAN, config=cfg)
    assert "entity_concentration" in blocked["violations"]


def test_sealed_continuous_projection_and_stream_identity(tmp_path):
    from test_experience_continuous import fixture

    from memecoin_experience.continuous import NAMESPACE, export
    from memecoin_population.adapters import continuous_coverage

    index, _, export_plan = fixture(tmp_path)
    exported = export(index, tmp_path / "export", export_plan)
    name = export_plan["pools"][0]["pool"]
    store = tmp_path / "facts"
    row = member(name, start=300000 * 10**9, end=318000 * 10**9)
    row["entity"] = "solana/pool/" + name
    pop = catalog.publish(store, "population", [row], cutoff_ns=10**18, definition=definition())
    stream = dict(
        name="bars",
        source=NAMESPACE,
        modality="ohlcv",
        representation="qualified-ohlcv-v1",
        unit="unique_bar_opens",
        tile_ns=18000 * 10**9,
        cadence_ns=300 * 10**9,
    )
    plan = dict(
        start_ns=300000 * 10**9,
        end_ns=318000 * 10**9,
        cutoff_ns=10**18,
        streams=[stream],
        selection=dict(salt="x", numerator=1, denominator=1),
        partitions=2,
    )
    initial = tmp_path / "initial"
    planning.create(store, pop, initial, plan)
    ref = dict(path=str(tmp_path / "export"), pin=exported["pin"])
    cov = continuous_coverage(store, ref, initial, file_hash(initial / "manifest.json"))
    result = planning.create(store, pop, tmp_path / "covered", plan, coverage=cov)
    assert result["actions"] == {"covered": result["cells"]}
    assert sum(r["observed"] for r in catalog.records(store, cov)) == 60
    changed = dict(plan, streams=[dict(stream, source="another-provider")])
    other = planning.create(store, pop, tmp_path / "other", changed, coverage=cov)
    assert other["actions"] == {"acquire_unobserved": other["cells"]}


def test_qualified_count_semantics_and_append_only_enrollment(tmp_path):
    from memecoin_experience.storage import digest

    # Population enrollment is cumulative; a later metadata interval is not a withdrawal.
    store = tmp_path / "facts"
    pin = catalog.publish(
        store,
        "population",
        [member(end=100, known=0), member(end=10, known=50)],
        cutoff_ns=100,
        definition=definition(),
    )
    view = catalog.View(tmp_path / "view.sqlite")
    view.ingest(store, pin)
    assert list(view.intervals(0, 100, 100)) == [("chain/market/A", 0, 100)]
    view.close()
    base = dict(
        entity="chain/market/A",
        start_ns=0,
        end_ns=100,
        known_ns=100,
        stream="test",
        contract=digest("test"),
        unit="events",
        evidence=["a" * 64],
        stage="qualified",
        status="complete",
        expected=8,
        observed=9,
    )
    for row in (base, dict(base, observed=7), dict(base, observed=8, status="partial")):
        with pytest.raises(ValueError, match="qualified_denominator"):
            catalog.checked(row, "coverage", 100)
    assert catalog.checked(dict(base, observed=8), "coverage", 100)


def test_generic_axis_gates_distinguish_enrollment_and_unknown_from_train_support(tmp_path):
    schema, rows = custom_samples()
    snap = build(
        tmp_path / "data", rows, schema=schema, sources=[dict(synthetic=True)], cutoff=10**18
    )["snapshot"]
    pop = catalog.publish(
        tmp_path / "facts",
        "population",
        [
            member("synthetic", end=10**18, known=0, venue="X"),
            member("unsupported", end=10**18, known=0, venue="Y"),
        ],
        cutoff_ns=10**18,
        definition=definition(),
    )
    cfg = config()
    cfg["axis_limits"] = [
        dict(axis=["venue"], min_known_values=2, max_share=0.8, max_unknown_fraction=0)
    ]
    split = dict(train_start_s=0, train_end_s=30000, validation_end_s=60000, test_end_s=90000)
    report = diversity.audit(
        tmp_path / "data",
        snap,
        tmp_path / "facts",
        pop,
        tmp_path / "audit",
        split=split,
        config=cfg,
    )
    assert not report["training_allowed"]
    assert 'insufficient_known_values:["venue"]' in report["violations"]
    assert 'axis_concentration:["venue"]' in report["violations"]
    assert report["train_distribution"]['["venue"]']["known_supported_values"] == 1
    assert report["train_distribution"]['["venue","regime"]']["known_supported_values"] == 0
    assert report["train_distribution"]['["venue","regime"]']["unknown_fraction"] == 1
    assert planning.action(dict(status="unknown", observed=None), None, None) == "reconcile_unknown"


def test_interval_export_does_not_claim_unknown_grid_cells():
    from memecoin_population.adapters import covers_grid

    export = dict(start_s=0, end_s=3000, intervals={"A": [[300, 900], [1500, 2100]]})
    assert covers_grid(export, "A", 1 * 10**9, 900 * 10**9)
    assert covers_grid(export, "A", 1500 * 10**9, 2100 * 10**9)
    assert not covers_grid(export, "A", 900 * 10**9, 1500 * 10**9)
    assert not covers_grid(export, "A", 300 * 10**9, 2100 * 10**9)
    assert not covers_grid(export, "A", 2100 * 10**9, 2400 * 10**9)
