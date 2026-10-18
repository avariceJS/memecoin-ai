from __future__ import annotations

from tools.brain_senses_buy_exact_v1_c4_state_bracketing import (
    _candidate_gates,
    _endpoint_status,
    _index_slot_writable_potential,
    _message_writable_flags,
    _required_accounts,
)


def test_static_header_writable_partition_exhaustively_for_small_messages() -> None:
    for static_count in range(1, 9):
        accounts = [
            {"index": index, "pubkey": f"static-{index}", "source": "static"}
            for index in range(static_count)
        ]
        for required in range(static_count + 1):
            for readonly_signed in range(required + 1):
                for readonly_unsigned in range(static_count - required + 1):
                    flags = _message_writable_flags(
                        accounts,
                        static_count,
                        {
                            "numRequiredSignatures": required,
                            "numReadonlySignedAccounts": readonly_signed,
                            "numReadonlyUnsignedAccounts": readonly_unsigned,
                        },
                    )
                    expected = {
                        f"static-{index}": (
                            index < required - readonly_signed
                            if index < required
                            else index < static_count - readonly_unsigned
                        )
                        for index in range(static_count)
                    }
                    assert flags == expected


def test_loaded_address_writable_occurrence_keeps_provenance_and_order() -> None:
    target = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    transaction = {
        "transaction": {
            "signatures": ["slot-signature"],
            "message": {
                "accountKeys": [
                    "ComputeBudget111111111111111111111111111111",
                    "11111111111111111111111111111111",
                ],
                "header": {
                    "numRequiredSignatures": 1,
                    "numReadonlySignedAccounts": 0,
                    "numReadonlyUnsignedAccounts": 1,
                },
                "addressTableLookups": [
                    {
                        "accountKey": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                        "writableIndexes": [7],
                        "readonlyIndexes": [],
                    }
                ],
                "instructions": [{"programIdIndex": 0, "accounts": [2], "data": ""}],
            },
        },
        "meta": {
            "err": None,
            "loadedAddresses": {"writable": [target], "readonly": []},
            "innerInstructions": [],
        },
    }

    indexed = _index_slot_writable_potential([transaction], {target})

    assert len(indexed[target]) == 1
    occurrence = indexed[target][0]
    assert occurrence["transaction_index"] == 0
    assert occurrence["instruction_paths_referencing_account"] == [[0]]
    assert occurrence["account_provenance"]["source"] == "loaded_writable"
    assert occurrence["account_provenance"]["lookup_index"] == 7
    assert "not_proof" in occurrence["privilege_semantics"]


def test_loaded_privileges_are_independent_of_static_header_partition() -> None:
    accounts = [
        {"index": 0, "pubkey": "signer", "source": "static"},
        {"index": 1, "pubkey": "static-readonly", "source": "static"},
        {"index": 2, "pubkey": "loaded-write", "source": "loaded_writable"},
        {"index": 3, "pubkey": "loaded-readonly", "source": "loaded_readonly"},
    ]

    flags = _message_writable_flags(
        accounts,
        2,
        {
            "numRequiredSignatures": 1,
            "numReadonlySignedAccounts": 0,
            "numReadonlyUnsignedAccounts": 1,
        },
    )

    assert flags == {
        "signer": True,
        "static-readonly": False,
        "loaded-write": True,
        "loaded-readonly": False,
    }


def test_missing_token_endpoint_is_explicitly_unknown() -> None:
    token_account = "token-account"
    status = _endpoint_status({"target_account_endpoint_availability": []}, [token_account])

    assert status["missing_or_partial_endpoint_count"] == 1
    assert status["unknown_accounts"] == [token_account]
    assert "zero" not in status["semantics"]


def test_required_accounts_keep_mint_and_token_account_groups_separate() -> None:
    roles = {
        "base_mint": "base-mint",
        "quote_mint": "quote-mint",
        "pool_base_token_account": "pool-base",
        "pool_quote_token_account": "pool-quote",
        "user_base_token_account": "user-base",
        "user_quote_token_account": "user-quote",
        "protocol_fee_recipient_token_account": "fee-recipient",
        "coin_creator_vault_ata": "creator-vault",
        "fee_quote_ata_suffix": "fee-suffix",
    }

    result = _required_accounts({"instruction": {"roles": roles}})

    assert result["unique_mint_keys"] == ["base-mint", "quote-mint"]
    assert len(result["unique_token_account_keys"]) == 7
    assert set(result["unique_full_state_keys"]) == set(roles.values())


def test_mint_only_state_bracket_does_not_admit_unresolved_token_account_sibling() -> None:
    roles = {
        "base_mint": "base-mint",
        "quote_mint": "quote-mint",
        "pool_base_token_account": "pool-base",
        "pool_quote_token_account": "pool-quote",
        "user_base_token_account": "user-base",
        "user_quote_token_account": "user-quote",
        "protocol_fee_recipient_token_account": "fee-recipient",
        "coin_creator_vault_ata": "creator-vault",
        "fee_quote_ata_suffix": "fee-suffix",
    }
    required = _required_accounts({"instruction": {"roles": roles}})
    occurrences = {key: [] for key in required["unique_full_state_keys"]}
    endpoint_rows = [
        {"account": key, "token_endpoint_state": "both_rows_present"}
        for key in required["unique_token_account_keys"]
    ]
    row = {
        "events": [
            {
                "candidate_descendant": True,
                "event_name": "BuyEvent",
                "event_exact": True,
                "event_roles_match_candidate_structure": True,
            }
        ],
        "event_descendant_count": 1,
        "target_account_endpoint_availability": endpoint_rows,
        "ordered_target_account_instruction_trace": [
            {
                "relation": "outside_candidate_subtree_sibling_or_unrelated",
                "path": [1],
                "program_id": "token-program",
                "touched_target_accounts": ["user-base"],
            }
        ],
    }

    gates = _candidate_gates(row, required, occurrences)

    assert gates["mint_only_no_other_transaction_writable_potential"] is True
    assert gates["mint_only_state_snapshot_bracket_feasible"] is True
    assert gates["full_state_slot_bracket_feasible"] is False
    assert gates["economic_path_qualified"] is False
