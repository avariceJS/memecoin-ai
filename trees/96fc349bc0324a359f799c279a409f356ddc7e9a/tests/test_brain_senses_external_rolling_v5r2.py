from __future__ import annotations

import os
import plistlib
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import brain_senses_capacity_coordinator_v2 as capacity
from tools import brain_senses_capacity_coordinator_v2_initialize as coordinator_init
from tools import brain_senses_external_orchestrator_v5 as v5_runtime
from tools import brain_senses_external_rolling_policy_v5 as policy
from tools import brain_senses_external_rolling_v5r2 as r2
from tools import brain_senses_external_schema_v5 as schema
from tools import brain_senses_external_verify_v5 as v5_verify
from tools.brain_senses_external_writer_v1 import DescriptorWriter

_TEST_COORDINATOR_POLICY_RAW = schema.canonical({"schema": "test-capacity-policy"})
_TEST_COORDINATOR_POLICY_SHA = schema.sha(_TEST_COORDINATOR_POLICY_RAW)
_TEST_ROOT_ID = "a" * 32
_TEST_VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"


def _activation_cap_bytes() -> bytes:
    return schema.canonical(
        {
            "schema": "brain-senses-external-rolling-v4-allocation-cap",
            "schema_version": 4,
            "created_ns": 1,
            "root_id": _TEST_ROOT_ID,
            "volume_uuid": _TEST_VOLUME_UUID,
            "available_at_activation_bytes": 219_384_123_392,
            "service_allocation_cap_bytes": 150_000_000_000,
        }
    )


def _test_coordinator_ledger() -> bytes:
    genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": _TEST_COORDINATOR_POLICY_SHA},
    )
    return capacity.canonical(genesis)


def _mock_coordinator_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_ledger = _test_coordinator_ledger()
    rows = capacity._verify_ledger(raw_ledger)
    monkeypatch.setattr(
        r2,
        "_ledger_rows",
        lambda _writer, **_kwargs: (
            _TEST_COORDINATOR_POLICY_RAW,
            raw_ledger,
            rows,
            {},
            {},
        ),
    )


def _receipt(sources: dict[str, str] | None = None) -> bytes:
    sources = sources or {"tools/release.py": "a" * 64}
    rows = []
    for index in range(r2.EXPECTED_ATTEMPTS):
        ambiguous = index in {23, 34}
        rows.append(
            {
                "epoch_id": "epoch",
                "request_id": f"request-{index:02d}",
                "status": "possibly_spent" if ambiguous else "captured",
                "request_debit": 1,
                "modeled_credit_debit": 10,
                "response_bytes_debit": policy.MAX_RESPONSE_BYTES if ambiguous else 100 + index,
                "physical_bytes_debit": policy.MAX_RESPONSE_BYTES
                + policy.PER_REQUEST_METADATA_RESERVE_BYTES
                if ambiguous
                else 200 + index,
                "decision_ns": 10 + index,
                "intent_event_ns": 11 + index,
                "dispatch_ns": None if ambiguous else 12 + index,
                "availability_ns": None if ambiguous else 13 + index,
                "outcome_event_ns": 14 + index,
                "intent_event_sha256": "b" * 64,
                "outcome_event_sha256": "c" * 64,
                "response_sha256": None if ambiguous else "d" * 64,
                "reservation_id": f"reservation-{index:02d}",
                "reservation_sha256": "e" * 64,
                "ledger_terminal_kind": "ambiguous_terminal" if ambiguous else "settled",
                "ledger_terminal_sha256": "f" * 64,
            }
        )
    previous = {
        "path": "services/external-rolling-v5/cutover-usage-v1.json",
        "sha256": "1" * 64,
        "requests": 616,
        "modeled_credits": 6160,
        "response_bytes_upper": 12_000,
        "physical_bytes_upper": 13_000,
        "possible_same_account": True,
    }
    row_response = sum(row["response_bytes_debit"] for row in rows)
    row_physical = sum(row["physical_bytes_debit"] for row in rows)
    receipt = {
        "schema": r2.RELEASE_SCHEMA,
        "schema_version": r2.RELEASE_VERSION,
        "release_id": r2.RELEASE_ID,
        "release_root": r2.RELEASE_RELATIVE_ROOT,
        "generated_ns": 1,
        "activation": "none",
        "provider_io": "none",
        "source_bindings": sources,
        "source_bindings_sha256": schema.sha(schema.canonical(sources)),
        "previous_usage": previous,
        "inherited_usage_rows": {
            "legacy_campaigns": [
                {
                    "campaign_id": "synthetic_previous",
                    "dispatch_start_ns": 1,
                    "dispatch_end_ns": 1,
                    "request_debit": 616,
                    "response_bytes_upper": 12_000,
                    "physical_bytes_upper": 13_000,
                }
            ],
            "rolling_v4_request_rows": [],
        },
        "v5_terminal_epochs": [],
        "attempt_count": r2.EXPECTED_ATTEMPTS,
        "captured_count": 33,
        "possibly_spent_count": 2,
        "attempt_rows": rows,
        "control": {
            "path": "control",
            "sha256": "2" * 64,
            "valid_terminal_prefix_sha256": "3" * 64,
            "abandon_event_ns": [1, 2],
            "abandon_event_sha256": ["4" * 64, "5" * 64],
        },
        "invalid_open_exception": {
            "classification": "invalid_open_no_dispatch",
            "epoch_id": r2.INVALID_OPEN_EPOCH,
            "control_event_seq": 4,
            "control_event_sha256": "6" * 64,
            "control_tail_sha256": "6" * 64,
            "plan_sha256": "7" * 64,
            "event_chain_sha256": "8" * 64,
            "intent_count": 0,
            "outcome_count": 0,
            "capture_count": 0,
            "capacity_reservation_count": 0,
            "reason": "invalid_open_after_two_abandons",
        },
        "shared_capacity": {
            "policy_path": capacity.POLICY_PATH,
            "policy_sha256": _TEST_COORDINATOR_POLICY_SHA,
            "ledger_path": capacity.LEDGER_PATH,
            "ledger_sha256": schema.sha(_test_coordinator_ledger()),
            "ledger_event_count": 1,
            "helius_attempt_reservations": 35,
            "invalid_open_reservations": 0,
            "nested_r2_accounting": "once_via_existing_external_rolling_v5_allocation_root",
        },
        "activation_bound_lifetime_cap": {
            "path": r2.ACTIVATION_CAP_PATH,
            "sha256": schema.sha(_activation_cap_bytes()),
            "available_at_activation_bytes": 219_384_123_392,
            "service_allocation_cap_bytes": 150_000_000_000,
        },
        "seed_usage": {
            "requests": 651,
            "modeled_credits": 6510,
            "response_bytes_upper": previous["response_bytes_upper"] + row_response,
            "physical_bytes_upper": 13_000 + 100_000,
            "v5_added_response_bytes_upper": row_response,
            "v5_added_physical_bytes_upper": row_physical,
            "v5_epoch_allocation_upper_bytes": 50_000,
            "invalid_open_allocation_upper_bytes": 1_000,
            "physical_free_bytes": 1_000_000_000_000,
        },
        "lifetime_usage": {
            "requests": 651,
            "modeled_credits": 6510,
            "response_bytes_upper": previous["response_bytes_upper"] + row_response,
            "physical_bytes_upper": previous["physical_bytes_upper"] + row_physical,
            "source": "verified_v4_cutover_receipt_plus_exact_v5_terminal_attempt_rows_once",
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
        },
        "gates": {
            name: True
            for name in (
                "rolling_requests",
                "rolling_credits_after_one_request",
                "rolling_response_after_one_reservation",
                "lifetime_usage_fully_accounted",
                "activation_bound_lifetime_capacity",
                "rolling_physical_with_next_epoch",
                "service_allocation_cap_with_next_epoch",
                "free_space_reserve",
                "two_abandon_recovery_after_first_expires",
                "no_dispatch_before_anchor",
            )
        },
        "earliest_fresh_anchor_ns": 1_791_918_000_000_000_000,
        "earliest_fresh_anchor_utc": "2026-10-13T19:00:00Z",
    }
    return schema.canonical(receipt)


def test_receipt_closes_35_attempts_and_debits_ambiguous_at_maximum() -> None:
    receipt = r2.verify_cutover_receipt(_receipt(), expected_sources={"tools/release.py": "a" * 64})
    assert receipt["attempt_count"] == 35
    assert receipt["captured_count"] == 33
    assert receipt["possibly_spent_count"] == 2
    assert (
        sum(
            row["response_bytes_debit"] == policy.MAX_RESPONSE_BYTES
            for row in receipt["attempt_rows"]
        )
        == 2
    )
    assert receipt["lifetime_usage"]["requests"] == receipt["previous_usage"]["requests"] + 35


def test_receipt_rejects_source_drift_and_altered_invalid_open_exception() -> None:
    raw = _receipt()
    value = schema.decode(raw)
    with pytest.raises(r2.ReleaseError, match="release_source_drift"):
        r2.verify_cutover_receipt(raw, expected_sources={"tools/release.py": "b" * 64})
    value["invalid_open_exception"]["classification"] = "valid_open"
    with pytest.raises(r2.ReleaseError, match="invalid_open_exception_altered"):
        r2.verify_cutover_receipt(
            schema.canonical(value), expected_sources={"tools/release.py": "a" * 64}
        )


def test_lifetime_usage_is_accounted_without_inventing_a_lifetime_cap() -> None:
    value = schema.decode(_receipt())
    extra = 300_000
    value["previous_usage"]["requests"] += extra
    value["previous_usage"]["modeled_credits"] += extra * policy.CONSERVATIVE_CREDITS_PER_REQUEST
    value["inherited_usage_rows"]["legacy_campaigns"][0]["request_debit"] += extra
    value["lifetime_usage"]["requests"] += extra
    value["lifetime_usage"]["modeled_credits"] += extra * policy.CONSERVATIVE_CREDITS_PER_REQUEST
    # The pinned v5 policy defines rolling caps; a cumulative counter is retained
    # for audit without creating a second, unapproved lifetime ceiling.
    receipt = r2.verify_cutover_receipt(
        schema.canonical(value), expected_sources={"tools/release.py": "a" * 64}
    )
    assert receipt["lifetime_usage"]["requests"] > policy.MAX_ROLLING_REQUESTS


def test_only_the_exact_historical_coordinator_hash_transition_is_accepted() -> None:
    current = {
        "tools/brain_senses_capacity_coordinator_v2.py": r2.CURRENT_COORDINATOR_SHA256,
        "other": "1" * 64,
    }
    planned = {
        **current,
        "tools/brain_senses_capacity_coordinator_v2.py": r2.OLD_COORDINATOR_SHA256,
    }
    r2._source_exception(planned, current)
    altered = {**planned, "other": "2" * 64}
    with pytest.raises(r2.ReleaseError, match="historical_source_drift"):
        r2._source_exception(altered, current)
    wrong_current = {**current, "tools/brain_senses_capacity_coordinator_v2.py": "f" * 64}
    with pytest.raises(r2.ReleaseError, match="current_coordinator_source_drift"):
        r2._source_exception(planned, wrong_current)


def test_r2_plan_is_nested_and_old_v5_verifier_rejects_it() -> None:
    sources = {name: "a" * 64 for name in r2.SOURCE_PATHS}
    plan = r2.build_plan(
        created_ns=r2.MIN_R2_ANCHOR_NS - policy.HOUR_NS,
        anchor_ns=r2.MIN_R2_ANCHOR_NS,
        root_id="a" * 32,
        volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        cutover_sha256="b" * 64,
        sources=sources,
    )
    r2.verify_plan(
        plan,
        expected_sources=sources,
        root_id="a" * 32,
        volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        cutover_sha256="b" * 64,
    )
    assert plan["storage"]["relative_root"].startswith(f"{r2.RELEASE_RELATIVE_ROOT}/epochs/")
    with pytest.raises(v5_verify.VerifyError):
        v5_verify.verify_plan(
            schema.canonical(plan),
            expected_sources=sources,
            expected_root_id="a" * 32,
            expected_volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        )
    with pytest.raises(r2.ReleaseError, match="r2_plan_storage_binding_invalid"):
        r2.verify_plan(
            {
                **plan,
                "storage": {
                    **plan["storage"],
                    "relative_root": f"services/external-rolling-v5/epochs/{plan['epoch_id']}",
                },
            },
            expected_sources=sources,
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
            cutover_sha256="b" * 64,
        )


def test_future_anchor_carries_both_abandons_and_cooldown() -> None:
    first = 1_791_828_695_959_217_000
    second = 1_791_839_246_909_538_000
    cooldown = second + policy.HOUR_NS
    earliest = max(first + policy.DAY_NS, cooldown)
    assert policy.next_fresh_anchor_ns(earliest) == 1_791_918_000_000_000_000


def test_old_coordinator_defaults_and_r2_plist_binding_are_preserved() -> None:
    assert v5_runtime.COORDINATOR_SERVICE_ROOTS["helius"] == "services/external-rolling-v5"
    assert v5_runtime.COORDINATOR_ALLOCATION_ROOTS["helius"] == (
        "services/external-rolling-v4",
        "services/external-rolling-v5",
    )
    assert v5_runtime.COORDINATOR_ALLOCATION_ROOTS["gecko"] == ("services/gecko-market-v1",)
    assert coordinator_init.SERVICE_ROOTS == v5_runtime.COORDINATOR_SERVICE_ROOTS
    assert coordinator_init.ALLOCATION_ROOTS == v5_runtime.COORDINATOR_ALLOCATION_ROOTS
    assert coordinator_init.SERVICE_LOCK_PATHS["helius"] == ".external-rolling-v4.lock"
    assert r2.RELEASE_RELATIVE_ROOT.startswith(
        v5_runtime.COORDINATOR_ALLOCATION_ROOTS["helius"][1] + "/releases/"
    )
    assert r2.RELEASE_RELATIVE_ROOT not in v5_runtime.COORDINATOR_ALLOCATION_ROOTS["helius"]
    assert capacity.COORDINATOR_ROOT == "services/senses-capacity-v2"
    plist_path = (
        Path(__file__).resolve().parents[1]
        / "ops/launchd/ai.memecoin.brain.senses.external-rolling-v5r2.plist"
    )
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist["Label"] == "ai.memecoin.brain.senses.external-rolling-v5r2"
    assert plist["RunAtLoad"] is False
    assert plist["KeepAlive"] is False
    assert r2.SOURCE_PATHS[-1] == "ops/launchd/ai.memecoin.brain.senses.external-rolling-v5r2.plist"
    assert r2.source_bindings()[r2.SOURCE_PATHS[-1]] == r2._sha(plist_path.read_bytes())
    assert r2._verify_plist(plist_path)


def _descriptor_writer(path: Path, *, fault_hook=None) -> DescriptorWriter:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        writer = DescriptorWriter(fd, fault_hook=fault_hook)
        writer.mkdir("services/external-rolling-v4")
        writer.create(r2.ACTIVATION_CAP_PATH, _activation_cap_bytes())
        return writer
    finally:
        os.close(fd)


def test_release_manifest_create_only_and_identical_replay(tmp_path: Path) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        assert (
            r2.publish_cutover_release(
                writer,
                receipt_bytes=_receipt(sources),
                root_id=root_id,
                volume_uuid=volume_uuid,
                expected_sources=sources,
            )
            == "created"
        )
        manifest, stored_receipt = r2.verify_release_manifest(
            writer, root_id=root_id, volume_uuid=volume_uuid, expected_sources=sources
        )
        assert stored_receipt == _receipt(sources)
        assert manifest["receipt"]["attempt_count"] == 35
        assert manifest["plist_sha256"] == sources[r2.SOURCE_PATHS[-1]]
        assert (
            r2.publish_cutover_release(
                writer,
                receipt_bytes=_receipt(sources),
                root_id=root_id,
                volume_uuid=volume_uuid,
                expected_sources=sources,
            )
            == "existing_identical"
        )
        conflicting = schema.decode(_receipt(sources))
        conflicting["generated_ns"] = 2
        with pytest.raises(r2.ReleaseError, match="cutover_receipt_create_only_conflict"):
            r2.publish_cutover_release(
                writer,
                receipt_bytes=schema.canonical(conflicting),
                root_id=root_id,
                volume_uuid=volume_uuid,
                expected_sources=sources,
            )


def test_release_manifest_rejects_source_or_storage_drift(tmp_path: Path) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        r2.publish_cutover_release(
            writer,
            receipt_bytes=_receipt(sources),
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
            expected_sources=sources,
        )
        wrong_sources = dict(sources)
        wrong_sources[r2.SOURCE_PATHS[0]] = "f" * 64
        with pytest.raises(r2.ReleaseError, match="release_source_drift"):
            r2.verify_release_manifest(
                writer,
                root_id="a" * 32,
                volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
                expected_sources=wrong_sources,
            )
        with pytest.raises(r2.ReleaseError, match="release_manifest_binding_invalid"):
            r2.verify_release_manifest(
                writer,
                root_id="b" * 32,
                volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
                expected_sources=sources,
            )


def test_release_reopens_and_pins_activation_cap_source(tmp_path: Path) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        r2.publish_cutover_release(
            writer,
            receipt_bytes=_receipt(sources),
            root_id=_TEST_ROOT_ID,
            volume_uuid=_TEST_VOLUME_UUID,
            expected_sources=sources,
        )
        changed = schema.decode(_activation_cap_bytes())
        changed["available_at_activation_bytes"] += 1
        writer.atomic_write(r2.ACTIVATION_CAP_PATH, schema.canonical(changed))
        with pytest.raises(r2.ReleaseError, match="activation_cap_source_changed"):
            r2.verify_release_manifest(
                writer,
                root_id=_TEST_ROOT_ID,
                volume_uuid=_TEST_VOLUME_UUID,
                expected_sources=sources,
            )


def test_release_publication_stays_on_held_descriptor_after_path_replacement(
    tmp_path: Path,
) -> None:
    root = tmp_path / "brain-data"
    held = tmp_path / "held-brain-data"
    moved = False

    def detach_before_create(operation: str) -> None:
        nonlocal moved
        if operation == "create" and not moved:
            os.rename(root, held)
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            moved = True

    writer = _descriptor_writer(root, fault_hook=detach_before_create)
    try:
        sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
        r2.publish_cutover_release(
            writer,
            receipt_bytes=_receipt(sources),
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
            expected_sources=sources,
        )
    finally:
        writer.close()
    assert moved
    assert (held / r2.RECEIPT_RELATIVE_PATH).is_file()
    assert (held / r2.MANIFEST_RELATIVE_PATH).is_file()
    assert list(root.iterdir()) == []
    assert stat.S_IMODE((held / r2.RECEIPT_RELATIVE_PATH).stat().st_mode) == 0o600


def test_r2_nested_epoch_initialization_and_reopen_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        _mock_coordinator_replay(monkeypatch)
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        epoch_id = r2.initialize_bound_epoch(
            writer,
            plan=plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        state = r2.verify_bound_epoch(
            writer,
            epoch_id=epoch_id,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        assert state["status"] == "verified_prefix"
        assert state["next_window_index"] == 0
        assert state["request_states"] == []
        assert (
            r2.initialize_bound_epoch(
                writer,
                plan=plan,
                receipt_bytes=receipt,
                manifest_bytes=manifest,
                expected_sources=sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
            == epoch_id
        )


def test_r2_nested_epoch_rejects_plan_mutation_and_old_root_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        _mock_coordinator_replay(monkeypatch)
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        r2.initialize_bound_epoch(
            writer,
            plan=plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        epoch_root = f"{r2.RELEASE_RELATIVE_ROOT}/epochs/{plan['epoch_id']}"
        raw_plan = writer.read_bytes(f"{epoch_root}/plan.json")
        changed = schema.decode(raw_plan)
        changed["r2_lineage"]["coordinator_prefix"]["ledger_sha256"] = "f" * 64
        writer.atomic_write(f"{epoch_root}/plan.json", schema.canonical(changed))
        with pytest.raises(r2.ReleaseError, match="r2_plan_lineage_invalid"):
            r2.verify_bound_epoch(
                writer,
                epoch_id=plan["epoch_id"],
                receipt_bytes=receipt,
                manifest_bytes=manifest,
                expected_sources=sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )


def test_r2_complete_epoch_closes_control_and_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    _mock_coordinator_replay(monkeypatch)
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        epoch_id = r2.initialize_bound_epoch(
            writer,
            plan=plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        for index, window in enumerate(plan["windows"]):
            r2._append_r2_event(
                writer,
                epoch_id=epoch_id,
                kind="window_missed",
                at_ns=window["end_ns"],
                fields={
                    "index": index,
                    "bucket": window["bucket"],
                    "kind": window["kind"],
                    "start_ns": window["start_ns"],
                    "end_ns": window["end_ns"],
                },
            )
        finalize_args = {
            "epoch_id": epoch_id,
            "at_ns": plan["windows"][-1]["end_ns"] + 1,
            "receipt_bytes": receipt,
            "manifest_bytes": manifest,
            "expected_sources": sources,
            "root_id": root_id,
            "volume_uuid": volume_uuid,
        }
        real_append = writer.append

        def fail_control_once(relative: str, payload: bytes) -> None:
            if relative == f"{r2.RELEASE_RELATIVE_ROOT}/{schema.CONTROL_FILE}":
                raise OSError("synthetic control append interruption")
            real_append(relative, payload)

        writer.append = fail_control_once  # type: ignore[method-assign]
        with pytest.raises(OSError, match="synthetic control append interruption"):
            r2.finalize_bound_epoch(writer, **finalize_args)
        writer.append = real_append  # type: ignore[method-assign]
        result = r2.finalize_bound_epoch(writer, **finalize_args)
        assert result["status"] == "verified_prefix"
        assert result["active_epoch_id"] is None
        terminal = schema.decode(
            writer.read_bytes(
                f"{r2.RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.TERMINAL_FILE}"
            )
        )
        assert terminal["status"] == "complete"
        control = schema.verify_event_chain(
            writer.read_bytes(f"{r2.RELEASE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"),
            schema=schema.CONTROL_SCHEMA,
        )
        assert [event["kind"] for event in control] == ["epoch_open", "epoch_close"]


def test_r2_multi_epoch_rollover_and_control_open_crash_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    _mock_coordinator_replay(monkeypatch)
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        first_plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        first_id = r2.initialize_bound_epoch(
            writer,
            plan=first_plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        unopened_later_plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS + policy.HOUR_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS + policy.DAY_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        with pytest.raises(r2.ReleaseError, match="r2_prior_epoch_not_terminal"):
            r2.initialize_bound_epoch(
                writer,
                plan=unopened_later_plan,
                receipt_bytes=receipt,
                manifest_bytes=manifest,
                expected_sources=sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
        for index, window in enumerate(first_plan["windows"]):
            r2._append_r2_event(
                writer,
                epoch_id=first_id,
                kind="window_missed",
                at_ns=window["end_ns"],
                fields={
                    "index": index,
                    "bucket": window["bucket"],
                    "kind": window["kind"],
                    "start_ns": window["start_ns"],
                    "end_ns": window["end_ns"],
                },
            )
        first_terminal_ns = first_plan["windows"][-1]["end_ns"] + 1
        r2.finalize_bound_epoch(
            writer,
            epoch_id=first_id,
            at_ns=first_terminal_ns,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        second_anchor = policy.next_fresh_anchor_ns(first_plan["windows"][-1]["end_ns"])
        assert second_anchor // policy.DAY_NS > first_plan["anchor_ns"] // policy.DAY_NS
        second_plan = r2.build_bound_plan(
            created_ns=first_terminal_ns + 1,
            anchor_ns=second_anchor,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )

        real_create = writer.create

        def fail_plan_once(relative: str, payload: bytes, **kwargs: Any) -> None:
            if relative.endswith(f"/{schema.PLAN_FILE}"):
                raise OSError("synthetic crash after control open")
            real_create(relative, payload, **kwargs)

        writer.create = fail_plan_once
        with pytest.raises(OSError, match="synthetic crash after control open"):
            r2.initialize_bound_epoch(
                writer,
                plan=second_plan,
                receipt_bytes=receipt,
                manifest_bytes=manifest,
                expected_sources=sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
        writer.create = real_create
        second_id = r2.initialize_bound_epoch(
            writer,
            plan=second_plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        state = r2.verify_bound_epoch(
            writer,
            epoch_id=second_id,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        assert state["epoch_ids"] == [first_id, second_id]
        assert state["active_epoch_id"] == second_id
        assert state["epoch_summaries"][0]["terminal_status"] == "complete"
        assert state["epoch_summaries"][1]["terminal_status"] is None
        assert (
            r2.initialize_bound_epoch(
                writer,
                plan=first_plan,
                receipt_bytes=receipt,
                manifest_bytes=manifest,
                expected_sources=sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
            == first_id
        )
        for index, window in enumerate(second_plan["windows"]):
            r2._append_r2_event(
                writer,
                epoch_id=second_id,
                kind="window_missed",
                at_ns=window["end_ns"],
                fields={
                    "index": index,
                    "bucket": window["bucket"],
                    "kind": window["kind"],
                    "start_ns": window["start_ns"],
                    "end_ns": window["end_ns"],
                },
            )
        second_terminal_ns = second_plan["windows"][-1]["end_ns"] + 1
        close_args = {
            "epoch_id": second_id,
            "at_ns": second_terminal_ns,
            "receipt_bytes": receipt,
            "manifest_bytes": manifest,
            "expected_sources": sources,
            "root_id": root_id,
            "volume_uuid": volume_uuid,
        }
        real_append = writer.append

        def fail_second_close(relative: str, payload: bytes) -> None:
            if relative == f"{r2.RELEASE_RELATIVE_ROOT}/{schema.CONTROL_FILE}":
                raise OSError("synthetic second close interruption")
            real_append(relative, payload)

        writer.append = fail_second_close  # type: ignore[method-assign]
        with pytest.raises(OSError, match="synthetic second close interruption"):
            r2.finalize_bound_epoch(writer, **close_args)
        writer.append = real_append  # type: ignore[method-assign]
        closed_state = r2.finalize_bound_epoch(writer, **close_args)
        assert closed_state["active_epoch_id"] is None
        assert closed_state["terminal_status"] == "complete"
        assert closed_state["epoch_ids"] == [first_id, second_id]
        control = schema.verify_event_chain(
            writer.read_bytes(f"{r2.RELEASE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"),
            schema=schema.CONTROL_SCHEMA,
        )
        assert [event["kind"] for event in control] == [
            "epoch_open",
            "epoch_close",
            "epoch_open",
            "epoch_close",
        ]


def test_r2_control_rejects_unreferenced_nested_epoch_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    _mock_coordinator_replay(monkeypatch)
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        epoch_id = r2.initialize_bound_epoch(
            writer,
            plan=plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        writer.mkdir(f"{r2.RELEASE_RELATIVE_ROOT}/epochs/20261014T190000Z-fedcba987654")
        with pytest.raises(r2.ReleaseError, match="r2_epoch_control_closure_invalid"):
            r2.verify_bound_epoch(
                writer,
                epoch_id=epoch_id,
                receipt_bytes=receipt,
                manifest_bytes=manifest,
                expected_sources=sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )


def test_combined_r2_usage_keeps_ambiguous_reservation_at_full_caps() -> None:
    now = r2.MIN_R2_ANCHOR_NS + 10_000
    fixture = schema.decode(_receipt())
    fixture["lifetime_usage"]["physical_bytes_upper"] = 100
    fixture["activation_bound_lifetime_cap"]["service_allocation_cap_bytes"] = 1_000_000_000
    row = {
        "decision_ns": now,
        "status": "possibly_spent",
        "response_bytes": policy.MAX_RESPONSE_BYTES,
        "allocated_bytes": policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES,
    }
    result = r2._combined_usage(fixture, [row], now_ns=now)
    assert result["requests"] == 1
    assert result["response_bytes"] == policy.MAX_RESPONSE_BYTES
    assert result["allocated_bytes"] == (
        policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
    )

    fixture["activation_bound_lifetime_cap"]["service_allocation_cap_bytes"] = 99
    with pytest.raises(r2.ReleaseError, match="r2_activation_bound_cap_exceeded"):
        r2._combined_usage(fixture, [row], now_ns=now)


def test_r2_budget_pause_evidence_replays_exceeded_then_rolling_headroom(
    tmp_path: Path,
) -> None:
    checked = r2.MIN_R2_ANCHOR_NS
    receipt = {
        "inherited_usage_rows": {
            "legacy_campaigns": [
                {
                    "dispatch_start_ns": checked,
                    "dispatch_end_ns": checked,
                    "request_debit": policy.MAX_ROLLING_REQUESTS + 1,
                    "response_bytes_upper": 1,
                    "physical_bytes_upper": 1,
                }
            ],
            "rolling_v4_request_rows": [],
        },
        "lifetime_usage": {"physical_bytes_upper": 100},
        "activation_bound_lifetime_cap": {"service_allocation_cap_bytes": 1_000_000},
    }
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        exceeded_sha = r2._write_r2_budget_evidence(
            writer,
            receipt=receipt,
            suffix_rows=[],
            epoch_id="20261013T190000Z-abcdef123456",
            reason="rolling_request_cap",
            disposition="exceeded",
            checked_ns=checked,
        )
        assert r2._r2_budget_evidence_map(
            writer, receipt=receipt, suffix_rows=[], now_ns=checked
        ) == {exceeded_sha: "exceeded:rolling_request_cap"}
        headroom_time = checked + policy.ROLLING_WINDOW_NS + 1
        headroom_sha = r2._write_r2_budget_evidence(
            writer,
            receipt=receipt,
            suffix_rows=[],
            epoch_id="20261013T190000Z-abcdef123456",
            reason="rolling_request_cap",
            disposition="headroom",
            checked_ns=headroom_time,
        )
        assert r2._r2_budget_evidence_map(
            writer,
            receipt=receipt,
            suffix_rows=[],
            now_ns=headroom_time,
        ) == {
            exceeded_sha: "exceeded:rolling_request_cap",
            headroom_sha: "headroom:rolling_request_cap",
        }
        evidence_path = r2._r2_budget_evidence_path(headroom_sha)
        changed = schema.decode(writer.read_bytes(evidence_path))
        changed["usage"]["requests"] += 1
        writer.atomic_write(evidence_path, schema.canonical(changed))
        with pytest.raises(r2.ReleaseError, match="r2_budget_evidence_invalid"):
            r2._r2_budget_evidence_map(
                writer,
                receipt=receipt,
                suffix_rows=[],
                now_ns=headroom_time,
            )


def test_r2_budget_pause_resume_requires_headroom_before_new_epoch() -> None:
    first_anchor = r2.MIN_R2_ANCHOR_NS
    first_epoch = schema.epoch_id(first_anchor, "111111111111")
    first_plan = schema.canonical({"epoch_id": first_epoch, "anchor_ns": first_anchor})
    first_plan_sha = r2._sha(first_plan)
    second_anchor = first_anchor + 40 * policy.DAY_NS
    second_epoch = schema.epoch_id(second_anchor, "222222222222")
    second_plan = schema.canonical({"epoch_id": second_epoch, "anchor_ns": second_anchor})
    second_plan_sha = r2._sha(second_plan)
    tail = "a" * 64
    terminal_sha = "b" * 64
    evidence_exceeded = "c" * 64
    evidence_headroom = "d" * 64
    rows = [
        (
            "epoch_open",
            first_anchor - 2 * policy.MINUTE_NS,
            {
                "epoch_id": first_epoch,
                "plan_sha256": first_plan_sha,
                "anchor_ns": first_anchor,
            },
        ),
        (
            "epoch_close",
            first_anchor + policy.DAY_NS,
            {
                "epoch_id": first_epoch,
                "terminal_sha256": terminal_sha,
                "event_tail_sha256": tail,
            },
        ),
        (
            "budget_pause",
            first_anchor + policy.DAY_NS,
            {
                "reason": "rolling_request_cap",
                "evidence_sha256": evidence_exceeded,
            },
        ),
        (
            "budget_resume",
            first_anchor + policy.DAY_NS + policy.ROLLING_WINDOW_NS,
            {
                "reason": "rolling_request_cap",
                "evidence_sha256": evidence_headroom,
            },
        ),
        (
            "epoch_open",
            second_anchor - 2 * policy.MINUTE_NS,
            {
                "epoch_id": second_epoch,
                "plan_sha256": second_plan_sha,
                "anchor_ns": second_anchor,
            },
        ),
    ]
    events = []
    previous = None
    for seq, (kind, at_ns, fields) in enumerate(rows):
        event = schema.event_record(
            schema=schema.CONTROL_SCHEMA,
            seq=seq,
            kind=kind,
            at_ns=at_ns,
            previous_sha256=previous,
            fields=fields,
        )
        events.append(event)
        previous = event["event_sha256"]
    projection = v5_verify.verify_control_chain(
        b"".join(schema.canonical(event) for event in events),
        plans={first_epoch: first_plan, second_epoch: second_plan},
        terminals={
            first_epoch: v5_verify.VerifiedTerminal(
                terminal_sha256=terminal_sha,
                event_tail_sha256=tail,
                status="cap_paused",
                pause_reason="rolling_request_cap",
            )
        },
        budget_evidence={
            evidence_exceeded: "exceeded:rolling_request_cap",
            evidence_headroom: "headroom:rolling_request_cap",
        },
        plan_validator=lambda raw, _epoch: schema.decode(raw),
    )
    assert projection.active_epoch_id == second_epoch
    assert projection.budget_paused is False
    without_resume_rows = rows[:3] + rows[4:]
    without_resume_events = []
    previous = None
    for seq, (kind, at_ns, fields) in enumerate(without_resume_rows):
        event = schema.event_record(
            schema=schema.CONTROL_SCHEMA,
            seq=seq,
            kind=kind,
            at_ns=at_ns,
            previous_sha256=previous,
            fields=fields,
        )
        without_resume_events.append(event)
        previous = event["event_sha256"]
    without_resume = b"".join(schema.canonical(event) for event in without_resume_events)
    with pytest.raises(v5_verify.VerifyError, match="epoch_open_transition"):
        v5_verify.verify_control_chain(
            without_resume,
            plans={first_epoch: first_plan, second_epoch: second_plan},
            terminals={
                first_epoch: v5_verify.VerifiedTerminal(
                    terminal_sha256=terminal_sha,
                    event_tail_sha256=tail,
                    status="cap_paused",
                    pause_reason="rolling_request_cap",
                )
            },
            budget_evidence={evidence_exceeded: "exceeded:rolling_request_cap"},
            plan_validator=lambda raw, _epoch: schema.decode(raw),
        )


def test_r2_pause_resume_api_is_create_only_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    _mock_coordinator_replay(monkeypatch)
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        epoch_id = r2.initialize_bound_epoch(
            writer,
            plan=plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )

        def fake_budget_evidence(
            descriptor: DescriptorWriter,
            *,
            receipt,
            suffix_rows,
            epoch_id,
            reason,
            disposition,
            checked_ns,
        ) -> str:
            del receipt, suffix_rows
            usage = {
                "requests": policy.MAX_ROLLING_REQUESTS + 1 if disposition == "exceeded" else 0,
                "modeled_credits": 0,
                "response_bytes": 0,
                "allocated_bytes": 0,
            }
            payload = schema.canonical(
                {
                    "schema": "brain-senses-external-rolling-v5r2-budget-evidence",
                    "schema_version": 1,
                    "release_id": r2.RELEASE_ID,
                    "epoch_id": epoch_id,
                    "reason": reason,
                    "disposition": disposition,
                    "checked_ns": checked_ns,
                    "usage": usage,
                }
            )
            digest = schema.sha(payload)
            descriptor.mkdir(f"{r2.RELEASE_RELATIVE_ROOT}/budget-evidence")
            evidence_path = r2._r2_budget_evidence_path(digest)
            if not r2._safe_file_exists(descriptor, evidence_path):
                descriptor.create(evidence_path, payload)
            return digest

        monkeypatch.setattr(r2, "_write_r2_budget_evidence", fake_budget_evidence)

        def fake_verify(_writer, *, epoch_id, now_ns=None, **_kwargs):
            del now_ns
            control = schema.verify_event_chain(
                writer.read_bytes(f"{r2.RELEASE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"),
                schema=schema.CONTROL_SCHEMA,
            )
            kinds = [event["kind"] for event in control]
            paused = "budget_pause" in kinds and "budget_resume" not in kinds
            if "budget_resume" in kinds:
                reason = None
            else:
                reason = "rolling_request_cap" if paused else None
            epoch_rows = schema.verify_event_chain(
                writer.read_bytes(
                    f"{r2.RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.EVENT_FILE}"
                ),
                schema=schema.EVENT_SCHEMA,
            )
            terminal = next(
                (
                    event["fields"]["status"]
                    for event in epoch_rows
                    if event["kind"] == "epoch_terminal"
                ),
                None,
            )
            usage = (
                {"requests": 0, "modeled_credits": 0, "response_bytes": 0, "allocated_bytes": 0}
                if paused or "budget_resume" in kinds
                else {
                    "requests": policy.MAX_ROLLING_REQUESTS + 1,
                    "modeled_credits": 0,
                    "response_bytes": 0,
                    "allocated_bytes": 0,
                }
            )
            return {
                "budget_paused": paused,
                "pause_reason": reason,
                "terminal_status": terminal,
                "terminal_pause_reason": "rolling_request_cap"
                if terminal == "cap_paused"
                else None,
                "active_epoch_id": None if terminal else epoch_id,
                "combined_usage": usage,
                "r2_capacity_rows": [],
                "request_states": [],
            }

        monkeypatch.setattr(r2, "verify_bound_epoch", fake_verify)
        real_append = writer.append
        control_path = f"{r2.RELEASE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"
        fail_control_append = True

        def fail_control_once(relative: str, payload: bytes) -> None:
            nonlocal fail_control_append
            if relative == control_path and fail_control_append:
                fail_control_append = False
                raise OSError("synthetic crash after cap terminal publication")
            real_append(relative, payload)

        writer.append = fail_control_once
        with pytest.raises(OSError, match="synthetic crash after cap terminal publication"):
            r2.pause_bound_epoch(
                writer,
                epoch_id=epoch_id,
                at_ns=r2.MIN_R2_ANCHOR_NS + 1,
                receipt_bytes=receipt,
                manifest_bytes=manifest,
                expected_sources=sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
        writer.append = real_append
        paused = r2.pause_bound_epoch(
            writer,
            epoch_id=epoch_id,
            at_ns=r2.MIN_R2_ANCHOR_NS + 1,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        assert paused["budget_paused"] is True
        control_path = f"{r2.RELEASE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"
        paused_count = len(
            schema.verify_event_chain(writer.read_bytes(control_path), schema=schema.CONTROL_SCHEMA)
        )
        assert (
            r2.pause_bound_epoch(
                writer,
                epoch_id=epoch_id,
                at_ns=r2.MIN_R2_ANCHOR_NS + 2,
                receipt_bytes=receipt,
                manifest_bytes=manifest,
                expected_sources=sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )["budget_paused"]
            is True
        )
        assert (
            len(
                schema.verify_event_chain(
                    writer.read_bytes(control_path), schema=schema.CONTROL_SCHEMA
                )
            )
            == paused_count
        )
        resumed = r2.resume_bound_budget(
            writer,
            epoch_id=epoch_id,
            at_ns=r2.MIN_R2_ANCHOR_NS + policy.ROLLING_WINDOW_NS,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        assert resumed["budget_paused"] is False
        control = schema.verify_event_chain(
            writer.read_bytes(control_path), schema=schema.CONTROL_SCHEMA
        )
        assert [event["kind"] for event in control] == [
            "epoch_open",
            "epoch_close",
            "budget_pause",
            "budget_resume",
        ]


def test_r2_resumed_cap_pause_opens_only_a_fresh_new_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    _mock_coordinator_replay(monkeypatch)
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        first_plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        first_id = r2.initialize_bound_epoch(
            writer,
            plan=first_plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        pause_ns = first_plan["windows"][-1]["end_ns"] + 1
        resume_ns = pause_ns + policy.ROLLING_WINDOW_NS + 1
        original_combined = r2._combined_usage

        def usage_by_time(receipt_doc, suffix_rows, *, now_ns, allow_rolling_exceeded=False):
            if now_ns < resume_ns:
                return {
                    "requests": policy.MAX_ROLLING_REQUESTS + 1,
                    "modeled_credits": 0,
                    "response_bytes": 0,
                    "allocated_bytes": 0,
                    "lifetime_physical_bytes": receipt_doc["lifetime_usage"][
                        "physical_bytes_upper"
                    ],
                    "activation_bound_cap_bytes": receipt_doc["activation_bound_lifetime_cap"][
                        "service_allocation_cap_bytes"
                    ],
                }
            return original_combined(
                receipt_doc,
                suffix_rows,
                now_ns=now_ns,
                allow_rolling_exceeded=allow_rolling_exceeded,
            )

        monkeypatch.setattr(r2, "_combined_usage", usage_by_time)
        monkeypatch.setattr(r2.time, "time_ns", lambda: resume_ns)
        paused = r2.pause_bound_epoch(
            writer,
            epoch_id=first_id,
            at_ns=pause_ns,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        assert paused["budget_paused"] is True
        resumed = r2.resume_bound_budget(
            writer,
            epoch_id=first_id,
            at_ns=resume_ns,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        assert resumed["budget_paused"] is False
        second_anchor = policy.next_fresh_anchor_ns(resume_ns)
        second_plan = r2.build_bound_plan(
            created_ns=resume_ns + 1,
            anchor_ns=second_anchor,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        second_id = r2.initialize_bound_epoch(
            writer,
            plan=second_plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        state = r2.verify_bound_epoch(
            writer,
            epoch_id=second_id,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
            now_ns=resume_ns,
        )
        assert state["epoch_ids"] == [first_id, second_id]
        assert state["active_epoch_id"] == second_id
        assert state["budget_paused"] is False
        assert state["epoch_summaries"][0]["terminal_status"] == "cap_paused"
        assert second_plan["anchor_ns"] > first_plan["windows"][-1]["end_ns"]


def test_coordinator_cutover_prefix_allows_valid_gecko_suffix_but_pins_prefix() -> None:
    policy_sha = "1" * 64
    genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": policy_sha},
    )
    reserve_fields = {
        "reservation_id": "gecko-v3:bucket:request-00",
        "service": "gecko",
        "request_id": "request-00",
        "intent_path": "services/gecko-market-v1/intents/request-00.json",
        "outcome_path": "services/gecko-market-v1/outcomes/request-00.json",
        "intent_sha256": "2" * 64,
        "intent_bytes_sha256": "2" * 64,
        "reserved_bytes": 80_000,
        "allocated_before_bytes": 100_000,
    }
    suffix = capacity._event(
        seq=1,
        kind="reserved",
        at_ns=2,
        previous=genesis["event_sha256"],
        fields=reserve_fields,
    )
    prefix_raw = capacity.canonical(genesis)
    current_raw = prefix_raw + capacity.canonical(suffix)
    prefix, later = r2.verify_coordinator_cutover_prefix(
        current_raw,
        policy_sha256=policy_sha,
        cutover_ledger_sha256=r2._sha(prefix_raw),
        cutover_event_count=1,
    )
    assert len(prefix) == 1
    assert [event["kind"] for event in later] == ["reserved"]
    changed_genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": "3" * 64},
    )
    changed_raw = capacity.canonical(changed_genesis) + capacity.canonical(
        capacity._event(
            seq=1,
            kind="reserved",
            at_ns=2,
            previous=changed_genesis["event_sha256"],
            fields=reserve_fields,
        )
    )
    with pytest.raises(r2.ReleaseError, match="coordinator_cutover_prefix_changed"):
        r2.verify_coordinator_cutover_prefix(
            changed_raw,
            policy_sha256=policy_sha,
            cutover_ledger_sha256=r2._sha(prefix_raw),
            cutover_event_count=1,
        )


def test_r2_suffix_links_durable_intent_and_treats_pending_as_spent(tmp_path: Path) -> None:
    epoch = "20261013T190000Z-abcdef123456"
    bucket = "20261013T190000Z"
    request_id = f"{bucket}-00"
    base = f"{r2.RELEASE_RELATIVE_ROOT}/epochs/{epoch}"
    intent_path = f"{base}/intents/{request_id}.json"
    outcome_path = f"{base}/outcomes/{request_id}.json"
    intent_doc = {
        "schema": "brain-senses-external-rolling-v5r2-capacity-intent",
        "schema_version": 1,
        "service": "helius",
        "release_id": r2.RELEASE_ID,
        "epoch_id": epoch,
        "request_id": request_id,
        "bucket": bucket,
        "ordinal": 0,
        "method": "getSlot",
        "params": [{"commitment": "finalized"}],
        "semantic_request_sha256": "2" * 64,
        "wire_request_sha256": "3" * 64,
        "decision_ns": 10,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    intent_bytes = schema.canonical(intent_doc)
    policy_sha = "1" * 64
    genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": policy_sha},
    )
    reservation_id = f"helius-v5r2:{epoch}:{request_id}"
    reserve = capacity._event(
        seq=1,
        kind="reserved",
        at_ns=2,
        previous=genesis["event_sha256"],
        fields={
            "reservation_id": reservation_id,
            "service": "helius",
            "request_id": request_id,
            "intent_path": intent_path,
            "outcome_path": outcome_path,
            "intent_sha256": r2._sha(intent_bytes),
            "intent_bytes_sha256": r2._sha(intent_bytes),
            "reserved_bytes": policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES,
            "allocated_before_bytes": 100_000,
        },
    )
    events = [
        {
            "kind": "request_intent",
            "fields": {
                "request_id": request_id,
                "bucket": bucket,
                "ordinal": 0,
                "method": "getSlot",
                "params": intent_doc["params"],
                "wire_request_sha256": intent_doc["wire_request_sha256"],
                "decision_ns": 10,
            },
        }
    ]
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        writer.create(intent_path, intent_bytes)
        rows = r2.verify_r2_capacity_suffix(
            writer,
            capacity.canonical(genesis) + capacity.canonical(reserve),
            receipt={
                "shared_capacity": {
                    "policy_sha256": policy_sha,
                    "ledger_sha256": r2._sha(capacity.canonical(genesis)),
                    "ledger_event_count": 1,
                }
            },
            verified_request_events=events,
        )
    assert rows == [
        {
            "request_id": request_id,
            "epoch_id": epoch,
            "status": "possibly_spent",
            "decision_ns": 10,
            "response_bytes": policy.MAX_RESPONSE_BYTES,
            "allocated_bytes": policy.MAX_RESPONSE_BYTES
            + policy.PER_REQUEST_METADATA_RESERVE_BYTES,
            "availability_ns": None,
            "reservation_id": reservation_id,
            "reservation_sha256": r2._sha(intent_bytes),
            "terminal_kind": None,
            "terminal_event_sha256": None,
        }
    ]


def test_r2_dispatch_persists_once_and_restart_never_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"

    class FakeCoordinator:
        def __init__(self, writer: DescriptorWriter) -> None:
            self.writer = writer
            self.reservations = 0
            self.settlements = 0

        def reserve_and_persist_intent(self, **kwargs):
            self.reservations += 1
            self.writer.create(kwargs["intent_path"], kwargs["intent_bytes"])
            return SimpleNamespace(**kwargs)

        def settle(self, _reservation, *, outcome_path: str, outcome_bytes: bytes, **_kwargs):
            self.settlements += 1
            self.writer.create(outcome_path, outcome_bytes)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, _method, _params):
            self.calls += 1
            return SimpleNamespace(
                response=b'{"jsonrpc":"2.0","id":1,"result":123}',
                request_started_ns=r2.MIN_R2_ANCHOR_NS + 6,
                received_ns=r2.MIN_R2_ANCHOR_NS + 7,
            )

    with _descriptor_writer(tmp_path / "brain-data") as writer:
        _mock_coordinator_replay(monkeypatch)
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        epoch_id = r2.initialize_bound_epoch(
            writer,
            plan=plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        window = plan["windows"][0]
        r2._append_r2_event(
            writer,
            epoch_id=epoch_id,
            kind="window_reserved",
            at_ns=r2.MIN_R2_ANCHOR_NS + 1,
            fields={
                "index": 0,
                "bucket": window["bucket"],
                "kind": window["kind"],
                "mode": "anchor",
                "request_cap": window["request_cap"],
            },
        )
        coordinator = FakeCoordinator(writer)
        client = FakeClient()
        times = iter((r2.MIN_R2_ANCHOR_NS + 5, r2.MIN_R2_ANCHOR_NS + 8))
        result = r2._dispatch_r2(
            writer,
            epoch_id=epoch_id,
            bucket=window["bucket"],
            ordinal=0,
            method="getSlot",
            params=[{"commitment": "finalized"}],
            now_ns=lambda: next(times),
            client=client,
            coordinator=coordinator,
            lane_lease=object(),
            guard_check=lambda: None,
        )
        assert result["response"]["result"] == 123
        assert client.calls == coordinator.reservations == coordinator.settlements == 1
        monkeypatch.setattr(r2, "verify_r2_capacity_suffix", lambda *_a, **_k: [])
        state = r2.verify_bound_epoch(
            writer,
            epoch_id=epoch_id,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        assert state["status"] == "verified_prefix"
        with pytest.raises(r2.ReleaseError, match="r2_request_ordinal_spent_or_gap"):
            r2._dispatch_r2(
                writer,
                epoch_id=epoch_id,
                bucket=window["bucket"],
                ordinal=0,
                method="getSlot",
                params=[{"commitment": "finalized"}],
                now_ns=lambda: r2.MIN_R2_ANCHOR_NS + 9,
                client=client,
                coordinator=coordinator,
                lane_lease=object(),
                guard_check=lambda: None,
            )
        assert client.calls == coordinator.reservations == coordinator.settlements == 1


def test_run_r2_frame_rechecks_deadline_before_http_and_spends_intent_on_late_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: f"{index:064x}" for index, name in enumerate(r2.SOURCE_PATHS, 1)}
    receipt = _receipt(sources)
    root_id = "a" * 32
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"

    class FakeCoordinator:
        def __init__(self, writer: DescriptorWriter) -> None:
            self.writer = writer
            self.reservations = 0
            self.settlements = 0

        def reserve_and_persist_intent(self, **kwargs):
            self.reservations += 1
            self.writer.create(kwargs["intent_path"], kwargs["intent_bytes"])
            return SimpleNamespace(**kwargs)

        def settle(self, _reservation, *, outcome_path: str, outcome_bytes: bytes, **_kwargs):
            self.settlements += 1
            self.writer.create(outcome_path, outcome_bytes)

    class NeverFetch:
        calls = 0

        def fetch(self, _method, _params):
            self.calls += 1
            raise AssertionError("expired request reached provider")

    _mock_coordinator_replay(monkeypatch)
    monkeypatch.setattr(r2.clock_check, "validate_progress", lambda *_a, **_k: None)
    with _descriptor_writer(tmp_path / "brain-data") as writer:
        r2.publish_cutover_release(
            writer,
            receipt_bytes=receipt,
            root_id=root_id,
            volume_uuid=volume_uuid,
            expected_sources=sources,
        )
        manifest = writer.read_bytes(r2.MANIFEST_RELATIVE_PATH)
        plan = r2.build_bound_plan(
            created_ns=r2.MIN_R2_ANCHOR_NS - 2 * policy.MINUTE_NS,
            anchor_ns=r2.MIN_R2_ANCHOR_NS,
            root_id=root_id,
            volume_uuid=volume_uuid,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            sources=sources,
        )
        epoch_id = r2.initialize_bound_epoch(
            writer,
            plan=plan,
            receipt_bytes=receipt,
            manifest_bytes=manifest,
            expected_sources=sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        window = plan["windows"][0]
        timeline = iter(
            (
                window["start_ns"] + 10,
                window["start_ns"] + 11,
                window["start_ns"] + 12,
                window["end_ns"] + 1,
                window["end_ns"] + 2,
            )
        )
        client = NeverFetch()
        coordinator = FakeCoordinator(writer)
        with pytest.raises(r2.ReleaseError):
            r2.run_r2_frame(
                writer,
                epoch_id=epoch_id,
                window_index=0,
                mode="anchor",
                now_ns=lambda: next(timeline),
                client=client,
                coordinator=coordinator,
                lane_lease=object(),
                guard_check=lambda: None,
                admission_check=lambda _now: None,
                clock_record={"synthetic": True},
            )
        assert client.calls == 0
        assert coordinator.reservations == coordinator.settlements == 1
        epoch_events = schema.verify_event_chain(
            writer.read_bytes(f"{r2.RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.EVENT_FILE}"),
            schema=schema.EVENT_SCHEMA,
        )
        assert [event["kind"] for event in epoch_events][-2:] == [
            "request_intent",
            "request_failed",
        ]
