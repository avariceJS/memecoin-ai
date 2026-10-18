from __future__ import annotations

from tools.brain_senses_c4_captured_class_replay_v1 import (
    _instructions,
    _program_id,
    _tx_status,
)


def test_transaction_status_does_not_turn_missing_meta_into_success():
    assert _tx_status({"meta": {"err": None}}) == "success"
    assert _tx_status({"meta": {"err": {"InstructionError": [0, "failed"]}}}) == "failed"
    assert _tx_status({"meta": {}}) == "unknown"
    assert _tx_status({"meta": None}) == "unknown"
    assert _tx_status({}) == "unknown"


def test_program_id_index_resolves_static_and_loaded_addresses_in_wire_order():
    keys = ["static-0", "static-1", "loaded-writable", "loaded-readonly"]
    assert _program_id({"programIdIndex": 0}, keys) == "static-0"
    assert _program_id({"programIdIndex": 2}, keys) == "loaded-writable"
    assert _program_id({"programIdIndex": 3}, keys) == "loaded-readonly"
    assert _program_id({"programIdIndex": 4}, keys) is None
    assert _program_id({"programIdIndex": True}, keys) is None


def test_instruction_exposure_keeps_outer_inner_and_unknown_denominators():
    tx = {
        "transaction": {
            "message": {
                "accountKeys": ["outer-program", "inner-program"],
                "instructions": [{"programIdIndex": 0}],
            }
        },
        "meta": {
            "loadedAddresses": {"writable": ["loaded"], "readonly": []},
            "innerInstructions": [
                {"index": 0, "instructions": [{"programIdIndex": 2}, {"programIdIndex": 9}]}
            ],
        },
    }

    records, unresolved, trace_unavailable = _instructions(tx)

    assert [(path, ix["_program"]) for path, ix in records] == [
        ("outer", "outer-program"),
        ("inner", "loaded"),
    ]
    assert unresolved == 1
    assert trace_unavailable == 0


def test_missing_inner_instruction_trace_is_not_reported_as_bad_program_index():
    records, unresolved, trace_unavailable = _instructions(
        {
            "transaction": {"message": {"accountKeys": ["program"], "instructions": []}},
            "meta": {"err": None},
        }
    )

    assert records == []
    assert unresolved == 0
    assert trace_unavailable == 1
