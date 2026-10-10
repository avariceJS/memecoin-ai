from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2 import (
    _transaction_privilege,
)


def _transaction(static_count: int, required: int, readonly_signed: int, readonly_unsigned: int):
    return {
        "transaction": {
            "message": {
                "accountKeys": [f"key-{i}" for i in range(static_count)],
                "header": {
                    "numRequiredSignatures": required,
                    "numReadonlySignedAccounts": readonly_signed,
                    "numReadonlyUnsignedAccounts": readonly_unsigned,
                },
            }
        },
        "meta": {},
    }


def test_static_privileges_match_all_small_valid_message_header_partitions():
    # Exhaust every valid header partition for messages up to 12 static keys.
    for static_count in range(1, 13):
        for required in range(static_count + 1):
            for readonly_signed in range(required + 1):
                for readonly_unsigned in range(static_count - required + 1):
                    transaction = _transaction(
                        static_count, required, readonly_signed, readonly_unsigned
                    )
                    for index in range(static_count):
                        writable, signer = _transaction_privilege(transaction, index)
                        assert signer is (index < required)
                        expected_writable = (
                            index < required - readonly_signed
                            or required <= index < static_count - readonly_unsigned
                        )
                        assert writable is expected_writable


def test_loaded_address_privileges_keep_writable_and_readonly_partitions_exact():
    for static_count in range(1, 5):
        for writable_count in range(5):
            for readonly_count in range(5):
                transaction = _transaction(static_count, 1, 0, 0)
                transaction["meta"]["loadedAddresses"] = {
                    "writable": [f"loaded-w-{i}" for i in range(writable_count)],
                    "readonly": [f"loaded-r-{i}" for i in range(readonly_count)],
                }
                for offset in range(writable_count + readonly_count):
                    writable, signer = _transaction_privilege(
                        transaction, static_count + offset
                    )
                    assert writable is (offset < writable_count)
                    assert signer is False


@pytest.mark.parametrize(
    ("required", "readonly_signed", "readonly_unsigned"),
    [
        (-1, 0, 0),
        (2, 3, 0),
        (2, 0, 4),
    ],
)
def test_invalid_header_privilege_partitions_fail_closed(
    required: int, readonly_signed: int, readonly_unsigned: int
):
    transaction = _transaction(3, required, readonly_signed, readonly_unsigned)
    with pytest.raises(ValueError):
        _transaction_privilege(transaction, 0)


def test_loaded_address_index_past_resolved_message_fails_closed():
    transaction = _transaction(2, 1, 0, 0)
    transaction["meta"]["loadedAddresses"] = {"writable": [], "readonly": []}

    with pytest.raises(ValueError, match="account_index_exceeds_resolved_message"):
        _transaction_privilege(transaction, 2)
