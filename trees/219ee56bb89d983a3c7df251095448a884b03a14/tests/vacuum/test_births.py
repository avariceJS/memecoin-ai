"""Synthetic ABI fixtures, never market evidence."""

import copy

import pytest

from memecoin_pumpswap.birth_schema import PROFILES
from memecoin_pumpswap.births import AUTHORITY, CREATE, GLOBAL, decode_births, fields
from memecoin_pumpswap.decoder import EVENT_TAG, PROGRAM, encode58


def fixture(profile):
    roles = {name: encode58(bytes([i + 1]) * 32) for i, name in enumerate(profile["accounts"])}
    roles.update(program=PROGRAM, global_config=GLOBAL, event_authority=AUTHORITY)
    values = dict(
        roles,
        coin_creator=encode58(bytes([99]) * 32),
        timestamp=100,
        base_mint_decimals=6,
        quote_mint_decimals=9,
    )

    def pack(layout):
        result = b""
        from memecoin_pumpswap.decoder import decode58

        for f in layout:
            v = values.get(f["name"], 1)
            size = {"u8": 1, "u16": 2, "u64": 8, "i64": 8, "bool": 1, "pubkey": 32}[f["type"]]
            result += decode58(v) if f["type"] == "pubkey" else v.to_bytes(size, "little")
        return result

    keys = list(roles.values())
    tx = dict(
        version=0,
        slot=50,
        blockTime=100,
        transaction=dict(
            signatures=["synthetic"],
            message=dict(
                accountKeys=keys,
                instructions=[
                    dict(
                        programId=PROGRAM,
                        accounts=keys,
                        data=encode58(CREATE + pack(profile["args"])),
                    )
                ],
            ),
        ),
        meta=dict(
            err=None,
            preBalances=[0] * len(keys),
            postBalances=[1] * len(keys),
            preTokenBalances=[],
            postTokenBalances=[
                dict(
                    accountIndex=keys.index(roles[f"pool_{s}_token_account"]),
                    mint=roles[f"{s}_mint"],
                    uiTokenAmount=dict(amount="1", decimals=values[f"{s}_mint_decimals"]),
                )
                for s in ("base", "quote")
            ],
            innerInstructions=[
                dict(
                    index=0,
                    instructions=[
                        dict(
                            programId=PROGRAM,
                            accounts=[AUTHORITY],
                            stackHeight=2,
                            data=encode58(
                                EVENT_TAG
                                + bytes(profile["discriminator"])
                                + pack(profile["fields"])
                            ),
                        )
                    ],
                )
            ],
        ),
    )
    return tx


@pytest.mark.parametrize("profile", PROFILES)
def test_exact_profile_and_role_binding(profile):
    tx = fixture(profile)
    result = decode_births(tx)
    assert result["status"] == "registry_metadata"
    assert result["births"][0]["training_eligible"] is False
    corrupt = copy.deepcopy(tx)
    corrupt["meta"]["innerInstructions"][0]["instructions"][0]["accounts"] = [GLOBAL]
    assert decode_births(corrupt)["status"] == "quarantined"
    tx["meta"]["preBalances"][0] = 1
    assert decode_births(tx)["status"] == "quarantined"


def test_suffix_never_ignored():
    with pytest.raises(ValueError, match="unknown_suffix"):
        fields(b"\x01\x00", [dict(name="x", type="u8")])
