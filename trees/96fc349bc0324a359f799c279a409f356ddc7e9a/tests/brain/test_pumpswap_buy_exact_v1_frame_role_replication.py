from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_pumpswap.compiled_economics import (  # noqa: E402
    FEE_RECIPIENTS_UPGRADE_2026_04_28,
    TOKEN,
    associated_token_address,
)
from tools.brain_senses_buy_exact_v1_frame_role_replication import (  # noqa: E402
    _classify_pair,
)


def _row(wallet: str, ata: str) -> dict[str, str]:
    return {"observed_fee_recipient": wallet, "observed_fee_ata": ata}


def _roles(mint: str = "So11111111111111111111111111111111111111112") -> dict[str, str]:
    return {"quote_mint": mint, "quote_token_program": TOKEN}


def test_frozen_pair_rule_covers_every_pinned_recipient_for_two_token_mints() -> None:
    # Exhaust the finite protocol-owned recipient set across native and legacy-token quote mints.
    mints = (
        "So11111111111111111111111111111111111111112",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    )
    for wallet in FEE_RECIPIENTS_UPGRADE_2026_04_28:
        for mint in mints:
            ata = associated_token_address(wallet, mint, TOKEN)
            result = _classify_pair(_row(wallet, ata), [wallet, ata], _roles(mint))
            assert result == {
                "pair_state": "source_supported_pair",
                "prefix_state": "none",
                "wallet_in_pinned_eight": True,
                "ata_matches_raw_quote_roles": True,
                "raw_pair_matches_census": True,
                "discordance_reasons": [],
            }


def test_three_account_shape_keeps_leading_account_unresolved() -> None:
    wallet = next(iter(FEE_RECIPIENTS_UPGRADE_2026_04_28))
    mint = "So11111111111111111111111111111111111111112"
    ata = associated_token_address(wallet, mint, TOKEN)
    result = _classify_pair(_row(wallet, ata), ["unknown-prefix", wallet, ata], _roles(mint))
    assert result["pair_state"] == "source_supported_pair"
    assert result["prefix_state"] == "unresolved_prefix"
    assert result["discordance_reasons"] == []


def test_wallet_and_ata_mismatches_are_explicitly_discordant() -> None:
    wallet = "11111111111111111111111111111111"
    mint = "So11111111111111111111111111111111111111112"
    ata = associated_token_address(wallet, mint, TOKEN)
    result = _classify_pair(_row(wallet, "wrong-census-ata"), [wallet, ata], _roles(mint))
    assert result["pair_state"] == "discordant"
    assert result["wallet_in_pinned_eight"] is False
    assert result["ata_matches_raw_quote_roles"] is True
    assert result["raw_pair_matches_census"] is False
    assert result["discordance_reasons"] == [
        "trailing_wallet_not_in_pinned_eight",
        "raw_pair_differs_from_census_pair",
    ]


def test_other_account_counts_remain_unknown() -> None:
    result = _classify_pair({}, ["wallet"], {"quote_mint": "", "quote_token_program": ""})
    assert result["pair_state"] == "unresolved"
    assert result["prefix_state"] == "none"
    assert result["discordance_reasons"] == ["remaining_account_count_outside_frozen_2_or_3_strata"]
