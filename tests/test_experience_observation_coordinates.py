import pytest

from memecoin_experience import observations, sparse
from memecoin_experience.storage import digest


def _domain(kind="finalized_slot", unit="slot"):
    return dict(
        version="typed-coordinate-domain-v1",
        name="solana_finalized_slot",
        network="solana-mainnet",
        kind=kind,
        unit=unit,
    )


def _schema(domain=None):
    domain = domain or _domain()
    return dict(
        version="market-observations-v2",
        role="train",
        availability="point_in_time",
        streams={
            "pumpswap_invocation": dict(
                process="synthetic pinned invocation reference",
                features=[dict(name="invocation", unit="indicator", modality="instruction")],
                coordinate_domain=domain,
                coordinate_domain_pin=digest(domain),
                identity_rule=dict(
                    version="observation-identity-rule-v1",
                    name="synthetic-coordinate-and-position-identity_v1",
                ),
                position_rule=dict(
                    version="observation-position-rule-v1",
                    name="synthetic-integer-position-path_v1",
                ),
                event_order_rule=dict(
                    version="observation-event-order-rule-v1",
                    name="coordinate_lexicographic_position_then_id_v1",
                ),
            )
        },
    )


def _row(slot, position, *, available_ns=7):
    domain = _schema()["streams"]["pumpswap_invocation"]["coordinate_domain"]
    return dict(
        kind="event",
        entity="solana/pool/synthetic",
        stream="pumpswap_invocation",
        id=f"event-{slot}-{position[-1]}",
        coordinate=slot,
        position=position,
        coordinate_domain_pin=digest(domain),
        available_ns=available_ns,
        evidence=["a" * 64],
        values=[1.0],
    )


def test_coordinate_fact_keeps_slot_position_and_receipt_availability(tmp_path):
    schema = _schema()
    rows = sorted([_row(454_538_109, [0, 2]), _row(454_538_109, [1, 0])], key=observations.key)
    store = tmp_path / "source"
    pin = observations.publish(
        store,
        rows,
        schema=schema,
        cutoff=17,
        sources=[dict(kind="synthetic_reconciliation", pin="b" * 64)],
        chunk=1,
    )
    source = observations.Source(store, pin, role="train")
    loaded = list(source.rows())
    assert [row["position"] for row in loaded] == [[0, 2], [1, 0]]
    assert {row["coordinate"] for row in loaded} == {454_538_109}
    assert {row["available_ns"] for row in loaded} == {7}
    assert all("event_ns" not in row for row in loaded)
    assert source.schema["streams"]["pumpswap_invocation"]["coordinate_domain_pin"] == digest(
        schema["streams"]["pumpswap_invocation"]["coordinate_domain"]
    )
    assert (
        source.schema["streams"]["pumpswap_invocation"]["identity_rule"]
        == schema["streams"]["pumpswap_invocation"]["identity_rule"]
    )


def test_duplicate_observation_identity_across_chunks_publishes_no_manifest(tmp_path):
    schema = _schema()
    row = _row(454_538_109, [0, 2])
    store = tmp_path / "duplicate-source"

    with pytest.raises(ValueError, match="observation_order_or_duplicate"):
        observations.publish(
            store,
            [row, dict(row)],
            schema=schema,
            cutoff=17,
            sources=[dict(kind="synthetic_reconciliation", pin="b" * 64)],
            chunk=1,
        )

    assert list((store / "observation-sources").glob("*.json")) == []


def test_coordinate_domain_pin_and_fields_are_verified(tmp_path):
    schema = _schema()
    schema["streams"]["pumpswap_invocation"]["coordinate_domain_pin"] = "c" * 64
    with pytest.raises(ValueError, match="coordinate_domain"):
        observations.publish(
            tmp_path / "bad",
            [_row(1, [0])],
            schema=schema,
            cutoff=17,
            sources=[dict(kind="synthetic", pin="b" * 64)],
        )


def test_coordinate_replication_preserves_contract_order_and_availability(tmp_path):
    rows = sorted([_row(101, [3]), _row(100, [9])], key=observations.key)
    source_store, replica_store = tmp_path / "source", tmp_path / "replica"
    pin = observations.publish(
        source_store,
        rows,
        schema=_schema(),
        cutoff=17,
        sources=[dict(kind="synthetic", pin="b" * 64)],
        chunk=1,
    )
    result = observations.replicate(source_store, replica_store, pin, role="train")
    assert result["source"] == pin
    original = observations.Source(source_store, pin, role="train")
    replicated = observations.Source(replica_store, pin, role="train")
    assert original.schema == replicated.schema
    assert list(original.rows()) == list(replicated.rows())
    assert replicated.schema["streams"]["pumpswap_invocation"]["event_order_rule"] == {
        "version": "observation-event-order-rule-v1",
        "name": "coordinate_lexicographic_position_then_id_v1",
    }


def test_identity_position_and_order_rules_are_schema_pinned(tmp_path):
    rows = [_row(100, [0])]
    original = _schema()
    changed = _schema()
    changed["streams"]["pumpswap_invocation"]["identity_rule"]["name"] = (
        "different-identity-rule-v1"
    )
    first = observations.publish(
        tmp_path / "first",
        rows,
        schema=original,
        cutoff=17,
        sources=[dict(kind="synthetic", pin="b" * 64)],
    )
    second = observations.publish(
        tmp_path / "second",
        rows,
        schema=changed,
        cutoff=17,
        sources=[dict(kind="synthetic", pin="b" * 64)],
    )
    assert first != second
    with pytest.raises(ValueError, match="stream_rule"):
        observations.contract(
            {
                **_schema(),
                "streams": {
                    "pumpswap_invocation": {
                        **_schema()["streams"]["pumpswap_invocation"],
                        "position_rule": {"version": "unknown-v9", "name": "position"},
                    }
                },
            }
        )


def test_utc_sparse_index_and_materializer_fail_before_reading_slot_rows(tmp_path):
    store = tmp_path / "source"
    pin = observations.publish(
        store,
        [_row(454_538_109, [0])],
        schema=_schema(),
        cutoff=17,
        sources=[dict(kind="synthetic", pin="b" * 64)],
    )
    source = observations.Source(store, pin, role="train")
    part = source.parts[0]
    object_path = store / "objects" / part["pin"][:2] / part["pin"]
    object_path.rename(object_path.with_name("removed"))
    with pytest.raises(ValueError, match="non_utc_coordinate"):
        source.index(tmp_path / "index.sqlite")
    with pytest.raises(ValueError, match="non_utc_coordinate"):
        sparse.materialize(store, pin, tmp_path / "experience", plan={})


def test_generic_coordinate_domains_are_pinned_without_time_conversion():
    schema = _schema(_domain(kind="ledger_sequence", unit="sequence_number"))
    observations.contract(schema)
    row = _row(900, [3], available_ns=2)
    row["coordinate_domain_pin"] = digest(
        schema["streams"]["pumpswap_invocation"]["coordinate_domain"]
    )
    observations.checked(row, schema, 10)
    assert row["coordinate"] == 900
    assert row["available_ns"] == 2
