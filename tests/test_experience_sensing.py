import json
import math
import struct
import time
import tracemalloc

import pytest

from memecoin_experience import evaluation, learning, observations, qualification, sensing, storage
from memecoin_experience.predict import Predictor
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


def _verified_member(
    store,
    process,
    process_pin,
    entity,
    field,
    values,
    *,
    start=0,
    end=101,
    available=101,
    fixture_available=None,
    closed_through=None,
):
    closed_through = end if closed_through is None else closed_through
    fixture_available = available if fixture_available is None else fixture_available
    fixture = dict(
        version="synthetic-qualification-fixture-v1",
        entity=entity,
        start_ns=start,
        end_ns=end,
        available_ns=fixture_available,
        closed_through_ns=closed_through,
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
    status = "complete" if closed_through == end else "partial"
    capabilities = CAPS if status == "complete" else ["observation_support", "population_coverage"]
    member = dict(
        entity=entity,
        start_ns=start,
        end_ns=end,
        available_ns=available,
        status=status,
        counts=counts,
        capabilities=capabilities,
        evidence=sorted(["a" * 64, input_pin]),
        verification=None,
    )
    member["verification"] = qualification.execute_verifier(
        store, "synthetic_closed_fixture_v1", input_pin, process, process_pin, member
    )
    return member


def _receipt(store, pop_pin, process, process_pin, scope_pin, members, *, role="train"):
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
        role=role,
        accounting_unit="candidate_process_records",
        available_ns=max(m["available_ns"] for m in members),
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


def _build_fixture(tmp_path, *, role="train", prefix="", scale=1, shift=0):
    def tick(value):
        return shift + value * scale

    def entity(name):
        return f"chain/market/{prefix}{name}"

    entity_a, entity_b, entity_c = entity("A"), entity("B"), entity("C")
    population_store = tmp_path / "population"
    population_pin = catalog.publish(
        population_store,
        "population",
        [
            dict(
                entity=e,
                start_ns=tick(0),
                end_ns=tick(300),
                known_ns=tick(20),
                attributes={},
                evidence=["a" * 64],
            )
            for e in (entity_a, entity_b)
        ]
        + [
            dict(
                entity=entity_c,
                start_ns=tick(start),
                end_ns=tick(end),
                known_ns=tick(start),
                attributes={},
                evidence=["a" * 64],
            )
            for start, end in ((0, 50), (70, 200), (210, 300), (310, 400), (510, 600))
        ]
        + [
            dict(
                entity=entity("D"),
                start_ns=tick(0),
                end_ns=tick(200),
                known_ns=tick(0),
                attributes={},
                evidence=["a" * 64],
            )
        ],
        cutoff_ns=tick(700),
        definition=dict(role=role, selection="synthetic sensing episode"),
    )
    receipt_store = tmp_path / "qualification"
    processes, pins, receipt_pins, qualified_members = [], [], [], []
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
                receipt_store,
                process,
                process_pin,
                entity_a,
                field,
                [(tick(at), value) for at, value in fixture_values[stream]],
                start=tick(0),
                end=tick(300),
                available=tick(300),
            ),
            _verified_member(
                receipt_store,
                process,
                process_pin,
                entity_b,
                field,
                [(tick(2), 1.0)],
                start=tick(0),
                end=tick(300),
                available=tick(300),
            ),
            _verified_member(
                receipt_store,
                process,
                process_pin,
                entity_c,
                field,
                [(tick(2), 1.0), (tick(12), 2.0), (tick(45), 45.0)],
                start=tick(0),
                end=tick(50),
                available=tick(50),
            ),
            _verified_member(
                receipt_store,
                process,
                process_pin,
                entity_c,
                field,
                [(tick(72), 1.0), (tick(82), 2.0), (tick(84), 3.0), (tick(175), 17.0)],
                start=tick(70),
                end=tick(200),
                available=tick(200),
            ),
            _verified_member(
                receipt_store,
                process,
                process_pin,
                entity_c,
                field,
                [(tick(210), 21.0)],
                start=tick(210),
                end=tick(300),
                available=tick(300),
                fixture_available=tick(220),
                closed_through=tick(220),
            ),
            _verified_member(
                receipt_store,
                process,
                process_pin,
                entity_c,
                field,
                [(tick(320), 32.0), (tick(352), 35.0), (tick(360), 36.0)],
                start=tick(310),
                end=tick(400),
                available=tick(400),
            ),
            _verified_member(
                receipt_store,
                process,
                process_pin,
                entity_c,
                field,
                [(tick(520), 52.0), (tick(552), 55.0), (tick(560), 56.0)],
                start=tick(510),
                end=tick(600),
                available=tick(600),
            ),
            _verified_member(
                receipt_store,
                process,
                process_pin,
                entity("D"),
                field,
                [(tick(2), 1.0), (tick(12), 2.0), (tick(31), 3.0)],
                start=tick(0),
                end=tick(200),
                available=tick(200),
                fixture_available=tick(60),
                closed_through=tick(60),
            ),
        ]
        scope_pin = qualification.publish_scope(
            receipt_store,
            members,
            population_store=population_store,
            population_pin=population_pin,
            role=role,
            process=process,
            process_pin=process_pin,
        )
        receipt_pin = _receipt(
            receipt_store, population_pin, process, process_pin, scope_pin, members, role=role
        )
        processes.append(process)
        pins.append(process_pin)
        receipt_pins.append(receipt_pin)
        qualified_members.append(members)
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
                start_ns=member["start_ns"],
                end_ns=member["end_ns"],
                available_ns=member["known_ns"],
                evidence=[population_pin],
            )
        )
    for process, process_pin, receipt_pin, members in zip(
        processes, pins, receipt_pins, qualified_members, strict=True
    ):
        field = "price" if process["stream"] == "prices" else "count"
        for member_row in members:
            member = member_row["entity"]
            verification = member_row["verification"]
            fixture = json.loads(
                (
                    receipt_store
                    / "qualification-synthetic-fixtures"
                    / f"{verification['input_pin']}.json"
                ).read_text()
            )
            assertion = dict(
                receipt_pin=receipt_pin,
                scope_pin=qualification.load_receipt(receipt_store, receipt_pin)["scope_pin"],
                population_pin=population_pin,
                entity=member,
                start_ns=member_row["start_ns"],
                end_ns=member_row["end_ns"],
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
            for event in fixture["events"]:
                rows.append(
                    dict(
                        kind="event",
                        entity=member,
                        stream=process["stream"],
                        id=event["id"],
                        event_ns=event["event_ns"],
                        available_ns=fixture["available_ns"],
                        evidence=sorted(
                            [
                                receipt_pin,
                                verification["input_pin"],
                            ]
                        ),
                        values=event["values"],
                    )
                )
    observation_store = tmp_path / "observations"
    observation_pin = observations.publish(
        observation_store,
        sorted(rows, key=observations.key),
        schema=dict(
            version="market-observations-v1",
            role=role,
            availability="point_in_time",
            streams=streams,
        ),
        cutoff=tick(700),
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
        role=role,
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
                dict(entity="chain/market/C", decision_ns=20),
                dict(entity="chain/market/C", decision_ns=80),
                dict(entity="chain/market/C", decision_ns=195),
                dict(entity="chain/market/C", decision_ns=225),
                dict(entity="chain/market/D", decision_ns=10),
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
    assert len(rows) == 9
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
    asof_history = next(r for r in rows if r["pool"].endswith("/C") and r["decision_ns"] == 225)
    history_row = rows.index(asof_history)
    history_frames = [
        matrix[history_row][i : i + width]
        for i in range(0, len(matrix[history_row]), width)
    ]
    history_feature = next(
        i
        for i, feature in enumerate(snapshot.schema["features"])
        if feature["name"] == "trades/count"
    )
    event_present_feature = next(
        i
        for i, feature in enumerate(snapshot.schema["features"])
        if feature["name"] == "trades/event_present"
    )
    assert any(
        not math.isnan(frame[history_feature]) for frame in history_frames[:-1]
    )  # decision-time history stays before the scheduled-origin frame
    assert math.isnan(history_frames[-1][history_feature])
    assert math.isnan(history_frames[-1][event_present_feature])
    ready = next(r for r in rows if r["pool"].endswith("/A") and r["decision_ns"] == 101)
    assert (
        ready["input_start_ns"] == 101
    )  # complete support is not available until the source cutoff
    assert first["accounting"] == {"schedule_origins": 9, "quiet_origins": 8}
    reentered = next(r for r in rows if r["pool"].endswith("/C") and r["decision_ns"] == 80)
    assert reentered["target_status"][count_index] == "observed"
    reentered_row = rows.index(reentered)
    assert target_values[reentered_row][count_index] == 2.0
    trades_index = next(
        i
        for i, feature in enumerate(snapshot.schema["features"])
        if feature["name"] == "trades/count"
    )
    reentered_frames = [
        matrix[reentered_row][i : i + width] for i in range(0, len(matrix[reentered_row]), width)
    ]
    assert 45.0 not in [frame[trades_index] for frame in reentered_frames]
    assert reentered["input_start_ns"] == 80
    boundary = next(r for r in rows if r["pool"].endswith("/C") and r["decision_ns"] == 195)
    assert boundary["target_status"][count_index] == "censored"
    later_reentry = next(r for r in rows if r["pool"].endswith("/C") and r["decision_ns"] == 225)
    later_reentry_row = rows.index(later_reentry)
    later_frames = [
        matrix[later_reentry_row][i : i + width]
        for i in range(0, len(matrix[later_reentry_row]), width)
    ]
    later_values = [frame[trades_index] for frame in later_frames]
    assert 21.0 in later_values
    assert 17.0 not in later_values
    assert later_reentry["input_start_ns"] == 210
    partial = next(r for r in rows if r["pool"].endswith("/D"))
    assert partial["target_status"][count_index] == "censored"
    assert math.isnan(target_values[rows.index(partial)][count_index])
    assert partial["target_details"][count_index]["lower_bound"] == 1
    assert partial["target_details"][count_index]["lower_bound_available_ns"] == 60
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
        cutoff=700,
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


def test_episode_schema_identity_is_cohort_independent_but_contract_bound(tmp_path):
    f = _build_fixture(tmp_path)
    source = observations.Source(f["observation_store"], f["observation_pin"], role="train")
    plan = dict(
        context=3,
        max_history_ns=50,
        horizons_ns=[5],
        price_targets=[
            dict(stream="prices", feature="price", horizon_ns=5, name="prices/return_5ns")
        ],
    )
    wrapper = sensing._load_source(f["sensing_store"], f["wrapper"]["source_pin"])
    schema = sensing._episode_schema(source, wrapper, plan)
    assert schema["representation"]["version"] == "2"

    separate_cohort = dict(wrapper)
    separate_cohort.update(
        observation_source_pin="d" * 64,
        population_pin="e" * 64,
        role="research_evaluation",
        fixture_source_pin="f" * 64,
    )
    separate_cohort["receipt_refs"] = list(reversed(wrapper["receipt_refs"]))
    assert sensing._episode_schema(source, separate_cohort, plan) == schema

    changed_contract = dict(separate_cohort)
    changed_contract["receipt_refs"] = [
        dict(ref, process_contract_pin="a" * 64) for ref in separate_cohort["receipt_refs"]
    ]
    assert sensing._episode_schema(source, changed_contract, plan) != schema
    changed_plan = dict(plan, max_history_ns=51)
    assert sensing._episode_schema(source, separate_cohort, changed_plan) != schema


def _materialize_fixture(f, output, *, scale, shift):
    def tick(value):
        return shift + value * scale

    prefix = f["prefix"]
    schedule_rows = [
        ("A", 20),
        ("A", 95),
        ("A", 150),
        ("A", 225),
        ("B", 20),
        ("C", 20),
        ("C", 80),
        ("C", 150),
        ("C", 195),
        ("C", 225),
        ("C", 350),
        ("C", 550),
        ("D", 10),
    ]
    origins = [
        dict(entity=f"chain/market/{prefix}{name}", decision_ns=tick(decision))
        for name, decision in schedule_rows
    ]
    schedule_store = output / "schedule"
    schedule_pin = sensing.publish_schedule(
        schedule_store,
        sorted(origins, key=lambda row: (row["entity"], row["decision_ns"])),
        population_store=f["population_store"],
        population_pin=f["population_pin"],
        role=f["role"],
    )
    plan = dict(context=3, max_history_ns=50 * scale, horizons_ns=[5 * scale], price_targets=[])
    result = sensing.materialize_episode(
        f["observation_store"],
        f["wrapper"]["source_pin"],
        sensing_store=f["sensing_store"],
        schedule_store=schedule_store,
        schedule_pin=schedule_pin,
        qualification_store=f["receipt_store"],
        population_store=f["population_store"],
        output=output / "snapshot",
        plan=plan,
        role=f["role"],
    )
    return result, plan


def test_scheduled_sensing_fits_resumes_and_scores_separate_synthetic_cohort(tmp_path):
    import numpy as np

    from memecoin_experience.learning import batches

    scale = 10**9
    train = _build_fixture(tmp_path / "train", scale=scale)
    train_result, _ = _materialize_fixture(train, tmp_path / "train-run", scale=scale, shift=0)
    snapshot = storage.Snapshot(tmp_path / "train-run/snapshot", train_result["snapshot"])
    trade_target = next(
        i
        for i, target in enumerate(snapshot.schema["targets"])
        if target["name"] == "trades/events_5000000000ns"
    )
    exact_zero = None
    exact_zero_value = None
    for index in range(len(snapshot.parts)):
        part_path, part_rows = snapshot.partition(index)
        for row_index, row in enumerate(part_rows):
            if row["pool"].endswith("/A") and row["decision_ns"] == 20 * scale:
                raw_y = struct.unpack(
                    f"<{len(part_rows) * len(snapshot.schema['targets'])}d",
                    (part_path / "y.f64").read_bytes(),
                )
                exact_zero = row
                exact_zero_value = raw_y[row_index * len(snapshot.schema["targets"]) + trade_target]
                break
        if exact_zero is not None:
            break
    assert exact_zero["target_status"][trade_target] == "observed"
    assert exact_zero_value == 0.0

    split = dict(train_start_s=1, train_end_s=350, validation_end_s=500, test_end_s=700)
    config = dict(
        width=8,
        batch=4,
        epochs=1,
        seed=19,
        cpu_threads=1,
        checkpoint_every=1,
        objective="partial_count_lower_bound_pinball_v1",
    )
    interrupted_path = tmp_path / "interrupted"
    interrupted = learning.train(
        tmp_path / "train-run/snapshot",
        train_result["snapshot"],
        interrupted_path,
        plan=split,
        config=config,
        stop_after=1,
    )
    assert interrupted["status"] == "interrupted"
    resumed = learning.train(
        tmp_path / "train-run/snapshot",
        train_result["snapshot"],
        interrupted_path,
        plan=split,
        config=config,
    )
    fresh = learning.train(
        tmp_path / "train-run/snapshot",
        train_result["snapshot"],
        tmp_path / "fresh",
        plan=split,
        config=config,
    )
    import torch

    resumed_weights = torch.load(interrupted_path / "artifact/weights.pt", weights_only=True)
    fresh_weights = torch.load(tmp_path / "fresh/artifact/weights.pt", weights_only=True)
    assert all(torch.equal(resumed_weights[key], fresh_weights[key]) for key in fresh_weights)
    assert fresh["production_eligible"] is False
    stats = resumed["statistics"]
    assert stats["population"]["train_usable"] > 0
    assert stats["population"]["validation_usable"] > 0
    assert stats["population"]["test_usable"] > 0
    assert stats["population"]["train_partial_count_usable"] > 0
    assert resumed["production_eligible"] is False

    eval_shift = 1000 * scale
    evaluation_fixture = _build_fixture(
        tmp_path / "evaluation",
        role="research_evaluation",
        prefix="eval-",
        scale=scale,
        shift=eval_shift,
    )
    evaluation_result, _ = _materialize_fixture(
        evaluation_fixture, tmp_path / "evaluation-run", scale=scale, shift=eval_shift
    )
    evaluation_snapshot = storage.Snapshot(
        tmp_path / "evaluation-run/snapshot", evaluation_result["snapshot"]
    )
    assert evaluation_snapshot.schema == snapshot.schema
    artifact = interrupted_path / "artifact"
    artifact_pin = storage.file_hash(artifact / "manifest.json")
    pools = [f"chain/market/eval-{name}" for name in "ABCD"]
    registered = evaluation.register(
        tmp_path / "protocols", artifact, artifact_pin, start_s=1000, end_s=1700, pools=pools
    )
    scored = evaluation.score(
        tmp_path / "protocols",
        registered["protocol"],
        tmp_path / "evaluation-run/snapshot",
        evaluation_result["snapshot"],
        artifact,
        tmp_path / "evaluation-report",
    )
    assert scored["supported_pools"] == 3  # D remains censored, not counted as exact support.
    assert scored["production_eligible"] is False
    test_batch = next(batches(snapshot, split, "test", 8, with_censoring=True))[0]
    assert np.isfinite(Predictor(artifact, artifact_pin).predict(test_batch)).all()


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
