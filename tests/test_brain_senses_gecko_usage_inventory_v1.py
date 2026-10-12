from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402
from tools.brain_senses_gecko_scout_chain_v1 import DescriptorScoutJournal  # noqa: E402
from tools.brain_senses_gecko_shared_usage_v1 import assess_usage  # noqa: E402
from tools.brain_senses_gecko_usage_inventory_v1 import (  # noqa: E402
    SERVICE_ROOT,
    InventoryError,
    build_usage_inventory,
)


def _initialize_v2_empty_root(writer: DescriptorWriter, root: str) -> None:
    source_pin = hashlib.sha256(b"synthetic-pin").hexdigest()
    population = scout.pool_order(
        [
            {
                "pool_id": f"pool-{index:03d}",
                "base_mint": f"mint-{index:03d}",
                "quote_mint": "wsol",
                "rank_sha256": hashlib.sha256(
                    f"{source_pin}\0pool-{index:03d}".encode()
                ).hexdigest(),
            }
            for index in range(scout.POOL_COUNT)
        ],
        source_pin,
    )
    core = {
        "schema": scout.SCHEMA,
        "schema_version": 1,
        "policy_id": scout.POLICY_ID,
        "source": {"source_pin": source_pin},
        "population_count": scout.POOL_COUNT,
        "population_sha256": scout.population_digest(population),
        "population": population,
        "provider_io": False,
        "activation_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    policy = {**core, "policy_sha256": hashlib.sha256(scout.canonical_json(core)).hexdigest()}
    stat = os.fstat(writer.root_fd)
    storage = {
        "root_id": "0123456789abcdef0123456789abcdef",
        "volume_uuid": "12345678-1234-1234-1234-123456789abc",
        "device": stat.st_dev,
        "inode": stat.st_ino,
    }
    journal = DescriptorScoutJournal(
        writer,
        root=root,
        policy=policy,
        storage_binding=storage,
        release_sha256=hashlib.sha256(b"release").hexdigest(),
    )
    close = 1_800_000_000 * scout.NS
    close -= close % scout.BUCKET_NS
    journal.initialize(first_bucket_close_ns=close)

    lock_paths = {
        "helius": "services/locks/helius.lock",
        "alchemy": "services/locks/alchemy.lock",
        "gecko": "services/locks/gecko.lock",
    }
    allocations = {
        "helius": ("services/helius",),
        "alchemy": ("services/alchemy",),
        "gecko": (SERVICE_ROOT,),
        "coordinator": (capacity.COORDINATOR_ROOT,),
    }
    coordinator_policy = capacity.canonical(
        capacity.policy_document(storage, lock_paths, allocations)
    )
    writer.mkdir(capacity.COORDINATOR_ROOT)
    writer.create(capacity.POLICY_PATH, coordinator_policy)
    genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": capacity.sha256(coordinator_policy)},
    )
    writer.append(capacity.LEDGER_PATH, capacity.canonical(genesis))


@pytest.fixture
def writer(tmp_path: Path) -> Any:
    base = tmp_path / "data"
    base.mkdir(mode=0o700)
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    store = DescriptorWriter(fd)
    try:
        yield store
    finally:
        store.close()
        os.close(fd)


def test_empty_known_service_tree_explicitly_proves_all_direct_roots_absent(
    writer: DescriptorWriter,
) -> None:
    writer.mkdir(SERVICE_ROOT)
    inventory = build_usage_inventory(writer)
    assert inventory["root_inventory"]["service_entries"] == []
    assert len(inventory["snapshots"]) == 3
    assert all(row["status"] == "verified_absent" for row in inventory["snapshots"])
    assessed = assess_usage(
        inventory["snapshots"],
        expected_roots=[row["root"] for row in inventory["snapshots"]],
        checked_ns=1_800_000_000_000_000_000,
        prospective_request_id="candidate",
    )
    assert assessed["admitted"]
    assert assessed["root_inventory_sha256"] == inventory["root_inventory_sha256"]


def test_unknown_service_root_fails_closed_instead_of_being_omitted(
    writer: DescriptorWriter,
) -> None:
    writer.mkdir(f"{SERVICE_ROOT}/v2-retired")
    with pytest.raises(InventoryError, match="gecko_service_entry_unknown"):
        build_usage_inventory(writer)


def test_unknown_pilot_directory_identity_fails_closed(writer: DescriptorWriter) -> None:
    writer.mkdir(f"{SERVICE_ROOT}/v3-pilots/not-a-release-id")
    with pytest.raises(InventoryError, match="gecko_pilot_identity_invalid"):
        build_usage_inventory(writer)


def test_present_v5_root_fails_closed_until_its_independent_usage_verifier_exists(
    writer: DescriptorWriter,
) -> None:
    writer.mkdir(f"{SERVICE_ROOT}/v5")
    writer.create(f"{SERVICE_ROOT}/v5/.runner.lock", b"v5-runner-lock\n")
    with pytest.raises(InventoryError, match="gecko_v5_usage_verifier_unavailable"):
        build_usage_inventory(writer)


def test_incomplete_v2_scout_chain_root_fails_closed(
    writer: DescriptorWriter,
) -> None:
    scout_root = f"{SERVICE_ROOT}/capture-only-v2/{'a' * 32}"
    writer.mkdir(scout_root)
    writer.create(f"{scout_root}/.runner.lock", b"scout-runner-lock\n")
    with pytest.raises(InventoryError, match="gecko_v2_root_incomplete"):
        build_usage_inventory(writer)


def test_valid_plan_only_v2_root_is_replayed_as_verified_none(
    writer: DescriptorWriter,
) -> None:
    root = f"{SERVICE_ROOT}/capture-only-v2/{'b' * 32}"
    _initialize_v2_empty_root(writer, root)
    inventory = build_usage_inventory(writer)
    snapshot = next(row for row in inventory["snapshots"] if row["root"] == root)
    assert snapshot["status"] == "verified_none"
    assert snapshot["attempts"] == []


def test_v2_root_rejects_standalone_clock_artifacts(writer: DescriptorWriter) -> None:
    root = f"{SERVICE_ROOT}/capture-only-v2/{'c' * 32}"
    _initialize_v2_empty_root(writer, root)
    writer.mkdir(f"{root}/clocks")
    with pytest.raises(InventoryError, match="gecko_v2_root_entry_unknown"):
        build_usage_inventory(writer)


def test_v2_inventory_accounts_atomic_temp_debris_without_promoting_it(
    writer: DescriptorWriter,
) -> None:
    root = f"{SERVICE_ROOT}/capture-only-v2/{'d' * 32}"
    _initialize_v2_empty_root(writer, root)
    writer.mkdir(f"{root}/results")
    writer.create(f"{root}/results/.outcome.json.123.0123456789abcdef.tmp", b"partial")
    inventory = build_usage_inventory(writer)
    snapshot = next(row for row in inventory["snapshots"] if row["root"] == root)
    assert snapshot["status"] == "verified_none"
    assert snapshot["attempts"] == []

    writer.create(f"{root}/results/unrecognized.tmp", b"unrecognized")
    with pytest.raises(InventoryError, match="gecko_v2_journal_file_unexpected"):
        build_usage_inventory(writer)


def test_complete_inventory_hash_cannot_change_between_root_replays(
    writer: DescriptorWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer.mkdir(SERVICE_ROOT)
    original = __import__(
        "tools.brain_senses_gecko_usage_inventory_v1", fromlist=["_discover_roots"]
    )._discover_roots
    calls = {"count": 0}

    def changed(current_writer: DescriptorWriter) -> Any:
        result = original(current_writer)
        calls["count"] += 1
        if calls["count"] == 1:
            current_writer.mkdir(f"{SERVICE_ROOT}/v4")
        return result

    monkeypatch.setattr("tools.brain_senses_gecko_usage_inventory_v1._discover_roots", changed)
    with pytest.raises(InventoryError, match="gecko_root_inventory_changed_during_replay"):
        build_usage_inventory(writer)
