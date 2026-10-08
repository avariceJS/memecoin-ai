import json

import pytest

from memecoin_experience import qualification
from memecoin_experience.storage import atomic_json, digest
from memecoin_population import catalog

ALL_CAPS = ["complete_support", "observation_support", "population_coverage"]
RULE_NAMES = {
    "event_identity_rule": "event-identity-rule-v1",
    "event_time_basis": "event-time-basis-v1",
    "interval_convention": "interval-convention-v1",
    "revision_policy": "revision-policy-v1",
}


def _counts(*, rejected=0, unresolved=0, qualified=0):
    decoded = unresolved + qualified
    return dict(
        captured=decoded + rejected,
        decoded=decoded,
        rejected=rejected,
        qualified=qualified,
        unresolved=unresolved,
    )


def _member(
    name="A", *, start=0, end=10, status="unknown", counts=None, capabilities=None, available=20
):
    return dict(
        entity="chain/market/" + name,
        start_ns=start,
        end_ns=end,
        available_ns=available,
        status=status,
        counts=counts or _counts(),
        capabilities=capabilities
        or (ALL_CAPS if status == "complete" else ["observation_support", "population_coverage"]),
        evidence=["a" * 64],
        verification=None,
    )


def _population(tmp_path, names=("A", "B")):
    store = tmp_path / "facts"
    rows = [
        dict(
            entity="chain/market/" + name,
            start_ns=0,
            end_ns=100,
            known_ns=20,
            attributes={},
            evidence=["a" * 64],
        )
        for name in names
    ]
    pin = catalog.publish(
        store,
        "population",
        rows,
        cutoff_ns=100,
        definition=dict(role="train", selection="synthetic qualification fixture"),
    )
    return store, pin


def _scope(tmp_path, pop_store, pop_pin, members, *, page_size=512):
    store = tmp_path / "receipts"
    process, process_pin = _process_contract(store)
    return qualification.publish_scope(
        store,
        members,
        population_store=pop_store,
        population_pin=pop_pin,
        role="train",
        process=process,
        process_pin=process_pin,
        page_size=page_size,
    )


def _process_contract(
    store,
    *,
    identity="key-v1",
    time_basis="block-time-v1",
    revision="append-revisions-v1",
    schema_fields=None,
):
    schema_pin = qualification.publish_payload_schema(
        store,
        dict(
            version="payload-schema-v1",
            fields=schema_fields
            or [dict(name="amount", unit="lamports", denomination="integer raw units")],
        ),
    )
    refs = {
        name: qualification.publish_rule(store, name, definition)
        for name, definition in {
            "event_identity_rule": identity,
            "event_time_basis": time_basis,
            "interval_convention": "half-open-start-inclusive-end-exclusive-v1",
            "revision_policy": revision,
        }.items()
    }
    contract = dict(
        version="process-contract-v1",
        name="ticks-v1",
        stream="ticks",
        semantics="all qualified ticks in half-open interval",
        unit="events",
        source_kind="synthetic_closed_fixture",
        entity_namespace="chain/market/",
        modality="events",
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
    name="A",
    *,
    count=0,
    unresolved=0,
    rejected=0,
    status="complete",
    start=0,
    end=10,
    available=20,
    capabilities=None,
):
    process, process_pin = _process_contract(store)
    fixture = dict(
        version="synthetic-qualification-fixture-v1",
        entity="chain/market/" + name,
        start_ns=start,
        end_ns=end,
        available_ns=available - 1,
        closed_through_ns=end if status == "complete" else end - 1,
        events=[dict(id=f"event-{i}", event_ns=start + i, values=[float(i)]) for i in range(count)],
        unresolved_events=[
            dict(id=f"unresolved-{i}", event_ns=start + i, values=[float(i)])
            for i in range(unresolved)
        ],
        rejected_events=[
            dict(id=f"rejected-{i}", event_ns=start + i, values=[float(i)]) for i in range(rejected)
        ],
    )
    input_pin = qualification.publish_synthetic_fixture(
        store, fixture, process=process, process_pin=process_pin
    )
    member = _member(
        name,
        start=start,
        end=end,
        status=status,
        counts=_counts(qualified=count, unresolved=unresolved, rejected=rejected),
        available=available,
        capabilities=capabilities
        or (ALL_CAPS if status == "complete" else ["observation_support", "population_coverage"]),
    )
    member["evidence"] = sorted([*member["evidence"], input_pin])
    member["verification"] = qualification.execute_verifier(
        store, "synthetic_closed_fixture_v1", input_pin, process, process_pin, member
    )
    return member


def _receipt(
    pop_pin,
    scope_pin,
    counts,
    *,
    store,
    available=20,
    capabilities=None,
    source_pin=None,
    decoder_pin=None,
    qualification_code_pin=None,
    contract_override=None,
):
    process, process_pin = contract_override or _process_contract(store)
    source_pin = source_pin or qualification.synthetic_source_pin(process_pin, scope_pin)
    decoder_pin = decoder_pin or qualification.SYNTHETIC_DECODER_PIN
    qualification_code_pin = qualification_code_pin or qualification._verifier_code_pin(
        "synthetic_closed_fixture_v1"
    )
    return dict(
        version="qualification-receipt-v1",
        process_contract=process,
        process_contract_pin=process_pin,
        inputs=sorted(
            {source_pin, decoder_pin, qualification_code_pin, process_pin, pop_pin, scope_pin}
        ),
        source_pin=source_pin,
        decoder_pin=decoder_pin,
        qualification_code_pin=qualification_code_pin,
        population_pin=pop_pin,
        scope_pin=scope_pin,
        role="train",
        accounting_unit="candidate_process_records",
        available_ns=available,
        counts=counts,
        capabilities=capabilities or ALL_CAPS,
    )


def _publish(tmp_path, pop_store, pop_pin, members):
    receipt_store = tmp_path / "receipts"
    scope_pin = _scope(tmp_path, pop_store, pop_pin, members)
    aggregate = qualification._sum_counts(members)
    pin = qualification.publish_receipt(
        receipt_store,
        _receipt(
            pop_pin,
            scope_pin,
            aggregate,
            store=receipt_store,
            available=max(m["available_ns"] for m in members),
        ),
    )
    return receipt_store, scope_pin, pin


def _assert(store, pop_store, pop_pin, scope_pin, pin, member, *, capability="observation_support"):
    return qualification.assert_support(
        store,
        pin,
        population_store=pop_store,
        population_pin=pop_pin,
        scope_pin=scope_pin,
        entity_name=member["entity"],
        start_ns=member["start_ns"],
        end_ns=member["end_ns"],
        capability=capability,
    )


def _destination(assertion, **changes):
    process = assertion["process_contract"]
    result = dict(
        version="population-stream-process-binding-v1",
        name=process["stream"],
        source=assertion["source_pin"],
        modality=process["modality"],
        representation="qualified-event-process-v1",
        unit=process["unit"],
        process_contract_pin=assertion["process_contract_pin"],
        payload_schema_pin=process["payload_schema_pin"],
    )
    result.update(changes)
    return result


def _observation_schema(assertion, **changes):
    process = assertion["process_contract"]
    result = dict(
        version="observation-process-binding-v1",
        process_name=process["name"],
        stream=process["stream"],
        process_contract_pin=assertion["process_contract_pin"],
        payload_schema_pin=process["payload_schema_pin"],
        modality=process["modality"],
        features=[dict(name="amount", unit="lamports", modality=process["modality"])],
    )
    result.update(changes)
    return result


def test_partition_receipt_separates_member_zero_unknown_and_lifecycle(tmp_path):
    pop_store, pop_pin = _population(tmp_path)
    complete = _verified_member(tmp_path / "receipts", "A")
    unknown = _member("B", status="unknown", counts=_counts(qualified=2))
    receipt_store, scope_pin, pin = _publish(tmp_path, pop_store, pop_pin, [complete, unknown])
    assert (
        qualification.publish_receipt(
            receipt_store,
            _receipt(pop_pin, scope_pin, _counts(qualified=2), store=receipt_store, available=20),
        )
        == pin
    )
    replica = tmp_path / "replica"
    expected_replica = dict(receipt_pin=pin, scope_pin=scope_pin)
    assert qualification.replicate_receipt(receipt_store, replica, pin) == expected_replica
    assert qualification.replicate_receipt(receipt_store, replica, pin) == expected_replica
    assert qualification.load_receipt(replica, pin) == qualification.load_receipt(
        receipt_store, pin
    )

    zero = _assert(receipt_store, pop_store, pop_pin, scope_pin, pin, complete)
    censored = _assert(receipt_store, pop_store, pop_pin, scope_pin, pin, unknown)
    assert zero["status"] == "complete" and zero["observed"] == 0 and zero["available_ns"] == 20
    assert censored["status"] == "unknown" and censored["observed"] is None
    coverage = qualification.coverage_fact(
        zero,
        receipt_store=receipt_store,
        population_store=pop_store,
        stream="ticks",
        destination_contract=_destination(zero),
    )
    assert (coverage["status"], coverage["expected"], coverage["observed"]) == ("complete", None, 0)
    censored_coverage = qualification.coverage_fact(
        censored,
        receipt_store=receipt_store,
        population_store=pop_store,
        stream="ticks",
        destination_contract=_destination(censored),
    )
    assert censored_coverage["observed"] is None
    partial = _verified_member(tmp_path / "receipts", "B", count=1, unresolved=1, status="partial")
    partial_store, partial_scope, partial_pin = _publish(tmp_path, pop_store, pop_pin, [partial])
    partial_assertion = _assert(
        partial_store, pop_store, pop_pin, partial_scope, partial_pin, partial
    )
    partial_support = qualification.observation_fact(
        partial_assertion,
        receipt_store=partial_store,
        population_store=pop_store,
        stream="ticks",
        destination_schema=_observation_schema(partial_assertion),
    )
    assert partial_support["status"] == "unknown"
    partial_coverage = qualification.coverage_fact(
        partial_assertion,
        receipt_store=partial_store,
        population_store=pop_store,
        stream="ticks",
        destination_contract=_destination(partial_assertion),
    )
    assert partial_coverage["status"] == "partial" and partial_coverage["observed"] == 1
    replicated = _assert(replica, pop_store, pop_pin, scope_pin, pin, complete)
    assert replicated["status"] == "complete" and replicated["observed"] == 0
    with pytest.raises(ValueError, match="process_binding_mismatch"):
        qualification.coverage_fact(
            zero,
            receipt_store=receipt_store,
            population_store=pop_store,
            stream="ticks",
            destination_contract=_destination(zero, process_contract_pin="f" * 64),
        )
    support = qualification.observation_fact(
        zero,
        receipt_store=receipt_store,
        population_store=pop_store,
        stream="ticks",
        destination_schema=_observation_schema(zero),
    )
    assert support["kind"] == "support" and "transition" not in support
    assert catalog.checked(coverage, "coverage", 100)["observed"] == 0
    with pytest.raises(ValueError, match="stream_mismatch"):
        qualification.observation_fact(
            zero,
            receipt_store=receipt_store,
            population_store=pop_store,
            stream="wrong",
            destination_schema=_observation_schema(zero),
        )

    with pytest.raises(ValueError, match="member_missing"):
        qualification.assert_support(
            receipt_store,
            pin,
            population_store=pop_store,
            population_pin=pop_pin,
            scope_pin=scope_pin,
            entity_name="chain/market/A",
            start_ns=1,
            end_ns=10,
        )
    with pytest.raises(ValueError, match="scope_mismatch"):
        qualification.assert_support(
            receipt_store,
            pin,
            population_store=pop_store,
            population_pin="f" * 64,
            scope_pin=scope_pin,
            entity_name="chain/market/A",
            start_ns=0,
            end_ns=10,
        )
    with pytest.raises(ValueError, match="entity_namespace"):
        qualification.assert_support(
            receipt_store,
            pin,
            population_store=pop_store,
            population_pin=pop_pin,
            scope_pin=scope_pin,
            entity_name="elsewhere/market/A",
            start_ns=0,
            end_ns=10,
        )


@pytest.mark.parametrize("status", ["partial", "unknown", "failed", "stopped", "unsupported"])
def test_incomplete_member_status_never_projects_zero(tmp_path, status):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    if status == "partial":
        member = _verified_member(tmp_path / "receipts", status="partial", count=1, unresolved=1)
    else:
        member = _member(status=status, counts=_counts(qualified=1))
    receipt_store, scope_pin, pin = _publish(tmp_path, pop_store, pop_pin, [member])
    assertion = _assert(receipt_store, pop_store, pop_pin, scope_pin, pin, member)
    row = qualification.coverage_fact(
        assertion,
        receipt_store=receipt_store,
        population_store=pop_store,
        stream="ticks",
        destination_contract=_destination(assertion),
    )
    if status == "partial":
        assert row["status"] == status and row["observed"] == 1
    else:
        assert row["status"] == status and row["observed"] is None
        assert assertion["observed"] is None
    assert catalog.checked(row, "coverage", 100)["status"] == status


@pytest.mark.parametrize("rejected,unresolved", [(1, 0), (0, 1)])
def test_incomplete_denominators_cannot_be_member_complete(tmp_path, rejected, unresolved):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    bad = _member(
        status="complete", counts=_counts(rejected=rejected, unresolved=unresolved, qualified=1)
    )
    with pytest.raises(ValueError, match="incomplete_zero"):
        _scope(tmp_path, pop_store, pop_pin, [bad])


def test_opaque_self_asserted_zero_cannot_grant_complete_support(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    opaque = _member(status="complete", counts=_counts())
    with pytest.raises(ValueError, match="incomplete_zero"):
        _scope(tmp_path, pop_store, pop_pin, [opaque])
    opaque_partial = _member("A", status="partial", counts=_counts(qualified=1))
    with pytest.raises(ValueError, match="partial_verification"):
        _scope(tmp_path, pop_store, pop_pin, [opaque_partial])


def test_synthetic_closed_fixture_verifies_zero_and_positive_counts(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A", "B"))
    zero = _verified_member(tmp_path / "receipts", "A", count=0)
    positive = _verified_member(tmp_path / "receipts", "B", count=3)
    store, scope, pin = _publish(tmp_path, pop_store, pop_pin, [zero, positive])
    assert _assert(store, pop_store, pop_pin, scope, pin, zero)["observed"] == 0
    assert _assert(store, pop_store, pop_pin, scope, pin, positive)["observed"] == 3


def test_verifier_implementation_identity_is_bound_into_proof(tmp_path, monkeypatch):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts")
    store, scope, pin = _publish(tmp_path, pop_store, pop_pin, [member])
    source = qualification.safe_path(qualification.__file__).read_bytes()
    modified_source = source.replace(
        b"qualification_synthetic_fixture_event_value",
        b"qualification_synthetic_fixture_event_value_changed",
        1,
    )
    changed_module = tmp_path / "qualification.py"
    changed_module.write_bytes(modified_source)
    changed_pin = qualification._module_implementation_pin(changed_module)
    assert changed_pin != qualification.VERIFIER_IMPLEMENTATION_PIN
    monkeypatch.setattr(qualification, "VERIFIER_IMPLEMENTATION_PIN", changed_pin)
    with pytest.raises(ValueError, match="qualification_code_pin"):
        _assert(store, pop_store, pop_pin, scope, pin, member)


@pytest.mark.parametrize("action", ["corrupt", "delete"])
def test_unselected_fixture_dependency_change_invalidates_cached_receipt(tmp_path, action):
    pop_store, pop_pin = _population(tmp_path, ("A", "B"))
    member_a = _verified_member(tmp_path / "receipts", "A")
    member_b = _verified_member(tmp_path / "receipts", "B", count=1)
    store, scope, pin = _publish(tmp_path, pop_store, pop_pin, [member_a, member_b])
    assert _assert(store, pop_store, pop_pin, scope, pin, member_a)["status"] == "complete"
    fixture_pin = member_b["verification"]["input_pin"]
    fixture_path = store / "qualification-synthetic-fixtures" / f"{fixture_pin}.json"
    if action == "delete":
        fixture_path.unlink()
        error = (ValueError, FileNotFoundError)
    else:
        fixture_path.write_text("{}")
        error = ValueError
    with pytest.raises(error):
        _assert(store, pop_store, pop_pin, scope, pin, member_a)


def test_synthetic_receipt_rejects_arbitrary_source_and_decoder_pins(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts")
    store, scope, _ = _publish(tmp_path, pop_store, pop_pin, [member])
    for changes, expected in (
        ({"source_pin": "f" * 64}, "synthetic_source_pin"),
        ({"decoder_pin": "e" * 64}, "synthetic_decoder_pin"),
        ({"qualification_code_pin": "a" * 64}, "qualification_code_pin"),
    ):
        receipt = _receipt(pop_pin, scope, _counts(), store=store, **changes)
        with pytest.raises(ValueError, match=expected):
            qualification.publish_receipt(store, receipt)


def test_synthetic_fixture_publisher_checks_schema_and_finite_values(tmp_path):
    store = tmp_path / "receipts"
    process, process_pin = _process_contract(store)
    fixture = dict(
        version="synthetic-qualification-fixture-v1",
        entity="chain/market/A",
        start_ns=0,
        end_ns=10,
        available_ns=20,
        closed_through_ns=10,
        events=[dict(id="a", event_ns=1, values=[float("nan")])],
        unresolved_events=[],
        rejected_events=[],
    )
    with pytest.raises(ValueError, match="event_value"):
        qualification.publish_synthetic_fixture(
            store, fixture, process=process, process_pin=process_pin
        )
    fixture["events"][0]["values"] = []
    with pytest.raises(ValueError, match="event_value"):
        qualification.publish_synthetic_fixture(
            store, fixture, process=process, process_pin=process_pin
        )


@pytest.mark.parametrize(
    "event",
    [
        dict(id="a", event_ns=1, values=["not numeric"]),
        dict(id="a", event_ns=-1, values=[1.0]),
        dict(id="a", event_ns=1, values=[]),
    ],
)
def test_verifier_revalidates_fixture_bypassing_publisher(tmp_path, event):
    store = tmp_path / "receipts"
    process, process_pin = _process_contract(store)
    fixture = dict(
        version="synthetic-qualification-fixture-v1",
        entity="chain/market/A",
        start_ns=0,
        end_ns=10,
        available_ns=20,
        closed_through_ns=10,
        events=[event],
        unresolved_events=[],
        rejected_events=[],
        process_contract_pin=process_pin,
    )
    input_pin = digest(fixture)
    fixture_dir = store / "qualification-synthetic-fixtures"
    fixture_dir.mkdir(parents=True)
    atomic_json(fixture_dir / f"{input_pin}.json", fixture)
    member = _member(status="complete", counts=_counts(qualified=1))
    member["evidence"] = sorted([*member["evidence"], input_pin])
    with pytest.raises(ValueError, match="event_value"):
        qualification.execute_verifier(
            store,
            "synthetic_closed_fixture_v1",
            input_pin,
            process,
            process_pin,
            member,
        )


def test_selected_verifier_input_is_rechecked_after_receipt_cache(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts", count=1)
    store, scope, pin = _publish(tmp_path, pop_store, pop_pin, [member])
    assert _assert(store, pop_store, pop_pin, scope, pin, member)["observed"] == 1
    input_pin = member["verification"]["input_pin"]
    fixture_path = store / "qualification-synthetic-fixtures" / f"{input_pin}.json"
    fixture = json.loads(fixture_path.read_text())
    fixture["events"].clear()
    fixture_path.write_text(json.dumps(fixture))
    with pytest.raises(ValueError, match="input_checksum"):
        _assert(store, pop_store, pop_pin, scope, pin, member)


def test_scope_capability_evidence_hash_accounting_and_time_boundaries(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts", available=50)
    receipt_store, scope_pin, pin = _publish(tmp_path, pop_store, pop_pin, [member])
    assertion = _assert(receipt_store, pop_store, pop_pin, scope_pin, pin, member)
    # Event interval ends at 10; availability is never backdated.
    assert assertion["available_ns"] == 50

    no_obs = _verified_member(
        tmp_path / "receipts", capabilities=["complete_support", "population_coverage"]
    )
    store2, scope2, pin2 = _publish(tmp_path, pop_store, pop_pin, [no_obs])
    with pytest.raises(ValueError, match="member_capability_missing"):
        _assert(store2, pop_store, pop_pin, scope2, pin2, no_obs)

    changed = dict(assertion, status="unknown")
    with pytest.raises(ValueError, match="checksum"):
        # Reprojection always reloads and verifies the immutable receipt/member scope.
        path = receipt_store / "qualification-receipts" / f"{pin}.json"
        original = json.loads(path.read_text())
        path.write_text(json.dumps(dict(original, role="research_evaluation")))
        qualification.observation_fact(
            changed,
            receipt_store=receipt_store,
            population_store=pop_store,
            stream="ticks",
            destination_schema=_observation_schema(changed),
        )


def test_partition_denominator_mismatch_and_population_role_rejected(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts")
    scope_pin = _scope(tmp_path, pop_store, pop_pin, [member])
    bad = _receipt(pop_pin, scope_pin, _counts(qualified=1), store=tmp_path / "receipts")
    with pytest.raises(ValueError, match="partition_accounting"):
        qualification.publish_receipt(tmp_path / "receipts", bad)

    other = dict(_member("OUT"), entity="chain/market/OUT")
    with pytest.raises(ValueError, match="outside_population"):
        store, scope, pin = _publish(tmp_path, pop_store, pop_pin, [other])
        _assert(store, pop_store, pop_pin, scope, pin, other)


def test_process_contract_pins_semantics_but_separates_producers(tmp_path):
    store = tmp_path / "contracts"
    base, base_pin = _process_contract(store)
    identity, identity_pin = _process_contract(store, identity="composite-v2")
    time, time_pin = _process_contract(store, time_basis="receipt-time-v1")
    revisions, revisions_pin = _process_contract(store, revision="replace-latest-v2")
    schema, schema_pin = _process_contract(
        store,
        schema_fields=[dict(name="amount", unit="lamports", denomination="decimal SOL")],
    )
    assert len({base_pin, identity_pin, time_pin, revisions_pin, schema_pin}) == 5
    # Provider locations are deliberately absent from semantic identity.
    producer_a = dict(provider_location="provider-a", process=base)
    producer_b = dict(provider_location="provider-b", process=base)
    assert digest(producer_a["process"]) == digest(producer_b["process"]) == base_pin

    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts")
    receipt_store, scope_pin, first = _publish(tmp_path, pop_store, pop_pin, [member])
    first_receipt = qualification.load_receipt(receipt_store, first)
    shared_process, shared_pin = _process_contract(receipt_store)
    second = qualification.publish_receipt(
        receipt_store,
        _receipt(
            pop_pin,
            scope_pin,
            _counts(),
            store=receipt_store,
            available=21,
            contract_override=(shared_process, shared_pin),
        ),
    )
    second_receipt = qualification.load_receipt(receipt_store, second)
    assert (
        first != second
        and first_receipt["process_contract_pin"] == second_receipt["process_contract_pin"]
    )

    unresolved = dict(base, payload_schema_pin="0" * 64)
    with pytest.raises(FileNotFoundError):
        qualification.process_contract(unresolved, store=store)
    for changes in (
        {"identity": "unregistered-identity-rule-v9"},
        {"time_basis": "unregistered-time-rule-v9"},
        {"revision": "unregistered-revision-rule-v9"},
    ):
        with pytest.raises(ValueError, match="rule_unsupported"):
            _process_contract(store, **changes)
    tampered = base["event_identity_rule"]
    rule_path = store / "qualification-rules" / f"{tampered['pin']}.json"
    rule_path.write_text(json.dumps(dict(version=tampered["version"], definition="changed")))
    with pytest.raises(ValueError, match="rule_unresolved"):
        qualification.process_contract(base, store=store)


def test_receipt_level_complete_capability_is_required(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts")
    receipt_store, scope_pin, pin = _publish(tmp_path, pop_store, pop_pin, [member])
    receipt = qualification.load_receipt(receipt_store, pin)
    receipt["capabilities"] = ["observation_support", "population_coverage"]
    altered_pin = digest(receipt)
    atomic_json(receipt_store / "qualification-receipts" / f"{altered_pin}.json", receipt)
    with pytest.raises(ValueError, match="complete_capability_missing"):
        qualification.assert_support(
            receipt_store,
            altered_pin,
            population_store=pop_store,
            population_pin=pop_pin,
            scope_pin=scope_pin,
            entity_name=member["entity"],
            start_ns=member["start_ns"],
            end_ns=member["end_ns"],
        )


def test_namespace_match_requires_a_segment_boundary(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _member()
    receipt_store = tmp_path / "receipts"
    process, _ = _process_contract(receipt_store)
    process["entity_namespace"] = "chain/market"
    process_pin = qualification.process_contract_pin(process, store=receipt_store)
    scope_pin = qualification.publish_scope(
        receipt_store,
        [member],
        population_store=pop_store,
        population_pin=pop_pin,
        role="train",
        process=process,
        process_pin=process_pin,
    )
    receipt = _receipt(
        pop_pin,
        scope_pin,
        _counts(),
        store=receipt_store,
        contract_override=(process, process_pin),
    )
    receipt_pin = qualification.publish_receipt(receipt_store, receipt)
    with pytest.raises(ValueError, match="entity_namespace"):
        qualification.assert_support(
            receipt_store,
            receipt_pin,
            population_store=pop_store,
            population_pin=pop_pin,
            scope_pin=scope_pin,
            entity_name="chain/marketx/A",
            start_ns=member["start_ns"],
            end_ns=member["end_ns"],
        )


def test_coverage_requires_matching_process_and_payload_contract(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts", count=1)
    store, scope, pin = _publish(tmp_path, pop_store, pop_pin, [member])
    assertion = _assert(store, pop_store, pop_pin, scope, pin, member)
    for changes in (
        {"payload_schema_pin": "f" * 64},
        {"modality": "lifecycle"},
        {"unit": "lamports"},
        {"source": "d" * 64},
        {"representation": "different-process-shape-v1"},
    ):
        with pytest.raises(ValueError, match="process_binding_mismatch"):
            qualification.coverage_fact(
                assertion,
                receipt_store=store,
                population_store=pop_store,
                stream="ticks",
                destination_contract=_destination(assertion, **changes),
            )


def test_observation_projection_requires_exact_destination_schema(tmp_path):
    pop_store, pop_pin = _population(tmp_path, ("A",))
    member = _verified_member(tmp_path / "receipts")
    store, scope, pin = _publish(tmp_path, pop_store, pop_pin, [member])
    assertion = _assert(store, pop_store, pop_pin, scope, pin, member)
    fact = qualification.observation_fact(
        assertion,
        receipt_store=store,
        population_store=pop_store,
        stream="ticks",
        destination_schema=_observation_schema(assertion),
    )
    assert fact["status"] == "complete"
    for changes in (
        {"process_name": "other-process"},
        {"stream": "other-stream"},
        {"payload_schema_pin": "f" * 64},
        {"features": [{"name": "amount", "unit": "USD", "modality": "events"}]},
        {"modality": "lifecycle"},
    ):
        with pytest.raises(ValueError, match="observation_schema_binding_mismatch"):
            qualification.observation_fact(
                assertion,
                receipt_store=store,
                population_store=pop_store,
                stream="ticks",
                destination_schema=_observation_schema(assertion, **changes),
            )


def test_paged_scope_last_member_lookup_and_corrupted_page_rejection(tmp_path, monkeypatch):
    names = tuple(f"M{i:04d}" for i in range(1025))
    pop_store, pop_pin = _population(tmp_path, names)
    members = [_member(name, status="unknown") for name in names]
    members[-1] = _verified_member(tmp_path / "receipts", names[-1])
    scans = []
    original_records = qualification.records

    def counted_records(*args):
        scans.append(1)
        yield from original_records(*args)

    monkeypatch.setattr(qualification, "records", counted_records)
    receipt_store, scope_pin, pin = _publish(tmp_path, pop_store, pop_pin, members)
    assert len(scans) == 1  # One streaming population pass at scope publication.
    full_sweeps = []
    original_scope_members = qualification._scope_members

    def counted_scope_members(*args):
        full_sweeps.append(1)
        yield from original_scope_members(*args)

    monkeypatch.setattr(qualification, "_scope_members", counted_scope_members)
    last = _assert(receipt_store, pop_store, pop_pin, scope_pin, pin, members[-1])
    assert last["entity"].endswith(names[-1]) and last["status"] == "complete"
    assert len(full_sweeps) == 1  # First use verifies the full receipt/scope once.

    page_reads = []
    original_page = qualification._page

    def counted_page(*args):
        page_reads.append(1)
        return original_page(*args)

    monkeypatch.setattr(qualification, "_page", counted_page)
    baseline = len(page_reads)
    for member in (members[0], members[512], members[-1], members[100]):
        _assert(receipt_store, pop_store, pop_pin, scope_pin, pin, member)
    assert len(page_reads) - baseline == 4  # One bounded page read per assertion.
    assert len(full_sweeps) == 1  # Later uses reuse the full immutable receipt validation.
    assert len(scans) == 1  # No population rescan per member.

    scope = qualification._scope_manifest(receipt_store, scope_pin)
    last_page = scope["pages"][-1]
    page_path = receipt_store / "qualification-scope-pages" / f"{last_page['pin']}.json"
    page_path.write_text("[]")
    with pytest.raises(ValueError, match="page_checksum"):
        qualification.assert_support(
            receipt_store,
            pin,
            population_store=pop_store,
            population_pin=pop_pin,
            scope_pin=scope_pin,
            entity_name=members[-1]["entity"],
            start_ns=members[-1]["start_ns"],
            end_ns=members[-1]["end_ns"],
        )


def test_population_coverage_stopped_status_and_planner_action():
    from memecoin_population.planning import action

    row = dict(
        entity="chain/market/A",
        stream="ticks",
        contract="e" * 64,
        start_ns=0,
        end_ns=10,
        known_ns=10,
        stage="qualified",
        status="stopped",
        unit="events",
        expected=None,
        observed=None,
        evidence=["a" * 64],
    )
    assert catalog.checked(row, "coverage", 10)["status"] == "stopped"
    assert action(None, row, None) == "review_stop"
    row["observed"] = 0
    with pytest.raises(ValueError, match="unknown_is_not_zero"):
        catalog.checked(row, "coverage", 10)
