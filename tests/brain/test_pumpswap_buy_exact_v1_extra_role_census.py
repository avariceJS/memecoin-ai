from __future__ import annotations

from memecoin_pumpswap.buy_exact_v1 import ACCOUNT_ROLES
from memecoin_pumpswap.compiled_economics import (
    FEE_RECIPIENTS_UPGRADE_2026_04_28,
    associated_token_address,
)
from memecoin_pumpswap.decoder import TOKEN
from tools.brain_senses_buy_exact_v1_extra_role_census import classify_suffix


def _case(suffix_state: str = "documented_fee_pair_suffix_candidate"):
    wallet = sorted(FEE_RECIPIENTS_UPGRADE_2026_04_28)[0]
    roles = {
        "pool": "pool-key",
        "base_mint": "base-mint-key",
        "quote_mint": "So11111111111111111111111111111111111111112",
        "base_token_program": TOKEN,
        "quote_token_program": TOKEN,
    }
    ata = associated_token_address(wallet, roles["quote_mint"], roles["quote_token_program"])
    row = {
        "suffix_state": suffix_state,
        "observed_fee_recipient": wallet,
        "observed_fee_ata": ata,
    }
    return row, roles, wallet, ata


def test_source_supported_trailing_pair_uses_quote_mint_and_program():
    row, roles, wallet, ata = _case()

    result = classify_suffix(row, [wallet, ata], roles)

    assert result["pair_state"] == "source_supported_pair"
    assert result["prefix_state"] == "none"
    assert result["wallet_in_pinned_eight"] is True
    assert result["ata_matches_raw_quote_roles"] is True
    assert result["raw_pair_matches_census"] is True
    assert result["discordance_reasons"] == []
    assert len(ACCOUNT_ROLES) == 23


def test_source_pair_keeps_leading_account_as_unresolved_prefix():
    row, roles, wallet, ata = _case("documented_fee_pair_with_unresolved_predecessor")

    result = classify_suffix(row, ["unclassified-prefix-key", wallet, ata], roles)

    assert result["pair_state"] == "source_supported_pair"
    assert result["prefix_state"] == "unresolved_prefix"


def test_census_claim_with_raw_ata_disagreement_is_discordant():
    row, roles, wallet, ata = _case("documented_fee_pair_suffix_candidate")
    assert ata != "different-ata"

    result = classify_suffix(row, [wallet, "different-ata"], roles)

    assert result["pair_state"] == "discordant"
    assert "trailing_ata_derivation_mismatch" in result["discordance_reasons"]
    assert "raw_suffix_differs_from_census" in result["discordance_reasons"]


def test_unqualified_short_suffix_remains_unknown():
    row, roles, _, _ = _case("fee_pair_unqualified")
    row["observed_fee_recipient"] = None
    row["observed_fee_ata"] = None

    result = classify_suffix(row, ["single-extra-key"], roles)

    assert result["pair_state"] == "unresolved_extension"
    assert result["prefix_state"] == "none"
    assert result["wallet_in_pinned_eight"] is None
