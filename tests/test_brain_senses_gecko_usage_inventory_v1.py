from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402
from tools.brain_senses_gecko_shared_usage_v1 import assess_usage  # noqa: E402
from tools.brain_senses_gecko_usage_inventory_v1 import (  # noqa: E402
    SERVICE_ROOT,
    InventoryError,
    build_usage_inventory,
)


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


def test_present_v5_scout_chain_root_fails_closed_until_usage_replay_exists(
    writer: DescriptorWriter,
) -> None:
    scout_root = f"{SERVICE_ROOT}/capture-only-v2/{'a' * 32}"
    writer.mkdir(scout_root)
    writer.create(f"{scout_root}/.runner.lock", b"scout-runner-lock\n")
    with pytest.raises(InventoryError, match="gecko_v5_usage_verifier_unavailable"):
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
