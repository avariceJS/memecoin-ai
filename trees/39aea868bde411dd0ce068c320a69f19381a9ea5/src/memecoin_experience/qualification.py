"""Hash-bound process qualification scopes and support projections.

This verifies identity, accounting, scope and capability assertions. Matching hashes and
self-declared receipts do not establish provider truth or replace domain qualification.
"""

import hashlib
import json
import math
import sqlite3
import tempfile
from bisect import bisect_left
from functools import lru_cache

from memecoin_population.catalog import entity, records
from memecoin_population.catalog import manifest as population_manifest

from .storage import PIN, atomic_json, canonical, digest, safe_path

STATUSES = {"complete", "partial", "unknown", "failed", "stopped", "unsupported"}
COUNT_KEYS = {"captured", "decoded", "rejected", "qualified", "unresolved"}
CAPABILITIES = {"complete_support", "observation_support", "population_coverage"}
OBSERVED_VALUE_CAPABILITY = "observed_values"
OBSERVED_VALUE_VERIFIERS = {"solana_address_signatures_v1"}
SCOPE_PAGE_SIZE = 512
RULES = {
    "event_identity_rule": "event-identity-rule-v1",
    "event_time_basis": "event-time-basis-v1",
    "interval_convention": "interval-convention-v1",
    "revision_policy": "revision-policy-v1",
}
SUPPORTED_RULE_DEFINITIONS = {
    "event_identity_rule": {"key-v1", "composite-v2"},
    "event_time_basis": {"block-time-v1", "receipt-time-v1"},
    "interval_convention": {"half-open-start-inclusive-end-exclusive-v1"},
    "revision_policy": {"append-revisions-v1", "replace-latest-v2"},
}
SYNTHETIC_DECODER_PIN = digest(
    {"version": "synthetic-decoder-v1", "implementation": "qualification-fixture-records-v1"}
)


def synthetic_source_pin(process_pin, scope_pin):
    return digest(
        {
            "version": "synthetic-source-manifest-v1",
            "process_contract_pin": process_pin,
            "scope_pin": scope_pin,
        }
    )


def publish_payload_schema(store, value):
    """Publish the small, versioned payload definition pinned by a process contract."""
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "fields"}
        or value["version"] != "payload-schema-v1"
        or not isinstance(value["fields"], list)
        or not value["fields"]
    ):
        raise ValueError("qualification_payload_schema")
    names = []
    for field in value["fields"]:
        if (
            not isinstance(field, dict)
            or set(field) != {"name", "unit", "denomination"}
            or any(not isinstance(field[k], str) or not field[k] for k in field)
        ):
            raise ValueError("qualification_payload_schema_field")
        names.append(field["name"])
    if len(set(names)) != len(names):
        raise ValueError("qualification_payload_schema_duplicate_field")
    store = safe_path(store)
    directory = store / "qualification-payload-schemas"
    directory.mkdir(parents=True, exist_ok=True)
    pin = digest(value)
    atomic_json(directory / f"{pin}.json", value)
    return pin


def publish_rule(store, name, definition):
    """Publish one supported versioned process rule; this is not a rule engine."""
    if name not in RULES or not isinstance(definition, str) or not definition:
        raise ValueError("qualification_rule")
    value = dict(version=RULES[name], definition=definition)
    store = safe_path(store)
    directory = store / "qualification-rules"
    directory.mkdir(parents=True, exist_ok=True)
    pin = digest(value)
    atomic_json(directory / f"{pin}.json", value)
    return dict(version=RULES[name], pin=pin)


def process_contract(value, *, store):
    required = {
        "version",
        "name",
        "stream",
        "semantics",
        "unit",
        "source_kind",
        "entity_namespace",
        "modality",
        "payload_schema_pin",
        "event_identity_rule",
        "event_time_basis",
        "timestamp_precision_ns",
        "interval_convention",
        "revision_policy",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value["version"] != "process-contract-v1"
        or any(
            not isinstance(value[k], str) or not value[k]
            for k in required
            - {
                "timestamp_precision_ns",
                "payload_schema_pin",
                "event_identity_rule",
                "event_time_basis",
                "interval_convention",
                "revision_policy",
            }
        )
        or any(
            len(value[k]) > 512
            for k in required
            - {
                "timestamp_precision_ns",
                "payload_schema_pin",
                "event_identity_rule",
                "event_time_basis",
                "interval_convention",
                "revision_policy",
            }
        )
    ):
        raise ValueError("process_contract_schema")
    precision = value["timestamp_precision_ns"]
    if precision is not None and (type(precision) is not int or precision <= 0):
        raise ValueError("process_contract_timestamp_precision")
    schema_pin = value["payload_schema_pin"]
    if not isinstance(schema_pin, str) or not PIN.fullmatch(schema_pin):
        raise ValueError("process_contract_payload_schema_pin")
    schema_path = safe_path(store) / "qualification-payload-schemas" / f"{schema_pin}.json"
    schema = json.loads(schema_path.read_text())
    if digest(schema) != schema_pin:
        raise ValueError("process_contract_payload_schema_checksum")
    publish_payload_schema_validation(schema)
    for name, version in RULES.items():
        ref = value[name]
        if not isinstance(ref, dict) or set(ref) != {"version", "pin"} or ref["version"] != version:
            raise ValueError("process_contract_rule_reference")
        if not isinstance(ref["pin"], str) or not PIN.fullmatch(ref["pin"]):
            raise ValueError("process_contract_rule_pin")
        path = safe_path(store) / "qualification-rules" / f"{ref['pin']}.json"
        rule = json.loads(path.read_text())
        if (
            digest(rule) != ref["pin"]
            or set(rule) != {"version", "definition"}
            or rule.get("version") != version
            or not isinstance(rule.get("definition"), str)
            or not rule["definition"]
        ):
            raise ValueError("process_contract_rule_unresolved")
        if rule["definition"] not in SUPPORTED_RULE_DEFINITIONS[name]:
            raise ValueError("process_contract_rule_unsupported")
    return value


def process_contract_pin(value, *, store):
    """Resolve pinned schema/rules and return the identity of the process semantics."""
    process_contract(value, store=store)
    return digest(value)


def publish_payload_schema_validation(value):
    """Validate an already-resolved payload schema without publishing it."""
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "fields"}
        or value["version"] != "payload-schema-v1"
        or not isinstance(value["fields"], list)
        or not value["fields"]
    ):
        raise ValueError("qualification_payload_schema")
    names = []
    for field in value["fields"]:
        if (
            not isinstance(field, dict)
            or set(field) != {"name", "unit", "denomination"}
            or any(not isinstance(field[k], str) or not field[k] for k in field)
        ):
            raise ValueError("qualification_payload_schema_field")
        names.append(field["name"])
    if len(set(names)) != len(names):
        raise ValueError("qualification_payload_schema_duplicate_field")


def _validate_synthetic_fixture(fixture, *, process, process_pin, store):
    required = {
        "version",
        "entity",
        "start_ns",
        "end_ns",
        "available_ns",
        "closed_through_ns",
        "events",
        "unresolved_events",
        "rejected_events",
        "process_contract_pin",
    }
    if not isinstance(fixture, dict) or set(fixture) != required:
        raise ValueError("qualification_synthetic_fixture_schema")
    if (
        fixture["version"] != "synthetic-qualification-fixture-v1"
        or any(
            type(fixture[k]) is not int
            for k in ("start_ns", "end_ns", "available_ns", "closed_through_ns")
        )
        or not 0 <= fixture["start_ns"] < fixture["end_ns"]
        or not fixture["start_ns"] <= fixture["closed_through_ns"] <= fixture["end_ns"]
        or fixture["closed_through_ns"] > fixture["available_ns"]
        or any(
            not isinstance(fixture[k], list)
            for k in ("events", "unresolved_events", "rejected_events")
        )
    ):
        raise ValueError("qualification_synthetic_fixture_interval")
    entity(fixture["entity"])
    if (
        fixture["process_contract_pin"] != process_pin
        or digest(process) != process_pin
        or process["source_kind"] != "synthetic_closed_fixture"
    ):
        raise ValueError("qualification_synthetic_fixture_process")
    schema = json.loads(
        (
            safe_path(store)
            / "qualification-payload-schemas"
            / f"{process['payload_schema_pin']}.json"
        ).read_text()
    )
    if digest(schema) != process["payload_schema_pin"]:
        raise ValueError("qualification_synthetic_fixture_payload_schema_pin")
    seen = set()
    for category in ("events", "unresolved_events", "rejected_events"):
        previous = None
        for event in fixture[category]:
            if not isinstance(event, dict) or set(event) != {"id", "event_ns", "values"}:
                raise ValueError("qualification_synthetic_fixture_event_schema")
            if (
                not isinstance(event["id"], str)
                or not event["id"]
                or type(event["event_ns"]) is not int
                or not fixture["start_ns"] <= event["event_ns"] < fixture["closed_through_ns"]
                or not isinstance(event["values"], list)
                or len(event["values"]) != len(schema["fields"])
                or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in event["values"]
                )
            ):
                raise ValueError("qualification_synthetic_fixture_event_value")
            key = (event["event_ns"], event["id"])
            if event["id"] in seen or (previous is not None and key <= previous):
                raise ValueError("qualification_synthetic_fixture_event_identity_or_order")
            seen.add(event["id"])
            previous = key
    return fixture


def publish_synthetic_fixture(store, fixture, *, process, process_pin):
    """Publish an explicit synthetic fixture; real source inputs are not accepted."""
    raw_required = {
        "version",
        "entity",
        "start_ns",
        "end_ns",
        "available_ns",
        "closed_through_ns",
        "events",
        "unresolved_events",
        "rejected_events",
    }
    if not isinstance(fixture, dict) or set(fixture) != raw_required:
        raise ValueError("qualification_synthetic_fixture_schema")
    process_contract(process, store=store)
    if digest(process) != process_pin or process["source_kind"] != "synthetic_closed_fixture":
        raise ValueError("qualification_synthetic_fixture_process")
    fixture = _validate_synthetic_fixture(
        dict(fixture, process_contract_pin=process_pin),
        process=process,
        process_pin=process_pin,
        store=store,
    )
    store = safe_path(store)
    directory = store / "qualification-synthetic-fixtures"
    directory.mkdir(parents=True, exist_ok=True)
    pin = digest(fixture)
    atomic_json(directory / f"{pin}.json", fixture)
    return pin


def _member_basis(value):
    return {k: v for k, v in value.items() if k != "verification"}


def _module_implementation_pin(path):
    return hashlib.sha256(safe_path(path).read_bytes()).hexdigest()


VERIFIER_IMPLEMENTATION_PIN = _module_implementation_pin(__file__)


def _verifier_code_pin(verifier_id):
    if verifier_id not in VERIFIERS:
        raise ValueError("qualification_verifier_unregistered")
    return VERIFIER_IMPLEMENTATION_PIN


def _verify_synthetic_closed_fixture(store, process, process_pin, member, input_pin):
    if process["source_kind"] != "synthetic_closed_fixture":
        raise ValueError("qualification_verifier_source_kind")
    interval_rule = process["interval_convention"]
    rule = json.loads(
        (safe_path(store) / "qualification-rules" / f"{interval_rule['pin']}.json").read_text()
    )
    if rule.get("definition") != "half-open-start-inclusive-end-exclusive-v1":
        raise ValueError("qualification_verifier_interval_convention")
    fixture_path = safe_path(store) / "qualification-synthetic-fixtures" / f"{input_pin}.json"
    fixture = json.loads(fixture_path.read_text())
    if digest(fixture) != input_pin:
        raise ValueError("qualification_verifier_input_checksum")
    fixture = _validate_synthetic_fixture(
        fixture, process=process, process_pin=process_pin, store=store
    )
    if (
        fixture["entity"] != member["entity"]
        or fixture.get("start_ns") != member["start_ns"]
        or fixture.get("end_ns") != member["end_ns"]
        or fixture.get("closed_through_ns") > member["end_ns"]
        or fixture.get("available_ns") > member["available_ns"]
        or not member["entity"].startswith(process["entity_namespace"].rstrip("/") + "/")
    ):
        raise ValueError("qualification_verifier_scope_mismatch")
    category_counts = {
        category: len(fixture[category])
        for category in ("events", "unresolved_events", "rejected_events")
    }
    qualified = category_counts["events"]
    unresolved = category_counts["unresolved_events"]
    rejected = category_counts["rejected_events"]
    captured = qualified + unresolved + rejected
    decoded = qualified + unresolved
    result_counts = dict(
        captured=captured,
        decoded=decoded,
        rejected=rejected,
        qualified=qualified,
        unresolved=unresolved,
    )
    status = (
        "complete"
        if fixture["closed_through_ns"] == member["end_ns"] and not rejected and not unresolved
        else "partial"
    )
    caps = ["observation_support", "population_coverage"]
    if status == "complete":
        caps.append("complete_support")
    caps.sort()
    if result_counts != member["counts"] or member["status"] != status:
        raise ValueError("qualification_verifier_count_mismatch")
    return dict(
        version="qualification-verifier-result-v1",
        verifier_id="synthetic_closed_fixture_v1",
        verifier_code_pin=_verifier_code_pin("synthetic_closed_fixture_v1"),
        input_pin=input_pin,
        process_contract_pin=process_pin,
        member_pin=digest(_member_basis(member)),
        status=status,
        counts=result_counts,
        capabilities=caps,
    )


VERIFIERS = {"synthetic_closed_fixture_v1": _verify_synthetic_closed_fixture}


def execute_verifier(store, verifier_id, input_pin, process, process_pin, member):
    """Execute the statically registered verifier and return its input/member-bound output pin."""
    if verifier_id not in VERIFIERS:
        raise ValueError("qualification_verifier_unregistered")
    result = VERIFIERS[verifier_id](store, process, process_pin, member, input_pin)
    return dict(
        verifier_id=verifier_id,
        verifier_code_pin=result["verifier_code_pin"],
        input_pin=input_pin,
        process_contract_pin=process_pin,
        result_pin=digest(result),
    )


def _verify_member(store, process, process_pin, member):
    proof = member.get("verification")
    if member["status"] not in {"complete", "partial"}:
        if proof is not None:
            raise ValueError("qualification_verification_unexpected")
        return None
    if not isinstance(proof, dict) or set(proof) != {
        "verifier_id",
        "verifier_code_pin",
        "input_pin",
        "process_contract_pin",
        "result_pin",
    }:
        raise ValueError("qualification_verification_missing")
    result = VERIFIERS.get(proof["verifier_id"])
    if result is None:
        raise ValueError("qualification_verifier_unregistered")
    expected = result(store, process, process_pin, member, proof["input_pin"])
    if (
        proof["verifier_code_pin"] != _verifier_code_pin(proof["verifier_id"])
        or proof["verifier_code_pin"] != expected["verifier_code_pin"]
        or proof["process_contract_pin"] != process_pin
        or proof["input_pin"] not in member["evidence"]
        or proof["result_pin"] != digest(expected)
        or not set(member["capabilities"]).issubset(expected["capabilities"])
    ):
        raise ValueError("qualification_verifier_result_mismatch")
    return expected


def _counts(value):
    if (
        not isinstance(value, dict)
        or set(value) != COUNT_KEYS
        or any(type(n) is not int or n < 0 for n in value.values())
    ):
        raise ValueError("qualification_counts")
    if value["captured"] != value["decoded"] + value["rejected"]:
        raise ValueError("qualification_capture_accounting")
    if value["decoded"] != value["qualified"] + value["unresolved"]:
        raise ValueError("qualification_decode_accounting")
    return value


def _member(value):
    required = {
        "entity",
        "start_ns",
        "end_ns",
        "available_ns",
        "status",
        "counts",
        "capabilities",
        "evidence",
        "verification",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("qualification_scope_member_schema")
    entity(value["entity"])
    if (
        any(type(value[k]) is not int for k in ("start_ns", "end_ns", "available_ns"))
        or not 0 <= value["start_ns"] < value["end_ns"] <= value["available_ns"]
    ):
        raise ValueError("qualification_scope_member_interval")
    if value["status"] not in STATUSES:
        raise ValueError("qualification_scope_member_status")
    _counts(value["counts"])
    caps = value["capabilities"]
    if (
        not isinstance(caps, list)
        or caps != sorted(set(caps))
        or any(c not in CAPABILITIES for c in caps)
    ):
        raise ValueError("qualification_scope_member_capabilities")
    if value["status"] == "complete" and (
        "complete_support" not in caps
        or value["counts"]["rejected"]
        or value["counts"]["unresolved"]
        or value["verification"] is None
    ):
        raise ValueError("qualification_scope_member_incomplete_zero")
    if value["status"] == "partial" and (
        value["verification"] is None or "complete_support" in caps
    ):
        raise ValueError("qualification_scope_member_partial_verification")
    if value["status"] not in {"complete", "partial"} and "complete_support" in caps:
        raise ValueError("qualification_scope_member_complete_capability_status")
    if value["verification"] is not None and (
        not isinstance(value["verification"], dict)
        or set(value["verification"])
        != {
            "verifier_id",
            "verifier_code_pin",
            "input_pin",
            "process_contract_pin",
            "result_pin",
        }
        or any(
            not isinstance(value["verification"][k], str)
            or not PIN.fullmatch(value["verification"][k])
            for k in (
                "verifier_code_pin",
                "input_pin",
                "process_contract_pin",
                "result_pin",
            )
        )
        or value["verification"]["verifier_id"] not in VERIFIERS
    ):
        raise ValueError("qualification_scope_member_verification")
    evidence = value["evidence"]
    if (
        not isinstance(evidence, list)
        or not evidence
        or evidence != sorted(set(evidence))
        or any(not isinstance(pin, str) or not PIN.fullmatch(pin) for pin in evidence)
    ):
        raise ValueError("qualification_scope_member_evidence")
    return value


def _member_key(value):
    return value["entity"], value["start_ns"], value["end_ns"]


def publish_scope(
    store,
    members,
    *,
    population_store,
    population_pin,
    role,
    process,
    process_pin,
    page_size=SCOPE_PAGE_SIZE,
):
    """Seal sorted member pages after one disk-backed population membership validation pass."""
    pop = population_manifest(population_store, population_pin)
    if pop["kind"] != "population" or pop["definition"]["role"] != role:
        raise ValueError("qualification_scope_population_role")
    if digest(process) != process_pin:
        raise ValueError("qualification_scope_process_pin")
    process_contract(process, store=store)
    if type(page_size) is not int or not 1 <= page_size <= SCOPE_PAGE_SIZE:
        raise ValueError("qualification_scope_page_size")
    store = safe_path(store)
    store.mkdir(parents=True, exist_ok=True)
    pages_dir = store / "qualification-scope-pages"
    roots_dir = store / "qualification-scopes"
    pages_dir.mkdir(parents=True, exist_ok=True)
    roots_dir.mkdir(parents=True, exist_ok=True)
    pages, batch, previous, previous_member, count = [], [], None, None, 0

    with tempfile.TemporaryDirectory(prefix="qualification-scope-", dir=store) as scratch:
        db = sqlite3.connect(f"{scratch}/membership.sqlite")
        db.execute("pragma cache_size=-8192")
        db.execute("pragma temp_store=FILE")
        db.executescript("""
            create table scope_members(entity text,start integer,end integer,
              primary key(entity,start,end)) without rowid;
            create table population_members(entity text,start integer,end integer,
              primary key(entity,start,end)) without rowid;
        """)

        def flush():
            if not batch:
                return
            pin = digest(batch)
            atomic_json(pages_dir / f"{pin}.json", batch)
            pages.append(
                dict(
                    pin=pin,
                    count=len(batch),
                    first=list(_member_key(batch[0])),
                    last=list(_member_key(batch[-1])),
                    fixture_pins=sorted(
                        {
                            row["verification"]["input_pin"]
                            for row in batch
                            if row["verification"] is not None
                        }
                    ),
                )
            )
            batch.clear()

        try:
            for raw in members:
                row = json.loads(canonical(_member(raw)))
                _verify_member(store, process, process_pin, row)
                key = _member_key(row)
                if previous is not None and key <= previous:
                    raise ValueError("qualification_scope_order_or_duplicate")
                if (
                    previous_member is not None
                    and key[0] == previous_member["entity"]
                    and key[1] < previous_member["end_ns"]
                ):
                    raise ValueError("qualification_scope_overlapping_member_intervals")
                previous = key
                previous_member = row
                batch.append(row)
                db.execute("insert into scope_members values(?,?,?)", key)
                count += 1
                if len(batch) == page_size:
                    flush()
            flush()
            if not count:
                raise ValueError("qualification_scope_empty")
            for row in records(population_store, population_pin):
                db.execute(
                    "insert or ignore into population_members values(?,?,?)",
                    (row["entity"], row["start_ns"], row["end_ns"]),
                )
            db.commit()
            missing = db.execute("""
                select 1 from scope_members s where not exists (
                  select 1 from population_members p where p.entity=s.entity
                  and p.start<=s.start and p.end>=s.end) limit 1
            """).fetchone()
            if missing:
                raise ValueError("qualification_scope_member_outside_population")
        finally:
            db.close()
    manifest = dict(
        version="qualification-scope-v1",
        role=role,
        population_pin=population_pin,
        process_contract_pin=process_pin,
        members=count,
        pages=pages,
    )
    pin = digest(manifest)
    atomic_json(roots_dir / f"{pin}.json", manifest)
    return pin


@lru_cache(maxsize=8)
def _cached_scope_manifest(path, scope_pin, mtime_ns, size):
    manifest = json.loads(path.read_text())
    if digest(manifest) != scope_pin or manifest.get("version") != "qualification-scope-v1":
        raise ValueError("qualification_scope_checksum")
    if (
        not isinstance(manifest.get("role"), str)
        or not isinstance(manifest.get("population_pin"), str)
        or not PIN.fullmatch(manifest["population_pin"])
        or not isinstance(manifest.get("process_contract_pin"), str)
        or not PIN.fullmatch(manifest["process_contract_pin"])
        or type(manifest.get("members")) is not int
        or manifest["members"] < 1
        or not isinstance(manifest.get("pages"), list)
    ):
        raise ValueError("qualification_scope_schema")
    total = 0
    previous_last = None
    last_keys = []
    for page in manifest["pages"]:
        if (
            not isinstance(page, dict)
            or set(page) != {"pin", "count", "first", "last", "fixture_pins"}
            or not isinstance(page["pin"], str)
            or not PIN.fullmatch(page["pin"])
            or type(page["count"]) is not int
            or page["count"] < 1
            or page["count"] > SCOPE_PAGE_SIZE
            or not isinstance(page["first"], list)
            or not isinstance(page["last"], list)
            or len(page["first"]) != 3
            or len(page["last"]) != 3
            or not isinstance(page["fixture_pins"], list)
            or page["fixture_pins"] != sorted(set(page["fixture_pins"]))
            or any(not isinstance(p, str) or not PIN.fullmatch(p) for p in page["fixture_pins"])
            or len(page["fixture_pins"]) > page["count"]
        ):
            raise ValueError("qualification_scope_page_reference")
        first, last = tuple(page["first"]), tuple(page["last"])
        if first > last or (previous_last is not None and first <= previous_last):
            raise ValueError("qualification_scope_page_directory_order")
        previous_last = last
        last_keys.append(last)
        total += page["count"]
    if total != manifest["members"]:
        raise ValueError("qualification_scope_count")
    manifest["_page_last_keys"] = last_keys
    return manifest


def _scope_manifest(store, scope_pin):
    if not isinstance(scope_pin, str) or not PIN.fullmatch(scope_pin):
        raise ValueError("qualification_scope_pin")
    path = safe_path(store) / "qualification-scopes" / f"{scope_pin}.json"
    stat = path.stat()
    return _cached_scope_manifest(path, scope_pin, stat.st_mtime_ns, stat.st_size)


def _page(store, page):
    value = json.loads(
        (safe_path(store) / "qualification-scope-pages" / f"{page['pin']}.json").read_text()
    )
    if digest(value) != page["pin"] or len(value) != page["count"] or len(value) > SCOPE_PAGE_SIZE:
        raise ValueError("qualification_scope_page_checksum")
    previous = None
    for row in value:
        _member(row)
        key = _member_key(row)
        if previous is not None and key <= previous:
            raise ValueError("qualification_scope_page_order")
        previous = key
    actual_fixtures = sorted(
        {row["verification"]["input_pin"] for row in value if row["verification"] is not None}
    )
    if actual_fixtures != page["fixture_pins"]:
        raise ValueError("qualification_scope_page_fixture_refs")
    if list(_member_key(value[0])) != page["first"] or list(_member_key(value[-1])) != page["last"]:
        raise ValueError("qualification_scope_page_bounds")
    return value


def _find_member(store, manifest, key):
    # Root metadata is cached by immutable pin; lookup opens one bounded member page.
    index = bisect_left(manifest["_page_last_keys"], key)
    if index < len(manifest["pages"]):
        page = manifest["pages"][index]
        if tuple(page["first"]) <= key <= tuple(page["last"]):
            for row in _page(store, page):
                if _member_key(row) == key:
                    return row
    raise ValueError("qualification_scope_member_missing")


def _scope_members(store, manifest):
    previous, count = None, 0
    for page in manifest["pages"]:
        for row in _page(store, page):
            key = _member_key(row)
            if previous is not None and (
                key <= previous[0]
                or (key[0] == previous[1]["entity"] and key[1] < previous[1]["end_ns"])
            ):
                raise ValueError("qualification_scope_member_order_or_overlap")
            previous = (key, row)
            count += 1
            yield row
    if count != manifest["members"]:
        raise ValueError("qualification_scope_count")


def _sum_counts(members):
    total = dict.fromkeys(COUNT_KEYS, 0)
    for member in members:
        for key in COUNT_KEYS:
            total[key] += member["counts"][key]
    return total


def checked_receipt(value, *, store, verify_scope=True):
    required = {
        "version",
        "process_contract",
        "process_contract_pin",
        "inputs",
        "source_pin",
        "decoder_pin",
        "qualification_code_pin",
        "population_pin",
        "scope_pin",
        "role",
        "accounting_unit",
        "available_ns",
        "counts",
        "capabilities",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value["version"] != "qualification-receipt-v1"
    ):
        raise ValueError("qualification_receipt_schema")
    process_contract(value["process_contract"], store=store)
    if (
        not isinstance(value["process_contract_pin"], str)
        or not PIN.fullmatch(value["process_contract_pin"])
        or digest(value["process_contract"]) != value["process_contract_pin"]
    ):
        raise ValueError("qualification_receipt_process_contract_pin")
    for key in (
        "source_pin",
        "decoder_pin",
        "qualification_code_pin",
        "population_pin",
        "scope_pin",
    ):
        if not isinstance(value[key], str) or not PIN.fullmatch(value[key]):
            raise ValueError("qualification_receipt_pin")
    if type(value["available_ns"]) is not int or value["available_ns"] < 0:
        raise ValueError("qualification_receipt_availability")
    _counts(value["counts"])
    caps = value["capabilities"]
    if (
        not isinstance(caps, list)
        or caps != sorted(set(caps))
        or any(c not in CAPABILITIES for c in caps)
    ):
        raise ValueError("qualification_receipt_capabilities")
    if not isinstance(value["role"], str) or value["role"] not in {"train", "research_evaluation"}:
        raise ValueError("qualification_receipt_role")
    if value["accounting_unit"] != "candidate_process_records":
        raise ValueError("qualification_receipt_accounting_unit")
    if (
        not isinstance(value["inputs"], list)
        or not value["inputs"]
        or value["inputs"] != sorted(set(value["inputs"]))
        or any(not isinstance(pin, str) or not PIN.fullmatch(pin) for pin in value["inputs"])
        or not {
            value[k]
            for k in (
                "source_pin",
                "decoder_pin",
                "qualification_code_pin",
                "process_contract_pin",
                "population_pin",
                "scope_pin",
            )
        }.issubset(value["inputs"])
    ):
        raise ValueError("qualification_receipt_inputs")
    scope = _scope_manifest(store, value["scope_pin"])
    if value["process_contract"]["source_kind"] == "synthetic_closed_fixture":
        if value["qualification_code_pin"] != _verifier_code_pin("synthetic_closed_fixture_v1"):
            raise ValueError("qualification_receipt_qualification_code_pin")
        if value["source_pin"] != synthetic_source_pin(
            value["process_contract_pin"], value["scope_pin"]
        ):
            raise ValueError("qualification_receipt_synthetic_source_pin")
        if value["decoder_pin"] != SYNTHETIC_DECODER_PIN:
            raise ValueError("qualification_receipt_synthetic_decoder_pin")
    if (
        scope["population_pin"] != value["population_pin"]
        or scope["role"] != value["role"]
        or scope["process_contract_pin"] != value["process_contract_pin"]
    ):
        raise ValueError("qualification_receipt_scope_binding")
    if verify_scope:
        total = dict.fromkeys(COUNT_KEYS, 0)
        for member in _scope_members(store, scope):
            if member["status"] == "complete" and "complete_support" not in value["capabilities"]:
                raise ValueError("qualification_receipt_complete_capability_missing")
            if not set(member["capabilities"]).issubset(value["capabilities"]):
                raise ValueError("qualification_receipt_member_capabilities")
            if member["available_ns"] > value["available_ns"]:
                raise ValueError("qualification_receipt_precedes_member_availability")
            _verify_member(store, value["process_contract"], value["process_contract_pin"], member)
            for key in COUNT_KEYS:
                total[key] += member["counts"][key]
        if total != value["counts"]:
            raise ValueError("qualification_receipt_partition_accounting")
    return value


def publish_receipt(store, receipt):
    """Publish one immutable receipt after verifying every bound scope page and denominator."""
    receipt = json.loads(canonical(checked_receipt(receipt, store=store)))
    store = safe_path(store)
    directory = store / "qualification-receipts"
    directory.mkdir(parents=True, exist_ok=True)
    pin = digest(receipt)
    atomic_json(directory / f"{pin}.json", receipt)
    return pin


def load_receipt(store, pin):
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("qualification_receipt_pin")
    store = safe_path(store)
    receipt_path = store / "qualification-receipts" / f"{pin}.json"
    value = json.loads(receipt_path.read_text())
    if digest(value) != pin:
        raise ValueError("qualification_receipt_checksum")
    value = checked_receipt(value, store=store, verify_scope=False)
    scope_path = store / "qualification-scopes" / f"{value['scope_pin']}.json"
    scope_stat = scope_path.stat()
    scope = _scope_manifest(store, value["scope_pin"])
    fixture_pins = set()
    dependency_stats = []
    for page in scope["pages"]:
        stat = (store / "qualification-scope-pages" / f"{page['pin']}.json").stat()
        dependency_stats.append(("page", page["pin"], stat.st_ctime_ns, stat.st_ino, stat.st_size))
        fixture_pins.update(page["fixture_pins"])
    fixture_directory = store / "qualification-synthetic-fixtures"
    for fixture_pin in sorted(fixture_pins):
        stat = (fixture_directory / f"{fixture_pin}.json").stat()
        dependency_stats.append(
            ("fixture", fixture_pin, stat.st_ctime_ns, stat.st_ino, stat.st_size)
        )
    process = value["process_contract"]
    artifact_refs = [("qualification-payload-schemas", process["payload_schema_pin"])]
    artifact_refs.extend(("qualification-rules", process[name]["pin"]) for name in RULES)
    for directory, artifact_pin in artifact_refs:
        stat = (store / directory / f"{artifact_pin}.json").stat()
        dependency_stats.append(
            (directory, artifact_pin, stat.st_ctime_ns, stat.st_ino, stat.st_size)
        )
    receipt_stat = receipt_path.stat()
    _validate_cached_receipt(
        str(store),
        pin,
        (receipt_stat.st_ctime_ns, receipt_stat.st_ino, receipt_stat.st_size),
        (scope_stat.st_ctime_ns, scope_stat.st_ino, scope_stat.st_size),
        tuple(dependency_stats),
    )
    return value


def _observed_values_code_pin(verifier_id):
    """Bind the v2 dispatcher and its one registered capture verifier implementation."""
    if verifier_id not in OBSERVED_VALUE_VERIFIERS:
        raise ValueError("qualification_observed_values_verifier_unregistered")
    verifier_path = safe_path(__file__).with_name("signature_capture.py")
    return digest(
        {
            "verifier_id": verifier_id,
            "qualification_module_sha256": _module_implementation_pin(__file__),
            "capture_verifier_module_sha256": _module_implementation_pin(verifier_path),
        }
    )


def _observed_values_accounting(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"captured", "observed", "unresolved"}
        or any(type(n) is not int or n < 0 for n in value.values())
        or value["captured"] != value["observed"] + value["unresolved"]
        or value["observed"] < 1
    ):
        raise ValueError("qualification_observed_values_accounting")
    return value


def _checked_observed_values_envelope(value, *, store):
    required = {
        "version",
        "process_contract",
        "process_contract_pin",
        "entity",
        "source_pin",
        "import_pin",
        "capture_set_pin",
        "capture_receipt_pin",
        "decoder_pin",
        "verifier_id",
        "qualification_code_pin",
        "inputs",
        "role",
        "available_ns",
        "accounting_unit",
        "accounting",
        "capabilities",
        "verification",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value["version"] != "qualification-observed-values-v2"
    ):
        raise ValueError("qualification_observed_values_schema")
    process_contract(value["process_contract"], store=store)
    process_pin = value["process_contract_pin"]
    if not isinstance(process_pin, str) or not PIN.fullmatch(process_pin):
        raise ValueError("qualification_observed_values_process_pin")
    if digest(value["process_contract"]) != process_pin:
        raise ValueError("qualification_observed_values_process_pin")
    process = value["process_contract"]
    entity(value["entity"])
    namespace = process["entity_namespace"].rstrip("/")
    if value["entity"] != namespace and not value["entity"].startswith(namespace + "/"):
        raise ValueError("qualification_observed_values_namespace")
    pins = (
        "source_pin",
        "import_pin",
        "capture_set_pin",
        "capture_receipt_pin",
        "decoder_pin",
        "qualification_code_pin",
    )
    if any(not isinstance(value[k], str) or not PIN.fullmatch(value[k]) for k in pins):
        raise ValueError("qualification_observed_values_pin")
    if (
        value["role"] not in {"train", "research_evaluation"}
        or type(value["available_ns"]) is not int
        or value["available_ns"] < 0
        or value["accounting_unit"] != "provider_signature_rows"
        or value["verifier_id"] not in OBSERVED_VALUE_VERIFIERS
    ):
        raise ValueError("qualification_observed_values_contract")
    accounting = _observed_values_accounting(value["accounting"])
    capabilities = value["capabilities"]
    if capabilities != [OBSERVED_VALUE_CAPABILITY]:
        raise ValueError("qualification_observed_values_capability")
    code_pin = _observed_values_code_pin(value["verifier_id"])
    if value["qualification_code_pin"] != code_pin:
        raise ValueError("qualification_observed_values_code_pin")
    verification = value["verification"]
    if (
        not isinstance(verification, dict)
        or set(verification)
        != {"verifier_id", "verifier_code_pin", "input_pin", "process_contract_pin", "result_pin"}
        or verification["verifier_id"] != value["verifier_id"]
        or verification["verifier_code_pin"] != code_pin
        or verification["input_pin"] != value["import_pin"]
        or verification["process_contract_pin"] != process_pin
        or not isinstance(verification["result_pin"], str)
        or not PIN.fullmatch(verification["result_pin"])
    ):
        raise ValueError("qualification_observed_values_verification")
    schema_pin = process["payload_schema_pin"]
    rule_pins = [process[name]["pin"] for name in RULES]
    expected_inputs = sorted(
        {
            value["source_pin"],
            value["import_pin"],
            value["capture_set_pin"],
            value["capture_receipt_pin"],
            value["decoder_pin"],
            process_pin,
            schema_pin,
            code_pin,
            *rule_pins,
        }
    )
    if value["inputs"] != expected_inputs:
        raise ValueError("qualification_observed_values_inputs")
    return accounting


def _observed_values_verifier_result(value, *, store, source_store, capture_root):
    """Execute the statically registered capture verifier; never emits support/coverage facts."""
    if value["verifier_id"] not in OBSERVED_VALUE_VERIFIERS:
        raise ValueError("qualification_observed_values_verifier_unregistered")
    from . import signature_capture

    return signature_capture._verify_observed_values_capture(
        value,
        store=store,
        source_store=source_store,
        capture_root=capture_root,
    )


def checked_observed_values_receipt(value, *, store, source_store, capture_root):
    """Validate v2 positive-fact receipt separately from all population/scope v1 readers."""
    accounting = _checked_observed_values_envelope(value, store=store)
    # Role/source metadata is checked before opening observation payloads. Entity is checked
    # above before the verifier reads raw capture pages or source facts.
    from .observations import Source

    source = Source(source_store, value["source_pin"], role=value["role"])
    if source.manifest["binding"]["cutoff_ns"] != value["available_ns"]:
        raise ValueError("qualification_observed_values_availability")
    result = _observed_values_verifier_result(
        value, store=store, source_store=source_store, capture_root=capture_root
    )
    if (
        result["accounting"] != accounting
        or result["entity"] != value["entity"]
        or result["source_pin"] != value["source_pin"]
        or result["import_pin"] != value["import_pin"]
        or result["capture_set_pin"] != value["capture_set_pin"]
        or result["capture_receipt_pin"] != value["capture_receipt_pin"]
        or result["decoder_pin"] != value["decoder_pin"]
        or result["process_contract_pin"] != value["process_contract_pin"]
        or result["role"] != value["role"]
        or result["available_ns"] != value["available_ns"]
        or digest(result) != value["verification"]["result_pin"]
    ):
        raise ValueError("qualification_observed_values_verifier_result")
    return value


def publish_observed_values_receipt(store, receipt, *, source_store, capture_root):
    """Run the registered verifier and publish only a capability for exact observed values."""
    value = json.loads(canonical(receipt))
    _checked_observed_values_envelope(value, store=store)
    result = _observed_values_verifier_result(
        value, store=store, source_store=source_store, capture_root=capture_root
    )
    value["verification"]["result_pin"] = digest(result)
    checked_observed_values_receipt(
        value, store=store, source_store=source_store, capture_root=capture_root
    )
    value = json.loads(canonical(value))
    root = safe_path(store) / "qualification-observed-values-v2"
    root.mkdir(parents=True, exist_ok=True)
    pin = digest(value)
    atomic_json(root / f"{pin}.json", value)
    return pin


def load_observed_values_receipt(store, pin, *, source_store, capture_root):
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("qualification_observed_values_receipt_pin")
    value = json.loads(
        (safe_path(store) / "qualification-observed-values-v2" / f"{pin}.json").read_text()
    )
    if digest(value) != pin:
        raise ValueError("qualification_observed_values_receipt_checksum")
    return checked_observed_values_receipt(
        value, store=store, source_store=source_store, capture_root=capture_root
    )


@lru_cache(maxsize=128)
def _validate_cached_receipt(store, pin, receipt_stat, scope_stat, dependency_stats):
    """Full receipt/scope validation cached by immutable identities and on-disk signatures."""
    store = safe_path(store)
    value = json.loads((store / "qualification-receipts" / f"{pin}.json").read_text())
    if digest(value) != pin:
        raise ValueError("qualification_receipt_checksum")
    checked_receipt(value, store=store, verify_scope=True)
    return pin


def replicate_receipt(source, target, receipt_pin):
    """Copy a verified receipt and its scope pages without changing their content identities."""
    receipt = load_receipt(source, receipt_pin)
    checked_receipt(receipt, store=source, verify_scope=True)
    scope = _scope_manifest(source, receipt["scope_pin"])
    target = safe_path(target)
    (target / "qualification-scope-pages").mkdir(parents=True, exist_ok=True)
    (target / "qualification-scopes").mkdir(parents=True, exist_ok=True)
    (target / "qualification-receipts").mkdir(parents=True, exist_ok=True)
    (target / "qualification-payload-schemas").mkdir(parents=True, exist_ok=True)
    (target / "qualification-rules").mkdir(parents=True, exist_ok=True)
    for page in scope["pages"]:
        rows = _page(source, page)
        for member in rows:
            proof = member["verification"]
            if proof is not None:
                fixture = json.loads(
                    (
                        safe_path(source)
                        / "qualification-synthetic-fixtures"
                        / f"{proof['input_pin']}.json"
                    ).read_text()
                )
                if digest(fixture) != proof["input_pin"]:
                    raise ValueError("qualification_replica_verifier_input_checksum")
                (target / "qualification-synthetic-fixtures").mkdir(exist_ok=True)
                atomic_json(
                    target / "qualification-synthetic-fixtures" / f"{proof['input_pin']}.json",
                    fixture,
                )
        atomic_json(target / "qualification-scope-pages" / f"{page['pin']}.json", rows)
    root = {key: value for key, value in scope.items() if not key.startswith("_")}
    atomic_json(target / "qualification-scopes" / f"{receipt['scope_pin']}.json", root)
    contract = receipt["process_contract"]
    schema_pin = contract["payload_schema_pin"]
    schema = json.loads(
        (safe_path(source) / "qualification-payload-schemas" / f"{schema_pin}.json").read_text()
    )
    if digest(schema) != schema_pin:
        raise ValueError("qualification_replica_schema_checksum")
    atomic_json(target / "qualification-payload-schemas" / f"{schema_pin}.json", schema)
    for name in RULES:
        ref = contract[name]
        rule = json.loads(
            (safe_path(source) / "qualification-rules" / f"{ref['pin']}.json").read_text()
        )
        if digest(rule) != ref["pin"]:
            raise ValueError("qualification_replica_rule_checksum")
        atomic_json(target / "qualification-rules" / f"{ref['pin']}.json", rule)
    atomic_json(target / "qualification-receipts" / f"{receipt_pin}.json", receipt)
    # Re-read from destination to catch partial or conflicting copies before reporting success.
    if load_receipt(target, receipt_pin) != receipt:
        raise ValueError("qualification_replica_identity")
    checked_receipt(receipt, store=target, verify_scope=True)
    return dict(receipt_pin=receipt_pin, scope_pin=receipt["scope_pin"])


def assert_support(
    store,
    receipt_pin,
    *,
    population_store,
    population_pin,
    scope_pin,
    entity_name,
    start_ns,
    end_ns,
    capability="observation_support",
):
    """Return the receipt-bound status of one exact member; aggregate counts never imply it."""
    receipt = load_receipt(store, receipt_pin)
    if receipt["population_pin"] != population_pin or receipt["scope_pin"] != scope_pin:
        raise ValueError("qualification_scope_mismatch")
    pop = population_manifest(population_store, population_pin)
    scope = _scope_manifest(store, scope_pin)
    if pop["kind"] != "population" or pop["definition"]["role"] != receipt["role"]:
        raise ValueError("qualification_scope_population_role")
    if scope["population_pin"] != population_pin or scope["role"] != receipt["role"]:
        raise ValueError("qualification_scope_mismatch")
    if scope["process_contract_pin"] != receipt["process_contract_pin"]:
        raise ValueError("qualification_scope_process_mismatch")
    namespace = receipt["process_contract"]["entity_namespace"].rstrip("/")
    if entity_name != namespace and not entity_name.startswith(namespace + "/"):
        raise ValueError("qualification_process_entity_namespace")
    if capability not in receipt["capabilities"]:
        raise ValueError("qualification_capability_missing")
    member = _find_member(store, scope, (entity(entity_name), start_ns, end_ns))
    if capability not in member["capabilities"]:
        raise ValueError("qualification_member_capability_missing")
    if member["status"] == "complete" and "complete_support" not in receipt["capabilities"]:
        raise ValueError("qualification_receipt_complete_capability_missing")
    if not set(member["capabilities"]).issubset(receipt["capabilities"]):
        raise ValueError("qualification_receipt_member_capabilities")
    if member["available_ns"] > receipt["available_ns"]:
        raise ValueError("qualification_receipt_precedes_member_availability")
    verification = _verify_member(
        store, receipt["process_contract"], receipt["process_contract_pin"], member
    )
    return dict(
        receipt_pin=receipt_pin,
        source_pin=receipt["source_pin"],
        scope_pin=scope_pin,
        population_pin=population_pin,
        process_contract_pin=receipt["process_contract_pin"],
        process_contract=receipt["process_contract"],
        entity=entity_name,
        start_ns=start_ns,
        end_ns=end_ns,
        available_ns=member["available_ns"],
        status=member["status"],
        observed=member["counts"]["qualified"] if verification is not None else None,
        verified=verification is not None,
        evidence=member["evidence"],
        capabilities=member["capabilities"],
    )


def _revalidate(assertion, *, receipt_store, population_store):
    return assert_support(
        receipt_store,
        assertion["receipt_pin"],
        population_store=population_store,
        population_pin=assertion["population_pin"],
        scope_pin=assertion["scope_pin"],
        entity_name=assertion["entity"],
        start_ns=assertion["start_ns"],
        end_ns=assertion["end_ns"],
        capability=assertion["projection_capability"],
    )


def observation_fact(
    assertion, *, receipt_store, population_store, stream, destination_schema, evidence=()
):
    assertion = dict(assertion, projection_capability="observation_support")
    assertion = _revalidate(
        assertion, receipt_store=receipt_store, population_store=population_store
    )
    if stream != assertion["process_contract"]["stream"]:
        raise ValueError("support_process_stream_mismatch")
    process = assertion["process_contract"]
    destination_fields = {
        "version",
        "process_name",
        "stream",
        "process_contract_pin",
        "payload_schema_pin",
        "modality",
        "features",
    }
    schema = json.loads(
        (
            safe_path(receipt_store)
            / "qualification-payload-schemas"
            / f"{process['payload_schema_pin']}.json"
        ).read_text()
    )
    expected_features = [
        {"name": field["name"], "unit": field["unit"], "modality": process["modality"]}
        for field in schema["fields"]
    ]
    if (
        not isinstance(destination_schema, dict)
        or set(destination_schema) != destination_fields
        or destination_schema["version"] != "observation-process-binding-v1"
        or destination_schema["process_name"] != process["name"]
        or destination_schema["stream"] != stream
        or destination_schema["process_contract_pin"] != assertion["process_contract_pin"]
        or destination_schema["payload_schema_pin"] != process["payload_schema_pin"]
        or destination_schema["modality"] != process["modality"]
        or destination_schema["features"] != expected_features
    ):
        raise ValueError("support_observation_schema_binding_mismatch")
    return dict(
        kind="support",
        entity=assertion["entity"],
        stream=stream,
        start_ns=assertion["start_ns"],
        end_ns=assertion["end_ns"],
        available_ns=assertion["available_ns"],
        status="unknown" if assertion["status"] == "partial" else assertion["status"],
        evidence=sorted(set([assertion["receipt_pin"], *assertion["evidence"], *evidence])),
    )


def coverage_fact(
    assertion,
    *,
    receipt_store,
    population_store,
    stream,
    destination_contract,
    unit="qualified_process_events",
):
    assertion = dict(assertion, projection_capability="population_coverage")
    assertion = _revalidate(
        assertion, receipt_store=receipt_store, population_store=population_store
    )
    if stream != assertion["process_contract"]["stream"]:
        raise ValueError("support_process_stream_mismatch")
    destination_required = {
        "version",
        "name",
        "source",
        "modality",
        "representation",
        "unit",
        "process_contract_pin",
        "payload_schema_pin",
    }
    if (
        not isinstance(destination_contract, dict)
        or not destination_required.issubset(destination_contract)
        or destination_contract["version"] != "population-stream-process-binding-v1"
        or any(
            not isinstance(destination_contract[k], str) or not destination_contract[k]
            for k in destination_required - {"version"}
        )
    ):
        raise ValueError("support_coverage_contract")
    process = assertion["process_contract"]
    if (
        destination_contract["name"] != process["stream"]
        or destination_contract["source"] != assertion["source_pin"]
        or destination_contract["modality"] != process["modality"]
        or destination_contract["representation"] != "qualified-event-process-v1"
        or destination_contract["unit"] != process["unit"]
        or destination_contract["process_contract_pin"] != assertion["process_contract_pin"]
        or destination_contract["payload_schema_pin"] != process["payload_schema_pin"]
    ):
        raise ValueError("support_coverage_process_binding_mismatch")
    contract_pin = digest(destination_contract)
    status = assertion["status"]
    observed = assertion["observed"] if status in {"complete", "partial"} else None
    # Event streams have no independent expected-event denominator, even when exhaustive.
    expected = None
    return dict(
        entity=assertion["entity"],
        stream=stream,
        contract=contract_pin,
        start_ns=assertion["start_ns"],
        end_ns=assertion["end_ns"],
        known_ns=assertion["available_ns"],
        stage="qualified",
        status=status,
        unit=unit,
        expected=expected,
        observed=observed,
        evidence=[assertion["receipt_pin"], *assertion["evidence"]],
    )
