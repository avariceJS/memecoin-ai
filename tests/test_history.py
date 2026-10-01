import copy
import json
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from memecoin_ai.collection import PilotStore
from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.history import EpochSchedule, ParameterHistory
from memecoin_ai.protocol import checkpoint, replay_transaction
from memecoin_ai.raydium import PROGRAM, TOKEN_2022, b58encode, discriminator
from memecoin_ai.sources import Capture

FIXTURES = Path(__file__).parent / "fixtures" / "token2022"


def anchor():
    cap = Capture.load(FIXTURES / "checkpoint-2")
    schedule = EpochSchedule.from_capture(
        Capture.load(FIXTURES.parent / "parameter_history" / "epoch-schedule")
    )
    return cap, ParameterHistory(cap, schedule)


def config_update(history, param=0, value=1000):
    return dict(
        programId=PROGRAM,
        accounts=["authority", history.addresses[1]],
        data=b58encode(
            discriminator("global:update_amm_config") + struct.pack("<BQ", param, value)
        ),
    )


def fee_update(history, bps=50, maximum="10000"):
    return dict(
        programId=TOKEN_2022,
        parsed=dict(
            type="setTransferFee",
            info=dict(mint=history.addresses[5], transferFeeBasisPoints=bps, maximumFee=maximum),
        ),
    )


def test_config_updates_preserve_order_and_cannot_use_terminal_terms():
    cap, h = anchor()
    initial = h.fees
    h.apply(config_update(h), has_children=False)
    assert h.fees.trade_ppm == 1000
    with pytest.raises(ValidationError, match="terminal config"):
        h.assert_endpoint(cap)
    h.apply(config_update(h, value=initial.trade_ppm), has_children=False)
    h.assert_endpoint(cap)  # endpoints alone would miss the intermediate tariff
    with pytest.raises(ValidationError):
        h.apply(config_update(h, value=1000000), has_children=False)
    assert h.fees == initial


@pytest.mark.parametrize("parameter,value", [(1, 1), (2, 2), (5, 12345), (6, 1), (7, 100)])
def test_all_numeric_config_fields(parameter, value):
    _, h = anchor()
    old = copy.deepcopy(h.accounts[1])
    h.apply(config_update(h, parameter, value), has_children=False)
    assert old != h.accounts[1]


def test_fee_update_scheduling_replacement_and_epoch_boundary():
    _, h = anchor()
    original = h.token_fees()[1]
    epoch = h.epoch
    h.apply(fee_update(h), has_children=False)
    assert h.token_fees()[1] == original
    h.advance(h.schedule.first_normal_slot + (epoch + 1 - h.schedule.first_normal_epoch) * 432000)
    assert h.token_fees()[1] == original
    h.apply(fee_update(h, 200), has_children=False)
    # Replacing a not-yet-active tariff schedules from this epoch; it must not activate early.
    h.advance(h.slot + 432000)
    assert h.token_fees()[1] == original
    h.advance(h.slot + 432000)
    assert h.token_fees()[1].bps == 200
    assert h.token_fees()[1].fee(100000) == 2000
    with pytest.raises(ValidationError, match="regressed"):
        h.advance(h.slot - 1)


def test_schedule_validation_clock_anchoring_and_warmup():
    cap, h = anchor()
    with pytest.raises(ValidationError, match="Clock"):
        ParameterHistory(cap, EpochSchedule(400000, 14, 524256))
    with pytest.raises(ValidationError, match="warmup"):
        EpochSchedule(432000, 14, 524256).epoch(1)
    for epoch in (14, 1047, 1048):
        first = epoch * 432000
        assert h.schedule.epoch(first) == epoch
        assert h.schedule.epoch(first + 431999) == epoch
    valid = replace(
        cap,
        method="getEpochSchedule",
        request_json=canonical(dict(params=[])),
        response=canonical(
            dict(
                jsonrpc="2.0",
                id=1,
                result=dict(
                    slotsPerEpoch=432000, firstNormalEpoch=0, firstNormalSlot=0, warmup=True
                ),
            )
        ).encode(),
    )
    assert EpochSchedule.from_capture(valid) == h.schedule
    bad = replace(valid, response=valid.response.replace(b"432000", b"true"))
    with pytest.raises(ValidationError):
        EpochSchedule.from_capture(bad)


def test_unknown_mutations_and_child_calls_fail_closed():
    _, h = anchor()
    for ix in (
        dict(
            programId=TOKEN_2022,
            parsed=dict(type="setAuthority", info=dict(account=h.addresses[5])),
        ),
        dict(
            programId=TOKEN_2022, parsed=dict(type="freezeAccount", info=dict(mint=h.addresses[5]))
        ),
    ):
        with pytest.raises(ValidationError):
            h.apply(ix, has_children=False)
    with pytest.raises(ValidationError):
        h.apply(fee_update(h), has_children=True)


def test_supply_changes_and_invalid_amounts():
    _, h = anchor()
    before = copy.deepcopy(h.accounts)
    ix = dict(
        programId=TOKEN_2022,
        parsed=dict(type="mintTo", info=dict(mint=h.addresses[5], amount="123")),
    )
    h.apply(ix, has_children=False)
    assert h.accounts != before
    ix["parsed"]["type"] = "burn"
    h.apply(ix, has_children=False)
    assert h.accounts == before
    ix["parsed"]["info"]["amount"] = True
    with pytest.raises(ValidationError):
        h.apply(ix, has_children=False)


def test_dependency_scan_registry_opt_in_and_immutability(tmp_path):
    cap, _ = anchor()
    pool = json.loads(cap.request_json)["params"][0][0]
    store = PilotStore(tmp_path / "pilot.sqlite")
    try:
        with pytest.raises(ValidationError, match="Clock"):
            store.configure([pool], cap.source_alias, 100, dependency_history=True)
        store.configure([pool], cap.source_alias, 100, token2022=True, dependency_history=True)
        store.checkpoint(store.save_capture(cap), pool)
        payload = json.loads(cap.response)
        payload["result"]["context"]["slot"] += 1
        import base64

        clock = bytearray(base64.b64decode(payload["result"]["value"][6]["data"][0]))
        struct.pack_into("<Q", clock, 0, payload["result"]["context"]["slot"])
        payload["result"]["value"][6]["data"][0] = base64.b64encode(clock).decode()
        end = replace(cap, response=canonical(payload).encode(), received_ns=cap.received_ns + 1)
        store.checkpoint(store.save_capture(end), pool)
        addresses = {r[0] for r in store.db.execute("SELECT address FROM scans")}
        assert addresses == set(json.loads(cap.request_json)["params"][0][:6])
        assert len(addresses) == 6
        with pytest.raises(ValidationError, match="immutable"):
            store.configure([pool], cap.source_alias, 100, token2022=True)
    finally:
        store.close()


def test_real_swap_prefix_works_with_ordered_parameters():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    sequence = next(s for s in manifest["sequences"] if "harvest" in s["start"])
    cap = Capture.load(FIXTURES / sequence["start"])
    ctx = checkpoint(cap, sequence["pool"])
    h = ParameterHistory(
        cap,
        EpochSchedule.from_capture(
            Capture.load(FIXTURES.parent / "parameter_history" / "epoch-schedule")
        ),
    )
    state, withheld = ctx.state, list(ctx.withheld)
    for name in sequence["transactions"]:
        tx = Capture.load(FIXTURES / name).result()
        state, detail = replay_transaction(ctx, state, tx, parameters=h, withheld=tuple(withheld))
        for op in detail["operations"]:
            for i, delta in enumerate(op.get("vault_withheld_delta", (0, 0))):
                withheld[i] += delta
    assert state != ctx.state


def parameter_transaction(h, instructions, failed=False):
    return dict(
        slot=h.slot + 1,
        version=1,
        transaction=dict(
            signatures=["fixture-parameter-tx"],
            message=dict(
                accountKeys=[dict(pubkey=k) for k in ["authority", h.addresses[1]]],
                instructions=instructions,
            ),
        ),
        meta=dict(
            err={"fixture": True} if failed else None,
            preTokenBalances=[],
            postTokenBalances=[],
            innerInstructions=[],
            logMessages=[
                line
                for _ in instructions
                for line in [f"Program {PROGRAM} invoke [1]", f"Program {PROGRAM} success"]
            ],
        ),
    )


def test_failed_transaction_and_rejected_replay_cannot_mutate_parameter_history():
    cap, h = anchor()
    ctx = checkpoint(cap, h.addresses[0])
    original = copy.deepcopy(h.accounts)
    tx = parameter_transaction(h, [config_update(h)], failed=True)
    assert replay_transaction(ctx, ctx.state, tx, parameters=h)[1]["failed"]
    assert h.accounts == original
    tx = parameter_transaction(h, [config_update(h), config_update(h, value=1000000)])
    with pytest.raises(ValidationError):
        replay_transaction(ctx, ctx.state, tx, parameters=h)
    assert h.accounts == original
    tx = parameter_transaction(h, [config_update(h), config_update(h, value=h.fees.trade_ppm)])
    _, detail = replay_transaction(ctx, ctx.state, tx, parameters=h)
    assert len(detail["operations"]) == 2
    assert h.accounts == original
    assert h.slot == tx["slot"]


def test_wrong_historical_tariff_is_not_repaired_from_swap_event():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    sequence = next(s for s in manifest["sequences"] if "harvest" in s["start"])
    cap = Capture.load(FIXTURES / sequence["start"])
    ctx = checkpoint(cap, sequence["pool"])
    h = ParameterHistory(
        cap,
        EpochSchedule.from_capture(
            Capture.load(FIXTURES.parent / "parameter_history" / "epoch-schedule")
        ),
    )
    h.apply(config_update(h, value=123456), has_children=False)
    before = copy.deepcopy(h.accounts)
    with pytest.raises(ValidationError, match="event|slippage|minimum net output"):
        replay_transaction(
            ctx,
            ctx.state,
            Capture.load(FIXTURES / sequence["transactions"][0]).result(),
            parameters=h,
        )
    assert h.accounts == before
