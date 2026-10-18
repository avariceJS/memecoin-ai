import random

import pytest

from memecoin_pumpswap.economic_buy_exact_pool_v2 import (
    _signed_transfer_deltas,
    inspect_pool_v2_buy_exact,
)


def test_transfer_delta_conservation_over_generated_atomic_amounts():
    """Generated transfer ledgers conserve each mint's atomic units across accounts."""
    rng = random.Random(20261010)
    for _ in range(1_000):
        transfers = []
        for _ in range(rng.randint(1, 20)):
            source = f"account-{rng.randrange(8)}"
            destination = f"account-{rng.randrange(8)}"
            while destination == source:
                destination = f"account-{rng.randrange(8)}"
            transfers.append(
                {
                    "source": source,
                    "destination": destination,
                    "amount_base_units": rng.randint(1, (1 << 64) - 1),
                }
            )
        deltas = _signed_transfer_deltas(transfers)
        assert sum(deltas.values()) == 0


@pytest.mark.parametrize("amount", [0, -1, 1 << 64])
def test_transfer_delta_rejects_nonpositive_or_out_of_u64_atomic_amounts(amount):
    with pytest.raises(ValueError, match="transfer_amount_or_endpoint_invalid"):
        _signed_transfer_deltas([{"source": "a", "destination": "b", "amount_base_units": amount}])


def test_old_two_tail_fixture_is_not_silently_promoted_to_pool_v2():
    # A missing three-tail structural proof must fail closed at the instruction layout gate.
    transaction = {
        "version": "legacy",
        "transaction": {"message": {"accountKeys": [], "instructions": []}},
        "meta": {"err": None, "innerInstructions": []},
    }
    fact = inspect_pool_v2_buy_exact(transaction, [0])
    assert fact["state"] == "quarantined"
