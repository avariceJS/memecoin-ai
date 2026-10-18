from memecoin_pumpswap.decoder import TOKEN, TOKEN2022, decode58, encode58
from memecoin_pumpswap.legacy_token_ledger import (
    LEDGER_CONTRACT,
    NATIVE_MINT,
    reconcile_legacy_token_accounts,
)


def _compiled_transaction(
    *, program: str = TOKEN, sibling_transfer: bool = False, close_destination: bool = False
) -> dict:
    accounts = [
        program,
        "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD",
        "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "11111111111111111111111111111111",
    ]
    init_account = {
        "programIdIndex": 0,
        "accounts": [2, 3],
        "data": encode58(b"\x12" + decode58(accounts[4])),
    }
    transfer_checked = {
        "programIdIndex": 0,
        "accounts": [1, 3, 2, 4],
        "data": encode58(b"\x0c" + (5).to_bytes(8, "little") + b"\x09"),
    }
    instructions = [init_account, transfer_checked]
    destination_balance = 5
    source_balance = 5
    if sibling_transfer:
        instructions.append(
            {
                "programIdIndex": 0,
                "accounts": [1, 3, 2, 4],
                "data": encode58(b"\x0c" + (2).to_bytes(8, "little") + b"\x09"),
            }
        )
        destination_balance = 7
        source_balance = 3
    if close_destination:
        instructions.extend(
            [
                {
                    "programIdIndex": 0,
                    "accounts": [2, 3, 4],
                    "data": encode58(b"\x08" + (5).to_bytes(8, "little")),
                },
                {"programIdIndex": 0, "accounts": [2, 1, 4], "data": encode58(b"\x09")},
            ]
        )
    return {
        "version": "legacy",
        "transaction": {
            "signatures": ["signature"],
            "message": {
                "accountKeys": accounts,
                "instructions": instructions,
            },
        },
        "meta": {
            "err": None,
            "innerInstructions": [],
            "preTokenBalances": [
                {
                    "accountIndex": 1,
                    "mint": accounts[3],
                    "owner": accounts[4],
                    "programId": TOKEN,
                    "uiTokenAmount": {"amount": "10", "decimals": 9},
                }
            ],
            "postTokenBalances": [
                {
                    "accountIndex": 1,
                    "mint": accounts[3],
                    "owner": accounts[4],
                    "programId": TOKEN,
                    "uiTokenAmount": {"amount": str(source_balance), "decimals": 9},
                },
                *(
                    []
                    if close_destination
                    else [
                        {
                            "accountIndex": 2,
                            "mint": accounts[3],
                            "owner": accounts[4],
                            "programId": TOKEN,
                            "uiTokenAmount": {"amount": str(destination_balance), "decimals": 9},
                        }
                    ]
                ),
            ],
        },
    }


def test_initialize_and_full_transaction_sibling_transfer_conserve():
    transaction = _compiled_transaction(sibling_transfer=True)
    account = transaction["transaction"]["message"]["accountKeys"][2]

    result = reconcile_legacy_token_accounts(
        transaction,
        {account: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
    )

    assert result["contract"] == LEDGER_CONTRACT
    assert result["status"] == "exact"
    assert result["exact_account_count"] == 1
    assert (
        result["account_facts"][account]["opening_state"] == "derived_zero_from_ordered_initialize"
    )
    assert result["account_facts"][account]["opening_amount"] == 0
    assert result["account_facts"][account]["closing_amount"] == 7
    assert result["account_facts"][account]["transfer_mint_burn_delta"] == 7


def test_initialize_burn_and_close_proves_exact_non_native_lifecycle():
    transaction = _compiled_transaction(close_destination=True)
    account = transaction["transaction"]["message"]["accountKeys"][2]

    result = reconcile_legacy_token_accounts(
        transaction,
        {account: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
    )

    assert result["status"] == "exact"
    assert (
        result["account_facts"][account]["closing_state"] == "closed_by_ordered_token_instruction"
    )
    assert result["account_facts"][account]["closing_amount"] == 0


def test_wrapped_sol_close_after_sync_is_explicitly_censored():
    transaction = _compiled_transaction(close_destination=True)
    accounts = transaction["transaction"]["message"]["accountKeys"]
    accounts[3] = NATIVE_MINT
    transaction["transaction"]["message"]["instructions"] = [
        transaction["transaction"]["message"]["instructions"][0],
        {"programIdIndex": 0, "accounts": [2], "data": encode58(b"\x11")},
        transaction["transaction"]["message"]["instructions"][1],
        transaction["transaction"]["message"]["instructions"][-1],
    ]
    for rows_name in ("preTokenBalances", "postTokenBalances"):
        for row in transaction["meta"][rows_name]:
            row["mint"] = NATIVE_MINT
    account = accounts[2]

    result = reconcile_legacy_token_accounts(transaction, {account: NATIVE_MINT})

    assert result["status"] == "censored_partial"
    assert result["censored_account_count"] == 1
    assert result["unknown_account_count"] == 0
    assert result["account_facts"][account]["status"] == "censored"
    assert result["account_facts"][account]["closing_state"] == (
        "native_account_closed_lamport_settlement_unknown"
    )


def test_wrapped_sol_sync_cannot_serve_as_a_balancing_residual():
    transaction = _compiled_transaction()
    accounts = transaction["transaction"]["message"]["accountKeys"]
    accounts[3] = NATIVE_MINT
    transaction["transaction"]["message"]["instructions"].insert(
        1, {"programIdIndex": 0, "accounts": [2], "data": encode58(b"\x11")}
    )
    for rows_name in ("preTokenBalances", "postTokenBalances"):
        for row in transaction["meta"][rows_name]:
            row["mint"] = NATIVE_MINT
    account = accounts[2]

    result = reconcile_legacy_token_accounts(transaction, {account: NATIVE_MINT})

    assert result["status"] == "censored_partial"
    assert result["account_facts"][account]["status"] == "censored"
    assert result["account_facts"][account]["reason"] == (
        "native_sync_lamport_delta_unproven"
    )


def test_token_2022_effect_touching_target_account_stays_unknown():
    transaction = _compiled_transaction(program=TOKEN2022)
    account = transaction["transaction"]["message"]["accountKeys"][2]

    result = reconcile_legacy_token_accounts(
        transaction,
        {account: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
    )

    assert result["status"] == "unknown_partial"
    assert result["unknown_reasons"] == {"token_2022_effect": 1}


def test_missing_inner_instruction_trace_stays_unknown():
    transaction = _compiled_transaction()
    del transaction["meta"]["innerInstructions"]
    account = transaction["transaction"]["message"]["accountKeys"][2]

    result = reconcile_legacy_token_accounts(
        transaction,
        {account: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
    )

    assert result["status"] == "unknown_partial"
    assert result["instruction_trace"] == "unqualified"
