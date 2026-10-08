import math
import struct
import time
import tracemalloc

import pytest

from memecoin_experience import observations, qualification, sensing, storage
from memecoin_population import catalog

CAPS = ["complete_support", "observation_support", "population_coverage"]


def _process(store, name, stream, field, unit):
    schema_pin = qualification.publish_payload_schema(
        store,
        dict(version="payload-schema-v1", fields=[dict(name=field, unit=unit, denomination=unit)]),
    )
    refs = {
        key: qualification.publish_rule(store, key, value)
        for key, value in {
            "event_identity_rule": "key-v1",
            "event_time_basis": "block-time-v1",
            "interval_convention": "half-open-start-inclusive-end-exclusive-v1",
            "revision_policy": "append-revisions-v1",
        }.items()
    }
    contract = dict(
        version="process-contract-v1",
        name=name,
        stream=stream,
        semantics=f"all synthetic {stream} in interval",
        unit="events",
        source_kind="synthetic_closed_fixture",
        entity_namespace="chain/market/",
        modality=stream,
        payload_schema_pin=schema_pin,
        event_identity_rule=refs["event_identity_rule"],
        event_time_basis=refs["event_time_basis"],
        timestamp_precision_ns=None,
        interval_convention=refs["interval_convention"],
        revision_policy=refs["revision_policy"],
    )
    return contract, qualification.process_contract_pin(contract, store=store)


def _verified_member(store, process, process_pin, entity, field, values):
    fixture = dict(
        version="synthetic-qualification-fixture-v1",
        entity=entity,
        start_ns=0,
        end_ns=101,
        available_ns=101,
        closed_through_ns=101,
        events=[dict(id=f"{field}-{t}", event_ns=t, values=[value]) for t, value in values],
        unresolved_events=[],
        rejected_events=[],
    )
    input_pin = qualification.publish_synthetic_fixture(
        store, fixture, process=process, process_pin=process_pin
    )
    counts = dict(
        captured=len(values), decoded=len(values), rejected=0, qualified=len(values), unresolved=0
    )
    member = dict(
        entity=entity,
        start_ns=0,
        end_ns=101,
        available_ns=101,
        status="complete",
        counts=counts,
        capabilities=CAPS,
        evidence=sorted(["a" * 64, input_pin]),
        verification=None,
    )
    member["verification"] = qualification.execute_verifier(
        store, "synthetic_closed_fixture_v1", input_pin, process, process_pin, member
    )
    return member


def _receipt(store, pop_pin, process, process_pin, scope_pin, members):
    source_pin = qualification.synthetic_source_pin(process_pin, scope_pin)
    decoder_pin = qualification.SYNTHETIC_DECODER_PIN
    code_pin = qualification._verifier_code_pin("synthetic_closed_fixture_v1")
    counts = qualification._sum_counts(members)
    receipt = dict(
        version="qualification-receipt-v1",
        process_contract=process,
        process_contract_pin=process_pin,
        inputs=sorted({source_pin, decoder_pin, code_pin, process_pin, pop_pin, scope_pin}),
        source_pin=source_pin,
        decoder_pin=decoder_pin,
        qualification_code_pin=code_pin,
        population_pin=pop_pin,
        scope_pin=scope_pin,
        role="train",
        accounting_unit="candidate_process_records",
        available_ns=101,
        counts=counts,
        capabilities=CAPS,
    )
    return qualification.publish_receipt(store, receipt)


def _destination(process, process_pin):
    return dict(
        version="observation-process-binding-v1",
        process_name=process["name"],
        stream=process["stream"],
        process_contract_pin=process_pin,
        payload_schema_pin=process["payload_schema_pin"],
        modality=process["modality"],
        features=[
            dict(
                name=process["stream"] == "prices" and "price" or "count",
                unit="usd" if process["stream"] == "prices" else "records",
                modality=process["modality"],
            )
        ],
    )


def _build_fixture(tmp_path):
    entity_a, entity_b = "chain/market/A", "chain/market/B"
    population_store = tmp_path / "population"
    population_pin = catalog.publish(
        population_store,
        "population",
        [
            dict(entity=e, start_ns=0, end_ns=200, known_ns=20, attributes={}, evidence=["a" * 64])
            for e in (entity_a, entity_b)
        ],
        cutoff_ns=200,
        definition=dict(role="train", selection="synthetic sensing episode"),
    )
    receipt_store = tmp_path / "qualification"
    processes, pins, receipt_pins = [], [], []
    fixture_values = {
        "trades": [(2, 1.0), (12, 2.0), (31, 3.0), (95, 4.0), (96, 5.0), (100, 6.0)],
        "prices": [(2, 1.0), (12, 2.0), (30, 4.0), (100, 8.0)],
    }
    for name, stream, field, unit in (
        ("trade-process", "trades", "count", "records"),
        ("price-process", "prices", "price", "usd"),
    ):
        process, process_pin = _process(receipt_store, name, stream, field, unit)
        members = [
            _verified_member(
                receipt_store, process, process_pin, entity_a, field, fixture_values[stream]
            ),
            _verified_member(receipt_store, process, process_pin, entity_b, field, [(2, 1.0)]),
        ]
        scope_pin = qualification.publish_scope(
            receipt_store,
            members,
            population_store=population_store,
            population_pin=population_pin,
            role="train",
            process=process,
            process_pin=process_pin,
        )
        receipt_pin = _receipt(
            receipt_store, population_pin, process, process_pin, scope_pin, members
        )
        processes.append(process)
        pins.append(process_pin)
        receipt_pins.append(receipt_pin)
    streams = {
        process["stream"]: dict(
            process=process["name"],
            features=[
                dict(
                    name="price" if process["stream"] == "prices" else "count",
                    unit="usd" if process["stream"] == "prices" else "records",
                    modality=process["modality"],
                )
            ],
        )
        for process in processes
    }
    rows = []
    for member in catalog.records(population_store, population_pin):
        rows.append(
            dict(
                kind="enrollment",
                entity=member["entity"],
                start_ns=0,
                end_ns=200,
                available_ns=20,
                evidence=[population_pin],
            )
        )
    for process, process_pin, receipt_pin in zip(processes, pins, receipt_pins, strict=True):
        field = "price" if process["stream"] == "prices" else "count"
        for member in (entity_a, entity_b):
            assertion = dict(
                receipt_pin=receipt_pin,
                scope_pin=qualification.load_receipt(receipt_store, receipt_pin)["scope_pin"],
                population_pin=population_pin,
                entity=member,
                start_ns=0,
                end_ns=101,
            )
            rows.append(
                qualification.observation_fact(
                    assertion,
                    receipt_store=receipt_store,
                    population_store=population_store,
                    stream=process["stream"],
                    destination_schema=_destination(process, process_pin),
                )
            )
            for t, value in fixture_values[process["stream"]] if member == entity_a else [(2, 1.0)]:
                rows.append(
                    dict(
                        kind="event",
                        entity=member,
                        stream=process["stream"],
                        id=f"{field}-{t}",
                        event_ns=t,
                        available_ns=101,
                        evidence=sorted(
                            [
                                receipt_pin,
                                next(
                                    p["input_pin"]
                                    for m in qualification._scope_members(
                                        receipt_store,
                                        qualification._scope_manifest(
                                            receipt_store,
                                            qualification.load_receipt(receipt_store, receipt_pin)[
                                                "scope_pin"
                                            ],
                                        ),
                                    )
                                    if m["entity"] == member
                                    for p in [m["verification"]]
                                ),
                            ]
                        ),
                        values=[value],
                    )
                )
    observation_store = tmp_path / "observations"
    observation_pin = observations.publish(
        observation_store,
        sorted(rows, key=observations.key),
        schema=dict(
            version="market-observations-v1",
            role="train",
            availability="point_in_time",
            streams=streams,
        ),
        cutoff=200,
        sources=[dict(kind="synthetic", pin="b" * 64)],
    )
    sensing_store = tmp_path / "sensing"
    wrapper = sensing.publish_source(
        sensing_store,
        observation_store=observation_store,
        observation_source_pin=observation_pin,
        qualification_store=receipt_store,
        population_store=population_store,
        population_pin=population_pin,
        role="train",
        receipt_pins=receipt_pins,
    )
    return locals()


def test_two_process_sensing_episode_is_reconciled_asof_and_replayable(tmp_path):
    f = _build_fixture(tmp_path)
    schedule_store = tmp_path / "schedule"
    schedule_pin = sensing.publish_schedule(
        schedule_store,
        iter(
            [
                dict(entity="chain/market/A", decision_ns=20),
                dict(entity="chain/market/A", decision_ns=95),
                dict(entity="chain/market/A", decision_ns=101),
                dict(entity="chain/market/B", decision_ns=20),
            ]
        ),
        population_store=f["population_store"],
        population_pin=f["population_pin"],
        role="train",
    )
    plan = dict(
        context=3,
        max_history_ns=50,
        horizons_ns=[5],
        price_targets=[
            dict(stream="prices", feature="price", horizon_ns=5, name="prices/return_5ns")
        ],
    )
    first = sensing.materialize_episode(
        f["observation_store"],
        f["wrapper"]["source_pin"],
        sensing_store=f["sensing_store"],
        schedule_store=schedule_store,
        schedule_pin=schedule_pin,
        qualification_store=f["receipt_store"],
        population_store=f["population_store"],
        output=tmp_path / "snapshot-a",
        plan=plan,
    )
    snapshot = storage.Snapshot(tmp_path / "snapshot-a", first["snapshot"])
    loaded = [snapshot.partition(partition) for partition in range(len(snapshot.parts))]
    rows = [row for _, part in loaded for row in part]
    matrix = []
    width = len(snapshot.schema["features"])
    for path, part in loaded:
        raw = struct.unpack(
            f"<{len(part) * snapshot.schema['context'] * width}d", (path / "x.f64").read_bytes()
        )
        matrix.extend(
            raw[i : i + snapshot.schema["context"] * width]
            for i in range(0, len(raw), snapshot.schema["context"] * width)
        )
    assert len(rows) == 4
    quiet = next(r for r in rows if r["pool"].endswith("/A") and r["decision_ns"] == 20)
    quiet_row = rows.index(quiet)
    assert all(
        math.isnan(v) for v in matrix[quiet_row][:-width]
    )  # event arrivals are after decision 20
    assert any(v == 1.0 for v in matrix[quiet_row][-width:])  # scheduled-origin status frame
    assert quiet["target_status"][0] == "observed"
    price_index = next(
        i
        for i, target in enumerate(snapshot.schema["targets"])
        if target["name"] == "prices/return_5ns"
    )
    count_index = next(
        i
        for i, target in enumerate(snapshot.schema["targets"])
        if target["name"] == "trades/events_5ns"
    )
    assert quiet["target_status"][price_index] == "missing_future"
    assert quiet["target_maturity_ns"] == [25, 25, 25]
    assert (
        quiet["input_start_ns"] == quiet["input_available_ns"] == 20
    )  # schedule page was available at the origin
    target_values = []
    for path, part in loaded:
        raw_y = struct.unpack(
            f"<{len(part) * len(snapshot.schema['targets'])}d", (path / "y.f64").read_bytes()
        )
        target_values.extend(
            raw_y[i : i + len(snapshot.schema["targets"])]
            for i in range(0, len(raw_y), len(snapshot.schema["targets"]))
        )
    assert target_values[quiet_row][count_index] == 0.0  # complete support makes this an exact zero
    later = next(r for r in rows if r["pool"].endswith("/A") and r["decision_ns"] == 95)
    assert later["target_status"][count_index] == "observed"
    assert later["target_maturity_ns"] == [100, 100, 100]
    later_row = rows.index(later)
    assert target_values[later_row][count_index] == 2.0, later[
        "target_details"
    ]  # (decision, maturity] includes 96 and 100, excludes 95
    ready = next(r for r in rows if r["pool"].endswith("/A") and r["decision_ns"] == 101)
    assert (
        ready["input_start_ns"] == 0
    )  # support interval dependency is purged from its valid-time start
    assert first["accounting"] == {"schedule_origins": 4, "quiet_origins": 3}
    replay = sensing.materialize_episode(
        f["observation_store"],
        f["wrapper"]["source_pin"],
        sensing_store=f["sensing_store"],
        schedule_store=schedule_store,
        schedule_pin=schedule_pin,
        qualification_store=f["receipt_store"],
        population_store=f["population_store"],
        output=tmp_path / "snapshot-a",
        plan=plan,
    )
    assert replay["snapshot"] == first["snapshot"]
    replica_store = tmp_path / "observation-replica"
    replicated = observations.replicate(
        f["observation_store"], replica_store, f["observation_pin"], role="train"
    )
    assert replicated["source"] == f["observation_pin"]
    replica_replay = sensing.materialize_episode(
        replica_store,
        f["wrapper"]["source_pin"],
        sensing_store=f["sensing_store"],
        schedule_store=schedule_store,
        schedule_pin=schedule_pin,
        qualification_store=f["receipt_store"],
        population_store=f["population_store"],
        output=tmp_path / "snapshot-replica",
        plan=plan,
    )
    assert replica_replay["snapshot"] == first["snapshot"]


def test_sensing_rejects_event_not_reconciled_to_fixture_pin(tmp_path):
    f = _build_fixture(tmp_path)
    original = observations.Source(f["observation_store"], f["observation_pin"], role="train")
    rows = list(original.rows())
    for row in rows:
        if row["kind"] == "event":
            row["values"] = [999.0]
            break
    bad_pin = observations.publish(
        tmp_path / "bad-observations",
        sorted(rows, key=observations.key),
        schema=original.schema,
        cutoff=200,
        sources=[dict(kind="synthetic", pin="c" * 64)],
    )
    with pytest.raises(ValueError, match="sensing_source_fixture_reconciliation"):
        sensing.publish_source(
            tmp_path / "bad-sensing",
            observation_store=tmp_path / "bad-observations",
            observation_source_pin=bad_pin,
            qualification_store=f["receipt_store"],
            population_store=f["population_store"],
            population_pin=f["population_pin"],
            role="train",
            receipt_pins=f["receipt_pins"],
        )


def test_sensing_rechecks_schedule_population_availability_on_use(tmp_path):
    f = _build_fixture(tmp_path)
    schedule_store = tmp_path / "forged-schedule"
    pages_dir = schedule_store / "sensing-schedule-pages"
    manifests_dir = schedule_store / "sensing-schedules"
    pages_dir.mkdir(parents=True)
    manifests_dir.mkdir()
    page = [dict(entity="chain/market/A", decision_ns=19)]
    page_pin = storage.digest(page)
    storage.atomic_json(pages_dir / f"{page_pin}.json", page)
    manifest = dict(
        version="sensing-schedule-v1",
        population_pin=f["population_pin"],
        role="train",
        origins=1,
        pages=[
            dict(
                pin=page_pin,
                count=1,
                first=["chain/market/A", 19],
                last=["chain/market/A", 19],
            )
        ],
    )
    schedule_pin = storage.digest(manifest)
    storage.atomic_json(manifests_dir / f"{schedule_pin}.json", manifest)
    with pytest.raises(ValueError, match="origin_outside_available_population"):
        sensing.materialize_episode(
            f["observation_store"],
            f["wrapper"]["source_pin"],
            sensing_store=f["sensing_store"],
            schedule_store=schedule_store,
            schedule_pin=schedule_pin,
            qualification_store=f["receipt_store"],
            population_store=f["population_store"],
            output=tmp_path / "must-not-build",
            plan=dict(context=3, max_history_ns=50, horizons_ns=[5], price_targets=[]),
        )


def test_schedule_pages_stream_1025_rows_and_record_resource_profile(tmp_path):
    pop_store = tmp_path / "pop"
    pop_pin = catalog.publish(
        pop_store,
        "population",
        [
            dict(
                entity="chain/market/0000",
                start_ns=0,
                end_ns=513,
                known_ns=0,
                attributes={},
                evidence=["a" * 64],
            ),
            dict(
                entity="chain/market/0000",
                start_ns=513,
                end_ns=2000,
                known_ns=513,
                attributes={},
                evidence=["a" * 64],
            ),
            dict(
                entity="chain/market/0001",
                start_ns=0,
                end_ns=2000,
                known_ns=0,
                attributes={},
                evidence=["a" * 64],
            ),
            dict(
                entity="chain/market/future",
                start_ns=0,
                end_ns=2000,
                known_ns=10,
                attributes={},
                evidence=["a" * 64],
            ),
        ],
        cutoff_ns=2000,
        definition=dict(role="train", selection="schedule scale fixture"),
    )
    schedule_store = tmp_path / "schedule"

    def origins():
        for n in range(1025):
            entity = "chain/market/0000" if n < 514 else "chain/market/0001"
            decision = n if n < 514 else n - 514
            yield dict(entity=entity, decision_ns=decision)

    tracemalloc.start()
    started = time.perf_counter()
    pin = sensing.publish_schedule(
        schedule_store, origins(), population_store=pop_store, population_pin=pop_pin, role="train"
    )
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    manifest = sensing._schedule_manifest(schedule_store, pin)
    assert manifest["origins"] == 1025 and len(manifest["pages"]) == 3
    assert sum(1 for _ in sensing.schedule_rows(schedule_store, pin)) == 1025
    assert elapsed >= 0 and peak > 0
    assert any(
        row["entity"].endswith("0000") and row["decision_ns"] == 513
        for row, _ in sensing.schedule_rows(schedule_store, pin)
    )
    with pytest.raises(ValueError, match="origin_outside_population"):
        sensing.publish_schedule(
            tmp_path / "future-known",
            [dict(entity="chain/market/future", decision_ns=9)],
            population_store=pop_store,
            population_pin=pop_pin,
            role="train",
        )


def test_budget_dry_run_requires_verified_entitlement_and_poison_ambiguous_account():
    pin = "a" * 64
    reqs = [
        dict(
            identity={"request": 1},
            account="amb",
            cost_units=2,
            unit="CU",
            cost_evidence_pin=pin,
            expires_ns=100,
        ),
        dict(
            identity={"request": 2},
            account="unknown",
            cost_units=1,
            unit="CU",
            cost_evidence_pin=pin,
            expires_ns=100,
        ),
        dict(
            identity={"request": 3},
            account="ok",
            cost_units=2,
            unit="CU",
            cost_evidence_pin=pin,
            expires_ns=100,
        ),
    ]
    reqs.sort(key=lambda r: storage.digest(r["identity"]))
    budgets = [
        dict(
            account="amb",
            known_ns=0,
            expires_ns=200,
            remaining_units=10,
            unit="CU",
            entitlement_state="verified",
            evidence_pin=pin,
        ),
        dict(
            account="unknown",
            known_ns=0,
            expires_ns=200,
            remaining_units=0,
            unit="CU",
            entitlement_state="unknown",
            evidence_pin=pin,
        ),
        dict(
            account="ok",
            known_ns=0,
            expires_ns=200,
            remaining_units=2,
            unit="CU",
            entitlement_state="verified",
            evidence_pin=pin,
        ),
    ]
    ledger = [
        dict(
            request_id=storage.digest({"prior": 1}),
            account="amb",
            state="ambiguous_attempt",
            cost_units=None,
            unit="CU",
        )
    ]
    result = sensing.budget_dry_run(
        requests=reqs, ledger=ledger, account_budgets=budgets, as_of_ns=1
    )
    assert {r["decision"] for r in result["decisions"]} == {
        "reconcile_ambiguous_attempt",
        "blocked_budget_unknown",
        "simulation_allow",
    }
    assert result["dispatched_requests"] == result["simulated_reservations"] == 0
