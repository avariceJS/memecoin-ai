import base64
import copy
import json
import struct

import pytest

from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.raydium import (
    PROGRAM,
    TOKEN,
    WSOL,
    account_bytes,
    b58decode,
    b58encode,
    decode_checkpoint,
    decode_pool,
    discriminator,
    swap_candidates,
)
from memecoin_ai.sources import Capture, SourceError


def account(raw, owner=PROGRAM):
    return dict(owner=owner, executable=False, data=[base64.b64encode(raw).decode(), "base64"])


def checkpoint():
    keys = [b58encode(bytes([i]) * 32) for i in range(1, 11)]
    keys[5] = WSOL
    keys[7] = keys[8] = TOKEN
    pool = bytearray(637)
    pool[:8] = discriminator("account:PoolState")
    for i, key in enumerate(keys):
        pool[8 + 32 * i : 40 + 32 * i] = b58decode(key)
    pool[331] = pool[332] = 9
    struct.pack_into("<7Q", pool, 333, 100, 10, 20, 30, 40, 1000, 4)
    struct.pack_into("<2Q", pool, 397, 50, 60)
    config = bytearray(236)
    config[:8] = discriminator("account:AmmConfig")
    struct.pack_into("<3Q", config, 12, 2500, 120000, 40000)
    values = [account(pool), account(config)]
    for mint in keys[5:7]:
        vault = bytearray(165)
        vault[:32] = b58decode(mint)
        vault[32:64] = b58decode(keys[1])
        struct.pack_into("<Q", vault, 64, 10000)
        vault[108] = 1
        values.append(account(vault, TOKEN))
    for _ in range(2):
        mint = bytearray(82)
        mint[44:46] = bytes([9, 1])
        values.append(account(mint, TOKEN))
    pool_id = b58encode(bytes([42]) * 32)
    addresses = decode_pool(values[0]).addresses(pool_id)
    request = canonical(
        dict(
            jsonrpc="2.0",
            id=1,
            method="getMultipleAccounts",
            params=[addresses, dict(encoding="base64", commitment="finalized", minContextSlot=100)],
        )
    )
    response = canonical(
        dict(jsonrpc="2.0", id=1, result=dict(context=dict(slot=101), value=values))
    ).encode()
    return Capture("fixture", "getMultipleAccounts", request, 200, 300, response), pool_id


def mutate(capture, index, offset, replacement):
    result = json.loads(capture.response)
    a = result["result"]["value"][index]
    raw = bytearray(base64.b64decode(a["data"][0]))
    raw[offset : offset + len(replacement)] = replacement
    a["data"][0] = base64.b64encode(raw).decode()
    return Capture(
        capture.source_alias,
        capture.method,
        capture.request_json,
        capture.request_started_ns,
        capture.received_ns,
        canonical(result).encode(),
    )


def test_checkpoint_excludes_all_fee_counters_and_preserves_time_semantics():
    capture, pool_id = checkpoint()
    decoded = decode_checkpoint(capture, pool_id)
    assert decoded["effective_reserves"] == [9910, 9880]
    assert decoded["context_slot"] == 101
    assert decoded["historical_event_time"] is None
    assert decoded["raw_received_ns"] == 300
    assert decoded["execution_qualified"] is False


@pytest.mark.parametrize(
    "index,offset,value",
    [
        (0, 0, b"bad!!!!!"),
        (0, 413, b"\1"),
        (0, 390, b"\2"),
        (2, 108, b"\2"),
        (4, 44, b"\6"),
        (1, 116, b"\1"),
    ],
)
def test_fail_closed_unknown_layout_and_invalid_token_state(index, offset, value):
    capture, pool_id = checkpoint()
    with pytest.raises(ValidationError):
        decode_checkpoint(mutate(capture, index, offset, value), pool_id)


def test_account_linkage_and_context_guards():
    capture, pool_id = checkpoint()
    req = json.loads(capture.request_json)
    req["params"][0][2], req["params"][0][3] = req["params"][0][3], req["params"][0][2]
    with pytest.raises(ValidationError, match="linkage"):
        decode_checkpoint(
            Capture("fixture", capture.method, canonical(req), 200, 300, capture.response), pool_id
        )
    req = json.loads(capture.request_json)
    req["params"][1]["minContextSlot"] = 102
    with pytest.raises(ValidationError, match="regressed"):
        decode_checkpoint(
            Capture("fixture", capture.method, canonical(req), 200, 300, capture.response), pool_id
        )
    with pytest.raises(ValidationError, match="fees exceed"):
        decode_checkpoint(mutate(capture, 2, 64, bytes(8)), pool_id)


def test_capture_roundtrip_and_corruption(tmp_path):
    capture, _ = checkpoint()
    capture.save(tmp_path / "raw")
    assert Capture.load(tmp_path / "raw") == capture
    (tmp_path / "raw/response.json").write_bytes(b"{}")
    with pytest.raises(SourceError, match="checksum"):
        Capture.load(tmp_path / "raw")


def test_base58_and_reject_token_extensions():
    for raw in [bytes(32), bytes(range(32)), b"\0\0\xff", b"\1"]:
        assert b58decode(b58encode(raw)) == raw
    with pytest.raises(ValidationError):
        b58decode("0")
    with pytest.raises(ValidationError, match="size/extensions"):
        account_bytes(account(bytes(166), TOKEN), TOKEN, 165)


def test_discovery_handles_cpi_and_failed_transactions():
    _, pool_id = checkpoint()
    instruction = dict(
        programId=PROGRAM,
        accounts=[pool_id] * 13,
        data=b58encode(discriminator("global:swap_base_input") + bytes(16)),
    )
    tx = dict(
        version=0,
        meta=dict(err=None, innerInstructions=[dict(instructions=[instruction])]),
        transaction=dict(message=dict(instructions=[])),
    )
    assert swap_candidates(tx) == [pool_id]
    failed = copy.deepcopy(tx)
    failed["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    assert swap_candidates(failed) == []


def test_token_extension_framing_preserves_unknown_types():
    from memecoin_ai.raydium import TOKEN_2022, token_inventory

    raw = bytearray(166)
    raw[165] = 1
    raw += struct.pack("<HH", 65000, 3) + b"abc"
    inventory = token_inventory(account(raw, TOKEN_2022), mint=True)
    assert inventory["extensions"] == [dict(type_id=65000, length=3)]
    assert inventory["transfer_semantics_verified"] is False
    with pytest.raises(ValidationError, match="truncated"):
        token_inventory(account(raw[:-1], TOKEN_2022), mint=True)
    with pytest.raises(ValidationError, match="duplicate"):
        token_inventory(account(raw + struct.pack("<HH", 65000, 0), TOKEN_2022), mint=True)
    raw[165] = 2
    with pytest.raises(ValidationError, match="account type"):
        token_inventory(account(raw, TOKEN_2022), mint=True)
