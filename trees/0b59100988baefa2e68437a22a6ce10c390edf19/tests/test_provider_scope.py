"""Synthetic scope diagnostics: never a replacement for full trace qualification."""

import copy
from dataclasses import replace

import pytest

from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.provider_audit import diagnose_block_account_scope
from memecoin_ai.sources import Capture


def block():
    transactions = []
    for i, (pubkey, writable) in enumerate((("pool", True), ("config", False), ("other", True))):
        transactions.append(
            dict(
                version=0,
                transaction=dict(
                    signatures=[f"synthetic-{i}"],
                    message=dict(accountKeys=[dict(pubkey=pubkey, writable=writable)]),
                ),
                meta=dict(
                    err=None,
                    fee=1,
                    preBalances=[1],
                    postBalances=[0],
                    preTokenBalances=[],
                    postTokenBalances=[],
                    innerInstructions=[],
                    logMessages=["synthetic complete log"],
                ),
            )
        )
    return dict(
        blockhash="synthetic",
        previousBlockhash="parent",
        parentSlot=1,
        blockTime=2,
        transactions=transactions,
    )


def captures(a, b):
    request = canonical(
        dict(
            jsonrpc="2.0",
            id=1,
            method="getBlock",
            params=[
                2,
                dict(commitment="finalized", encoding="jsonParsed", transactionDetails="full"),
            ],
        )
    )
    left = Capture(
        "synthetic-left",
        "getBlock",
        request,
        1,
        2,
        canonical(dict(jsonrpc="2.0", id=1, result=a)).encode(),
    )
    return left, replace(
        left,
        source_alias="synthetic-right",
        response=canonical(dict(jsonrpc="2.0", id=1, result=b)).encode(),
    )


def test_scope_keeps_readonly_and_unrelated_truncation_and_full_failure():
    a = block()
    b = copy.deepcopy(a)
    for i in (1, 2):
        b["transactions"][i]["meta"]["logMessages"] = ["Log truncated"]
    report = diagnose_block_account_scope(*captures(a, b), ["pool", "config"])
    assert report["strict_comparison"]["status"] == "mismatch"
    assert report["groups"]["writable_reference"]["exact_matches"] == 1
    assert report["groups"]["readonly_reference"]["mismatched_indices"] == [1]
    assert report["groups"]["no_reference"]["truncated_right"] == [2]
    assert not report["scope_completeness_verified"]
    assert not report["market_dataset_qualified"]


def test_identical_truncation_is_still_unavailable_and_failed_writers_remain():
    a = block()
    a["transactions"][0]["meta"].update(err={"synthetic": True}, logMessages=["Log truncated"])
    r = diagnose_block_account_scope(*captures(a, a), ["pool"])
    assert r["strict_comparison"]["status"] == "unavailable"
    assert r["groups"]["writable_reference"]["count"] == 1
    assert r["groups"]["writable_reference"]["truncated_left"] == [0]


@pytest.mark.parametrize(
    "mutation", ["missing_flag", "integer_flag", "duplicate", "different_keys", "order"]
)
def test_scope_rejects_ambiguous_or_unaligned_account_mapping(mutation):
    a = block()
    keys = a["transactions"][0]["transaction"]["message"]["accountKeys"]
    if mutation == "missing_flag":
        del keys[0]["writable"]
    elif mutation == "integer_flag":
        keys[0]["writable"] = 1
    elif mutation == "duplicate":
        keys.append(copy.deepcopy(keys[0]))
    b = copy.deepcopy(a)
    if mutation == "different_keys":
        b["transactions"][0]["transaction"]["message"]["accountKeys"][0]["writable"] = False
    if mutation == "order":
        b["transactions"].reverse()
    with pytest.raises(ValidationError):
        diagnose_block_account_scope(*captures(a, b), ["pool"])


def test_unknown_metadata_and_block_fields_remain_in_strict_comparison():
    a = block()
    b = copy.deepcopy(a)
    b["transactions"][0]["meta"]["future_field"] = 7
    b["future_block_field"] = 8
    r = diagnose_block_account_scope(*captures(a, b), ["pool"])
    assert r["strict_comparison"]["differences"]["total_sites"] == 2
    assert r["groups"]["writable_reference"]["mismatched_indices"] == [0]
