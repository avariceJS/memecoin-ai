"""Bounded offline sensing episodes over sealed observation and qualification artifacts."""

import json
import math
import sqlite3
import tempfile
from pathlib import Path

from memecoin_population.catalog import manifest as population_manifest
from memecoin_population.catalog import records as population_records

from . import observations, qualification, sparse
from .schema import validate as validate_experience_schema
from .storage import PIN, atomic_json, build, canonical, digest, file_hash, safe_path

NS = 10**9
PAGE_SIZE = 512


def _page(store, pin, *, kind):
    path = safe_path(store) / f"sensing-{kind}-pages" / f"{pin}.json"
    rows = json.loads(path.read_text())
    if digest(rows) != pin or not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise ValueError(f"sensing_{kind}_page_checksum")
    return rows


def _schedule_manifest(store, pin):
    path = safe_path(store) / "sensing-schedules" / f"{pin}.json"
    manifest = json.loads(path.read_text())
    if digest(manifest) != pin or manifest.get("version") != "sensing-schedule-v1":
        raise ValueError("sensing_schedule_checksum")
    pages = manifest.get("pages")
    if (
        type(manifest.get("origins")) is not int
        or manifest["origins"] < 1
        or not isinstance(pages, list)
        or not pages
        or any(
            not isinstance(page, dict)
            or type(page.get("count")) is not int
            or not 1 <= page["count"] <= PAGE_SIZE
            for page in pages
        )
        or sum(page["count"] for page in pages) != manifest["origins"]
    ):
        raise ValueError("sensing_schedule_manifest")
    return manifest


def schedule_rows(store, pin):
    manifest = _schedule_manifest(store, pin)
    previous = None
    count = 0
    for page in manifest["pages"]:
        if (
            set(page) != {"pin", "count", "first", "last"}
            or not isinstance(page["pin"], str)
            or not PIN.fullmatch(page["pin"])
            or type(page["count"]) is not int
            or not 1 <= page["count"] <= PAGE_SIZE
        ):
            raise ValueError("sensing_schedule_page_ref")
        rows = _page(store, page["pin"], kind="schedule")
        if len(rows) != page["count"]:
            raise ValueError("sensing_schedule_page_count")
        for row in rows:
            if (
                set(row) != {"entity", "decision_ns"}
                or not isinstance(row["entity"], str)
                or type(row["decision_ns"]) is not int
                or row["decision_ns"] < 0
            ):
                raise ValueError("sensing_schedule_row")
            key = row["entity"], row["decision_ns"]
            if previous is not None and key <= previous:
                raise ValueError("sensing_schedule_order")
            previous = key
            count += 1
            yield row, page["pin"]
        if (
            list((rows[0]["entity"], rows[0]["decision_ns"])) != page["first"]
            or list((rows[-1]["entity"], rows[-1]["decision_ns"])) != page["last"]
        ):
            raise ValueError("sensing_schedule_page_bounds")
    if count != manifest["origins"]:
        raise ValueError("sensing_schedule_count")


def publish_schedule(
    store, origins, *, population_store, population_pin, role, page_size=PAGE_SIZE
):
    """Seal sorted scheduled decisions in bounded pages; decisions do not depend on arrivals."""
    population = population_manifest(population_store, population_pin)
    if population["kind"] != "population" or population["definition"]["role"] != role:
        raise ValueError("sensing_schedule_population_role")
    if type(page_size) is not int or not 1 <= page_size <= PAGE_SIZE:
        raise ValueError("sensing_schedule_page_size")
    store = safe_path(store)
    store.mkdir(parents=True, exist_ok=True)
    (store / "sensing-schedule-pages").mkdir(exist_ok=True)
    (store / "sensing-schedules").mkdir(exist_ok=True)
    pages, batch, previous, count = [], [], None, 0
    with tempfile.TemporaryDirectory(prefix="sensing-schedule-", dir=store) as scratch:
        db = sqlite3.connect(Path(scratch) / "members.sqlite")
        db.execute(
            "create table members("
            "entity text,start integer,end integer,known integer,"
            "primary key(entity,start,end))"
        )
        db.execute("create index member_interval_lookup on members(entity,start,end,known)")
        try:
            for row in population_records(population_store, population_pin):
                db.execute(
                    "insert into members values(?,?,?,?)",
                    (row["entity"], row["start_ns"], row["end_ns"], row["known_ns"]),
                )

            def flush():
                if not batch:
                    return
                pin = digest(batch)
                atomic_json(store / "sensing-schedule-pages" / f"{pin}.json", batch)
                pages.append(
                    dict(
                        pin=pin,
                        count=len(batch),
                        first=[batch[0]["entity"], batch[0]["decision_ns"]],
                        last=[batch[-1]["entity"], batch[-1]["decision_ns"]],
                    )
                )
                batch.clear()

            for origin in origins:
                if not isinstance(origin, dict) or set(origin) != {"entity", "decision_ns"}:
                    raise ValueError("sensing_schedule_row")
                entity = origin["entity"]
                decision = origin["decision_ns"]
                if not isinstance(entity, str) or type(decision) is not int:
                    raise ValueError("sensing_schedule_row")
                member = db.execute(
                    "select start,end from members where entity=? and start<=? "
                    "and end>? and known<=? order by start desc limit 1",
                    (entity, decision, decision, decision),
                ).fetchone()
                if member is None or not member[0] <= decision < member[1]:
                    raise ValueError("sensing_schedule_origin_outside_population")
                key = entity, decision
                if previous is not None and key <= previous:
                    raise ValueError("sensing_schedule_order_or_duplicate")
                previous = key
                batch.append(dict(entity=entity, decision_ns=decision))
                count += 1
                if len(batch) == page_size:
                    flush()
            flush()
        finally:
            db.close()
    if not count:
        raise ValueError("sensing_schedule_empty")
    manifest = dict(
        version="sensing-schedule-v1",
        population_pin=population_pin,
        role=role,
        origins=count,
        pages=pages,
    )
    pin = digest(manifest)
    atomic_json(store / "sensing-schedules" / f"{pin}.json", manifest)
    return pin


def _stream_binding(source_schema, stream, process, process_pin, payload_schema):
    stream_schema = source_schema["streams"].get(stream)
    if stream_schema is None or stream_schema["process"] != process["name"]:
        raise ValueError("sensing_observation_process_binding")
    expected = [
        dict(name=field["name"], unit=field["unit"], modality=process["modality"])
        for field in payload_schema["fields"]
    ]
    if stream_schema["features"] != expected:
        raise ValueError("sensing_observation_payload_binding")
    return dict(
        version="observation-process-binding-v1",
        process_name=process["name"],
        stream=stream,
        process_contract_pin=process_pin,
        payload_schema_pin=process["payload_schema_pin"],
        modality=process["modality"],
        features=stream_schema["features"],
    )


def _expected_source_rows(
    source,
    qualification_store,
    population_store,
    population_pin,
    role,
    receipt_refs,
    scratch,
):
    """Build disk-backed expected event/support/enrollment sets from receipt verified fixtures."""
    db = sqlite3.connect(Path(scratch) / "crosscheck.sqlite")
    db.executescript("""create table expected_events(k text primary key,payload text);
    create table expected_support(k text primary key,payload text);
    create table expected_enrollment(k text primary key,payload text);""")
    pop = population_manifest(population_store, population_pin)
    if pop["definition"]["role"] != role:
        raise ValueError("sensing_population_role")
    try:
        for member in population_records(population_store, population_pin):
            enrollment = dict(
                kind="enrollment",
                entity=member["entity"],
                start_ns=member["start_ns"],
                end_ns=member["end_ns"],
                available_ns=member["known_ns"],
                evidence=[population_pin],
            )
            db.execute(
                "insert into expected_enrollment values(?,?)",
                (
                    canonical([member["entity"], member["start_ns"], member["end_ns"]]).decode(),
                    canonical(enrollment).decode(),
                ),
            )
        for ref in receipt_refs:
            receipt = qualification.load_receipt(qualification_store, ref["receipt_pin"])
            process, process_pin = receipt["process_contract"], receipt["process_contract_pin"]
            if (
                receipt["population_pin"] != population_pin
                or receipt["role"] != role
                or process_pin != ref["process_contract_pin"]
            ):
                raise ValueError("sensing_receipt_binding")
            stream = process["stream"]
            payload_schema = json.loads(
                (
                    safe_path(qualification_store)
                    / "qualification-payload-schemas"
                    / f"{process['payload_schema_pin']}.json"
                ).read_text()
            )
            destination = _stream_binding(
                source.schema, stream, process, process_pin, payload_schema
            )
            scope = qualification._scope_manifest(qualification_store, receipt["scope_pin"])
            for member in qualification._scope_members(qualification_store, scope):
                verified = member["status"] in {"complete", "partial"}
                proof = member["verification"]
                if verified:
                    result = qualification._verify_member(
                        qualification_store, process, process_pin, member
                    )
                    if result is None:
                        raise ValueError("sensing_member_verification_missing")
                    fixture_path = (
                        safe_path(qualification_store)
                        / "qualification-synthetic-fixtures"
                        / f"{proof['input_pin']}.json"
                    )
                    fixture = json.loads(fixture_path.read_text())
                    if digest(fixture) != proof["input_pin"]:
                        raise ValueError("sensing_fixture_checksum")
                else:
                    # These states may project only their explicit support
                    # assertion. They carry no event or candidate-count claim.
                    if proof is not None:
                        raise ValueError("sensing_unqualified_member_proof")
                    fixture = {"events": []}
                expected_support = qualification.observation_fact(
                    dict(
                        receipt_pin=ref["receipt_pin"],
                        scope_pin=receipt["scope_pin"],
                        population_pin=population_pin,
                        entity=member["entity"],
                        start_ns=member["start_ns"],
                        end_ns=member["end_ns"],
                    ),
                    receipt_store=qualification_store,
                    population_store=population_store,
                    stream=stream,
                    destination_schema=destination,
                )
                db.execute(
                    "insert into expected_support values(?,?)",
                    (
                        canonical(
                            [member["entity"], stream, member["start_ns"], member["end_ns"]]
                        ).decode(),
                        canonical(expected_support).decode(),
                    ),
                )
                for event in fixture["events"]:
                    fact = dict(
                        kind="event",
                        entity=member["entity"],
                        stream=stream,
                        id=event["id"],
                        event_ns=event["event_ns"],
                        available_ns=fixture["available_ns"],
                        evidence=sorted([ref["receipt_pin"], proof["input_pin"]]),
                        values=event["values"],
                    )
                    db.execute(
                        "insert into expected_events values(?,?)",
                        (
                            canonical(
                                [fact["entity"], stream, fact["event_ns"], fact["id"]]
                            ).decode(),
                            canonical(fact).decode(),
                        ),
                    )
        db.commit()
        return db
    except BaseException:
        db.close()
        raise


def publish_source(
    store,
    *,
    observation_store,
    observation_source_pin,
    qualification_store,
    population_store,
    population_pin,
    role,
    receipt_pins,
):
    """Publish wrapper only after exact source rows reconcile to receipt-backed fixtures."""
    source = observations.Source(observation_store, observation_source_pin, role=role)
    if not isinstance(receipt_pins, list) or not receipt_pins:
        raise ValueError("sensing_receipt_refs")
    receipt_refs = []
    seen_streams = set()
    for pin in sorted(set(receipt_pins)):
        receipt = qualification.load_receipt(qualification_store, pin)
        process = receipt["process_contract"]
        stream = process["stream"]
        if (
            stream in seen_streams
            or receipt["population_pin"] != population_pin
            or receipt["role"] != role
        ):
            raise ValueError("sensing_receipt_duplicate_or_scope")
        seen_streams.add(stream)
        receipt_refs.append(
            dict(
                receipt_pin=pin,
                scope_pin=receipt["scope_pin"],
                process_contract_pin=receipt["process_contract_pin"],
                stream=stream,
            )
        )
    if seen_streams != set(source.schema["streams"]):
        raise ValueError("sensing_process_stream_set")
    fixture_source_pin = digest(
        dict(
            version="sensing-fixture-source-v1",
            observation_source_pin=observation_source_pin,
            population_pin=population_pin,
            receipt_refs=receipt_refs,
        )
    )
    wrapper = dict(
        version="sensing-source-v1",
        observation_source_pin=observation_source_pin,
        population_pin=population_pin,
        role=role,
        receipt_refs=receipt_refs,
        qualification_receipt_pins=[r["receipt_pin"] for r in receipt_refs],
        process_contract_pins=[r["process_contract_pin"] for r in receipt_refs],
        fixture_source_pin=fixture_source_pin,
    )
    with tempfile.TemporaryDirectory(prefix="sensing-crosscheck-") as scratch:
        expected = _expected_source_rows(
            source,
            qualification_store,
            population_store,
            population_pin,
            role,
            receipt_refs,
            scratch,
        )
        try:
            for row in source.rows():
                kind = row["kind"]
                if kind == "lifecycle":
                    raise ValueError("sensing_lifecycle_requires_independent_evidence")
                if kind == "enrollment":
                    key = canonical([row["entity"], row["start_ns"], row["end_ns"]]).decode()
                    table = "expected_enrollment"
                elif kind == "event":
                    key = canonical(
                        [row["entity"], row["stream"], row["event_ns"], row["id"]]
                    ).decode()
                    table = "expected_events"
                elif kind == "support":
                    key = canonical(
                        [row["entity"], row["stream"], row["start_ns"], row["end_ns"]]
                    ).decode()
                    table = "expected_support"
                else:
                    raise ValueError("sensing_observation_kind")
                expected_row = expected.execute(
                    f"select payload from {table} where k=?", (key,)
                ).fetchone()
                if expected_row is None or expected_row[0] != canonical(row).decode():
                    raise ValueError("sensing_source_fixture_reconciliation")
                expected.execute(f"delete from {table} where k=?", (key,))
            for table in ("expected_enrollment", "expected_events", "expected_support"):
                if expected.execute(f"select 1 from {table} limit 1").fetchone():
                    raise ValueError("sensing_source_fixture_missing_fact")
        finally:
            expected.close()
    store = safe_path(store)
    (store / "sensing-sources").mkdir(parents=True, exist_ok=True)
    pin = digest(wrapper)
    atomic_json(store / "sensing-sources" / f"{pin}.json", wrapper)
    return dict(source_pin=pin, fixture_source_pin=fixture_source_pin)


def _load_source(store, pin):
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("sensing_source_pin")
    wrapper = json.loads((safe_path(store) / "sensing-sources" / f"{pin}.json").read_text())
    if digest(wrapper) != pin or wrapper.get("version") != "sensing-source-v1":
        raise ValueError("sensing_source_checksum")
    expected_fixture_pin = digest(
        dict(
            version="sensing-fixture-source-v1",
            observation_source_pin=wrapper["observation_source_pin"],
            population_pin=wrapper["population_pin"],
            receipt_refs=wrapper["receipt_refs"],
        )
    )
    if wrapper["fixture_source_pin"] != expected_fixture_pin:
        raise ValueError("sensing_fixture_source_pin")
    if wrapper.get("qualification_receipt_pins") != [
        r["receipt_pin"] for r in wrapper["receipt_refs"]
    ] or wrapper.get("process_contract_pins") != [
        r["process_contract_pin"] for r in wrapper["receipt_refs"]
    ]:
        raise ValueError("sensing_source_bindings")
    return wrapper


def _ordered_receipt_refs(wrapper):
    """Keep sensing feature/target order semantic, not dependent on receipt hash order."""
    return sorted(wrapper["receipt_refs"], key=lambda ref: ref["stream"])


def _episode_schema(source, wrapper, plan):
    required = {"context", "max_history_ns", "horizons_ns", "price_targets"}
    if (
        set(plan) != required
        or type(plan["context"]) is not int
        or not 1 <= plan["context"] <= 4096
        or type(plan["max_history_ns"]) is not int
        or plan["max_history_ns"] <= 0
        or not isinstance(plan["horizons_ns"], list)
        or not plan["horizons_ns"]
        or any(type(h) is not int or h <= 0 for h in plan["horizons_ns"])
        or len(set(plan["horizons_ns"])) != len(plan["horizons_ns"])
        or not isinstance(plan["price_targets"], list)
    ):
        raise ValueError("sensing_episode_plan")
    if source.schema["availability"] != "point_in_time":
        raise ValueError("sensing_source_requires_point_in_time")
    features, targets = [], []
    refs = _ordered_receipt_refs(wrapper)
    stream_contracts = []
    for ref in refs:
        stream = ref["stream"]
        stream_schema = source.schema["streams"][stream]
        stream_contracts.append(
            dict(
                name=stream,
                process_contract_pin=ref["process_contract_pin"],
                process=stream_schema["process"],
                features=stream_schema["features"],
            )
        )
        for field in stream_schema["features"]:
            features.append(dict(field, name=f"{stream}/{field['name']}"))
        features.extend(
            dict(
                name=f"{stream}/{field['name']}_observed", unit="indicator", modality="feature_mask"
            )
            for field in stream_schema["features"]
        )
        features.extend(
            [
                dict(
                    name=f"{stream}/event_present", unit="indicator", modality="observation_process"
                ),
                dict(
                    name=f"{stream}/log_age_seconds",
                    unit="log1p_seconds",
                    modality="observation_process",
                ),
            ]
        )
        features.extend(
            dict(name=f"{stream}/support_{status}", unit="indicator", modality="support_status")
            for status in (
                "complete",
                "unknown",
                "failed",
                "stopped",
                "unsupported",
                "unasserted_gap",
            )
        )
        for horizon in plan["horizons_ns"]:
            targets.append(
                dict(
                    name=f"{stream}/events_{horizon}ns",
                    unit="events",
                    kind=f"observed_process_count@{horizon}ns",
                )
            )
    for target in plan["price_targets"]:
        if (
            not isinstance(target, dict)
            or set(target) != {"stream", "feature", "horizon_ns", "name"}
            or target["stream"] not in source.schema["streams"]
            or target["feature"]
            not in {f["name"] for f in source.schema["streams"][target["stream"]]["features"]}
            or type(target["horizon_ns"]) is not int
            or target["horizon_ns"] <= 0
            or not isinstance(target["name"], str)
            or not target["name"]
        ):
            raise ValueError("sensing_price_target")
        targets.append(
            dict(
                name=target["name"],
                unit="log_ratio",
                kind=f"endpoint_log_return@{target['horizon_ns']}ns",
            )
        )
    return validate_experience_schema(
        dict(
            schema="brain-experience-v2",
            layout="dense-sequence-f64-v1",
            context=plan["context"],
            features=features,
            targets=targets,
            representation=dict(
                name="brain-sensing-episode",
                version="2",
                code_sha=digest(
                    dict(
                        implementation_sha=file_hash(Path(__file__)),
                        plan=plan,
                        observation_version=source.schema["version"],
                        availability=source.schema["availability"],
                        streams=stream_contracts,
                    )
                ),
            ),
            availability=source.schema["availability"],
        )
    )


def materialize_episode(
    source_store,
    source_pin,
    *,
    sensing_store,
    schedule_store,
    schedule_pin,
    qualification_store,
    population_store,
    output,
    plan,
    role="train",
    chunk=256,
):
    """Build one scheduled episode without mutating sparse.materialize defaults."""
    wrapper = _load_source(sensing_store, source_pin)
    source = observations.Source(source_store, wrapper["observation_source_pin"], role=role)
    if source.pin != wrapper["observation_source_pin"] or wrapper["role"] != role:
        raise ValueError("sensing_source_role_or_pin")
    schedule = _schedule_manifest(schedule_store, schedule_pin)
    if schedule["population_pin"] != wrapper["population_pin"] or schedule["role"] != role:
        raise ValueError("sensing_schedule_binding")
    schema = _episode_schema(source, wrapper, plan)
    cutoff = source.manifest["binding"]["cutoff_ns"]
    # Re-run the fixture-to-source reconciliation on consumption. A hash-valid
    # wrapper is only a claim; it is not itself qualification evidence.
    checked = publish_source(
        sensing_store,
        observation_store=source_store,
        observation_source_pin=wrapper["observation_source_pin"],
        qualification_store=qualification_store,
        population_store=population_store,
        population_pin=wrapper["population_pin"],
        role=role,
        receipt_pins=[ref["receipt_pin"] for ref in wrapper["receipt_refs"]],
    )
    if checked["source_pin"] != source_pin:
        raise ValueError("sensing_source_revalidation_mismatch")
    index = tempfile.TemporaryDirectory(prefix="sensing-index-")
    db = source.index(Path(index.name) / "index.sqlite")
    counts = {"schedule_origins": 0, "quiet_origins": 0}
    stream_names = [ref["stream"] for ref in _ordered_receipt_refs(wrapper)]
    try:

        def rows():
            for origin, schedule_page_pin in schedule_rows(schedule_store, schedule_pin):
                entity = origin["entity"]
                decision = origin["decision_ns"]
                member = db.execute(
                    "select start,end,available from members where entity=? "
                    "and start<=? and end>? and available<=? order by start desc limit 1",
                    (entity, decision, decision, decision),
                ).fetchone()
                if member is None or not member[0] <= decision < member[1] or member[2] > decision:
                    raise ValueError("sensing_schedule_origin_outside_available_population")
                history_start = max(member[0], max(0, decision - plan["max_history_ns"]))
                input_deps = {(schedule_page_pin, decision, decision)}
                support_context = []
                support_by_stream = {}
                quiet = True
                for stream in stream_names:
                    names = [f["name"] for f in source.schema["streams"][stream]["features"]]
                    history = [
                        json.loads(r[0])
                        for r in db.execute(
                            "select payload from events where entity=? and stream=? "
                            "and t>=? and t<=? and available<=? order by t desc,id desc limit ?",
                            (
                                entity,
                                stream,
                                history_start,
                                decision,
                                decision,
                                max(0, plan["context"] - 1),
                            ),
                        )
                    ]
                    history.reverse()
                    if history:
                        quiet = False
                    support_state = sparse.support(
                        db,
                        entity,
                        stream,
                        history_start,
                        decision + 1,
                        decision,
                    )
                    support_rows = db.execute(
                        "select payload from support where entity=? and stream=? "
                        "and start<? and end>? and available<=?",
                        (
                            entity,
                            stream,
                            decision + 1,
                            history_start,
                            decision,
                        ),
                    ).fetchall()
                    support_values = [json.loads(row[0]) for row in support_rows]
                    support_statuses = {row["status"] for row in support_values}
                    support_by_stream[stream] = (support_state, support_values, support_statuses)
                    support_context.append(
                        dict(
                            stream=stream,
                            status="complete" if support_state["complete"] else "unknown",
                            reasons=support_state["reasons"],
                            available_ns=support_state["available"],
                        )
                    )
                # Store each stream's ordered history in separate context lanes.
                values = [[None] * len(schema["features"]) for _ in range(plan["context"])]
                feature_cursor = 0
                for stream in stream_names:
                    support_state, support_values, support_statuses = support_by_stream[stream]
                    names = [f["name"] for f in source.schema["streams"][stream]["features"]]
                    events = [
                        json.loads(r[0])
                        for r in db.execute(
                            "select payload from events where entity=? and stream=? "
                            "and t>=? and t<=? and available<=? order by t desc,id desc limit ?",
                            (
                                entity,
                                stream,
                                history_start,
                                decision,
                                decision,
                                max(0, plan["context"] - 1),
                            ),
                        )
                    ]
                    events.reverse()
                    offset = plan["context"] - 1 - len(events)
                    for j, event in enumerate(events):
                        t = offset + j
                        for k, value in enumerate(event["values"]):
                            values[t][feature_cursor + k] = value
                            values[t][feature_cursor + len(names) + k] = (
                                0.0 if value is None else 1.0
                            )
                        values[t][feature_cursor + 2 * len(names)] = 1.0
                        values[t][feature_cursor + 2 * len(names) + 1] = math.log1p(
                            (decision - event["event_ns"]) / NS
                        )
                        shard = db.execute(
                            "select json_extract(payload,'$._source_shard') from events "
                            "where entity=? and stream=? and t=? and id=?",
                            (entity, stream, event["event_ns"], event["id"]),
                        ).fetchone()[0]
                        input_deps.add((shard, event["event_ns"], event["available_ns"]))
                    feature_cursor += 2 * len(names) + 2 + 6
                    for support_row in support_values:
                        input_deps.add(
                            (
                                support_row["_source_shard"],
                                support_row["start_ns"],
                                support_row["available_ns"],
                            )
                        )
                    # The last frame is reserved for scheduled-origin state. It
                    # is independent of any event arrivals and carries support
                    # status without embedding future or price values.
                    offset = feature_cursor - (2 * len(names) + 2 + 6)
                    channel = {
                        name: 0.0
                        for name in (
                            "complete",
                            "unknown",
                            "failed",
                            "stopped",
                            "unsupported",
                            "unasserted_gap",
                        )
                    }
                    if support_state["complete"]:
                        channel["complete"] = 1.0
                    else:
                        for status in support_statuses:
                            if status == "unknown":
                                channel["unknown"] = 1.0
                            elif status in channel:
                                channel[status] = 1.0
                        if "unasserted_gap" in support_state["reasons"]:
                            channel["unasserted_gap"] = 1.0
                    for j, status in enumerate(channel):
                        values[-1][offset + 2 * len(names) + 2 + j] = channel[status]
                if quiet:
                    counts["quiet_origins"] += 1
                targets = []
                maturity_values, target_status, target_available, target_captures, details = (
                    [],
                    [],
                    [],
                    [],
                    [],
                )
                for ref in _ordered_receipt_refs(wrapper):
                    stream = ref["stream"]
                    for horizon in plan["horizons_ns"]:
                        maturity = decision + horizon
                        maturity_values.append(maturity)
                        if maturity > cutoff:
                            target_status.append("pending")
                            target_available.append(None)
                            target_captures.append([])
                            details.append(
                                dict(
                                    reason="fit_cutoff",
                                    process=source.schema["streams"][stream]["process"],
                                    interval_start_ns=decision + 1,
                                    interval_end_ns=maturity + 1,
                                )
                            )
                            targets.append(None)
                            continue
                        member = db.execute(
                            "select end from members where entity=? and start<=? and end>? "
                            "order by start desc limit 1",
                            (entity, decision, decision),
                        ).fetchone()
                        if member is None:
                            raise ValueError("sensing_schedule_nonmember")
                        interval_end = min(maturity, member[0] - 1)
                        n, lower_available = sparse.event_lower_bound(
                            db, entity, stream, decision, interval_end
                        )
                        coverage = sparse.support(
                            db, entity, stream, decision + 1, maturity + 1, cutoff
                        )
                        if maturity >= member[0]:
                            target_status.append("censored")
                            target_available.append(None)
                            target_captures.append([])
                            details.append(
                                dict(
                                    reason="enrollment_boundary",
                                    process=source.schema["streams"][stream]["process"],
                                    interval_start_ns=decision + 1,
                                    interval_end_ns=maturity + 1,
                                    observed_until_ns=member[0],
                                    lower_bound=n,
                                    lower_bound_available_ns=lower_available,
                                )
                            )
                            value = None
                        elif coverage["complete"]:
                            target_status.append("observed")
                            target_available.append(max(maturity, coverage["available"]))
                            target_captures.append(coverage["evidence"])
                            value = float(n)
                            details.append(
                                dict(
                                    reason="complete_observation_process",
                                    process=source.schema["streams"][stream]["process"],
                                    interval_start_ns=decision + 1,
                                    interval_end_ns=maturity + 1,
                                    lower_bound=n,
                                )
                            )
                        else:
                            target_status.append("censored")
                            target_available.append(None)
                            target_captures.append([])
                            details.append(
                                dict(
                                    reason="incomplete_observation_support",
                                    process=source.schema["streams"][stream]["process"],
                                    interval_start_ns=decision + 1,
                                    interval_end_ns=maturity + 1,
                                    lower_bound=n,
                                    lower_bound_available_ns=lower_available,
                                    support_reasons=coverage["reasons"],
                                )
                            )
                            value = None
                        # values are appended in the same order as target contracts.
                        targets.append(value)
                for target in plan["price_targets"]:
                    stream, feature, horizon = (
                        target["stream"],
                        target["feature"],
                        target["horizon_ns"],
                    )
                    maturity = decision + horizon
                    maturity_values.append(maturity)
                    member = db.execute(
                        "select end from members where entity=? and start<=? and end>? "
                        "order by start desc limit 1",
                        (entity, decision, decision),
                    ).fetchone()
                    if member is None:
                        raise ValueError("sensing_schedule_nonmember")
                    event = db.execute(
                        "select payload from events where entity=? and stream=? "
                        "and t=? and available<=? limit 2",
                        (entity, stream, maturity, cutoff),
                    ).fetchall()
                    index_feature = [
                        f["name"] for f in source.schema["streams"][stream]["features"]
                    ].index(feature)
                    base_rows = db.execute(
                        "select payload from events where entity=? and stream=? "
                        "and t=? and available<=? limit 2",
                        (entity, stream, decision, decision),
                    ).fetchall()
                    base = None
                    if len(base_rows) == 1:
                        base_payload = json.loads(base_rows[0])
                        base = base_payload["values"][index_feature]
                        input_deps.add(
                            (
                                base_payload["_source_shard"],
                                base_payload["event_ns"],
                                base_payload["available_ns"],
                            )
                        )
                    if maturity > cutoff:
                        status, value, at, pins, reason = "pending", None, None, [], "fit_cutoff"
                    elif maturity >= member[0]:
                        status, value, at, pins, reason = (
                            "censored",
                            None,
                            None,
                            [],
                            "enrollment_boundary",
                        )
                    elif len(base_rows) != 1 or len(event) != 1 or base is None or base <= 0:
                        status, value, at, pins, reason = (
                            "missing_future",
                            None,
                            None,
                            [],
                            "no_exact_price_endpoint",
                        )
                    else:
                        future = json.loads(event[0][0])
                        price = future["values"][index_feature]
                        if price is None or price <= 0:
                            status, value, at, pins, reason = (
                                "unsupported",
                                None,
                                None,
                                [],
                                "invalid_endpoint_price",
                            )
                        else:
                            status = "observed"
                            value = math.log(price / base)
                            at = max(maturity, future["available_ns"])
                            pins = future["evidence"]
                            reason = "exact_observed_price_endpoint"
                    target_status.append(status)
                    target_available.append(at)
                    target_captures.append(pins)
                    details.append(
                        dict(
                            reason=reason,
                            process=source.schema["streams"][stream]["process"],
                            interval_start_ns=decision,
                            interval_end_ns=maturity,
                        )
                    )
                    targets.append(value)
                if len(targets) != len(schema["targets"]):
                    raise ValueError("sensing_target_accounting")
                deps = [dict(artifact_sha=p, event_ns=t, available_ns=a) for p, t, a in input_deps]
                deps.sort(key=lambda d: (d["event_ns"], d["available_ns"], d["artifact_sha"]))
                deps = [dict(v) for _, v in sorted({digest(d): d for d in deps}.items())]
                counts["schedule_origins"] += 1
                yield dict(
                    id=f"scheduled:{schedule_pin}:{entity}:{decision}",
                    pool=entity,
                    decision_ns=decision,
                    input_start_ns=min(d["event_ns"] for d in deps),
                    input_available_ns=max(d["available_ns"] for d in deps),
                    input_dependencies=deps,
                    values=values,
                    targets=targets,
                    target_status=target_status,
                    target_available_ns=target_available,
                    target_maturity_ns=maturity_values,
                    target_captures=target_captures,
                    target_details=details,
                    sensing_support=support_context,
                    sensing_origin=dict(
                        schedule_pin=schedule_pin, fixture_source_pin=wrapper["fixture_source_pin"]
                    ),
                    schedule_available_ns=decision,
                )

        built = build(
            output,
            rows(),
            sources=[
                dict(kind="sensing-source", path=str(sensing_store), pin=source_pin),
                dict(kind="sensing-schedule", path=str(schedule_store), pin=schedule_pin),
            ],
            cutoff=cutoff,
            context=plan["context"],
            chunk=chunk,
            schema=schema,
            role=role,
            selection=dict(sensing_plan=plan, schedule_pin=schedule_pin),
        )
    finally:
        db.close()
        index.cleanup()
    return dict(**built, accounting=counts)


def budget_dry_run(*, requests, ledger, account_budgets, as_of_ns):
    """Pure simulation decision; this function has no transport or dispatch capability."""
    if type(as_of_ns) is not int or as_of_ns < 0:
        raise ValueError("sensing_budget_as_of")
    accounts = {}
    budget_evidence = []
    for budget in account_budgets:
        if set(budget) != {
            "account",
            "known_ns",
            "expires_ns",
            "remaining_units",
            "unit",
            "entitlement_state",
            "evidence_pin",
        }:
            raise ValueError("sensing_budget_account_schema")
        if (
            budget["account"] in accounts
            or not isinstance(budget["account"], str)
            or not budget["account"]
        ):
            raise ValueError("sensing_budget_account_duplicate")
        if not isinstance(budget["evidence_pin"], str) or not PIN.fullmatch(budget["evidence_pin"]):
            raise ValueError("sensing_budget_evidence")
        if (
            type(budget["known_ns"]) is not int
            or type(budget["expires_ns"]) is not int
            or budget["known_ns"] < 0
            or budget["expires_ns"] <= budget["known_ns"]
            or (
                budget["remaining_units"] is not None
                and (type(budget["remaining_units"]) is not int or budget["remaining_units"] < 0)
            )
        ):
            raise ValueError("sensing_budget_account_interval")
        if (
            budget["entitlement_state"] not in {"verified", "unknown"}
            or not isinstance(budget["unit"], str)
            or not budget["unit"]
        ):
            raise ValueError("sensing_budget_entitlement")
        accounts[budget["account"]] = budget
        budget_evidence.append(budget["evidence_pin"])
    ledger_by_id = {}
    poisoned = set()
    spent = {}
    ledger_evidence = []
    for row in ledger:
        if (
            set(row) != {"request_id", "account", "state", "cost_units", "unit"}
            or row["request_id"] in ledger_by_id
        ):
            raise ValueError("sensing_budget_ledger_schema")
        if row["state"] not in {"reserved", "received", "ambiguous_attempt", "none"}:
            raise ValueError("sensing_budget_ledger_state")
        if not isinstance(row["request_id"], str) or not PIN.fullmatch(row["request_id"]):
            raise ValueError("sensing_budget_ledger_identity")
        ledger_by_id[row["request_id"]] = row
        if not isinstance(row["account"], str) or not row["account"]:
            raise ValueError("sensing_budget_ledger_account")
        ledger_evidence.append(digest(row))
        if row["state"] == "ambiguous_attempt":
            poisoned.add(row["account"])
        elif row["state"] in {"reserved", "received"}:
            budget = accounts.get(row["account"])
            if (
                type(row["cost_units"]) is int
                and row["cost_units"] >= 0
                and budget is not None
                and row["unit"] == budget["unit"]
            ):
                spent[row["account"]] = spent.get(row["account"], 0) + row["cost_units"]
            else:
                poisoned.add(row["account"])
    output = []
    request_evidence = []
    previous = None
    for request in requests:
        if set(request) != {
            "identity",
            "account",
            "cost_units",
            "cost_evidence_pin",
            "expires_ns",
            "unit",
        }:
            raise ValueError("sensing_budget_request_schema")
        identity = request["identity"]
        request_id = digest(identity)
        if previous is not None and request_id <= previous:
            raise ValueError("sensing_budget_request_order")
        previous = request_id
        request_evidence.append(request_id)
        if type(request["expires_ns"]) is not int:
            raise ValueError("sensing_budget_request_expiry")
        ledger_row = ledger_by_id.get(request_id)
        state = ledger_row["state"] if ledger_row else "none"
        if state == "ambiguous_attempt":
            decision = "reconcile_ambiguous_attempt"
        elif state in {"reserved", "received"}:
            decision = "already_reserved_or_received"
        elif as_of_ns >= request["expires_ns"]:
            decision = "blocked_expired"
        elif request["account"] in poisoned:
            decision = "reconcile_ambiguous_attempt"
        else:
            budget = accounts.get(request["account"])
            if (
                budget is None
                or budget["entitlement_state"] != "verified"
                or budget["remaining_units"] is None
                or type(budget["remaining_units"]) is not int
                or budget["known_ns"] > as_of_ns
                or budget["expires_ns"] <= as_of_ns
            ):
                decision = "blocked_budget_unknown"
            elif (
                type(request["cost_units"]) is not int
                or request["cost_units"] < 0
                or request["unit"] != budget["unit"]
                or not isinstance(request["cost_evidence_pin"], str)
                or not PIN.fullmatch(request["cost_evidence_pin"])
            ):
                decision = "blocked_cost_unknown"
            elif (
                spent.get(request["account"], 0) + request["cost_units"] > budget["remaining_units"]
            ):
                decision = "budget_exhausted"
            else:
                decision = "simulation_allow"
                spent[request["account"]] = spent.get(request["account"], 0) + request["cost_units"]
        output.append(dict(request_id=request_id, decision=decision))
    evidence = dict(
        budgets=sorted(budget_evidence),
        requests=request_evidence,
        ledger=sorted(ledger_evidence),
    )
    return dict(
        version="sensing-budget-dry-run-v1",
        as_of_ns=as_of_ns,
        decisions=output,
        evidence=evidence,
        simulated_reservations=0,
        dispatched_requests=0,
        pin=digest(dict(as_of_ns=as_of_ns, decisions=output, evidence=evidence)),
    )
