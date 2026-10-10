from __future__ import annotations

import json
from pathlib import Path

from memecoin_pumpswap.buy_exact_v1 import ACCOUNT_ROLES
from memecoin_pumpswap.decoder import PROGRAM
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _source_receipt_metadata
from tools.brain_senses_buy_exact_v1_role_qualifier import TARGET_ROLES, _roles_from_raw


def test_capture_rows_reads_full_blocks_from_v3_epoch_receipts(tmp_path: Path):
    epoch = tmp_path / "20261010T050000Z"
    epoch.mkdir(parents=True)
    (epoch / "attempt-context.json").write_text("{}")
    capture_dir = epoch / "captures"

    def write_capture(sequence: int, method: str, details: str | None):
        folder = capture_dir / f"{sequence:012d}"
        folder.mkdir(parents=True)
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": [455116910]}
        if details is not None:
            request["params"].append({"transactionDetails": details})
        receipt = {
            "source_alias": "helius-free",
            "method": method,
            "request_json": json.dumps(request),
            "storage": {"path": "response.json.zst"},
        }
        path = folder / "receipt.json"
        path.write_text(json.dumps(receipt))
        return path

    inventory = write_capture(1, "getBlocks", None)
    signature_only = write_capture(2, "getBlock", "signatures")
    full = write_capture(3, "getBlock", "full")

    rows = _capture_rows(epoch, v3_layout=True)

    full_receipt = json.loads(full.read_text())
    assert rows == [
        (
            full.parent / "response.json.zst",
            full_receipt,
            json.loads(full_receipt["request_json"]),
        )
    ]
    assert inventory not in [row[0] for row in rows]
    assert signature_only not in [row[1] for row in rows]


def test_source_receipt_metadata_accepts_legacy_capture_census_receipt():
    metadata = _source_receipt_metadata(
        {
            "git_commit": "d2f39b519db33fb2a5b5edca84acb03f1d663464",
            "offline_replay": True,
        }
    )

    assert metadata == {
        "provider_audit_code_commit": "d2f39b519db33fb2a5b5edca84acb03f1d663464",
        "provider_audit_analysis_sha256": None,
    }


def test_source_receipt_metadata_preserves_explicit_audit_fields():
    metadata = _source_receipt_metadata(
        {
            "audit_code_commit": "audit-commit",
            "git_commit": "fallback-commit",
            "analysis_sha256": "analysis-hash",
        }
    )

    assert metadata == {
        "provider_audit_code_commit": "audit-commit",
        "provider_audit_analysis_sha256": "analysis-hash",
    }


def test_raw_role_qualifier_uses_idl_positions_and_loaded_addresses():
    keys = [f"account-{index}" for index in range(len(ACCOUNT_ROLES) - 1)]
    keys[ACCOUNT_ROLES.index("pool")] = "pool-key"
    keys[ACCOUNT_ROLES.index("base_mint")] = "base-mint-key"
    keys[ACCOUNT_ROLES.index("quote_mint")] = "quote-mint-key"
    keys[ACCOUNT_ROLES.index("base_token_program")] = "base-token-program-key"
    keys[ACCOUNT_ROLES.index("quote_token_program")] = "quote-token-program-key"
    accounts = list(range(len(keys))) + [len(keys)]
    tx = {
        "transaction": {
            "message": {
                "accountKeys": keys,
                "instructions": [
                    {"programIdIndex": len(keys), "accounts": accounts, "data": "1"}
                ],
            },
            "signatures": ["signature"],
        },
        "meta": {"loadedAddresses": {"writable": [], "readonly": [PROGRAM]}},
    }

    roles, wire = _roles_from_raw(tx, [0])

    assert tuple(roles) == TARGET_ROLES
    assert roles == {
        "pool": "pool-key",
        "base_mint": "base-mint-key",
        "quote_mint": "quote-mint-key",
        "base_token_program": "base-token-program-key",
        "quote_token_program": "quote-token-program-key",
    }
    assert wire == "00"
