from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools import brain_senses_external_schema_v5 as schema


@given(times=st.lists(st.integers(min_value=1, max_value=10**18), min_size=1, max_size=50))
@settings(max_examples=100, deadline=None)
def test_hash_chain_round_trip_and_order_binding(times: list[int]) -> None:
    times.sort()
    records = []
    previous = None
    for seq, at_ns in enumerate(times):
        row = schema.event_record(
            schema=schema.CONTROL_SCHEMA,
            seq=seq,
            kind="budget_pause",
            at_ns=at_ns,
            previous_sha256=previous,
            fields={"reason": "rolling_cap"},
        )
        records.append(row)
        previous = row["event_sha256"]
    raw = b"".join(schema.canonical(row) for row in records)
    assert schema.verify_event_chain(raw, schema=schema.CONTROL_SCHEMA) == tuple(records)


@given(
    at_ns=st.integers(min_value=1, max_value=10**18),
    fields=st.dictionaries(st.text(max_size=20), st.integers(), max_size=8),
)
@settings(max_examples=100, deadline=None)
def test_event_record_is_deterministic_for_same_inputs(at_ns: int, fields: dict[str, int]) -> None:
    first = schema.event_record(
        schema=schema.EVENT_SCHEMA,
        seq=0,
        kind="epoch_started",
        at_ns=at_ns,
        previous_sha256=None,
        fields=fields,
    )
    second = schema.event_record(
        schema=schema.EVENT_SCHEMA,
        seq=0,
        kind="epoch_started",
        at_ns=at_ns,
        previous_sha256=None,
        fields=dict(reversed(list(fields.items()))),
    )
    assert schema.canonical(first) == schema.canonical(second)


@pytest.mark.parametrize(
    "mutation",
    ["sequence", "previous", "kind", "timestamp", "field", "digest", "tail"],
)
def test_hash_chain_rejects_mutation_and_torn_tail(mutation: str) -> None:
    first = schema.event_record(
        schema=schema.CONTROL_SCHEMA,
        seq=0,
        kind="epoch_open",
        at_ns=10,
        previous_sha256=None,
        fields={"epoch_id": "20261012T110000Z-0123456789ab", "plan_sha256": "a" * 64},
    )
    second = schema.event_record(
        schema=schema.CONTROL_SCHEMA,
        seq=1,
        kind="epoch_close",
        at_ns=20,
        previous_sha256=first["event_sha256"],
        fields={"epoch_id": "20261012T110000Z-0123456789ab", "terminal_sha256": "b" * 64},
    )
    raw = schema.canonical(first) + schema.canonical(second)
    if mutation == "tail":
        changed = raw[:-1]
    else:
        decoded_first = dict(first)
        decoded_second = dict(second)
        target = (
            decoded_first
            if mutation in {"sequence", "kind", "timestamp", "field", "digest"}
            else decoded_second
        )
        if mutation == "sequence":
            target["seq"] = 2
        elif mutation == "previous":
            target["previous_sha256"] = "c" * 64
        elif mutation == "kind":
            target["kind"] = "not-allowed"
        elif mutation == "timestamp":
            target["at_ns"] = 0
        elif mutation == "field":
            target["fields"] = {"epoch_id": "changed"}
        elif mutation == "digest":
            target["event_sha256"] = "d" * 64
        changed = schema.canonical(decoded_first) + schema.canonical(decoded_second)
    with pytest.raises(schema.SchemaError):
        schema.verify_event_chain(changed, schema=schema.CONTROL_SCHEMA)


def test_decode_rejects_duplicate_keys_and_non_finite_values() -> None:
    with pytest.raises(schema.SchemaError, match="json_duplicate_key"):
        schema.decode(b'{"x":1,"x":2}')
    with pytest.raises(schema.SchemaError, match="json_nonfinite"):
        schema.decode(b'{"x":NaN}')


def test_hash_chain_rejects_wall_clock_regression_even_with_valid_hashes() -> None:
    first = schema.event_record(
        schema=schema.EVENT_SCHEMA,
        seq=0,
        kind="epoch_started",
        at_ns=20,
        previous_sha256=None,
        fields={},
    )
    second = schema.event_record(
        schema=schema.EVENT_SCHEMA,
        seq=1,
        kind="window_missed",
        at_ns=19,
        previous_sha256=first["event_sha256"],
        fields={},
    )
    with pytest.raises(schema.SchemaError, match="event_chain_record_invalid"):
        schema.verify_event_chain(
            schema.canonical(first) + schema.canonical(second), schema=schema.EVENT_SCHEMA
        )
