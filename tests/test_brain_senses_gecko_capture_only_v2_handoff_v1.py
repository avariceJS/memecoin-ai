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

from memecoin_research_storage import StorageError  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_handoff_v1 as handoff  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v4 as inventory_v4  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

NEW_ID = "a" * 32
NEW_ROOT = f"services/gecko-market-v1/capture-only-v2/{NEW_ID}"
OLD_IDS = [f"request-{index}" for index in range(4)]
POOL_IDS = [f"pool-{index}" for index in range(6)]


def _sha(value: Any) -> str:
    return hashlib.sha256(shared_usage.canonical_json(value)).hexdigest()


def _attempts() -> list[dict[str, Any]]:
    return [
        {
            "request_id": OLD_IDS[index],
            "decision_ns": handoff.OLD_PARTIAL_CLOSE_NS + (61 + index) * 1_000_000_000,
            "maximum_response_bytes": 32_000_000,
            "response_bytes": 11 + index if index < 3 else None,
            "response_sha256": _sha({"response": index}) if index < 3 else None,
        }
        for index in range(4)
    ]


def _attempt_rows() -> list[dict[str, Any]]:
    return [
        {
            "slot_index": 6 + index,
            "pool_id": POOL_IDS[index],
            "request_id": OLD_IDS[index],
            "terminal_sha256": _sha({"terminal": index}),
            "coordinator_reserved_event_sha256": _sha({"coordinator_reserve": index}),
            "coordinator_terminal_event_sha256": _sha({"coordinator": index}),
            "coordinator_charge_count": 1,
            "outcome": "received" if index < 3 else "ambiguous_spent",
            "response_bytes": 11 + index if index < 3 else None,
            "response_sha256": _attempts()[index]["response_sha256"],
        }
        for index in range(4)
    ]


def _slots() -> list[dict[str, Any]]:
    rows = []
    for index, pool_id in enumerate(POOL_IDS):
        if index < 4:
            attempt = _attempt_rows()[index]
            rows.append(
                {
                    "slot_index": 6 + index,
                    "pool_id": pool_id,
                    "intent_present": True,
                    "dispatch_present": True,
                    "skip_present": False,
                    "request_id": OLD_IDS[index],
                    "terminal_sha256": attempt["terminal_sha256"],
                    "coordinator_reserved_event_sha256": attempt[
                        "coordinator_reserved_event_sha256"
                    ],
                    "coordinator_terminal_event_sha256": attempt[
                        "coordinator_terminal_event_sha256"
                    ],
                    "coordinator_charge_count": 1,
                    "outcome": attempt["outcome"],
                }
            )
        else:
            rows.append(
                {
                    "slot_index": 6 + index,
                    "pool_id": pool_id,
                    "intent_present": False,
                    "dispatch_present": False,
                    "skip_present": False,
                    "request_id": None,
                    "terminal_sha256": None,
                    "coordinator_reserved_event_sha256": None,
                    "coordinator_terminal_event_sha256": None,
                    "coordinator_charge_count": 0,
                    "outcome": "expired_without_intent_or_dispatch",
                }
            )
    return rows


def _expected_genesis(
    *,
    predecessor_snapshot_sha256: str = "1" * 64,
    predecessor_prefix_sha256: str = "2" * 64,
    census_sha256: str = "3" * 64,
    snapshot_set_sha256: str = "4" * 64,
) -> dict[str, Any]:
    return {
        "successor_root": NEW_ROOT,
        "successor_release_id": NEW_ID,
        "source_sha256": "5" * 64,
        "policy_sha256": "6" * 64,
        "population_sha256": "7" * 64,
        "storage": {
            "root_id": "8" * 32,
            "volume_uuid": "00000000-0000-0000-0000-000000000001",
            "device": 101,
            "inode": 202,
        },
        "predecessor_root": handoff.OLD_ROOT,
        "predecessor_file_prefix_sha256": predecessor_prefix_sha256,
        "predecessor_chain_tail_sha256": "9" * 64,
        "predecessor_attempt_tail_sha256": "a" * 64,
        "predecessor_snapshot_sha256": predecessor_snapshot_sha256,
        "preinit_census_sha256": census_sha256,
        "preinit_snapshot_set_sha256": snapshot_set_sha256,
        "coordinator_prefix_sha256": "b" * 64,
        "coordinator_usage_sha256": "c" * 64,
        "clock_proof_sha256": "d" * 64,
        "old_agent_quiescence_sha256": "e" * 64,
        "old_agent_absent": True,
        "provider_io": False,
        "cursor_in": 6,
        "cursor_out": 12,
    }


def _partial_fields() -> dict[str, Any]:
    return {
        "bucket_close_ns": handoff.OLD_PARTIAL_CLOSE_NS,
        "cursor_in": 6,
        "cursor_out": 12,
        "prior_plan_cursor_out": 12,
        "selected_slots": [
            {"slot_index": 6 + index, "pool_id": pool_id} for index, pool_id in enumerate(POOL_IDS)
        ],
        "attempts": [
            {
                key: row[key]
                for key in (
                    "slot_index",
                    "pool_id",
                    "request_id",
                    "terminal_sha256",
                    "coordinator_reserved_event_sha256",
                    "coordinator_terminal_event_sha256",
                    "coordinator_charge_count",
                    "outcome",
                )
            }
            for row in _attempt_rows()
        ],
        "expired_slots": [
            {
                "slot_index": 10 + index,
                "pool_id": POOL_IDS[4 + index],
                "intent_present": False,
                "dispatch_present": False,
                "provider_io": False,
                "charged_attempts": 0,
                "disposition": "expired_without_intent_or_dispatch",
            }
            for index in range(2)
        ],
    }


def _gap_fields(close_ns: int = handoff.UNPLANNED_GAP_CLOSE_NS) -> dict[str, Any]:
    return {
        "bucket_close_ns": close_ns,
        "next_close_ns": close_ns + 900_000_000_000,
        "potential_opportunities": 6,
        "selected_pool_ids": None,
        "selection_status": "unobserved_no_plan",
        "plan_present": False,
        "intent_count": 0,
        "dispatch_count": 0,
        "provider_io": False,
        "cursor_in": 12,
        "cursor_out": 12,
        "retention_changed": False,
    }


def _journal(genesis: dict[str, Any] | None = None) -> bytes:
    return handoff.make_journal(genesis or _expected_genesis(), _partial_fields(), _gap_fields())


def _storage(writer: DescriptorWriter) -> dict[str, Any]:
    return {
        "root_id": "8" * 32,
        "volume_uuid": "00000000-0000-0000-0000-000000000001",
        "device": writer.device,
        "inode": os.fstat(writer.root_fd).st_ino,
    }


def _genesis_for_writer(writer: DescriptorWriter, **kwargs: str) -> dict[str, Any]:
    genesis = _expected_genesis(**kwargs)
    genesis["storage"] = _storage(writer)
    return genesis


def test_exact_four_plus_two_then_unplanned_gap_is_verified_without_dispatch_authority() -> None:
    replay = handoff.verify_journal(
        _journal(),
        successor_root=NEW_ROOT,
        expected_genesis=_expected_genesis(),
        predecessor_attempts=_attempt_rows(),
        predecessor_slots=_slots(),
        observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
    )
    assert replay["status"] == "verified_handoff"
    assert replay["attempt_count"] == 4
    assert replay["received_count"] == 3
    assert replay["ambiguous_spent_count"] == 1
    assert replay["expired_without_intent_count"] == 2
    assert replay["unplanned_gap_opportunities"] == 6
    assert replay["cursor_out"] == 12
    assert replay["dispatch_authorized"] is False
    assert replay["retry_authorized"] is False
    assert replay == handoff.verify_journal(
        _journal(),
        successor_root=NEW_ROOT,
        expected_genesis=_expected_genesis(),
        predecessor_attempts=_attempt_rows(),
        predecessor_slots=_slots(),
        observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("cursor", "handoff_partial_cursor"),
        ("pool_duplicate", "handoff_selected_slots_invalid"),
        ("attempt_status", "handoff_attempt_row_invalid"),
        ("expired_dispatch", "handoff_expired_slot_has_old_artifact"),
        ("gap_plan", "handoff_unplanned_gap_invalid"),
        ("genesis_source", "handoff_genesis_binding_mismatch"),
    ],
)
def test_handoff_mutations_fail_closed(mutation: str, reason: str) -> None:
    genesis = _expected_genesis()
    partial = _partial_fields()
    gap = _gap_fields()
    if mutation == "cursor":
        partial["cursor_out"] = 11
    elif mutation == "pool_duplicate":
        partial["selected_slots"][1]["pool_id"] = POOL_IDS[0]
    elif mutation == "attempt_status":
        partial["attempts"][3]["outcome"] = "received"
    elif mutation == "expired_dispatch":
        _slots()[4]["dispatch_present"] = True
        slots = _slots()
        slots[4]["dispatch_present"] = True
    elif mutation == "gap_plan":
        gap["plan_present"] = True
    elif mutation == "genesis_source":
        genesis["source_sha256"] = "f" * 64
    raw = handoff.make_journal(genesis, partial, gap)
    slots = _slots()
    if mutation == "expired_dispatch":
        slots[4]["dispatch_present"] = True
    with pytest.raises(handoff.HandoffError, match=reason):
        handoff.verify_journal(
            raw,
            successor_root=NEW_ROOT,
            expected_genesis=_expected_genesis(),
            predecessor_attempts=_attempt_rows(),
            predecessor_slots=slots,
            observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
        )


def test_journal_chain_tamper_and_duplicate_json_keys_rejected() -> None:
    raw = _journal()
    changed = raw.replace(b'"cursor_out":12', b'"cursor_out":13', 1)
    with pytest.raises(handoff.HandoffError):
        handoff.verify_journal(
            changed,
            successor_root=NEW_ROOT,
            expected_genesis=_expected_genesis(),
            predecessor_attempts=_attempt_rows(),
            predecessor_slots=_slots(),
            observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
        )


def test_elapsed_gap_chain_requires_each_close_to_be_observed() -> None:
    raw = handoff.make_journal(
        _expected_genesis(),
        _partial_fields(),
        [
            _gap_fields(handoff.UNPLANNED_GAP_CLOSE_NS),
            _gap_fields(handoff.UNPLANNED_GAP_CLOSE_NS + 900_000_000_000),
        ],
    )
    with pytest.raises(handoff.HandoffError, match="handoff_future_gap_record"):
        handoff.verify_journal(
            raw,
            successor_root=NEW_ROOT,
            expected_genesis=_expected_genesis(),
            predecessor_attempts=_attempt_rows(),
            predecessor_slots=_slots(),
            observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS + 899_000_000_000,
        )
    result = handoff.verify_journal(
        raw,
        successor_root=NEW_ROOT,
        expected_genesis=_expected_genesis(),
        predecessor_attempts=_attempt_rows(),
        predecessor_slots=_slots(),
        observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS + 900_000_000_000,
    )
    assert result["unplanned_gap_count"] == 2
    assert result["unplanned_gap_opportunities"] == 12
    assert result["next_unaccounted_close_ns"] == handoff.UNPLANNED_GAP_CLOSE_NS + 1_800_000_000_000


def test_elapsed_close_cannot_be_skipped_from_gap_chain() -> None:
    raw = _journal()
    with pytest.raises(handoff.HandoffError, match="handoff_elapsed_gap_coverage"):
        handoff.verify_journal(
            raw,
            successor_root=NEW_ROOT,
            expected_genesis=_expected_genesis(),
            predecessor_attempts=_attempt_rows(),
            predecessor_slots=_slots(),
            observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS + 900_000_000_000,
        )
    duplicate = b'{"a":1,"a":2}\n'
    with pytest.raises(handoff.HandoffError):
        handoff.verify_journal(
            duplicate,
            successor_root=NEW_ROOT,
            expected_genesis=_expected_genesis(),
            predecessor_attempts=_attempt_rows(),
            predecessor_slots=_slots(),
            observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
        )


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


def _seed_root(writer: DescriptorWriter, genesis: dict[str, Any] | None = None) -> None:
    writer.mkdir(handoff.OLD_ROOT)
    writer.create(f"{handoff.OLD_ROOT}/.runner.lock", b"old runner lock\n")
    writer.mkdir(NEW_ROOT)
    writer.create(f"{NEW_ROOT}/.runner.lock", b"runner lock\n")
    writer.create(f"{NEW_ROOT}/binding.json", b"{}\n")
    writer.create(f"{NEW_ROOT}/policy.json", b"{}\n")
    writer.mkdir(f"{NEW_ROOT}/chain")
    writer.create(
        f"{NEW_ROOT}/{handoff.JOURNAL_NAME}", _journal(genesis or _genesis_for_writer(writer))
    )


def test_successor_root_shape_requires_exact_journal_and_safe_entries(
    writer: DescriptorWriter,
) -> None:
    genesis = _genesis_for_writer(writer)
    _seed_root(writer, genesis)
    result = inventory_v4.verify_successor_root_shape(
        writer,
        root=NEW_ROOT,
        expected_genesis=genesis,
        expected_storage=_storage(writer),
        predecessor_attempts=_attempt_rows(),
        predecessor_slots=_slots(),
        observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
    )
    assert result["status"] == "verified_handoff"
    wrong_mount = _storage(writer)
    wrong_mount["device"] += 1
    with pytest.raises(inventory_v4.InventoryV4Error, match="successor_storage_context_mismatch"):
        inventory_v4.verify_successor_root_shape(
            writer,
            root=NEW_ROOT,
            expected_genesis=genesis,
            expected_storage=wrong_mount,
            predecessor_attempts=_attempt_rows(),
            predecessor_slots=_slots(),
            observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
        )
    writer.create(f"{NEW_ROOT}/surprise.json", b"{}\n")
    with pytest.raises(inventory_v4.InventoryV4Error, match="successor_v2_root_shape_incomplete"):
        inventory_v4.verify_successor_root_shape(
            writer,
            root=NEW_ROOT,
            expected_genesis=genesis,
            expected_storage=_storage(writer),
            predecessor_attempts=_attempt_rows(),
            predecessor_slots=_slots(),
            observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
        )


def test_handoff_journal_must_be_owner_only_regular_file(
    writer: DescriptorWriter, tmp_path: Path
) -> None:
    genesis = _genesis_for_writer(writer)
    _seed_root(writer, genesis)
    journal_path = tmp_path / "data" / NEW_ROOT / handoff.JOURNAL_NAME
    os.chmod(journal_path, 0o644)
    with pytest.raises(StorageError, match="external_writer_unsafe_file"):
        inventory_v4.verify_successor_root_shape(
            writer,
            root=NEW_ROOT,
            expected_genesis=genesis,
            expected_storage=_storage(writer),
            predecessor_attempts=_attempt_rows(),
            predecessor_slots=_slots(),
            observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
        )


def test_inventory_v4_overlays_handoff_only_after_full_inventory_and_binds_census(
    writer: DescriptorWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    genesis = _genesis_for_writer(writer)
    _seed_root(writer, genesis)
    predecessor_attempts = _attempts()
    predecessor = shared_usage.make_verified_root_snapshot(
        root=handoff.OLD_ROOT,
        status="verified_prefix",
        root_inventory_sha256="1" * 64,
        source_prefix_sha256="2" * 64,
        attempts=predecessor_attempts,
    )
    handoff_snapshot = shared_usage.make_verified_root_snapshot(
        root=NEW_ROOT,
        status="verified_none",
        root_inventory_sha256="1" * 64,
        source_prefix_sha256="3" * 64,
        attempts=[],
    )
    snapshots = [predecessor, handoff_snapshot]
    manifest = {"root_paths": [handoff.OLD_ROOT, NEW_ROOT]}
    census_hash = "1" * 64
    monkeypatch.setattr(
        inventory_v2,
        "_discover_roots",
        lambda _writer: ([handoff.OLD_ROOT, NEW_ROOT], manifest, census_hash),
    )

    def base_build(proxy: Any) -> dict[str, Any]:
        assert handoff.JOURNAL_NAME not in proxy.list_names(NEW_ROOT)
        return {
            "schema": "base-v2",
            "root_inventory": manifest,
            "root_inventory_sha256": census_hash,
            "snapshots": snapshots,
            "provider_io": False,
        }

    monkeypatch.setattr(inventory_v2, "build_usage_inventory", base_build)
    snapshot_set_sha = hashlib.sha256(
        inventory_v2.canonical_json([row["snapshot_sha256"] for row in snapshots])
    ).hexdigest()
    genesis = _genesis_for_writer(
        writer,
        predecessor_snapshot_sha256=predecessor["snapshot_sha256"],
        predecessor_prefix_sha256=predecessor["source_prefix_sha256"],
        census_sha256=census_hash,
        snapshot_set_sha256=snapshot_set_sha,
    )
    writer.atomic_write(f"{NEW_ROOT}/{handoff.JOURNAL_NAME}", _journal(genesis))
    context_attempts = _attempt_rows()
    for index, row in enumerate(context_attempts):
        row["response_sha256"] = predecessor_attempts[index]["response_sha256"]
        row["response_bytes"] = predecessor_attempts[index]["response_bytes"]
    result = inventory_v4.build_usage_inventory(
        writer,
        required_successor_root=NEW_ROOT,
        expected_handoffs={
            NEW_ROOT: {
                "expected_genesis": genesis,
                "predecessor_attempts": context_attempts,
                "predecessor_slots": _slots(),
                "expected_storage": _storage(writer),
            }
        },
        observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
    )
    assert result["schema"] == inventory_v4.SCHEMA
    assert result["provider_io"] is False
    assert result["dispatch_authorized"] is False
    assert result["handoffs"][NEW_ROOT]["handoff"]["attempt_count"] == 4
    assert result["snapshots"][1]["source_prefix_sha256"] == inventory_v2._root_files_sha256(
        writer, NEW_ROOT
    )
