from __future__ import annotations

from typing import Any

from hypothesis import example, given
from hypothesis import strategies as st

from tools.brain_senses_sell23_lamport_gate_v1 import (
    TOKEN,
    WSOL,
    project_destination_effects,
    project_temporary_account,
)

WALLET = "wallet"
TEMP = "temporary-wsol-account"
OTHER = "other-account"


def _source_effects() -> list[dict[str, object]]:
    return [
        {
            "kind": "create_account",
            "order": 1,
            "path": [2, 1],
            "payer": WALLET,
            "account": TEMP,
            "lamports": 1_488_440,
            "space_bytes": 165,
            "owner_program": TOKEN,
        },
        {
            "kind": "initialize_account",
            "order": 2,
            "path": [2, 2],
            "account": TEMP,
            "mint": WSOL,
            "variant": 18,
        },
        {
            "kind": "system_transfer",
            "order": 3,
            "path": [5],
            "source": WALLET,
            "destination": TEMP,
            "lamports": 400_000,
        },
        {"kind": "sync_native", "order": 4, "path": [6], "account": TEMP},
        {
            "kind": "transfer_checked",
            "order": 5,
            "path": [7, 1],
            "source": TEMP,
            "destination": OTHER,
            "mint": WSOL,
            "amount_atomic": 224_238,
            "decimals": 9,
        },
        {
            "kind": "system_transfer",
            "order": 6,
            "path": [7, 2],
            "source": TEMP,
            "destination": OTHER,
            "lamports": 20_000,
        },
        {
            "kind": "close_account",
            "order": 7,
            "path": [8],
            "account": TEMP,
            "destination": WALLET,
        },
    ]


def _generated_source_effects(
    created: int, credited: int, debited: int, native_transfer: int
) -> list[dict[str, object]]:
    effects = _source_effects()
    effects[0] = {**effects[0], "lamports": created}
    effects[2] = {**effects[2], "lamports": credited}
    effects[4] = {**effects[4], "amount_atomic": native_transfer}
    effects[5] = {**effects[5], "lamports": debited}
    return effects


@st.composite
def _valid_source_history(draw: Any) -> tuple[int, int, int, int]:
    created = draw(st.integers(min_value=1, max_value=10**9))
    credited = draw(st.integers(min_value=0, max_value=10**9))
    native_transfer = draw(st.integers(min_value=0, max_value=created + credited))
    debited = draw(st.integers(min_value=0, max_value=created + credited - native_transfer))
    return created, credited, debited, native_transfer


def test_corrected_source_projection_subtracts_native_transfer_lamports() -> None:
    result = project_temporary_account(
        account=TEMP,
        destination=WALLET,
        token_program=TOKEN,
        mint=WSOL,
        pre_lamports=0,
        post_lamports=0,
        effects=_source_effects(),
    )
    assert result["status"] == "source_projection_reconciled_candidate"
    assert result["created_lamports"] == 1_488_440
    assert result["system_credited_lamports"] == 400_000
    assert result["system_debited_lamports"] == 20_000
    assert result["native_transfer_amount_atomic_units"] == 224_238
    assert result["native_transfer_lamports_under_legacy_wsol_semantics"] == 224_238
    assert result["pre_close_lamports_candidate"] == 1_644_202
    assert result["close_credit_lamports"] is None


@given(history=_valid_source_history())
@example(history=(1_488_440, 224_238, 0, 224_238))
def test_generated_temp_histories_conserve_lamports_and_effect_perturbations(
    history: tuple[int, int, int, int],
) -> None:
    created, credited, debited, native_transfer = history
    effects = _generated_source_effects(created, credited, debited, native_transfer)

    def project(rows: list[dict[str, object]]) -> dict[str, object]:
        return project_temporary_account(
            account=TEMP,
            destination=WALLET,
            token_program=TOKEN,
            mint=WSOL,
            pre_lamports=0,
            post_lamports=0,
            effects=rows,
        )

    base = project(effects)
    candidate = base["pre_close_lamports_candidate"]
    assert base["status"] == "source_projection_reconciled_candidate"
    assert type(candidate) is int and candidate >= 0
    assert candidate == created + credited - debited - native_transfer

    # Altering any explicit numeric lamport effect must alter the projection or
    # fail closed. This guards each term independently, including native debit.
    for effect_index, field, direction in (
        (0, "lamports", 1),
        (2, "lamports", 1),
        (4, "amount_atomic", 1),
        (5, "lamports", 1),
    ):
        altered = list(effects)
        altered[effect_index] = {
            **altered[effect_index],
            field: int(altered[effect_index][field]) + direction,
        }
        result = project(altered)
        next_candidate = result["pre_close_lamports_candidate"]
        assert (
            result["status"] != "source_projection_reconciled_candidate"
            or next_candidate != candidate
        )

    # Removing any required lifecycle event and moving a transfer before its
    # SyncNative boundary both fail closed.
    for effect_index in (0, 1, 3, 4, 6):
        omitted = [row for index, row in enumerate(effects) if index != effect_index]
        result = project(omitted)
        assert result["pre_close_lamports_candidate"] is None
    reordered = list(effects)
    reordered[3], reordered[4] = reordered[4], reordered[3]
    assert project(reordered)["pre_close_lamports_candidate"] is None


def test_historical_native_transfer_corrects_old_close_candidate() -> None:
    effects = _generated_source_effects(
        created=1_488_440,
        credited=224_238,
        debited=0,
        native_transfer=224_238,
    )
    result = project_temporary_account(
        account=TEMP,
        destination=WALLET,
        token_program=TOKEN,
        mint=WSOL,
        pre_lamports=0,
        post_lamports=0,
        effects=effects,
    )
    old_candidate_without_native_debit = 1_488_440 + 224_238
    corrected_diagnostic_candidate = result["pre_close_lamports_candidate"]
    assert old_candidate_without_native_debit == corrected_diagnostic_candidate + 224_238
    assert corrected_diagnostic_candidate == 1_488_440
    assert result["close_credit_lamports"] is None


def test_opaque_parent_keeps_source_candidate_diagnostic_only() -> None:
    result = project_temporary_account(
        account=TEMP,
        destination=WALLET,
        token_program=TOKEN,
        mint=WSOL,
        pre_lamports=0,
        post_lamports=0,
        effects=_source_effects(),
        opaque_writable_paths=[[7]],
    )
    assert result["status"] == "opaque_parent_semantics_unresolved"
    assert result["pre_close_lamports_candidate"] == 1_644_202
    assert result["candidate_is_diagnostic_only"] is True
    assert result["close_credit_lamports"] is None


def test_source_projection_rejects_bad_order_missing_endpoint_and_wrong_units() -> None:
    effects = _source_effects()
    effects[4] = {**effects[4], "order": 3}
    bad_order = project_temporary_account(
        account=TEMP,
        destination=WALLET,
        token_program=TOKEN,
        mint=WSOL,
        pre_lamports=0,
        post_lamports=0,
        effects=effects,
    )
    assert bad_order["status"] == "unsupported"
    assert bad_order["pre_close_lamports_candidate"] is None

    missing = project_temporary_account(
        account=TEMP,
        destination=WALLET,
        token_program=TOKEN,
        mint=WSOL,
        pre_lamports=None,
        post_lamports=0,
        effects=_source_effects(),
    )
    assert missing["status"] == "missing_endpoint"

    wrong_decimals = _source_effects()
    wrong_decimals[4] = {**wrong_decimals[4], "decimals": 6}
    wrong_units = project_temporary_account(
        account=TEMP,
        destination=WALLET,
        token_program=TOKEN,
        mint=WSOL,
        pre_lamports=0,
        post_lamports=0,
        effects=wrong_decimals,
    )
    assert "native_transferchecked_roles_or_units_invalid" in wrong_units["reasons"]
    assert wrong_units["pre_close_lamports_candidate"] is None


def test_destination_projection_counts_creation_refund_sibling_and_fee_overlap_once() -> None:
    effects = [
        {
            "kind": "create_account",
            "order": 0,
            "path": [2, 1],
            "payer": WALLET,
            "account": TEMP,
            "lamports": 1_488_440,
        },
        {
            "kind": "system_transfer",
            "order": 1,
            "path": [5],
            "source": OTHER,
            "destination": WALLET,
            "lamports": 500_000,
        },
        {
            "kind": "system_transfer",
            "order": 2,
            "path": [6, 0],
            "source": WALLET,
            "destination": OTHER,
            "lamports": 10_000,
        },
        {
            "kind": "system_transfer",
            "order": 3,
            "path": [6, 1],
            "source": WALLET,
            "destination": OTHER,
            "lamports": 100,
        },
    ]
    boundary = -5_000 - 1_488_440 + 500_000 - 10_000 - 100
    result = project_destination_effects(
        destination=WALLET,
        fee_payer=WALLET,
        pre_lamports=3_000_000,
        post_lamports=3_000_000 + boundary,
        fee_lamports=5_000,
        effects=effects,
    )
    assert result["status"] == "destination_ambiguous"
    assert result["decoded_projection_delta_lamports"] == boundary
    assert result["complete_equation_residual_lamports"] == 0
    assert result["unexplained_delta_lamports"] == 0
    assert result["fee_applied_once_to_message_fee_payer"] is True
    assert result["close_credit_in_equation"] is False


def test_new_destination_credit_requires_zero_prebalance_and_no_earlier_touch() -> None:
    create = {
        "kind": "create_account",
        "order": 1,
        "path": [1],
        "payer": OTHER,
        "account": WALLET,
        "lamports": 100,
    }
    refund = {
        "kind": "system_transfer",
        "order": 2,
        "path": [2, 0],
        "source": OTHER,
        "destination": WALLET,
        "lamports": 25,
    }
    accepted = project_destination_effects(
        destination=WALLET,
        fee_payer=OTHER,
        pre_lamports=0,
        post_lamports=125,
        fee_lamports=0,
        effects=[create, refund],
    )
    assert accepted["decoded_projection_delta_lamports"] == 125
    assert accepted["complete_equation_residual_lamports"] == 0
    assert accepted["partial_arithmetic_match_diagnostic_only"] is True

    prior_touch = {
        "kind": "system_transfer",
        "order": 0,
        "path": [0],
        "source": OTHER,
        "destination": WALLET,
        "lamports": 1,
    }
    rejected = project_destination_effects(
        destination=WALLET,
        fee_payer=OTHER,
        pre_lamports=0,
        post_lamports=126,
        fee_lamports=0,
        effects=[prior_touch, create, refund],
    )
    assert [1] in rejected["opaque_writable_paths"]
    assert rejected["withheld_destination_create_amounts_lamports"] == [100]
    assert rejected["complete_equation_residual_lamports"] is None
    assert rejected["status"] == "destination_ambiguous"


def test_failed_missing_dimensions_and_nested_order_fail_closed() -> None:
    failed = project_destination_effects(
        destination=WALLET,
        fee_payer=WALLET,
        pre_lamports=10,
        post_lamports=10,
        fee_lamports=5,
        effects=[],
        transaction_success=False,
    )
    assert failed["status"] == "failed_excluded"

    missing = project_destination_effects(
        destination=WALLET,
        fee_payer=WALLET,
        pre_lamports=None,
        post_lamports=10,
        fee_lamports=5,
        effects=[],
    )
    assert missing["status"] == "missing_endpoint"

    token_units = project_destination_effects(
        destination=WALLET,
        fee_payer=WALLET,
        pre_lamports=10,
        post_lamports=9,
        fee_lamports=1,
        effects=[
            {
                "kind": "token_transfer",
                "dimension": "token_atomic_units",
                "order": 0,
                "path": [3, 2, 1],
                "amount": 1,
            }
        ],
    )
    assert token_units["status"] == "destination_ambiguous"
    assert [3, 2, 1] in token_units["opaque_writable_paths"]

    # The flattened ordered leaf is counted once; its opaque parent remains a blocker.
    nested = project_destination_effects(
        destination=WALLET,
        fee_payer=WALLET,
        pre_lamports=10_000,
        post_lamports=8_999,
        fee_lamports=1_000,
        effects=[
            {
                "kind": "system_transfer",
                "order": 0,
                "path": [3, 2, 1],
                "source": WALLET,
                "destination": OTHER,
                "lamports": 1,
            }
        ],
        opaque_writable_paths=[[3]],
    )
    assert nested["decoded_projection_delta_lamports"] == -1_001
    assert nested["partial_arithmetic_match_diagnostic_only"] is False
    assert nested["complete_equation_residual_lamports"] is None
    assert nested["status"] == "destination_ambiguous"
    assert [3] in nested["opaque_writable_paths"]


def _destination_create(
    *, order: int = 1, path: list[int] | None = None, amount: int = 100
) -> dict[str, object]:
    return {
        "kind": "create_account",
        "order": order,
        "path": path or [1],
        "payer": OTHER,
        "account": WALLET,
        "lamports": amount,
    }


def _project_destination(effects: list[dict[str, object]], *, pre: int = 0) -> dict[str, object]:
    return project_destination_effects(
        destination=WALLET,
        fee_payer=OTHER,
        pre_lamports=pre,
        post_lamports=100,
        fee_lamports=0,
        effects=effects,
    )


def test_ordered_opaque_markers_gate_new_destination_create_credit() -> None:
    create = _destination_create()
    prior = {"kind": "opaque_writable_touch", "order": 0, "path": [0]}
    nested_create = _destination_create(order=1, path=[1, 1])
    parent = {"kind": "opaque_writable_touch", "order": 0, "path": [1]}
    later = {"kind": "opaque_writable_touch", "order": 2, "path": [2]}

    for effects in ([prior, create], [parent, nested_create]):
        result = _project_destination(list(effects))
        assert result["decoded_projection_delta_lamports"] == 0
        assert result["raw_destination_create_amounts_lamports"] == [100]
        assert result["withheld_destination_create_amounts_lamports"] == [100]
        assert result["complete_equation_residual_lamports"] is None
        assert result["status"] == "destination_ambiguous"

    after = _project_destination([create, later])
    assert after["decoded_projection_delta_lamports"] == 100
    assert after["withheld_destination_create_amounts_lamports"] == []
    assert after["complete_equation_residual_lamports"] is None
    assert after["status"] == "destination_ambiguous"


def test_create_credit_requires_complete_order_and_proven_readonly_prior() -> None:
    create = _destination_create()
    missing_order = {"kind": "opaque_writable_touch", "path": [0]}
    result = project_destination_effects(
        destination=WALLET,
        fee_payer=OTHER,
        pre_lamports=0,
        post_lamports=100,
        fee_lamports=0,
        effects=[missing_order, create],
    )
    assert result["status"] == "unsupported"
    assert result["raw_destination_create_amounts_lamports"] == [100]
    assert result["withheld_destination_create_amounts_lamports"] == [100]

    readonly = {"kind": "read_only_touch", "order": 0, "path": [0]}
    admitted = _project_destination([readonly, create])
    assert admitted["decoded_projection_delta_lamports"] == 100
    assert admitted["withheld_destination_create_amounts_lamports"] == []
    assert admitted["status"] == "destination_ambiguous"

    # Message-wide writable privilege with unproven inner AccountMeta is opaque.
    unknown_inner_meta = {
        "kind": "opaque_writable_touch",
        "order": 0,
        "path": [0, 1],
    }
    suppressed = _project_destination([unknown_inner_meta, create])
    assert suppressed["decoded_projection_delta_lamports"] == 0
    assert suppressed["withheld_destination_create_amounts_lamports"] == [100]


def test_duplicate_or_preexisting_create_is_unknown_not_zero() -> None:
    first = _destination_create(order=0, path=[0], amount=100)
    second = _destination_create(order=1, path=[1], amount=200)
    duplicate = _project_destination([first, second])
    assert duplicate["raw_destination_create_amounts_lamports"] == [100, 200]
    assert duplicate["withheld_destination_create_amounts_lamports"] == [100, 200]
    assert duplicate["unknown_contribution_count"] > 0
    assert duplicate["complete_equation_residual_lamports"] is None

    preexisting = _project_destination([_destination_create()], pre=1)
    assert preexisting["decoded_projection_delta_lamports"] == 0
    assert preexisting["withheld_destination_create_amounts_lamports"] == [100]
    assert preexisting["complete_equation_residual_lamports"] is None


def test_opaque_marker_never_adds_zero_or_upgrades_status() -> None:
    result = _project_destination(
        [
            {"kind": "opaque_writable_touch", "order": 0, "path": [0]},
            {
                "kind": "system_transfer",
                "order": 1,
                "path": [1],
                "source": OTHER,
                "destination": WALLET,
                "lamports": 50,
            },
        ]
    )
    assert result["decoded_projection_delta_lamports"] == 50
    assert result["unknown_contribution_count"] == 1
    assert result["unexplained_delta_lamports"] == 50
    assert result["complete_equation_residual_lamports"] is None
    assert result["partial_arithmetic_match_diagnostic_only"] is False
    assert result["status"] == "destination_ambiguous"
