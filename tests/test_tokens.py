import base64
import struct

import pytest

from memecoin_ai.cpmm import U64, EconomicState, Fees
from memecoin_ai.domain import ValidationError
from memecoin_ai.raydium import TOKEN_2022, b58encode
from memecoin_ai.tokens import TransferFee, mint_terms, transfer_swap, vault_terms


def account(raw):
    return dict(owner=TOKEN_2022, executable=False, data=[base64.b64encode(raw).decode(), "base64"])


def mint(extensions):
    raw = bytearray(166)
    raw[44] = 6
    raw[45] = 1
    raw[165] = 1
    for kind, value in extensions:
        raw += struct.pack("<HH", kind, len(value)) + value
    return account(raw)


def config(older=(5, 100, 100), newer=(10, 500, 500)):
    return bytes(72) + struct.pack("<QQHQQH", *older, *newer)


def test_fee_epoch_activation_and_inverse():
    m = mint([(1, config())])
    assert mint_terms(m, 6, 9)["transfer_fee"] == dict(epoch=5, maximum=100, bps=100)
    assert mint_terms(m, 6, 10)["transfer_fee"] == dict(epoch=10, maximum=500, bps=500)
    for bps in (0, 1, 100, 300, 9999, 10000):
        for maximum in (0, 1, 10, U64):
            fee = TransferFee(0, maximum, bps)
            for amount in (0, 1, 9, 10, 11, 1000, 1000000):
                expected = min((amount * bps + 9999) // 10000, maximum)
                assert fee.fee(amount) == expected
                net = amount - expected
                gross = fee.gross(net)
                assert gross - fee.fee(gross) == net
                assert gross <= amount
                if gross:
                    assert gross - 1 - fee.fee(gross - 1) < net
    with pytest.raises(ValidationError):
        TransferFee(0, 1, 1).gross(U64)
    for bad in (-1, True, 10001):
        with pytest.raises(ValidationError):
            TransferFee(0, 10, bad)


@pytest.mark.parametrize("kind", [3, 4, 6, 9, 12, 14, 16, 25, 26, 65535])
def test_unreviewed_mint_extensions_never_admitted(kind):
    with pytest.raises(ValidationError, match="unsupported mint"):
        mint_terms(mint([(kind, b"\0")]), 6, 0)


def test_duplicate_truncated_and_incompatible_extensions():
    for exts in (
        [(1, config()), (1, config())],
        [(1, bytes(107))],
        [(18, bytes(63))],
        [(19, bytes(2))],
    ):
        with pytest.raises(ValidationError):
            mint_terms(mint(exts), 6, 0)
    with pytest.raises(ValidationError):
        mint_terms(mint([]), 9, 0)


def test_withheld_is_separate_from_spendable_vault():
    raw = bytearray(166)
    raw[:32] = bytes([1]) * 32
    raw[32:64] = bytes([2]) * 32
    struct.pack_into("<Q", raw, 64, 1000)
    raw[108] = 1
    raw[165] = 2
    raw += struct.pack("<HHQ", 2, 8, 27)
    terms = vault_terms(account(raw), b58encode(bytes([1]) * 32))
    assert terms["amount"] == 1000 and terms["withheld"] == 27
    frozen = bytearray(raw)
    frozen[108] = 2
    with pytest.raises(ValidationError):
        vault_terms(account(frozen), b58encode(bytes([1]) * 32))
    raw += struct.pack("<HHB", 8, 1, 0)  # even disabled memo extension needs explicit semantics
    with pytest.raises(ValidationError, match="unsupported vault"):
        vault_terms(account(raw), b58encode(bytes([1]) * 32))


@pytest.mark.parametrize("direction", [0, 1])
@pytest.mark.parametrize("mode", [0, 1, 2])
def test_transfer_and_creator_fees_compose_without_double_charging(direction, mode):
    state = EconomicState((10**12, 2 * 10**12), (100, 200), (10, 20), (1, 2), 1000)
    fees = Fees(2500, 120000, 40000, 1000)
    tf = (TransferFee(0, 10000, 300), TransferFee(0, 5000, 200))
    q, gross, net, inf, outf = transfer_swap(
        state,
        fees,
        direction,
        100000,
        0,
        exact_input=True,
        creator_fee_on=mode,
        creator_enabled=True,
        token_fees=tf,
    )
    assert gross == 100000 and q.input_amount == gross - inf
    assert q.output_amount == net + outf
    assert q.after.vaults[direction] - state.vaults[direction] == gross - inf
    assert state.vaults[1 - direction] - q.after.vaults[1 - direction] == net + outf
    inverse, gross2, net2, inf2, outf2 = transfer_swap(
        state,
        fees,
        direction,
        net,
        U64,
        exact_input=False,
        creator_fee_on=mode,
        creator_enabled=True,
        token_fees=tf,
    )
    assert net2 == net and gross2 <= gross
    assert inverse.input_amount == gross2 - inf2 and inverse.output_amount == net2 + outf2
    with pytest.raises(ValidationError, match="maximum gross"):
        transfer_swap(
            state,
            fees,
            direction,
            net,
            gross2 - 1,
            exact_input=False,
            creator_fee_on=mode,
            creator_enabled=True,
            token_fees=tf,
        )
    with pytest.raises(ValidationError, match="minimum net"):
        transfer_swap(
            state,
            fees,
            direction,
            gross,
            net + 1,
            exact_input=True,
            creator_fee_on=mode,
            creator_enabled=True,
            token_fees=tf,
        )


def test_real_allowlist_checkpoints_and_explicit_unsupported_pool():
    import json
    from pathlib import Path

    from memecoin_ai.domain import digest
    from memecoin_ai.raydium import decode_checkpoint, decode_pool
    from memecoin_ai.sources import Capture
    from memecoin_ai.tokens import decode_supported_checkpoint

    root = Path(__file__).parent / "fixtures" / "token2022"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, sha in manifest["files"].items():
        assert digest((root / name).read_bytes()) == sha
    for case in manifest["cases"]:
        cap = Capture.load(root / case["path"])
        report = decode_supported_checkpoint(cap, case["pool"])
        assert report["schema_version"] == 2
        assert report["clock_epoch"] > 0
        assert report["execution_qualified"] is False
        assert all(
            report["vault_balances"][i] - report["pool"]["excluded_fees"][i]
            == report["effective_reserves"][i]
            for i in range(2)
        )
        with pytest.raises(ValidationError):
            decode_checkpoint(cap, case["pool"])
    unsupported = Capture.load(root / "unsupported").result()["value"]
    pool = decode_pool(unsupported[0])
    with pytest.raises(ValidationError, match="unsupported mint extensions"):
        mint_terms(unsupported[4], pool.decimals[0], 2000)


@pytest.mark.parametrize(
    "fault", ["clock-slot", "clock-owner", "fee-length", "fee-account", "owner", "unknown"]
)
def test_extended_checkpoint_rejects_inconsistent_evidence(fault):
    import json
    from dataclasses import replace
    from pathlib import Path

    from memecoin_ai.domain import canonical
    from memecoin_ai.sources import Capture
    from memecoin_ai.tokens import decode_supported_checkpoint

    root = Path(__file__).parent / "fixtures" / "token2022"
    cap = Capture.load(root / "checkpoint-1")
    response = json.loads(cap.response)
    values = response["result"]["value"]
    request = json.loads(cap.request_json)
    if fault == "clock-owner":
        values[6]["owner"] = TOKEN_2022
    elif fault == "owner":
        values[3]["owner"] = "unknown"
    else:
        index = 6 if fault == "clock-slot" else 5 if fault in ("fee-length", "unknown") else 3
        raw = bytearray(base64.b64decode(values[index]["data"][0]))
        if fault == "clock-slot":
            raw[0] ^= 1
        elif fault == "fee-account":
            raw[166:168] = struct.pack("<H", 7)
        elif fault == "unknown":
            raw[166:168] = struct.pack("<H", 14)
        else:
            offset = 166
            while struct.unpack_from("<H", raw, offset)[0] != 1:
                offset += 4 + struct.unpack_from("<H", raw, offset + 2)[0]
            raw[offset + 2 : offset + 4] = struct.pack("<H", 107)
        values[index]["data"][0] = base64.b64encode(raw).decode()
    with pytest.raises(ValidationError):
        decode_supported_checkpoint(
            replace(cap, response=canonical(response).encode()), request["params"][0][0]
        )


def test_real_creator_transfer_fee_and_harvest_sequences():
    import json
    from pathlib import Path

    from memecoin_ai.protocol import checkpoint, replay_transaction
    from memecoin_ai.sources import Capture

    root = Path(__file__).parent / "fixtures" / "token2022"
    manifest = json.loads((root / "manifest.json").read_text())
    seen_input = seen_output = seen_harvest = False
    for case in manifest["sequences"]:
        ctx = checkpoint(Capture.load(root / case["start"]), case["pool"])
        state = ctx.state
        withheld = ctx.withheld
        for path in case["transactions"]:
            tx = Capture.load(root / path).result()
            state, detail = replay_transaction(ctx, state, tx, withheld=withheld)
            for op in detail["operations"]:
                delta = op["vault_withheld_delta"]
                withheld = (withheld[0] + delta[0], withheld[1] + delta[1])
                if op["quote_checked"]:
                    assert op["quote"]["creator_fee"] > 0
                    seen_input |= op["token_transfer"]["input_fee"] > 0
                    seen_output |= op["token_transfer"]["output_fee"] > 0
                else:
                    assert op["kind"] == "harvest_withheld_to_mint"
                    assert delta[1] < 0 and withheld[1] == 0
                    seen_harvest = True
        if case["end"]:
            end = checkpoint(Capture.load(root / case["end"]), case["pool"])
            assert state == end.state and withheld == end.withheld
    assert seen_input and seen_output and seen_harvest
