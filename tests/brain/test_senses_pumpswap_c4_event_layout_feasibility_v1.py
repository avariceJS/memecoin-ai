from __future__ import annotations

import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools import brain_senses_pumpswap_c4_event_layout_feasibility_v1 as feasibility
from tools import brain_senses_pumpswap_idl_history_qualification_v1 as structural


def _field_strategy(kind: str):
    return {
        "u64": st.integers(min_value=0, max_value=(1 << 64) - 1),
        "i64": st.integers(min_value=-(1 << 63), max_value=(1 << 63) - 1),
        "i128": st.integers(min_value=-(1 << 127), max_value=(1 << 127) - 1),
        "bool": st.booleans(),
        "pubkey": st.binary(min_size=32, max_size=32),
        "string": st.text(max_size=48),
    }[kind]


def _reference_encode(
    fields: list[dict[str, str]], values: dict[str, object]
) -> tuple[bytes, list[tuple[str, int, int]]]:
    """Small independent test encoder using the locked Borsh primitive contract."""
    output = bytearray()
    offsets = []
    for field in fields:
        name, kind = field["name"], field["type"]
        start = len(output)
        value = values[name]
        if kind == "u64":
            output.extend(struct.pack("<Q", value))
        elif kind == "i64":
            output.extend(struct.pack("<q", value))
        elif kind == "i128":
            output.extend(int(value).to_bytes(16, "little", signed=True))
        elif kind == "bool":
            output.append(1 if value else 0)
        elif kind == "pubkey":
            assert isinstance(value, bytes) and len(value) == 32
            output.extend(value)
        elif kind == "string":
            assert isinstance(value, str)
            encoded = value.encode("utf-8")
            output.extend(struct.pack("<I", len(encoded)))
            output.extend(encoded)
        else:  # pragma: no cover - fixture schemas are source validated
            raise AssertionError(kind)
        offsets.append((name, start, len(output)))
    return bytes(output), offsets


LAYOUT_PARAMS = []
_seen = set()
for _layout in feasibility.load_layouts()[0]:
    if not _layout["available"]:
        continue
    _key = (_layout["event_name"], _layout["layout_sha256"])
    if _key not in _seen:
        _seen.add(_key)
        LAYOUT_PARAMS.append((_layout["event_name"], _layout["source_commit"], _layout["fields"]))


@pytest.mark.parametrize("event_name,source_commit,fields", LAYOUT_PARAMS)
@settings(max_examples=24, derandomize=True, database=None, deadline=None)
@given(data=st.data())
def test_generated_borsh_layout_round_trips_exact_bytes(event_name, source_commit, fields, data):
    del event_name, source_commit
    values = {field["name"]: data.draw(_field_strategy(field["type"])) for field in fields}
    encoded, expected_offsets = _reference_encode(fields, values)
    parsed = feasibility.parse_borsh_fields(fields, encoded)
    assert parsed["status"] == "exact"
    assert parsed["fields"] == values
    assert [
        (item["name"], item["start"], item["end"]) for item in parsed["offsets"]
    ] == expected_offsets
    assert parsed["consumed"] == len(encoded)


def _wire(
    fields: list[dict[str, str]], values: dict[str, object], event: str = "BuyEvent"
) -> bytes:
    layouts, receipt = feasibility.load_layouts()
    row = next(r for r in layouts if r["event_name"] == event and r["fields"] == fields)
    disc = bytes.fromhex(row["discriminator_hex"])
    body, _ = _reference_encode(fields, values)
    return structural.ANCHOR_EVENT_TAG_LE + disc + body


def test_parser_rejects_prefix_boolean_utf8_and_trailing_mutations():
    fields = [{"name": "value", "type": "bool"}]
    tag = structural.ANCHOR_EVENT_TAG_LE
    disc = b"12345678"
    assert (
        feasibility.parse_event_data(
            b"x" * 8 + disc + b"\x00", event_tag=tag, event_discriminator=disc, fields=fields
        )["status"]
        == "bad_event_tag"
    )
    assert (
        feasibility.parse_event_data(
            tag + disc[:-1], event_tag=tag, event_discriminator=disc, fields=fields
        )["status"]
        == "truncated_event_discriminator"
    )
    assert (
        feasibility.parse_event_data(
            tag + b"x" * 8 + b"\x00", event_tag=tag, event_discriminator=disc, fields=fields
        )["status"]
        == "event_discriminator_mismatch"
    )
    assert feasibility.parse_borsh_fields(fields, b"\x02")["status"] == "invalid_bool"
    assert feasibility.parse_borsh_fields(fields, b"\x01\x00")["status"] == "trailing_bytes"
    assert (
        feasibility.parse_borsh_fields([{"name": "s", "type": "string"}], b"\x01\x00\x00\x00\xff")[
            "status"
        ]
        == "invalid_utf8"
    )


def test_untrusted_string_length_is_checked_before_slice_or_decode():
    fields = [{"name": "s", "type": "string"}]

    class GuardedBytes(bytes):
        def __getitem__(self, item):
            if isinstance(item, slice) and item.start == 4 and item.stop and item.stop > 4:
                raise AssertionError(
                    "oversized declared length must be rejected before payload slicing"
                )
            return super().__getitem__(item)

    result = feasibility.parse_borsh_fields(fields, GuardedBytes(b"\xff\xff\xff\xff"))
    assert result["status"] == "truncated"
    assert result["declared_bytes"] == 0xFFFFFFFF
    assert result["remaining_bytes"] == 0


def test_historical_snapshot_layouts_are_distinguished_without_date_selection():
    layouts, _ = feasibility.load_layouts()
    for event_name in ("BuyEvent", "SellEvent"):
        distinct = {
            row["layout_sha256"]
            for row in layouts
            if row["event_name"] == event_name and row["available"]
        }
        assert len(distinct) >= 1
    # Two exact source rows with equivalent schema are equivalent, not ambiguous.
    fields = [{"name": "n", "type": "u64"}]
    value, _ = _reference_encode(fields, {"n": 7})
    disc = b"12345678"
    data = structural.ANCHOR_EVENT_TAG_LE + disc + value
    rows = [
        {
            "event_name": "BuyEvent",
            "source_commit": "a",
            "source_idl_sha256": "1",
            "layout_sha256": "same",
            "discriminator_hex": disc.hex(),
            "fields": fields,
            "available": True,
        },
        {
            "event_name": "BuyEvent",
            "source_commit": "b",
            "source_idl_sha256": "2",
            "layout_sha256": "same",
            "discriminator_hex": disc.hex(),
            "fields": fields,
            "available": True,
        },
    ]
    assert (
        feasibility.classify_layouts(data, "BuyEvent", rows, structural.ANCHOR_EVENT_TAG_LE)[0]
        == "equivalent"
    )
    rows[1]["layout_sha256"] = "different"
    assert (
        feasibility.classify_layouts(data, "BuyEvent", rows, structural.ANCHOR_EVENT_TAG_LE)[0]
        == "ambiguous"
    )
    assert (
        feasibility.classify_layouts(data[:-1], "BuyEvent", rows, structural.ANCHOR_EVENT_TAG_LE)[0]
        == "malformed"
    )


def test_selected_ten_frame_replay_accounting_and_wire_profiles():
    report = feasibility.replay()
    assert report["activation_authorized"] is False
    assert report["live_rpc_requests"] == 0
    assert len(report["capture_inputs"]) == 160
    accounting = report["reference_accounting"]
    assert accounting["total_target_references"] == 9_130
    assert accounting["by_event_and_transaction_status"] == {
        "BuyEvent": {"success": 3_947, "failed": 437},
        "SellEvent": {"success": 4_491, "failed": 255},
    }
    assert accounting["observed_wire_length_bytes_including_anchor_tag_and_discriminator"] == [
        {"event_name": "BuyEvent", "wire_bytes": 497, "references": 1169},
        {"event_name": "BuyEvent", "wire_bytes": 500, "references": 178},
        {"event_name": "BuyEvent", "wire_bytes": 508, "references": 35},
        {"event_name": "BuyEvent", "wire_bytes": 512, "references": 2576},
        {"event_name": "BuyEvent", "wire_bytes": 515, "references": 426},
        {"event_name": "SellEvent", "wire_bytes": 449, "references": 4746},
    ]
    assert (
        sum(
            row["references"]
            for row in accounting["by_event_and_overall_layout_outcome"]
            if row["layout_outcome"] == "unique"
        )
        == 9_130
    )
    assert not any(
        row["layout_outcome"] in {"ambiguous", "unsupported", "malformed"}
        for row in accounting["by_event_and_overall_layout_outcome"]
    )


def test_source_manifest_is_pinned_and_has_required_borsh_provenance():
    result = feasibility.verify_anchor_encoding_sources()
    assert result["anchor_commit"] == "23b90bffc023058aec1004dbc0898c233fb6e9b0"
    assert result["borsh_version"] == "0.9.3"
    assert result["solana_program_version"] == "1.15.2"
    assert len(result["cargo_lock_sha256"]) == 64
