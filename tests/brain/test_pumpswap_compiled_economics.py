from memecoin_pumpswap.compiled_economics import (
    FEE_RECIPIENTS_UPGRADE_2026_04_28,
    associated_token_address,
    diagnose_pumpswap_sell,
)
from memecoin_pumpswap.decoder import TOKEN, decode58


def test_fee_recipient_snapshot_is_explicit_and_bounded():
    assert len(FEE_RECIPIENTS_UPGRADE_2026_04_28) == 8
    assert all(len(decode58(address)) == 32 for address in FEE_RECIPIENTS_UPGRADE_2026_04_28)


def test_associated_token_derivation_is_deterministic_and_returns_pubkey():
    owner = "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD"
    wrapped_sol = "So11111111111111111111111111111111111111112"
    first = associated_token_address(owner, wrapped_sol, TOKEN)
    assert first == associated_token_address(owner, wrapped_sol, TOKEN)
    assert len(decode58(first)) == 32


def test_missing_compiled_transaction_stays_quarantined():
    result = diagnose_pumpswap_sell(None)
    assert result["status"] == "quarantined"
    assert result["reason"] == "transaction_shape"
    assert result["admission"] == "none"
    assert not result["training_eligible"]
    assert not result["execution_supported"]
