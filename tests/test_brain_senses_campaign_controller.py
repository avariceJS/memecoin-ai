from __future__ import annotations

import hashlib
import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

from tools import brain_senses_campaign_controller as controller


class FakeLaunchctl:
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.commands: list[list[str]] = []
        self.crash_after_first_bootstrap = False

    def __call__(self, args, **kwargs):
        args = list(args)
        self.commands.append(args)
        if args[1] == "print":
            label = args[2].rsplit("/", 1)[-1]
            return SimpleNamespace(returncode=0 if label in self.loaded else 113)
        if args[1] == "bootstrap":
            plist = plistlib.loads(Path(args[-1]).read_bytes())
            self.loaded.add(plist["Label"])
            if self.crash_after_first_bootstrap:
                self.crash_after_first_bootstrap = False
                raise KeyboardInterrupt("simulated process death after launchd loaded service")
            return SimpleNamespace(returncode=0)
        if args[1] == "bootout":
            self.loaded.discard(args[2].rsplit("/", 1)[-1])
            return SimpleNamespace(returncode=0)
        raise AssertionError(args)


def _closed_v2(root: Path) -> Path:
    root.mkdir(parents=True)
    stop = {
        "campaign_id": "helius-inventory-v2-16-hourly-20261010",
        "status": "closed_after_authorized_final_bucket",
        "last_terminal_status": "frame_complete",
        "last_applied_bucket": "20261010T020000Z",
        "manifest_sha256": "a" * 64,
        "final_observed_anchor_slot": 455082523,
        "final_anchor_receipt_ns": 1_791_597_609_195_251_000,
        "last_inventoried_interval": {
            "start": 455066012,
            "end_exclusive": 455082524,
            "inventory_sha256": "b" * 64,
            "provider_returned_produced_slots": 16512,
        },
        "usage": {
            "requests": 104,
            "raw_bytes": 186022930,
            "stored_bytes": 41818169,
            "reserved_raw_bytes": 0,
            "reserved_stored_bytes": 0,
        },
        "inherited_gaps": [{"bucket": "20261010T000000Z", "reason": "oversized_frame"}],
    }
    (root / "STOP").write_text(json.dumps(stop, sort_keys=True))
    return root


def test_controller_creates_one_campaign_and_carries_v2_gap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(controller, "_verify_v2_predecessor", lambda root, stop: None)
    monkeypatch.setattr(
        controller.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=controller.DISK_FLOOR_BYTES + 2_000_000_000),
    )
    launchctl = FakeLaunchctl()
    predecessor = _closed_v2(tmp_path / "v2")
    root = tmp_path / "controller"
    agents = tmp_path / "LaunchAgents"
    first = controller.tick(
        root, predecessor, now_ns=1_791_600_000_000_000_000, launch_agents=agents, runner=launchctl
    )
    assert first["status"] == "successor_loaded"
    state = json.loads((root / "controller-state.json").read_text())
    assert state["campaign_count"] == 1
    campaign_root = Path(state["current_campaign_root"])
    lineage = json.loads((campaign_root / "predecessor-lineage.json").read_text())
    assert lineage["predecessor_campaign_id"] == "helius-inventory-v2-16-hourly-20261010"
    assert lineage["gap_intervals"][0]["reason"] == "oversized_frame"
    plist = plistlib.loads(Path(state["current_campaign_plist"]).read_bytes())
    assert plist["RunAtLoad"] is False
    assert "--label" in plist["ProgramArguments"]
    assert all("api" not in arg.lower() for arg in plist["ProgramArguments"])

    repeated = controller.tick(
        root,
        predecessor,
        now_ns=1_791_600_060_000_000_000,
        launch_agents=agents,
        runner=launchctl,
    )
    assert repeated["status"] == "campaign_active"
    assert json.loads((root / "controller-state.json").read_text())["campaign_count"] == 1
    assert len([cmd for cmd in launchctl.commands if cmd[1] == "bootstrap"]) == 1


def test_controller_lease_expiry_is_durable_and_creates_no_successor(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(controller, "_verify_v2_predecessor", lambda root, stop: None)
    monkeypatch.setattr(
        controller.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=controller.DISK_FLOOR_BYTES + 2_000_000_000),
    )
    launchctl = FakeLaunchctl()
    predecessor = _closed_v2(tmp_path / "v2")
    root = tmp_path / "controller"
    agents = tmp_path / "LaunchAgents"
    t0 = 1_791_600_000_000_000_000
    controller.tick(root, predecessor, now_ns=t0, launch_agents=agents, runner=launchctl)
    expired = t0 + controller.LEASE_NS + 1
    result = controller.tick(
        root, predecessor, now_ns=expired, launch_agents=agents, runner=launchctl
    )
    assert result["status"] == "controller_stopped"
    stop = json.loads((root / "STOP").read_text())
    assert stop["reason"] == "finite_72_hour_lease_expired"
    assert stop["campaign_count"] == 1
    assert len([cmd for cmd in launchctl.commands if cmd[1] == "bootstrap"]) == 1


def _completed_controller_state(root: Path, campaign_root: Path, *, count: int = 1) -> None:
    campaign_root.mkdir(parents=True)
    (campaign_root / "STOP").write_text(
        json.dumps(
            {
                "campaign_id": f"helius-inventory-v3-20261010-{count}",
                "reason": "finite_attempt_cap_reached",
                "attempt_count": 24,
                "accounting_error_class": None,
                "active_reservation_status": "reconciled",
                "last_attempt_bucket": "20261011T020000Z",
                "observed_anchor_slot": 455200000,
                "observed_anchor_receipt_ns": 1_791_700_000_000_000_000,
                "last_inventoried_interval": {"start": 455190000, "end_exclusive": 455200000},
                "gap_intervals": [],
                "manifest_sha256": "c" * 64,
            },
            sort_keys=True,
        )
    )
    state = {
        "schema_version": 1,
        "controller_id": "helius-inventory-controller-20261010-72h",
        "status": "active",
        "lease_start_ns": 1_791_600_000_000_000_000,
        "lease_expires_ns": 1_791_600_000_000_000_000 + controller.LEASE_NS,
        "max_campaigns": 3,
        "campaign_count": count,
        "aggregate_requests": 0,
        "aggregate_raw_bytes": 0,
        "aggregate_stored_bytes": 0,
        "current_campaign_id": f"helius-inventory-v3-20261010-{count}",
        "current_campaign_root": str(campaign_root),
        "current_campaign_label": "ai.memecoin.brain.senses.previous",
        "current_campaign_plist": str(root / "previous.plist"),
        "pending_bootstrap": False,
        "predecessor_root": str(root / "v2"),
    }
    root.mkdir(exist_ok=True)
    campaign_plist = root / "previous.plist"
    campaign_plist.write_bytes(b"test plist binding")
    state["current_campaign_plist_sha256"] = hashlib.sha256(campaign_plist.read_bytes()).hexdigest()
    policy = controller._policy(state)
    state["policy_sha256"] = hashlib.sha256(
        (controller.canonical(policy) + "\n").encode()
    ).hexdigest()
    (root / "controller-policy.json").write_text(controller.canonical(policy) + "\n")
    (root / "controller-state.json").write_text(json.dumps(state, sort_keys=True))


def test_normal_predecessor_handoff_creates_next_day_campaign(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        controller.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=controller.DISK_FLOOR_BYTES + 2_000_000_000),
    )
    monkeypatch.setattr(
        controller,
        "_verify_normal_campaign",
        lambda root, stop: {
            "requests": 816,
            "raw_bytes": 2_000_000_000,
            "stored_bytes": 500_000_000,
        },
    )
    root = tmp_path / "controller"
    campaign = tmp_path / "campaign-1"
    _completed_controller_state(root, campaign)
    launchctl = FakeLaunchctl()
    result = controller.tick(
        root,
        tmp_path / "v2",
        now_ns=1_791_686_000_000_000_000,
        launch_agents=tmp_path / "LaunchAgents",
        runner=launchctl,
    )
    assert result["status"] == "successor_loaded"
    state = json.loads((root / "controller-state.json").read_text())
    assert state["campaign_count"] == 2
    next_root = Path(state["current_campaign_root"])
    lineage = json.loads((next_root / "predecessor-lineage.json").read_text())
    assert lineage["predecessor_campaign_id"] == "helius-inventory-v3-20261010-1"
    assert lineage["last_attempt_bucket"] == "20261011T020000Z"
    assert state["aggregate_requests"] == 816


def test_abnormal_predecessor_and_aggregate_overrun_halt_without_successor(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        controller.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=controller.DISK_FLOOR_BYTES + 2_000_000_000),
    )
    root = tmp_path / "controller"
    campaign = tmp_path / "campaign-1"
    _completed_controller_state(root, campaign)
    monkeypatch.setattr(
        controller,
        "_verify_normal_campaign",
        lambda root, stop: (_ for _ in ()).throw(ValueError("unreconciled")),
    )
    launchctl = FakeLaunchctl()
    result = controller.tick(
        root,
        tmp_path / "v2",
        now_ns=1_791_686_000_000_000_000,
        launch_agents=tmp_path / "LaunchAgents",
        runner=launchctl,
    )
    assert result["reason"] == "abnormal_predecessor_stop"
    assert json.loads((root / "STOP").read_text())["campaign_count"] == 1
    assert not [cmd for cmd in launchctl.commands if cmd[1] == "bootstrap"]

    root2 = tmp_path / "controller2"
    campaign2 = tmp_path / "campaign-2"
    _completed_controller_state(root2, campaign2)
    monkeypatch.setattr(
        controller,
        "_verify_normal_campaign",
        lambda root, stop: {
            "requests": 816,
            "raw_bytes": 2_000_000_000,
            "stored_bytes": 500_000_000,
        },
    )
    state_path = root2 / "controller-state.json"
    state = json.loads(state_path.read_text())
    state["aggregate_stored_bytes"] = controller.LEASE_STORED_BYTES - 1
    state_path.write_text(json.dumps(state, sort_keys=True))
    result = controller.tick(
        root2,
        tmp_path / "v2",
        now_ns=1_791_686_000_000_000_000,
        launch_agents=tmp_path / "LaunchAgents2",
        runner=launchctl,
    )
    assert result["reason"] == "finite_campaign_or_aggregate_request_cap_reached"
    assert json.loads((root2 / "STOP").read_text())["campaign_count"] == 1


def test_controller_disk_preflight_writes_stop_before_any_campaign(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(controller, "_verify_v2_predecessor", lambda root, stop: None)
    monkeypatch.setattr(
        controller.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=controller.DISK_FLOOR_BYTES),
    )
    predecessor = _closed_v2(tmp_path / "v2")
    root = tmp_path / "controller"
    launchctl = FakeLaunchctl()
    result = controller.tick(
        root,
        predecessor,
        now_ns=1_791_600_000_000_000_000,
        launch_agents=tmp_path / "LaunchAgents",
        runner=launchctl,
    )
    assert result["status"] == "controller_stopped"
    assert "STOP" in {p.name for p in root.iterdir()}
    assert not [cmd for cmd in launchctl.commands if cmd[1] == "bootstrap"]


def test_crash_after_campaign_load_reuses_same_service(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(controller, "_verify_v2_predecessor", lambda root, stop: None)
    monkeypatch.setattr(
        controller.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=controller.DISK_FLOOR_BYTES + 2_000_000_000),
    )
    predecessor = _closed_v2(tmp_path / "v2")
    root = tmp_path / "controller"
    launchctl = FakeLaunchctl()
    launchctl.crash_after_first_bootstrap = True
    t0 = 1_791_600_000_000_000_000
    try:
        controller.tick(
            root,
            predecessor,
            now_ns=t0,
            launch_agents=tmp_path / "LaunchAgents",
            runner=launchctl,
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected simulated process death")

    resumed = controller.tick(
        root,
        predecessor,
        now_ns=t0 + 1_000_000_000,
        launch_agents=tmp_path / "LaunchAgents",
        runner=launchctl,
    )
    assert resumed["status"] == "campaign_active"
    assert json.loads((root / "controller-state.json").read_text())["campaign_count"] == 1
    assert len([cmd for cmd in launchctl.commands if cmd[1] == "bootstrap"]) == 1


def test_crash_after_stop_publish_retries_only_controller_bootout(tmp_path, monkeypatch) -> None:
    root = tmp_path / "controller"
    state = {
        "controller_id": "controller-test",
        "status": "active",
        "lease_start_ns": 1,
        "lease_expires_ns": 2,
        "campaign_count": 1,
        "aggregate_requests": 34,
        "aggregate_raw_bytes": 100,
        "aggregate_stored_bytes": 50,
        "current_campaign_id": "campaign-1",
    }
    root.mkdir()
    (root / "controller-state.json").write_text(json.dumps(state))
    original_replace = controller._replace

    def crash_on_terminal_state(path, value):
        if path.name == "controller-state.json" and value.get("status") == "stopped":
            raise KeyboardInterrupt("simulated death after durable STOP")
        return original_replace(path, value)

    monkeypatch.setattr(controller, "_replace", crash_on_terminal_state)
    try:
        controller._stop(root, state, "test_terminal")
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected simulated process death")
    monkeypatch.setattr(controller, "_replace", original_replace)
    launchctl = FakeLaunchctl()
    launchctl.loaded.add(controller.CONTROLLER_LABEL)
    result = controller.tick(
        root,
        tmp_path / "v2",
        now_ns=3,
        launch_agents=tmp_path / "LaunchAgents",
        runner=launchctl,
    )
    assert result["status"] == "controller_stopped"
    assert [cmd[1] for cmd in launchctl.commands] == ["bootout"]


def test_three_campaign_lease_never_creates_fourth(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        controller,
        "_verify_normal_campaign",
        lambda root, stop: {"requests": 816, "raw_bytes": 1_000, "stored_bytes": 500},
    )
    root = tmp_path / "controller"
    campaign = tmp_path / "campaign-3"
    _completed_controller_state(root, campaign, count=3)
    launchctl = FakeLaunchctl()
    result = controller.tick(
        root,
        tmp_path / "v2",
        now_ns=1_791_686_000_000_000_000,
        launch_agents=tmp_path / "LaunchAgents",
        runner=launchctl,
    )
    assert result["reason"] == "finite_campaign_or_aggregate_request_cap_reached"
    assert json.loads((root / "STOP").read_text())["campaign_count"] == 3
    assert not [cmd for cmd in launchctl.commands if cmd[1] == "bootstrap"]
