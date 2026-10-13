from __future__ import annotations

import base64
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO / "src", REPO / "tools", REPO / "tests"):
    sys.path.insert(0, str(entry))

import test_brain_senses_capacity_coordinator_v2 as coordinator_fixture  # noqa: E402
import test_brain_senses_gecko_capture_only_v2_verify as old_verify_fixture  # noqa: E402
import test_brain_senses_gecko_capture_only_v3_verify as verifier_fixture  # noqa: E402

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_capture_host_v2 as host  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_service as service  # noqa: E402
from tools import brain_senses_gecko_capture_only_v3_verify as verify  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v2 as chain  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json  # noqa: E402
from tools.brain_senses_gecko_capture_descriptor_v2 import (  # noqa: E402
    CaptureWriteError,
    DescriptorCaptureWriterV2,
    sha256,
)


def _clock(wall: int, mono: int) -> dict[str, Any]:
    return old_verify_fixture._clock(wall, mono)[0]


def _signed_context(
    policy: dict[str, Any],
    binding: dict[str, Any],
    certificate: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[Any, Any, dict[str, str], dict[str, bytes], dict[str, Any]]:
    sources = {"tools/capture-test.py": sha256(b"source")}
    source_bytes = {"tools/capture-test.py": b"source"}
    clock_record = _clock(plan["bucket_close_ns"] + 60 * scout.NS, 100 * scout.NS)
    observed = {
        "storage": dict(binding["storage"]),
        "plist_sha256": sha256(b"plist"),
        "watchdog_sha256": sha256(b"watchdog"),
        "clock_sha256": sha256(canonical_json(clock_record)),
        "plan_sha256": plan["record_sha256"],
        "cutover_certificate_sha256": certificate["certificate_sha256"],
        "clock_record": clock_record,
        "now_ns": plan["bucket_close_ns"] + 60 * scout.NS,
        "monotonic_ns": 100 * scout.NS,
    }
    release_core = {
        "schema": host.RELEASE_SCHEMA,
        "schema_version": 2,
        "source_bindings": sources,
        "storage": observed["storage"],
        "plist_sha256": observed["plist_sha256"],
        "watchdog_sha256": observed["watchdog_sha256"],
        "clock_sha256": observed["clock_sha256"],
        "plan_sha256": observed["plan_sha256"],
        "cutover_certificate_sha256": observed["cutover_certificate_sha256"],
        "capture_authorized": True,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    release = {**release_core, "release_sha256": sha256(canonical_json(release_core))}
    activation_core = {
        "schema": host.ACTIVATION_SCHEMA,
        "schema_version": 2,
        "release_sha256": release["release_sha256"],
        "source_bindings": sources,
        "storage": observed["storage"],
        "plist_sha256": observed["plist_sha256"],
        "watchdog_sha256": observed["watchdog_sha256"],
        "clock_sha256": observed["clock_sha256"],
        "plan_sha256": observed["plan_sha256"],
        "cutover_certificate_sha256": observed["cutover_certificate_sha256"],
        "dispatch_authorized": True,
    }
    activation = {
        **activation_core,
        "activation_sha256": sha256(canonical_json(activation_core)),
    }
    return release, activation, sources, source_bytes, observed


def _full_replay(
    *,
    writer: DescriptorWriter,
    store: DescriptorCaptureWriterV2,
    journal: Any,
    policy: dict[str, Any],
    certificate: dict[str, Any],
    certificate_inputs: dict[str, Any],
) -> dict[str, Any]:
    return verify.verify_capture_prefix_v3(
        **_replay_arguments(
            writer=writer,
            store=store,
            journal=journal,
            policy=policy,
            certificate=certificate,
            certificate_inputs=certificate_inputs,
        )
    )


def _replay_arguments(
    *,
    writer: DescriptorWriter,
    store: DescriptorCaptureWriterV2,
    journal: Any,
    policy: dict[str, Any],
    certificate: dict[str, Any],
    certificate_inputs: dict[str, Any],
) -> dict[str, Any]:
    def read_dir(suffix: str) -> list[dict[str, Any]]:
        try:
            names = writer.list_names(store.path(suffix))
        except FileNotFoundError:
            return []
        return [store.read_json(f"{suffix}/{name}") for name in names]

    intents = read_dir("intents")
    terminals = read_dir("results")
    confirmations = read_dir("confirmations")
    dispatch_path = store.path("dispatch.jsonl")
    dispatches = store.read_dispatches() if writer.exists(dispatch_path) else []
    raw = {
        row["request_id"]: base64.b64decode(row["raw_body_b64"])
        for row in terminals
        if row["raw_body_b64"] is not None
    }
    _, coordinator_events = store.coordinator._load_ledger()
    return {
        "root": store.root,
        "binding": journal.binding,
        "policy": policy,
        "chain_records": journal.records(),
        "intents": intents,
        "dispatch_records": dispatches,
        "terminals": terminals,
        "raw_responses": raw,
        "confirmations": confirmations,
        "coordinator_events": coordinator_events,
        "skips": [],
        "certificate": certificate,
        "certificate_inputs": certificate_inputs,
    }


def _new_transaction(tmp_path: Path):
    fixture = coordinator_fixture.Fixture(tmp_path / "storage-root")
    policy, certificate, certificate_inputs, chain_records = verifier_fixture._verified_plan(
        fixture.writer
    )
    journal = chain.DescriptorScoutJournalV2(
        fixture.writer,
        root=verifier_fixture.chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=certificate_inputs,
    )
    plan = chain_records[-1]
    # Keep coordinator event times in this synthetic plan's future window.
    fixture.counter = plan["bucket_close_ns"] + 59 * scout.NS
    _, initial_events = fixture.coordinator._load_ledger()
    genesis_fields = dict(initial_events[0]["fields"])
    genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=plan["bucket_close_ns"] + 59 * scout.NS,
        previous=None,
        fields=genesis_fields,
    )
    fixture.writer.atomic_write(
        capacity.LEDGER_PATH, capacity.canonical(genesis), create_only=False
    )
    binding = journal.binding
    store = DescriptorCaptureWriterV2(
        fixture.writer,
        root=verifier_fixture.chain_fixture.ROOT,
        coordinator=fixture.coordinator,
        expected_storage=binding["storage"],
    )
    release_context = _signed_context(policy, binding, certificate, plan)
    return fixture, policy, certificate, certificate_inputs, journal, plan, store, release_context


def _execute_successful(
    fixture: Any,
    policy: dict[str, Any],
    certificate: dict[str, Any],
    cert_inputs: dict[str, Any],
    journal: Any,
    plan: dict[str, Any],
    store: DescriptorCaptureWriterV2,
    release_context: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], Callable[[], dict[str, Any]]]:
    close = plan["bucket_close_ns"]
    samples = iter(
        [
            (close + 60 * scout.NS, 100 * scout.NS, _clock(close + 60 * scout.NS, 100 * scout.NS)),
            (close + 60 * scout.NS, 100 * scout.NS, _clock(close + 60 * scout.NS, 100 * scout.NS)),
            (close + 61 * scout.NS, 101 * scout.NS, _clock(close + 61 * scout.NS, 101 * scout.NS)),
            (close + 64 * scout.NS, 104 * scout.NS, _clock(close + 64 * scout.NS, 104 * scout.NS)),
        ]
    )
    body = b'{"data":{"attributes":{"ohlcv_list":[]}}}'
    monkeypatch.setattr(
        host,
        "fetch_with_hard_deadline",
        lambda fetcher, request, timeout_seconds, latest_start_ns=None: host.FetchObservation(
            service.FetchResult(200, body, {}),
            close + 62 * scout.NS,
            102 * scout.NS,
            close + 63 * scout.NS,
            103 * scout.NS,
            False,
        ),
    )

    def replay_args() -> dict[str, Any]:
        return _replay_arguments(
            writer=fixture.writer,
            store=store,
            journal=journal,
            policy=policy,
            certificate=certificate,
            certificate_inputs=cert_inputs,
        )

    result = host.execute_one_slot(
        store=store,
        plan_record=plan,
        slot_index=0,
        policy=policy,
        binding=journal.binding,
        certificate=certificate,
        certificate_inputs=cert_inputs,
        replay_arguments=replay_args,
        admission_factory=old_verify_fixture._admission,
        clock_sample=lambda: next(samples),
        fetch_once=lambda request: service.FetchResult(200, body, {}),
        release_context=release_context,
    )
    return result, replay_args


def test_successful_one_slot_writes_intent_dispatch_terminal_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, policy, certificate, cert_inputs, journal, plan, store, release_context = (
        _new_transaction(tmp_path)
    )
    result, _ = _execute_successful(
        fixture,
        policy,
        certificate,
        cert_inputs,
        journal,
        plan,
        store,
        release_context,
        monkeypatch,
    )
    close = plan["bucket_close_ns"]
    assert result["status"] == "empty_response"
    assert result["provider_io"] is True
    assert result["decision_eligible_ns"] > close + 63 * scout.NS
    replayed = _full_replay(
        writer=fixture.writer,
        store=store,
        journal=journal,
        policy=policy,
        certificate=certificate,
        certificate_inputs=cert_inputs,
    )
    assert replayed["intent_count"] == replayed["dispatch_count"] == 1
    assert replayed["terminal_count"] == replayed["confirmation_count"] == 1
    assert replayed["unresolved_count"] == 0
    fixture.close()


def test_release_and_source_mismatch_fail_before_intent_or_fetch(tmp_path: Path) -> None:
    fixture, policy, certificate, cert_inputs, journal, plan, store, release_context = (
        _new_transaction(tmp_path)
    )
    release, activation, sources, source_bytes, observed = release_context
    changed_bytes = {"tools/capture-test.py": b"changed"}
    before_files = fixture.writer.list_files()
    called = False

    def fetch(_request: Any) -> service.FetchResult:
        nonlocal called
        called = True
        return service.FetchResult(200, b"{}", {})

    close = plan["bucket_close_ns"]
    result = host.execute_one_slot(
        store=store,
        plan_record=plan,
        slot_index=0,
        policy=policy,
        binding=journal.binding,
        certificate=certificate,
        certificate_inputs=cert_inputs,
        replay_arguments=lambda: {},
        admission_factory=old_verify_fixture._admission,
        clock_sample=lambda: (close + 60 * scout.NS, 100 * scout.NS, observed["clock_record"]),
        fetch_once=fetch,
        release_context=(release, activation, sources, changed_bytes, observed),
    )
    assert result["status"] == "failed_closed"
    assert called is False
    assert fixture.writer.list_files() == before_files
    fixture.close()


def test_restart_recovers_confirmation_without_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, policy, certificate, cert_inputs, journal, plan, store, release_context = (
        _new_transaction(tmp_path)
    )
    result, replay_args = _execute_successful(
        fixture,
        policy,
        certificate,
        cert_inputs,
        journal,
        plan,
        store,
        release_context,
        monkeypatch,
    )
    assert result["provider_io"] is True
    request_id = result["request_id"]
    store.writer.unlink(store.path(f"confirmations/{request_id}.json"))
    close = plan["bucket_close_ns"]
    samples = iter(
        [
            (close + 64 * scout.NS, 104 * scout.NS, _clock(close + 64 * scout.NS, 104 * scout.NS)),
            (close + 65 * scout.NS, 105 * scout.NS, _clock(close + 65 * scout.NS, 105 * scout.NS)),
        ]
    )
    recovered = host.recover_slot_without_provider(
        store=store,
        request_id=request_id,
        replay_arguments=replay_args,
        clock_sample=lambda: next(samples),
    )
    assert recovered["status"] == "recovered_confirmation"
    assert recovered["provider_io"] is False
    assert recovered["replay_forbidden"] is True
    assert (
        _full_replay(
            writer=fixture.writer,
            store=store,
            journal=journal,
            policy=policy,
            certificate=certificate,
            certificate_inputs=cert_inputs,
        )["unresolved_count"]
        == 0
    )
    fixture.close()


def test_intent_only_restart_is_spent_and_never_calls_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, policy, certificate, cert_inputs, journal, plan, store, release_context = (
        _new_transaction(tmp_path)
    )
    close = plan["bucket_close_ns"]
    decision_ns, decision_mono = close + 60 * scout.NS, 100 * scout.NS
    request = verify.expected_request(policy, journal.binding["binding_sha256"], plan, 0)
    intent = verify.build_intent(
        root=store.root,
        policy=policy,
        binding=journal.binding,
        plan_record=plan,
        slot_index=0,
        decision_ns=decision_ns,
        decision_monotonic_ns=decision_mono,
        clock_record=_clock(decision_ns, decision_mono),
        admission=old_verify_fixture._admission(request["request_id"], decision_ns),
    )
    with store.locked() as lease:
        store.create_intent_and_reserve(
            intent=intent,
            reserve_bytes=verify.MAX_RESPONSE_BYTES,
            lane_lease=lease,
        )

    def replay_args() -> dict[str, Any]:
        return _replay_arguments(
            writer=fixture.writer,
            store=store,
            journal=journal,
            policy=policy,
            certificate=certificate,
            certificate_inputs=cert_inputs,
        )

    def forbidden_transport(_request: Any) -> service.FetchResult:
        raise AssertionError("spent intent must never replay")

    result = host.execute_one_slot(
        store=store,
        plan_record=plan,
        slot_index=0,
        policy=policy,
        binding=journal.binding,
        certificate=certificate,
        certificate_inputs=cert_inputs,
        replay_arguments=replay_args,
        admission_factory=old_verify_fixture._admission,
        clock_sample=lambda: (
            close + 61 * scout.NS,
            101 * scout.NS,
            _clock(close + 61 * scout.NS, 101 * scout.NS),
        ),
        fetch_once=forbidden_transport,
        release_context=release_context,
    )
    assert result["status"] == "failed_closed"
    assert result["provider_io"] is False
    assert result["provider_io_possible"] is False
    recovered = host.recover_slot_without_provider(
        store=store,
        request_id=intent["request_id"],
        replay_arguments=replay_args,
        clock_sample=lambda: (
            close + 62 * scout.NS,
            102 * scout.NS,
            _clock(close + 62 * scout.NS, 102 * scout.NS),
        ),
    )
    assert recovered["status"] == "spent_unresolved"
    assert recovered["provider_io"] is False
    assert store.read_dispatches() == []
    fixture.close()


def test_detach_during_dispatch_write_never_falls_back_or_fetches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, policy, certificate, cert_inputs, journal, plan, _old_store, release_context = (
        _new_transaction(tmp_path)
    )
    mount = fixture.path
    detached = tmp_path / "detached-storage"
    root_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    original = os.fstat(root_fd)
    detached_once = False

    def fault_hook(operation: str) -> None:
        nonlocal detached_once
        if operation == "append_write" and not detached_once:
            detached_once = True
            os.rename(mount, detached)
            mount.mkdir(mode=0o700)
            sentinel = mount / "underlay-sentinel"
            sentinel.write_bytes(b"unchanged")
            sentinel.chmod(0o600)

    def revalidate() -> None:
        if not mount.exists() or (mount.stat().st_dev, mount.stat().st_ino) != (
            original.st_dev,
            original.st_ino,
        ):
            raise CaptureWriteError("capture_storage_identity_changed")

    guarded_writer = DescriptorWriter(root_fd, fault_hook=fault_hook)
    store = DescriptorCaptureWriterV2(
        guarded_writer,
        root=verifier_fixture.chain_fixture.ROOT,
        coordinator=fixture.coordinator,
        expected_storage=journal.binding["storage"],
        revalidate_storage=revalidate,
    )
    close = plan["bucket_close_ns"]
    samples = iter(
        [
            (close + 60 * scout.NS, 100 * scout.NS, _clock(close + 60 * scout.NS, 100 * scout.NS)),
            (close + 60 * scout.NS, 100 * scout.NS, _clock(close + 60 * scout.NS, 100 * scout.NS)),
            (close + 61 * scout.NS, 101 * scout.NS, _clock(close + 61 * scout.NS, 101 * scout.NS)),
            (close + 62 * scout.NS, 102 * scout.NS, _clock(close + 62 * scout.NS, 102 * scout.NS)),
        ]
    )
    called = False

    def fetch(_request: Any) -> service.FetchResult:
        nonlocal called
        called = True
        return service.FetchResult(200, b"{}", {})

    def replay_args() -> dict[str, Any]:
        _, events = fixture.coordinator._load_ledger()
        return {
            "root": store.root,
            "binding": journal.binding,
            "policy": policy,
            "chain_records": journal.records(),
            "intents": [],
            "dispatch_records": [],
            "terminals": [],
            "raw_responses": {},
            "confirmations": [],
            "coordinator_events": events,
            "skips": [],
            "certificate": certificate,
            "certificate_inputs": cert_inputs,
        }

    result = host.execute_one_slot(
        store=store,
        plan_record=plan,
        slot_index=0,
        policy=policy,
        binding=journal.binding,
        certificate=certificate,
        certificate_inputs=cert_inputs,
        replay_arguments=replay_args,
        admission_factory=old_verify_fixture._admission,
        clock_sample=lambda: next(samples),
        fetch_once=fetch,
        release_context=release_context,
    )
    assert detached_once
    assert result["status"] == "failed_closed"
    assert result["provider_io"] is False
    assert called is False
    assert (mount / "underlay-sentinel").read_bytes() == b"unchanged"
    assert list(mount.iterdir()) == [mount / "underlay-sentinel"]
    assert (detached / "services").is_dir()
    guarded_writer.close()
    os.close(root_fd)
    fixture.close()


def test_hard_deadline_kills_transport_child_without_survivor() -> None:
    def slow_fetch(_request: Any) -> service.FetchResult:
        time.sleep(2)
        return service.FetchResult(200, b"{}", {})

    observed = host.fetch_with_hard_deadline(slow_fetch, {}, timeout_seconds=0.05)
    assert observed.timed_out is True
    assert observed.fetched.transport_error_class == "TimeoutError"
    assert observed.fetched.body is None


def test_writer_rejects_wrong_descriptor_and_path_escape(tmp_path: Path) -> None:
    fixture = coordinator_fixture.Fixture(tmp_path / "storage-root")
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    other_fd = os.open(other, os.O_RDONLY | os.O_DIRECTORY)
    wrong_writer = DescriptorWriter(other_fd)
    with pytest.raises(CaptureWriteError, match="descriptor_mismatch"):
        DescriptorCaptureWriterV2(
            wrong_writer,
            root=verifier_fixture.chain_fixture.ROOT,
            coordinator=fixture.coordinator,
        )
    store = DescriptorCaptureWriterV2(
        fixture.writer,
        root=verifier_fixture.chain_fixture.ROOT,
        coordinator=fixture.coordinator,
    )
    with pytest.raises(CaptureWriteError, match="relative_path"):
        store.path("../outside")
    wrong_writer.close()
    os.close(other_fd)
    fixture.close()
