from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

from tools import brain_senses_gecko_usage_inventory_v2 as inventory  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json  # noqa: E402


@pytest.fixture
def writer(tmp_path: Path) -> Any:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    handle = DescriptorWriter(fd)
    try:
        yield handle
    finally:
        handle.close()
        os.close(fd)


def _prepare_diagnostic(writer: DescriptorWriter, *, release_id: str = "f" * 32) -> str:
    close = 1_800_000_000_000_000_000
    writer.mkdir(f"{inventory.SERVICE_ROOT}/capture-only-v2/{release_id}")
    root = f"{inventory.WATCHDOG_ROOT}/{release_id}"
    writer.mkdir(root)
    proof = {
        "bucket_close_ns": close,
        "confirmation_count": 6,
        "dispatch_count": 6,
        "dispatch_ns": [close + 61_000_000_000 + i * 12_000_000_000 for i in range(6)],
        "economic_admission": False,
        "eligible_new_open_count": 0,
        "intent_count": 6,
        "market_source_qualified": False,
        "missed_dispatch_count": 0,
        "pre_publication_census": {
            "includes_this_receipt": False,
            "observed_ns": close + 300_000_000_000,
            "root_inventory_sha256": "a" * 64,
        },
        "provider_io": False,
        "result_count": 6,
        "shared_usage_admitted": True,
        "timing_gate": "passed",
        "training_admission": False,
    }
    core = {
        "activation_authorized": False,
        "bucket_close_ns": close,
        "deadline_ns": close + 300_000_000_000,
        "observed_ns": close + 301_000_000_000,
        "proof": proof,
        "provider_io": False,
        "release_id": release_id,
        "schema": "brain-senses-gecko-canary-watchdog-v2",
        "schema_version": 2,
        "status": "passed",
    }
    receipt = {
        **core,
        "record_sha256": hashlib.sha256(canonical_json(core)).hexdigest(),
    }
    writer.create(f"{root}/{close}.json", canonical_json(receipt))
    return root


def test_watchdog_receipt_is_zero_attempt_diagnostic_and_prepublication_hash_is_labeled(
    writer: DescriptorWriter,
) -> None:
    root = _prepare_diagnostic(writer)
    snapshot, metadata = inventory._watchdog_diagnostic_snapshot(
        writer, root=root, root_inventory_sha256="b" * 64
    )
    assert snapshot["status"] == "verified_none"
    assert snapshot["attempts"] == []
    assert metadata["receipt"]["proof"]["pre_publication_census"]["includes_this_receipt"] is False


def test_watchdog_orphan_fails_closed(writer: DescriptorWriter) -> None:
    root = f"{inventory.WATCHDOG_ROOT}/{'e' * 32}"
    writer.mkdir(root)
    writer.create(f"{root}/1800000000000000000.json", b"{}")
    with pytest.raises(inventory.InventoryError, match="watchdog_capture_sibling_missing"):
        inventory._watchdog_diagnostic_snapshot(writer, root=root, root_inventory_sha256="b" * 64)


@pytest.mark.parametrize("corruption", ["tampered", "extra_file"])
def test_watchdog_tampering_and_unrecognized_files_fail_closed(
    writer: DescriptorWriter, corruption: str
) -> None:
    root = _prepare_diagnostic(writer)
    receipt_path = f"{root}/1800000000000000000.json"
    if corruption == "tampered":
        writer.atomic_write(receipt_path, b"{}")
    else:
        writer.create(f"{root}/extra.json", b"{}")
    with pytest.raises(inventory.InventoryError):
        inventory._watchdog_diagnostic_snapshot(writer, root=root, root_inventory_sha256="b" * 64)


def test_watchdog_receipt_changing_between_reads_fails_closed(writer: DescriptorWriter) -> None:
    root = _prepare_diagnostic(writer)
    original_read = writer.read_bytes
    calls = 0

    class ChangingWriter:
        def __getattr__(self, name: str) -> Any:
            return getattr(writer, name)

        def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
            nonlocal calls
            calls += 1
            raw = original_read(path, max_bytes=max_bytes)
            return raw if calls == 1 else raw + b" "

    with pytest.raises(inventory.InventoryError, match="watchdog_root_changed_during_replay"):
        inventory._watchdog_diagnostic_snapshot(
            ChangingWriter(), root=root, root_inventory_sha256="b" * 64
        )
