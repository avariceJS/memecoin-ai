"""Versioned scope: real new layouts, synthetic boundaries, unchanged swap economics."""

import base64
import json
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from memecoin_ai.causal import observed_checkpoint
from memecoin_ai.cpmm import EconomicState, Fees
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.execution_targets import quote
from memecoin_ai.protocol import Invocation, checkpoint, replay_transaction, transition
from memecoin_ai.raydium import decode_config
from memecoin_ai.sources import Capture
from memecoin_ai.tokens import TransferFee, decode_supported_checkpoint, transfer_swap

FIXTURES = Path(__file__).parent / "fixtures"


def cap(name="token2022"):
    return Capture.load(FIXTURES / "config-reserved" / name)


def change_word(c, offset, value):
    body = json.loads(c.response)
    b = bytearray(base64.b64decode(body["result"]["value"][1]["data"][0]))
    struct.pack_into("<Q", b, offset, value)
    body["result"]["value"][1]["data"][0] = base64.b64encode(b).decode()
    return replace(c, response=canonical(body).encode())


@pytest.mark.parametrize("name", ["token2022", "classic"])
def test_real_layout_is_explicitly_supported_only_for_v2_snapshot_quotes(name):
    c = cap(name)
    pool = json.loads(c.request_json)["params"][0][0]
    sha = digest(c.response)
    with pytest.raises(ValidationError, match="AmmConfig padding"):
        checkpoint(c, pool)  # Historical collection/replay path retains v1 guard.
    o = observed_checkpoint(c, pool, c.received_ns, config_layout_version=2)
    terms = o["economic"]["config_collection_terms"]
    assert terms["creator_fee_share_default_ppm"] == 50000
    assert terms["effective_creator_fee_share_ppm"] is None
    assert not terms["collection_execution_supported"]
    assert terms["applied_at"] == "creator_fee_collection; not swap"
    assert digest(c.response) == sha
    zero = change_word(c, 116, 0)
    z = observed_checkpoint(zero, pool, zero.received_ns, config_layout_version=2)
    for direction in (0, 1):
        amount = o["economic"]["effective_reserves"][direction] // 10000
        assert quote(o, direction, amount) == quote(z, direction, amount)


@pytest.mark.parametrize("rate", [0, 1, 50000, 1000000])
def test_v2_share_range_and_remainder_still_fail_closed(rate):
    c = change_word(cap(), 116, rate)
    pool = json.loads(c.request_json)["params"][0][0]
    decoded = decode_supported_checkpoint(c, pool, config_layout_version=2)
    assert decoded["config_collection_terms"]["creator_fee_share_default_ppm"] == rate
    with pytest.raises(ValidationError, match="padding"):
        decode_supported_checkpoint(change_word(c, 124, 1), pool, config_layout_version=2)
    with pytest.raises(ValidationError, match="share rate"):
        decode_supported_checkpoint(change_word(c, 116, 1000001), pool, config_layout_version=2)


def test_v2_never_bypasses_clock_or_unknown_layout_guard():
    c = Capture.load(FIXTURES / "clock-mismatch/first")
    pool = json.loads(c.request_json)["params"][0][0]
    with pytest.raises(ValidationError, match="Clock does not match"):
        decode_supported_checkpoint(c, pool, config_layout_version=2)
    account = json.loads(cap().response)["result"]["value"][1]
    with pytest.raises(ValidationError, match="layout version"):
        decode_config(account, layout_version=3)


@pytest.mark.parametrize("share", [0, 1, 50000, 999999, 1000000])
def test_documented_collection_reclassification_preserves_reserves_and_quotes(share):
    # Independent algebraic check of the documented settlement, not an executable
    # collection adapter: override PDA, destinations and CPI traces are unobserved.
    before = EconomicState(
        (100000003, 200000009), (10000, 20000), (5000, 6000), (12345, 56789), 100000
    )
    shared = tuple(c * share // 1000000 for c in before.creator)
    payout = tuple(c - s for c, s in zip(before.creator, shared, strict=True))
    after = replace(
        before,
        vaults=tuple(v - p for v, p in zip(before.vaults, payout, strict=True)),
        protocol=tuple(p + s for p, s in zip(before.protocol, shared, strict=True)),
        creator=(0, 0),
    )
    assert after.reserves == before.reserves
    assert all(s + p == c for s, p, c in zip(shared, payout, before.creator, strict=True))
    for direction in (0, 1):
        terms = dict(
            exact_input=True,
            creator_fee_on=0,
            creator_enabled=True,
            token_fees=(TransferFee(), TransferFee()),
        )
        a = transfer_swap(before, Fees(2500, 120000, 40000, 500), direction, 123456, 0, **terms)
        b = transfer_swap(after, Fees(2500, 120000, 40000, 500), direction, 123456, 0, **terms)
        assert a[1:] == b[1:]
        assert a[0].after.reserves == b[0].after.reserves


def test_snapshot_profile_cannot_silently_enter_legacy_reconstruction():
    c = cap()
    pool = json.loads(c.request_json)["params"][0][0]
    ctx = checkpoint(c, pool, config_layout_version=2)
    with pytest.raises(ValidationError, match="snapshot/quote only"):
        replay_transaction(ctx, ctx.state, {})
    with pytest.raises(ValidationError, match="snapshot/quote only"):
        transition(ctx, ctx.state, Invocation({}, 1))
