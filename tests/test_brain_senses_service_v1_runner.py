from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_ai.domain import canonical
from tools import brain_senses_service_v1 as service
from tools import brain_senses_service_v1_runner as runner

_DEFAULT_WRITER_INVENTORY = runner._default_writer_inventory


def _ns(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000_000_000)


def _predecessor(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    stop_path = root / "STOP"
    receipt_path = root / "terminal-receipt.json"
    stop = {
        "campaign_id": "helius-inventory-v3-20261010-4",
        "reason": "finite_attempt_cap_reached",
        "attempt_count": 24,
        "active_reservation_status": "reconciled",
        "active_bucket": "20261010T230000Z",
        "accounting_error_class": None,
        "epoch_summary_sha256": "a" * 64,
        "frame_reservation_sha256": "b" * 64,
        "observed_anchor_slot": 99,
        "observed_frontier": 100,
        "observed_anchor_receipt_ns": _ns("2026-10-09T23:00:00Z"),
        "last_inventoried_interval": None,
        "gap_intervals": [],
    }
    stop_path.write_text(canonical(stop) + "\n")
    receipt = {
        "campaign_id": stop["campaign_id"],
        "status": "normal_finite_terminal",
        "attempt_count": 24,
        "active_reservation_status": "reconciled",
        "accounting_error_count": 0,
        "stop_sha256": service._sha(stop_path.read_bytes()),
        "post_stop_rpc_requests": 0,
    }
    receipt_path.write_text(canonical(receipt) + "\n")
    return stop_path, receipt_path


def _gate(path: Path, stop: Path, receipt: Path, **changes: object) -> None:
    value = {
        "schema_version": 1,
        "status": "activation_approved_after_review",
        "predecessor_stop_sha256": service._sha(stop.read_bytes()),
        "terminal_receipt_sha256": service._sha(receipt.read_bytes()),
        "post_stop_rpc_requests": 0,
        "measured_tails_qualified": True,
        "account_limit_verified": True,
        "independent_review_complete": True,
        "reviewer_authorized_activation": True,
        "predecessor_campaign_id": runner.PREDECESSOR_CAMPAIGN_ID,
        "predecessor_launchagent_label": runner.PREDECESSOR_LABEL,
        "predecessor_root": str(runner.PREDECESSOR_ROOT),
        **changes,
    }
    path.write_text(canonical(value) + "\n")


class FakeService:
    def __init__(self, *, action: str = "frame", status: str = "policy_ready") -> None:
        self.action = action
        self.service_status = status
        self.reservations: list[int] = []
        self.marked: list[str] = []
        self.sealed: list[str] = []
        self.status_calls = 0

    def read_status(self, _root: Path) -> dict:
        self.status_calls += 1
        return {
            "status": self.service_status,
            "source_bindings_match": True,
            "reconciliation_errors": [],
            "usage": {"outstanding": 0},
            "writer_pending_reconciliation": False,
        }

    def reserve_bucket(self, _root: Path, *, bucket_ns: int, request_time_ns: int) -> dict:
        if bucket_ns in self.reservations:
            raise ValueError("bucket_identity_already_spent_or_reserved")
        self.reservations.append(bucket_ns)
        return {
            "reservation_id": f"r{len(self.reservations)}",
            "campaign_id": "campaign-1",
            "action": self.action,
        }

    def reserve_bootstrap(self, _root: Path, *, at_ns: int) -> dict:
        self.reservations.append(at_ns - at_ns % runner.BUCKET_NS)
        return {"reservation_id": "bootstrap-1", "action": "initial_bootstrap"}

    def run_reserved_bootstrap(
        self, _root: Path, *, reservation_id: str, at_ns: int, clients: dict
    ) -> dict:
        self.sealed.append(reservation_id)
        return {"reservation_id": reservation_id, "outcome": "bootstrap_complete"}

    def seal_unstarted_frame_reservation(
        self, _root: Path, *, reservation_id: str, at_ns: int
    ) -> dict:
        self.sealed.append(reservation_id)
        return {"reservation_id": reservation_id, "outcome": "unstarted"}

    def _load_ledger(self, _root: Path) -> list[dict]:
        return [{"kind": "reservation", "bucket_ns": bucket} for bucket in self.reservations]

    def mark_epoch_started(self, _root: Path, *, reservation_id: str, at_ns: int) -> None:
        self.marked.append(reservation_id)

    def seal_from_epoch(self, _root: Path, *, reservation_id: str, at_ns: int) -> dict:
        self.sealed.append(reservation_id)
        return {"reservation_id": reservation_id, "outcome": "complete"}


@pytest.fixture(autouse=True)
def isolate_activation_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "PREDECESSOR_ROOT", tmp_path / "predecessor")
    monkeypatch.setattr(runner, "HOST_WRITER_LOCK", tmp_path / "host-writer.lock")
    monkeypatch.setattr(runner, "_default_writer_inventory", lambda: (set(), set()))


def _ready_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = tmp_path / "root"
    plist = tmp_path / "runner.plist"
    plist.write_text("test plist")
    runner._write_binding(root, plist)
    stop, receipt = _predecessor(tmp_path / "predecessor")
    gate = tmp_path / "gate.json"
    _gate(gate, stop, receipt)
    return root, plist, stop, receipt, gate


@pytest.mark.parametrize(
    "minute, expected", [(4, False), (5, True), (10, True), (15, True), (16, False)]
)
def test_tick_window_is_utc_and_bounded(minute: int, expected: bool) -> None:
    assert runner._in_tick_window(datetime(2026, 10, 10, 10, minute, tzinfo=UTC)) is expected


def test_launchd_template_is_path_only_and_opt_in() -> None:
    root = ET.parse(runner.DEFAULT_PLIST).getroot()
    entries = root.find("dict")
    assert entries is not None
    pairs = list(entries)
    document = {pairs[i].text: pairs[i + 1] for i in range(0, len(pairs), 2)}
    args = [node.text for node in document["ProgramArguments"]]
    assert args[0].endswith("/.venv/bin/python")
    assert args[1].endswith("/tools/brain_senses_service_v1_runner.py")
    assert "--scheduled-tick" in args
    assert all("://" not in (value or "") for value in args)
    assert all("API_KEY" not in (value or "") and "RPC_URL" not in (value or "") for value in args)
    assert "EnvironmentVariables" not in document
    assert document["RunAtLoad"].tag == "false"
    assert document["KeepAlive"].tag == "false"
    schedule = document["StartCalendarInterval"]
    schedule_values = list(schedule)
    assert schedule_values[1].text == "10"


def test_runner_binding_is_immutable_and_detects_plist_drift(tmp_path: Path) -> None:
    root = tmp_path / "root"
    plist = tmp_path / "service.plist"
    plist.write_text("one")
    runner._write_binding(root, plist)
    assert runner._verify_binding(root, plist)
    plist.write_text("two")
    assert not runner._verify_binding(root, plist)
    with pytest.raises(ValueError, match="runner_or_plist_binding_changed"):
        runner._write_binding(root, plist)


def test_runner_binding_includes_shared_private_file_reader(tmp_path: Path) -> None:
    root = tmp_path / "root"
    plist = tmp_path / "service.plist"
    plist.write_text("one")
    binding = runner._write_binding(root, plist)
    assert binding["private_config_reader_sha256"] == runner._sha(
        (runner.REPO_ROOT / "src/memecoin_collector/private_config.py").read_bytes()
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://mainnet.helius-rpc.com/?api-key=fixture",
        "https://user@mainnet.helius-rpc.com/?api-key=fixture",
        "https://mainnet.helius-rpc.com:444/?api-key=fixture",
        "https://mainnet.helius-rpc.com/?api-key=fixture#fragment",
        "https://mainnet.helius-rpc.com/?api-key=one&api-key=two",
        "https://evil.example/?api-key=fixture",
    ],
)
def test_private_endpoint_loader_rejects_ambiguous_or_unsafe_url(
    tmp_path: Path, endpoint: str
) -> None:
    config = tmp_path / ".env.local"
    config.write_text(f"MEMECOIN_HELIUS_RPC_URL={endpoint}\n")
    config.chmod(0o600)
    assert runner._load_private_endpoint(config) is None


def test_private_endpoint_loader_accepts_one_private_helius_rpc_url(tmp_path: Path) -> None:
    config = tmp_path / ".env.local"
    config.write_text(
        'MEMECOIN_HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key=fixture-secret"\n'
    )
    config.chmod(0o600)
    endpoint = runner._load_private_endpoint(config)
    assert endpoint is not None and endpoint.endswith("api-key=fixture-secret")


def test_private_endpoint_loader_rejects_duplicate_config_key(tmp_path: Path) -> None:
    config = tmp_path / ".env.local"
    config.write_text(
        "MEMECOIN_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=one\n"
        "MEMECOIN_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=two\n"
    )
    config.chmod(0o600)
    assert runner._load_private_endpoint(config) is None


def test_private_endpoint_loader_rejects_hardlink_alias(tmp_path: Path) -> None:
    config = tmp_path / ".env.local"
    config.write_text("MEMECOIN_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=fixture\n")
    config.chmod(0o600)
    alias = tmp_path / "alias"
    os.link(config, alias)
    assert runner._load_private_endpoint(alias) is None


def test_initialize_pins_runner_and_plist_before_service_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "service"
    plist = tmp_path / "service.plist"
    plist.write_text("pinned")
    stop, receipt = _predecessor(tmp_path / "predecessor")
    observed = []

    def initialize(_root, **kwargs):
        observed.append((root / runner.RUNNER_BINDING).is_file())
        return {"schema_version": 2}

    monkeypatch.setattr(runner.service, "initialize", initialize)
    result = runner.initialize_service(
        root,
        plist_path=plist,
        service_id="service-test",
        authorized_at_ns=_ns("2026-10-10T00:00:00Z"),
        expires_at_ns=_ns("2026-10-11T00:00:00Z"),
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
    )
    assert observed == [True]
    assert result["status"] == "initialized_offline"


def test_late_tick_reconciles_then_returns_without_credentials_or_reservation(
    tmp_path: Path,
) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    calls = []
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 16, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: calls.append("credential") or {"helius-free": object()},
        unloaded_probe=lambda _label: True,
    )
    assert result["status"] == "no_rpc"
    assert result["reason"] == "outside_scheduled_tick_window"
    assert not calls and not fake.reservations


def test_missing_credential_is_no_rpc_before_reservation(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: None,
        unloaded_probe=lambda _label: True,
    )
    assert result == {"status": "no_rpc", "reason": "provider_credential_unavailable"}
    assert fake.status_calls == 1
    assert not fake.reservations


def test_initial_bootstrap_is_one_reserved_anchor_bucket(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService(status="awaiting_bootstrap")
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: {"helius-free": object()},
        unloaded_probe=lambda _label: True,
    )
    assert result["status"] == "bucket_sealed"
    assert result["action"] == "initial_bootstrap"
    assert fake.reservations == [_ns("2026-10-10T10:00:00Z")]
    assert fake.sealed == ["bootstrap-1"]


def test_gate_and_reconciliation_precede_credential_loader(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    sequence = []

    def load():
        sequence.append("credential")
        return {"helius-free": object()}

    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=load,
        unloaded_probe=lambda label: sequence.append(f"unloaded:{label}") or True,
        scheduler=lambda *args, **kwargs: (
            sequence.append("scheduler") or {"status": "frame_complete"}
        ),
    )
    assert result["status"] == "bucket_sealed"
    assert sequence[0] == f"unloaded:{runner.PREDECESSOR_LABEL}"
    assert sequence.index("credential") < sequence.index("scheduler")
    assert fake.reservations == [_ns("2026-10-10T10:00:00Z")]
    assert fake.marked == ["r1"] and fake.sealed == ["r1"]


def test_actual_versioned_c4_label_blocks_gate_when_generic_label_is_absent(
    tmp_path: Path,
) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    probed = []
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: pytest.fail("loaded c4 must block credential loading"),
        unloaded_probe=lambda label: probed.append(label) or False,
        writer_inventory=lambda: (set(), set()),
    )
    assert result == {
        "status": "no_rpc",
        "reason": "predecessor_launchagent_not_proven_unloaded",
    }
    assert probed == [runner.PREDECESSOR_LABEL]
    assert runner.PREDECESSOR_LABEL != "ai.memecoin.brain.senses.helius-inventory-v3"
    assert not fake.reservations


def test_loaded_senses_namespace_writer_blocks_activation(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: pytest.fail("competing writer must block credential loading"),
        unloaded_probe=lambda label: label == runner.PREDECESSOR_LABEL,
        writer_inventory=lambda: ({"ai.memecoin.brain.senses.unknown-writer"}, set()),
    )
    assert result == {
        "status": "no_rpc",
        "reason": "other_senses_writer_present_or_ambiguous",
    }
    assert not fake.reservations


def test_competing_senses_process_blocks_activation(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: pytest.fail("competing process must block credentials"),
        unloaded_probe=lambda label: label == runner.PREDECESSOR_LABEL,
        writer_inventory=lambda: (set(), {123}),
    )
    assert result == {
        "status": "no_rpc",
        "reason": "other_senses_writer_present_or_ambiguous",
    }
    assert not fake.reservations


def test_unknown_writer_inventory_blocks_activation(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()

    def unavailable():
        raise ValueError("inventory command failed")

    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: pytest.fail("unknown inventory must block credentials"),
        unloaded_probe=lambda label: label == runner.PREDECESSOR_LABEL,
        writer_inventory=unavailable,
    )
    assert result == {"status": "no_rpc", "reason": "senses_writer_inventory_unavailable"}
    assert not fake.reservations


def test_mismatched_predecessor_label_binding_fails_closed(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    _gate(
        gate,
        stop,
        receipt,
        predecessor_launchagent_label="ai.memecoin.brain.senses.helius-inventory-v3",
    )
    fake = FakeService()
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: pytest.fail("mismatched label must block credentials"),
        unloaded_probe=lambda _label: True,
        writer_inventory=lambda: (set(), set()),
    )
    assert result == {"status": "no_rpc", "reason": "activation_gate_incomplete"}
    assert not fake.reservations


def test_launchd_probe_error_is_not_treated_as_unloaded() -> None:
    def runner_fn(_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="launchd unavailable")

    assert not runner._default_unloaded_probe(runner.PREDECESSOR_LABEL, runner=runner_fn)


def test_launchd_exact_not_found_response_proves_unloaded() -> None:
    label = runner.PREDECESSOR_LABEL

    def runner_fn(_args, **_kwargs):
        return SimpleNamespace(
            returncode=113,
            stdout="",
            stderr=(
                "Bad request.\n"
                f'Could not find service "{label}" in domain for user gui: {os.getuid()}\n'
            ),
        )

    assert runner._default_unloaded_probe(label, runner=runner_fn)


def test_writer_inventory_censuses_senses_labels_and_processes_without_echoing_args() -> None:
    outputs = [
        SimpleNamespace(
            returncode=0,
            stdout=(
                "PID\tStatus\tLabel\n"
                f"-\t0\t{runner.SERVICE_LABEL}\n"
                f"-\t0\t{runner.PREDECESSOR_LABEL}\n"
                "-\t0\tcom.apple.example\n"
            ),
        ),
        SimpleNamespace(
            returncode=0,
            stdout=(
                f"{os.getpid()} /python/tools/brain_senses_service_v1_runner.py\n"
                "123 /python/tools/brain_senses_old_writer.py --credential=never-echo\n"
                "124 python3 tools/brain_senses_legacy_worker.py --credential=never-echo\n"
            ),
        ),
    ]

    def runner_fn(_args, **_kwargs):
        return outputs.pop(0)

    labels, pids = _DEFAULT_WRITER_INVENTORY(runner=runner_fn)
    assert labels == {runner.SERVICE_LABEL, runner.PREDECESSOR_LABEL}
    assert pids == {123, 124}


def test_host_lock_excludes_a_second_service_root(tmp_path: Path) -> None:
    first = tmp_path / "service-one"
    second = tmp_path / "service-two"
    with runner._writer_ownership(first):
        with pytest.raises(ValueError, match="service_host_writer_already_active"):
            with runner._writer_ownership(second):
                pytest.fail("second service root acquired the shared host lock")


def test_writer_guard_repeats_immediately_before_frame_io(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    probe_calls = []

    def probe(label: str) -> bool:
        probe_calls.append(label)
        return len(probe_calls) == 1

    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: {"helius-free": object()},
        unloaded_probe=probe,
        writer_inventory=lambda: (set(), set()),
    )
    assert result == {
        "status": "no_rpc",
        "reason": "predecessor_launchagent_not_proven_unloaded",
        "reservation_id": "r1",
        "outcome": "unstarted",
    }
    assert probe_calls == [runner.PREDECESSOR_LABEL, runner.PREDECESSOR_LABEL]
    assert fake.reservations == [_ns("2026-10-10T10:00:00Z")]
    assert fake.sealed == ["r1"]


def test_unverified_gate_never_loads_credential_or_reserves(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    _gate(gate, stop, receipt, account_limit_verified=False)
    fake = FakeService()
    credential_calls = []
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: credential_calls.append(True) or {"helius-free": object()},
        unloaded_probe=lambda _label: True,
    )
    assert result == {"status": "no_rpc", "reason": "activation_gate_incomplete"}
    assert not credential_calls and not fake.reservations


def test_duplicate_current_bucket_cannot_be_replayed(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    calls = []
    credential_calls = []
    tick = datetime(2026, 10, 10, 10, 10, tzinfo=UTC)
    kwargs = dict(
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=tick,
        service_api=fake,
        client_loader=lambda: credential_calls.append(True) or {"helius-free": object()},
        unloaded_probe=lambda _label: True,
        scheduler=lambda *args, **kwargs: calls.append("scheduled") or {"status": "frame_complete"},
    )
    runner.run_tick(root, **kwargs)
    second = runner.run_tick(root, **kwargs)
    assert second["reason"] == "bucket_identity_already_spent_or_reserved"
    assert calls == ["scheduled"]
    assert credential_calls == [True]
    assert len(fake.reservations) == 1


def test_pending_reservation_is_reconciled_without_rpc_or_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    monkeypatch.setattr(
        runner,
        "_reconcile_outstanding",
        lambda _root, _ns: {"reservation_id": "old", "outcome": "ambiguous"},
    )
    fake = FakeService()
    fake.read_status = lambda _root: {
        "status": "reconciliation_required",
        "source_bindings_match": True,
        "reconciliation_errors": [],
        "usage": {"outstanding": 1},
        "writer_pending_reconciliation": True,
    }
    credential_calls = []
    result = runner.run_tick(
        root,
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: credential_calls.append(True) or {"helius-free": object()},
    )
    assert result == {
        "status": "reconciled_no_rpc",
        "outcome": "ambiguous",
        "reservation_id": "old",
    }
    assert not credential_calls and not fake.reservations


def test_durable_stop_boots_out_only_the_service_label(tmp_path: Path, monkeypatch) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    (root / "STOP").write_text("{}\n")
    seen = []

    def subprocess_runner(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0)

    assert runner._bootout_exact_label(runner=subprocess_runner)
    assert seen == [["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{runner.SERVICE_LABEL}"]]


def test_tick_boots_out_only_after_durable_stop(tmp_path: Path) -> None:
    root, plist, stop, receipt, gate = _ready_inputs(tmp_path)
    fake = FakeService()
    calls = []
    kwargs = dict(
        plist_path=plist,
        gate_path=gate,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
        now=datetime(2026, 10, 10, 10, 10, tzinfo=UTC),
        service_api=fake,
        client_loader=lambda: None,
        bootout=lambda: calls.append("bootout") or True,
    )
    before = runner.run_tick(root, **kwargs)
    assert before["status"] == "no_rpc"
    assert not calls

    (root / "STOP").write_text("{}\n")
    result = runner.run_tick(root, **kwargs)
    assert result == {"status": "service_stopped", "bootout_succeeded": True}
    assert calls == ["bootout"]


def test_status_path_never_loads_credentials_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(runner, "_load_private_endpoint", lambda: pytest.fail("credential access"))
    monkeypatch.setattr(
        runner.service, "read_status", lambda _root: {"status": "awaiting_bootstrap"}
    )
    assert runner.main(["--root", str(tmp_path), "--status-only"]) == 0
    assert json.loads(capsys.readouterr().out)["service"]["status"] == "awaiting_bootstrap"


def test_private_endpoint_loader_rejects_mode_0600_fifo_without_reading(tmp_path: Path) -> None:
    fifo = tmp_path / "endpoint-config"
    os.mkfifo(fifo, 0o600)
    os.chmod(fifo, 0o600)
    assert runner._load_private_endpoint(fifo) is None
