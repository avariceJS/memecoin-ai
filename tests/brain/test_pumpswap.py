import copy

import pytest

from memecoin_pumpswap.decoder import EVENT_TAG, PROGRAM, TOKEN, decode, encode58, event_payload
from memecoin_pumpswap.schema import SCHEMA


def fixture():
    names = SCHEMA["instructions"]["buy"]["accounts"]
    roles = {k: encode58(bytes([n + 1]) * 32) for n, k in enumerate(names)}
    roles.update(program=PROGRAM, base_token_program=TOKEN, quote_token_program=TOKEN)
    vals = {k: 0 for k, t in SCHEMA["fields"]["BuyEvent"]}
    vals.update({k: v for k, v in roles.items() if k in vals})
    vals.update(
        base_amount_out=100,
        max_quote_amount_in=60,
        user_quote_amount_in=53,
        quote_amount_in=50,
        quote_amount_in_with_lp_fee=51,
        lp_fee=1,
        protocol_fee=1,
        coin_creator_fee=1,
        ix_name="buy",
        min_base_amount_out=100,
    )
    ev = EVENT_TAG + bytes(SCHEMA["events"]["BuyEvent"])
    for k, t in SCHEMA["fields"]["BuyEvent"]:
        v = vals[k]
        if t == "pubkey":
            # independently chosen fixed public-key bytes; zero for unused recipients
            part = bytes([names.index(k) + 1]) * 32 if k in names else bytes(32)
        elif t == "string":
            part = len(v).to_bytes(4, "little") + v.encode()
        else:
            part = int(v).to_bytes(
                {"u64": 8, "i64": 8, "i128": 16, "bool": 1}[t], "little", signed=t.startswith("i")
            )
        ev += part
    instr = dict(
        programId=PROGRAM,
        accounts=[roles[k] for k in names],
        stackHeight=1,
        data=encode58(
            bytes(SCHEMA["instructions"]["buy"]["discriminator"])
            + (100).to_bytes(8, "little")
            + (60).to_bytes(8, "little")
        ),
    )

    def transfer(a, b, m, amount, auth):
        return dict(
            programId=TOKEN,
            stackHeight=2,
            parsed=dict(
                type="transferChecked",
                info=dict(
                    source=roles[a],
                    destination=roles[b],
                    mint=roles[m],
                    authority=roles[auth],
                    tokenAmount=dict(amount=str(amount), decimals=6),
                ),
            ),
        )

    transfers = [
        transfer("pool_base_token_account", "user_base_token_account", "base_mint", 100, "pool"),
        transfer("user_quote_token_account", "pool_quote_token_account", "quote_mint", 51, "user"),
        transfer(
            "user_quote_token_account",
            "protocol_fee_recipient_token_account",
            "quote_mint",
            1,
            "user",
        ),
        transfer("user_quote_token_account", "coin_creator_vault_ata", "quote_mint", 1, "user"),
    ]
    transfers.append(
        dict(
            programId=PROGRAM, stackHeight=2, accounts=[roles["event_authority"]], data=encode58(ev)
        )
    )
    keys = [roles[k] for k in names]

    def bal(k, m, amount):
        return dict(
            accountIndex=keys.index(roles[k]),
            mint=roles[m],
            uiTokenAmount=dict(amount=str(amount), decimals=6),
        )

    tx = dict(
        slot=10,
        blockTime=1,
        version=0,
        transaction=dict(
            signatures=["fixture-signature"], message=dict(accountKeys=keys, instructions=[instr])
        ),
        meta=dict(
            err=None,
            innerInstructions=[dict(index=0, instructions=transfers)],
            preTokenBalances=[
                bal("pool_base_token_account", "base_mint", 1000),
                bal("pool_quote_token_account", "quote_mint", 1000),
            ],
            postTokenBalances=[
                bal("pool_base_token_account", "base_mint", 900),
                bal("pool_quote_token_account", "quote_mint", 1051),
            ],
        ),
    )
    return tx, ev


def test_valid_integer_reconciliation_and_no_training_permission():
    tx, ev = fixture()
    r = decode(tx)
    assert event_payload(ev)["exact"]
    assert r["status"] == "reconciled_candidate"
    assert r["vault_balance_reconciled"]
    assert r["swaps"][0]["movement"]["external_quote_fees"] == 2
    assert r["swaps"][0]["movement"]["quote_user_amount"] == 53
    assert not r["training_eligible"] and not r["execution_supported"]


@pytest.mark.parametrize(
    "mutation",
    [
        "emitter",
        "authority",
        "amount",
        "decimals",
        "vault_delta",
        "height",
        "duplicate_event",
        "missing_event",
        "pool",
        "token_program",
    ],
)
def test_corrupted_trace_never_qualifies(mutation):
    tx, _ = fixture()
    ins = tx["meta"]["innerInstructions"][0]["instructions"]
    if mutation == "emitter":
        ins[-1]["programId"] = TOKEN
    if mutation == "authority":
        ins[0]["parsed"]["info"]["authority"] = "foreign"
    if mutation == "amount":
        ins[0]["parsed"]["info"]["tokenAmount"]["amount"] = "101"
    if mutation == "decimals":
        ins[0]["parsed"]["info"]["tokenAmount"]["decimals"] = 9
    if mutation == "vault_delta":
        tx["meta"]["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "901"
    if mutation == "height":
        ins[0]["stackHeight"] = 3
    if mutation == "duplicate_event":
        ins.append(copy.deepcopy(ins[-1]))
    if mutation == "missing_event":
        ins.pop()
    if mutation == "pool":
        tx["transaction"]["message"]["instructions"][0]["accounts"][0] = "foreign"
    if mutation == "token_program":
        ins[0]["programId"] = "foreign"
    r = decode(tx)
    assert r["status"] != "reconciled_candidate"
    assert not r["training_eligible"]


def test_new_event_tail_is_not_silently_accepted():
    tx, ev = fixture()
    ins = tx["meta"]["innerInstructions"][0]["instructions"]
    ins[-1]["data"] = encode58(ev + bytes(8))
    r = decode(tx)
    assert r["vault_balance_reconciled"] and r["status"] == "quarantined"
    assert "unknown_event_suffix" in r["swaps"][0]["blockers"]
    with pytest.raises(ValueError, match="event_truncated"):
        event_payload(ev[:-1])


def test_failed_transaction_emits_no_successful_swaps():
    tx, _ = fixture()
    tx["meta"]["err"] = {"InstructionError": [1, "Custom"]}
    r = decode(tx)
    assert r["status"] == "failed_transaction" and r["swaps"] == []
    assert decode(None)["status"] == "unavailable"


def test_router_cpi_parent_is_scoped_and_multiple_pools_remain_separate():
    tx, _ = fixture()
    outer = tx["transaction"]["message"]["instructions"][0]
    inner = tx["meta"]["innerInstructions"][0]["instructions"]
    outer["stackHeight"] = 2
    for i in inner:
        i["stackHeight"] = 3
    tx["transaction"]["message"]["instructions"] = [dict(programId="router")]
    tx["meta"]["innerInstructions"][0]["instructions"] = [outer] + inner
    r = decode(tx)
    assert r["status"] == "reconciled_candidate"
    assert r["swaps"][0]["path"] == [0, 0]
