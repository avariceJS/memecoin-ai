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

import test_brain_senses_gecko_capture_only_v2_handoff_v1 as handoff_fixture  # noqa: E402

from tools import brain_senses_gecko_capture_only_v2_handoff_v1 as handoff  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v1 as chain_v1  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v2 as chain_v2  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v3 as inventory_v3  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

ROOT = handoff_fixture.NEW_ROOT
SOURCE_PIN = hashlib.sha256(b"source-v2").hexdigest()
POPULATION = [
    {
        "pool_id": f"pool-{index:03d}",
        "base_mint": f"mint-{index:03d}",
        "quote_mint": "wsol",
        "rank_sha256": hashlib.sha256(f"{SOURCE_PIN}\0pool-{index:03d}".encode()).hexdigest(),
    }
    for index in range(scout.POOL_COUNT)
]
ORDERED = scout.pool_order(POPULATION, SOURCE_PIN)


def _policy() -> dict[str, Any]:
    core = {
        "schema": scout.SCHEMA,
        "schema_version": 1,
        "policy_id": scout.POLICY_ID,
        "source": {"source_pin": SOURCE_PIN},
        "population_count": scout.POOL_COUNT,
        "population_sha256": scout.population_digest(ORDERED),
        "population": ORDERED,
        "provider_io": False,
        "activation_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return {**core, "policy_sha256": hashlib.sha256(scout.canonical_json(core)).hexdigest()}


def _certificate_inputs(writer: DescriptorWriter) -> dict[str, Any]:
    policy = _policy()
    old_first_close = handoff.OLD_PARTIAL_CLOSE_NS - 2 * scout.BUCKET_NS
    old_genesis = chain_v1.build_genesis(policy, old_first_close)
    old_plan_1 = chain_v1.append_plan(policy, old_genesis, bucket_close_ns=old_first_close)
    old_plan_2 = chain_v1.append_plan(
        policy, old_plan_1, bucket_close_ns=old_first_close + scout.BUCKET_NS
    )
    old_chain_records = [old_genesis, old_plan_1, old_plan_2]
    assert old_plan_2["cursor_out"] == 12
    genesis = handoff_fixture._expected_genesis()
    genesis["storage"] = handoff_fixture._storage(writer)
    genesis["policy_sha256"] = policy["policy_sha256"]
    genesis["population_sha256"] = policy["population_sha256"]
    genesis["source_sha256"] = SOURCE_PIN
    genesis["predecessor_chain_tail_sha256"] = old_plan_2["record_sha256"]
    raw = handoff.make_journal(
        genesis,
        handoff_fixture._partial_fields(),
        handoff_fixture._gap_fields(handoff.UNPLANNED_GAP_CLOSE_NS),
    )
    return {
        "handoff_raw": raw,
        "successor_root": ROOT,
        "expected_handoff_genesis": genesis,
        "predecessor_attempts": handoff_fixture._attempt_rows(),
        "predecessor_slots": handoff_fixture._slots(),
        "predecessor_chain_records": old_chain_records,
        "policy": policy,
        "predecessor_file_prefix_sha256": genesis["predecessor_file_prefix_sha256"],
        "predecessor_last_plan_sha256": old_plan_2["record_sha256"],
        "coordinator_prefix_sha256": genesis["coordinator_prefix_sha256"],
        "coordinator_usage_sha256": genesis["coordinator_usage_sha256"],
        "preinit_census_sha256": genesis["preinit_census_sha256"],
        "preinit_snapshot_set_sha256": genesis["preinit_snapshot_set_sha256"],
        "observed_now_ns": handoff.UNPLANNED_GAP_CLOSE_NS,
    }


def _certificate(writer: DescriptorWriter) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = _certificate_inputs(writer)
    cert = chain_v2.build_cutover_certificate(**inputs)
    return cert, inputs


@pytest.fixture
def writer(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(fd)
    try:
        yield writer
    finally:
        writer.close()
        os.close(fd)


def test_v2_genesis_consumes_cutover_certificate_and_cursor_twelve(
    writer: DescriptorWriter,
) -> None:
    cert, inputs = _certificate(writer)
    chain_v2.verify_cutover_certificate(cert, **inputs)
    genesis = chain_v2.build_genesis(_policy(), cert)
    assert genesis["schema"] == chain_v2.GENESIS_SCHEMA
    assert genesis["first_bucket_close_ns"] == handoff.UNPLANNED_GAP_CLOSE_NS
    assert genesis["next_bucket_close_ns"] == handoff.UNPLANNED_GAP_CLOSE_NS
    assert genesis["cursor"] == 12
    assert genesis["cutover_certificate_sha256"] == cert["certificate_sha256"]
    assert genesis["retention"] == []
    assert (
        chain_v2.verify_chain(_policy(), cert, [genesis], certificate_inputs=inputs)["cursor"] == 12
    )


def test_elapsed_gaps_keep_cursor_twelve_and_first_plan_uses_selector_at_twelve(
    writer: DescriptorWriter,
) -> None:
    cert, inputs = _certificate(writer)
    genesis = chain_v2.build_genesis(_policy(), cert)
    with pytest.raises(chain_v2.ChainV2Error, match="v2_initial_gap_required"):
        chain_v2.append_plan(_policy(), genesis, bucket_close_ns=handoff.UNPLANNED_GAP_CLOSE_NS)
    gap_0445 = chain_v2.append_gap(
        _policy(), genesis, bucket_close_ns=handoff.UNPLANNED_GAP_CLOSE_NS
    )
    gap_0500 = chain_v2.append_gap(
        _policy(), gap_0445, bucket_close_ns=handoff.UNPLANNED_GAP_CLOSE_NS + scout.BUCKET_NS
    )
    plan_0515 = chain_v2.append_plan(
        _policy(),
        gap_0500,
        bucket_close_ns=handoff.UNPLANNED_GAP_CLOSE_NS + 2 * scout.BUCKET_NS,
    )
    expected_selection = scout.plan_bucket(
        ORDERED,
        source_pin=SOURCE_PIN,
        bucket_close_ns=plan_0515["bucket_close_ns"],
        cursor=12,
        prior_retention=[],
        prior_evidence=[],
    )
    assert plan_0515["cursor_in"] == 12
    assert plan_0515["selection"] == expected_selection
    assert plan_0515["prior_evidence"] == []
    assert plan_0515["selection"]["retention"] == []
    replay = chain_v2.verify_chain(
        _policy(), cert, [genesis, gap_0445, gap_0500, plan_0515], certificate_inputs=inputs
    )
    assert replay["cursor"] == plan_0515["cursor_out"]
    assert replay["gap_count"] == 2
    assert replay["provider_io"] is False


def test_cutover_certificate_and_genesis_tampering_fail_closed(writer: DescriptorWriter) -> None:
    cert, inputs = _certificate(writer)
    changed = dict(cert)
    changed["handoff_tail_sha256"] = "f" * 64
    with pytest.raises(chain_v2.ChainV2Error, match="v2_cutover_certificate_mismatch"):
        chain_v2.verify_cutover_certificate(changed, **inputs)
    forged = dict(cert)
    core = dict(forged)
    core.pop("certificate_sha256")
    core["cursor_out"] = 0
    forged = {**core, "certificate_sha256": hashlib.sha256(scout.canonical_json(core)).hexdigest()}
    with pytest.raises(chain_v2.ChainV2Error, match="v2_cutover_policy_or_cursor"):
        chain_v2.build_genesis(_policy(), forged)


def test_v1_chain_remains_cursor_zero_and_does_not_accept_v2_genesis(
    writer: DescriptorWriter,
) -> None:
    cert, _ = _certificate(writer)
    genesis_v2 = chain_v2.build_genesis(_policy(), cert)
    assert genesis_v2["cursor"] == 12
    # The new schema is separate: a v1 verifier cannot silently accept its cursor seed.
    from tools import brain_senses_gecko_scout_chain_v1 as chain_v1

    with pytest.raises(chain_v1.ChainError):
        chain_v1.verify_chain(_policy(), [genesis_v2])


def test_descriptor_journal_initializes_v2_genesis_create_only(writer: DescriptorWriter) -> None:
    cert, inputs = _certificate(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer, root=ROOT, policy=_policy(), certificate=cert, certificate_inputs=inputs
    )
    initialized = journal.initialize()
    assert initialized["status"] == "initialized"
    assert journal.records()[0]["cursor"] == 12
    assert journal.initialize()["genesis_sha256"] == initialized["genesis_sha256"]


def test_descriptor_restart_records_elapsed_gaps_then_plans_from_cursor_twelve(
    writer: DescriptorWriter,
) -> None:
    cert, inputs = _certificate(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer, root=ROOT, policy=_policy(), certificate=cert, certificate_inputs=inputs
    )
    journal.initialize()
    bucket_close = handoff.UNPLANNED_GAP_CLOSE_NS + 2 * scout.BUCKET_NS
    plan = journal.ensure_plan_for_current_bucket(
        now_ns=bucket_close + scout.DISPATCH_EARLIEST_NS,
        bucket_close_ns=bucket_close,
    )
    records = journal.records()
    replay = chain_v2.verify_chain(_policy(), cert, records, certificate_inputs=inputs)
    assert [row["schema"] for row in records] == [
        chain_v2.GENESIS_SCHEMA,
        chain_v2.GAP_SCHEMA,
        chain_v2.GAP_SCHEMA,
        chain_v2.PLAN_SCHEMA,
    ]
    assert plan["cursor_in"] == 12
    assert replay["cursor"] == plan["cursor_out"]
    assert plan["prior_evidence"] == []
    assert plan["selection"]["retention"] == []


def test_plan_creation_window_opens_at_plus_sixty_seconds(writer: DescriptorWriter) -> None:
    cert, inputs = _certificate(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer, root=ROOT, policy=_policy(), certificate=cert, certificate_inputs=inputs
    )
    journal.initialize()
    close = handoff.UNPLANNED_GAP_CLOSE_NS + scout.BUCKET_NS
    with pytest.raises(chain_v2.ChainV2Error, match="v2_window_not_open"):
        journal.ensure_plan_for_current_bucket(
            now_ns=close + scout.DISPATCH_EARLIEST_NS - 1, bucket_close_ns=close
        )
    plan = journal.ensure_plan_for_current_bucket(
        now_ns=close + scout.DISPATCH_EARLIEST_NS, bucket_close_ns=close
    )
    assert plan["schema"] == chain_v2.PLAN_SCHEMA
    assert plan["bucket_close_ns"] == close
    assert [row["schema"] for row in journal.records()] == [
        chain_v2.GENESIS_SCHEMA,
        chain_v2.GAP_SCHEMA,
        chain_v2.PLAN_SCHEMA,
    ]


def test_plan_at_plus_180_seconds_is_gap_not_late_plan(writer: DescriptorWriter) -> None:
    cert, inputs = _certificate(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer, root=ROOT, policy=_policy(), certificate=cert, certificate_inputs=inputs
    )
    journal.initialize()
    close = handoff.UNPLANNED_GAP_CLOSE_NS
    with pytest.raises(chain_v2.ChainV2Error, match="v2_bucket_elapsed"):
        journal.ensure_plan_for_current_bucket(
            now_ns=close + chain_v2.PLAN_WINDOW_END_NS, bucket_close_ns=close
        )
    rows = journal.records()
    assert [row["schema"] for row in rows] == [chain_v2.GENESIS_SCHEMA, chain_v2.GAP_SCHEMA]
    assert rows[-1]["bucket_close_ns"] == close


def test_descriptor_root_replacement_never_writes_to_mountpoint_underlay(
    tmp_path: Path,
) -> None:
    mount = tmp_path / "mount"
    mount.mkdir(mode=0o700)
    fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(fd)
    os.close(fd)
    moved = tmp_path / "detached-root"
    mount.rename(moved)
    mount.mkdir(mode=0o700)
    try:
        cert, inputs = _certificate(writer)
        journal = chain_v2.DescriptorScoutJournalV2(
            writer, root=ROOT, policy=_policy(), certificate=cert, certificate_inputs=inputs
        )
        journal.initialize()
        assert list(mount.iterdir()) == []
        assert (moved / ROOT / "predecessor-cutover.json").exists()
        assert journal.records()[0]["cursor"] == 12
    finally:
        writer.close()


def test_inventory_v3_dispatches_exact_chain_v2_binding_without_v1_cursor_reset(
    writer: DescriptorWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _certificate_inputs(writer)
    cert = chain_v2.build_cutover_certificate(**inputs)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=ROOT,
        policy=_policy(),
        certificate=cert,
        certificate_inputs=inputs,
    )
    journal.initialize()
    writer.mkdir(handoff.OLD_ROOT)
    writer.create(f"{handoff.OLD_ROOT}/.runner.lock", b"old lock\n")
    writer.mkdir("services/gecko-market-v1/capture-only-v2")

    predecessor_attempts = inputs["predecessor_attempts"]
    predecessor = shared_usage.make_verified_root_snapshot(
        root=handoff.OLD_ROOT,
        status="verified_prefix",
        root_inventory_sha256="1" * 64,
        source_prefix_sha256=inputs["expected_handoff_genesis"]["predecessor_file_prefix_sha256"],
        attempts=predecessor_attempts,
    )
    full_manifest = {"root_paths": [handoff.OLD_ROOT, ROOT]}
    filtered_manifest = {"root_paths": [handoff.OLD_ROOT]}
    full_hash = hashlib.sha256(scout.canonical_json(full_manifest)).hexdigest()
    filtered_hash = hashlib.sha256(scout.canonical_json(filtered_manifest)).hexdigest()
    monkeypatch.setattr(
        inventory_v2,
        "_discover_roots",
        lambda _writer: ([handoff.OLD_ROOT, ROOT], full_manifest, full_hash),
    )

    def base_build(proxy: Any) -> dict[str, Any]:
        assert ROOT.rsplit("/", 1)[-1] not in proxy.list_names(
            "services/gecko-market-v1/capture-only-v2"
        )
        return {
            "root_inventory": filtered_manifest,
            "root_inventory_sha256": filtered_hash,
            "snapshots": [predecessor],
            "provider_io": False,
        }

    monkeypatch.setattr(inventory_v2, "build_usage_inventory", base_build)
    result = inventory_v3.build_usage_inventory(
        writer,
        required_successor_root=ROOT,
        expected_handoffs={},
        expected_v2_chains={ROOT: {"certificate_inputs": inputs}},
        observed_now_ns=handoff.UNPLANNED_GAP_CLOSE_NS,
    )
    assert result["root_inventory"] == full_manifest
    assert result["root_inventory_sha256"] == full_hash
    assert result["handoffs"][ROOT]["chain_status"] == "verified_chain_v2"
    assert result["handoffs"][ROOT]["cursor"] == 12
    assert result["snapshots"][-1]["root"] == ROOT
    assert result["snapshots"][-1]["attempts"] == []
    assert result["dispatch_authorized"] is False
