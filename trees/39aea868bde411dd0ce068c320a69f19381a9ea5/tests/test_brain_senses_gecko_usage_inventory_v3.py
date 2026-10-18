from __future__ import annotations

import json
import time
from typing import Any

import pytest

from tools import brain_senses_gecko_shared_usage_v1 as shared_usage
from tools import brain_senses_gecko_usage_inventory_v3 as inventory_v3


def _receipt() -> dict[str, Any]:
    from tools.brain_senses_gecko_capture_only_v6_host import (
        PRIOR_USAGE_CENSUS_PATH,
        REPO_ROOT,
    )

    return json.loads((REPO_ROOT / PRIOR_USAGE_CENSUS_PATH).read_bytes())


def _historical_projection(receipt: dict[str, Any]) -> dict[str, Any]:
    by_root = {root: [] for root in receipt["root_inventory"]["root_paths"]}
    for row in receipt["attempts"]:
        by_root[row["root"]].append({key: value for key, value in row.items() if key != "root"})
    return {
        "schema": "brain-senses-gecko-shared-usage-inventory-v1",
        "root_inventory": receipt["root_inventory"],
        "root_inventory_sha256": receipt["root_inventory_sha256"],
        "snapshots": [
            {
                "root": root,
                "snapshot_sha256": digest,
                "attempts": by_root[root],
            }
            for root, digest in receipt["snapshot_sha256_by_root"].items()
        ],
        "provider_io": False,
    }


def test_historical_projection_requires_exact_receipt_roots_hashes_and_attempts() -> None:
    receipt = _receipt()
    inventory = _historical_projection(receipt)
    inventory_v3._historical_projection_matches(inventory, receipt)

    forged_attempt = json.loads(json.dumps(inventory))
    forged_attempt["snapshots"][0]["attempts"].append({"request_id": "forged"})
    with pytest.raises(inventory_v3.InventoryV3Error, match="historical_attempt_mismatch"):
        inventory_v3._historical_projection_matches(forged_attempt, receipt)

    missing_root = json.loads(json.dumps(inventory))
    missing_root["snapshots"].pop()
    with pytest.raises(inventory_v3.InventoryV3Error, match="historical_snapshot_hash_mismatch"):
        inventory_v3._historical_projection_matches(missing_root, receipt)

    wrong_receipt = dict(receipt)
    wrong_receipt["attempt_count"] += 1
    with pytest.raises(inventory_v3.InventoryV3Error, match="historical_projection_mismatch"):
        inventory_v3._historical_projection_matches(inventory, wrong_receipt)


def test_full_shape_rejects_unknown_service_entry_and_malformed_v6_sibling() -> None:
    class Names:
        def __init__(self, entries: tuple[str, ...], children: tuple[str, ...] = ()) -> None:
            self.entries = entries
            self.children = children

        def list_names(self, path: str = "") -> tuple[str, ...]:
            if path == inventory_v3.SERVICE_ROOT:
                return self.entries
            if path == inventory_v3.CAPTURE_V6_ROOT:
                return self.children
            raise AssertionError(path)

    base = tuple(sorted(inventory_v3.inventory_v2.KNOWN_SERVICE_ENTRIES | {"capture-only-v6"}))
    release = inventory_v3.LEGACY_V6_RELEASE_ID
    assert inventory_v3._full_shape(Names(base, (release,))) == (list(base), [release])

    with pytest.raises(inventory_v3.InventoryV3Error, match="service_entry_unknown"):
        inventory_v3._full_shape(Names((*base, "capture-only-v7"), (release,)))
    with pytest.raises(inventory_v3.InventoryV3Error, match="release_id_invalid"):
        inventory_v3._full_shape(Names(base, (release, "unexpected-file")))


def test_shared_usage_assessment_counts_all_verified_roots_and_reserves_prospective() -> None:
    receipt = _receipt()
    root = "services/gecko-market-v1/capture-only-v6/" + "1" * 32
    attempts = [
        {
            "request_id": "a" * 64,
            "decision_ns": time.time_ns() - 60_000_000_000,
            "maximum_response_bytes": 16 * 1024,
            "response_bytes": None,
            "response_sha256": None,
        }
    ]
    roots = sorted([*receipt["root_inventory"]["root_paths"], root])
    historical_by_root: dict[str, list[dict[str, Any]]] = {path: [] for path in roots}
    for row in receipt["attempts"]:
        historical_by_root[row["root"]].append(
            {key: value for key, value in row.items() if key != "root"}
        )
    digest = "b" * 64
    snapshots = [
        shared_usage.make_verified_root_snapshot(
            root=path,
            status=(
                "verified_prefix" if path == root or historical_by_root[path] else "verified_none"
            ),
            root_inventory_sha256=digest,
            source_prefix_sha256="c" * 64,
            attempts=(
                [*historical_by_root[path], *attempts] if path == root else historical_by_root[path]
            ),
        )
        for path in roots
    ]
    inventory = {
        "snapshots": snapshots,
        "root_inventory": {"root_paths": roots},
    }
    assessment = inventory_v3.assess_shared_usage(
        inventory,
        checked_ns=time.time_ns(),
        prospective_request_id="next-request",
    )
    assert assessment["admitted"] is True
    assert assessment["rolling_30d"]["request_count_including_prospective"] == 64
    assert assessment["unresolved_attempt_count_in_rolling_30d"] == 4
    assert assessment["rolling_30d"]["physical_reserve_including_prospective"] == (
        64 * inventory_v3.policy_v6.PHYSICAL_RESERVE_PER_INTENT
    )


def test_shared_usage_assessment_fails_closed_at_daily_request_cap() -> None:
    now = time.time_ns()
    root = "services/gecko-market-v1/capture-only-v6/" + "2" * 32
    attempts = [
        {
            "request_id": f"{index:064x}",
            "decision_ns": now - 1_000_000,
            "maximum_response_bytes": 16 * 1024,
            "response_bytes": None,
            "response_sha256": None,
        }
        for index in range(inventory_v3.policy_v6.MAX_INTENTS_PER_UTC_DAY)
    ]
    snapshot = shared_usage.make_verified_root_snapshot(
        root=root,
        status="verified_prefix",
        root_inventory_sha256="d" * 64,
        source_prefix_sha256="e" * 64,
        attempts=attempts,
    )
    assessment = inventory_v3.assess_shared_usage(
        {"snapshots": [snapshot], "root_inventory": {"root_paths": [root]}},
        checked_ns=now,
        prospective_request_id="one-too-many",
    )
    assert assessment["admitted"] is False
    assert "utc_day_request_cap" in assessment["reasons"]
