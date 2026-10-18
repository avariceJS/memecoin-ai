import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memecoin_collector.wss_campaign import WssReferenceCampaign, default_authorization
from memecoin_collector.wss_reference import DISK_FLOOR_BYTES
from tools import brain_senses_wss_operator_bridge_v1 as bridge


def _authorization(*, expires_at_ns=10_000):
    return default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=expires_at_ns,
    )


def _paths(tmp_path, *, name="campaign", authorization=None):
    root = tmp_path / name
    auth_path = tmp_path / f"{name}-authorization.json"
    plan_path = tmp_path / f"{name}-plan.json"
    authorization = authorization or _authorization()
    auth_path.write_text(json.dumps(authorization, sort_keys=True, separators=(",", ":")) + "\n")
    auth_path.chmod(0o600)
    plan_path.write_bytes(bridge._canonical(bridge.make_offline_plan(root, authorization)))
    plan_path.chmod(0o600)
    return root, plan_path, auth_path


class _CompleteSyntheticClient:
    def __init__(self, campaign, *, at_ns=200):
        self.campaign = campaign
        self.at_ns = at_ns

    def run_one_shot(self):
        self.campaign.stop("synthetic_session_complete", self.at_ns)


def _run(root, plan, auth, factory, *, now=200, free=DISK_FLOOR_BYTES + 1):
    return bridge.run_injected_once(
        root,
        plan,
        auth,
        client_factory=factory,
        clock_ns=lambda: now,
        free_disk_bytes=lambda _path: free,
    )


def test_injected_one_shot_is_source_bound_and_receipt_cannot_claim_activation(tmp_path):
    root, plan, auth = _paths(tmp_path)
    built = []

    def factory(campaign):
        built.append(campaign)
        return _CompleteSyntheticClient(campaign)

    receipt = _run(root, plan, auth, factory)
    assert built
    assert receipt["activation_authorized"] is False
    assert receipt["provider_io_performed_by_bridge"] is False
    assert receipt["injected_client_io_observation"] == "unknown_not_observed"
    assert receipt["client_factory_called"] is True
    assert receipt["campaign_stop_reason"] == "synthetic_session_complete"
    assert (tmp_path / "campaign.operator-receipt.json").exists()
    assert bridge.read_status(root)["filesystem_mutated"] is False


def test_fresh_process_reopen_is_terminal_and_never_calls_factory(tmp_path):
    root, plan, auth = _paths(tmp_path)
    script = r"""
import json, sys
from pathlib import Path
from tools import brain_senses_wss_operator_bridge_v1 as b
from memecoin_collector.wss_reference import DISK_FLOOR_BYTES
root, plan, auth = map(Path, sys.argv[1:])
class C:
    def __init__(self, campaign): self.campaign = campaign
    def run_one_shot(self): self.campaign.stop('child_complete', 200)
r = b.run_injected_once(root, plan, auth, client_factory=C,
    clock_ns=lambda: 200, free_disk_bytes=lambda _: DISK_FLOOR_BYTES + 1)
print(json.dumps(r, sort_keys=True))
"""
    first = subprocess.run(
        [sys.executable, "-c", script, str(root), str(plan), str(auth)],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    first_receipt = json.loads(first.stdout)
    assert first_receipt["client_factory_called"] is True

    def forbidden(_campaign):
        raise AssertionError("recovery must never construct a client")

    original_receipt_path = tmp_path / "campaign.operator-receipt.json"
    original_receipt_before = original_receipt_path.read_bytes()
    second = _run(root, plan, auth, forbidden)
    assert second["client_factory_called"] is False
    assert second["original_receipt_client_factory_called"] is True
    assert second["original_operator_receipt_sha256"] == bridge._sha(original_receipt_before)
    assert second["dispatch_denial_reasons"] == ["existing_campaign_terminal_no_replay"]
    assert second["campaign_stop_sha256"] == first_receipt["campaign_stop_sha256"]
    assert original_receipt_path.read_bytes() == original_receipt_before
    assert json.loads((root / "CAMPAIGN_STOP.json").read_text())["reason"] == "child_complete"


def test_existing_root_recovery_binds_its_actual_authorization(tmp_path):
    root, plan, auth_path = _paths(tmp_path)
    campaign = WssReferenceCampaign(root, _authorization(), opened_ns=200)
    campaign.start_session(at_ns=201, free_disk_bytes=DISK_FLOOR_BYTES + 1)
    campaign.close()
    other_auth = _authorization()
    other_auth["c4_stop_sha256"] = "2" * 64
    external_auth = tmp_path / "other-authorization.json"
    external_auth.write_bytes(bridge._canonical(other_auth))
    external_auth.chmod(0o600)
    other_plan = tmp_path / "other-plan.json"
    other_plan.write_bytes(bridge._canonical(bridge.make_offline_plan(root, other_auth)))
    other_plan.chmod(0o600)
    called = []
    receipt = _run(root, other_plan, external_auth, lambda client: called.append(client), now=220)
    campaign_auth = json.loads((root / "authorization.json").read_text())
    assert called == []
    assert receipt["authorization_sha256"] == bridge._sha(bridge._canonical(campaign_auth))
    assert receipt["external_authorization_matches_campaign"] is False
    assert "external_authorization_mismatch" in receipt["dispatch_denial_reasons"]
    assert receipt["campaign_stop_reason"] == "process_interrupted_no_resume"
    original_path = tmp_path / "campaign.operator-receipt.json"
    original_raw = original_path.read_bytes()
    reopened = _run(
        root, other_plan, external_auth, lambda _c: pytest.fail("must not replay"), now=221
    )
    assert reopened["client_factory_called"] is False
    assert reopened["external_authorization_matches_campaign"] is False
    assert "external_authorization_mismatch" in reopened["dispatch_denial_reasons"]
    assert reopened["original_operator_receipt_sha256"] == bridge._sha(original_raw)
    assert original_path.read_bytes() == original_raw


def test_expired_existing_prefix_is_recovered_even_below_disk_floor(tmp_path):
    root, plan, auth_path = _paths(tmp_path, authorization=_authorization(expires_at_ns=1_000))
    campaign = WssReferenceCampaign(root, _authorization(expires_at_ns=1_000), opened_ns=200)
    campaign.start_session(at_ns=201, free_disk_bytes=DISK_FLOOR_BYTES + 1)
    campaign.close()
    called = []
    receipt = _run(root, plan, auth_path, lambda client: called.append(client), now=1_001, free=0)
    assert called == []
    assert receipt["client_factory_called"] is False
    assert "external_authorization_expired_or_not_yet_active" in receipt["dispatch_denial_reasons"]
    assert "disk_floor" in receipt["dispatch_denial_reasons"]
    assert (
        json.loads((root / "CAMPAIGN_STOP.json").read_text())["reason"]
        == "process_interrupted_no_resume"
    )


def test_existing_root_recovery_does_not_require_valid_external_plan(tmp_path):
    root, plan, auth = _paths(tmp_path)
    campaign = WssReferenceCampaign(root, _authorization(), opened_ns=200)
    campaign.start_session(at_ns=201, free_disk_bytes=DISK_FLOOR_BYTES + 1)
    campaign.close()
    plan.write_text("{}")
    plan.chmod(0o600)
    called = []
    receipt = _run(root, plan, auth, lambda client: called.append(client), now=220)
    assert called == []
    assert receipt["external_authorization_matches_campaign"] is True
    assert "external_plan_or_source_mismatch" in receipt["dispatch_denial_reasons"]


@pytest.mark.parametrize(
    ("now", "free", "mutate", "expected"),
    [
        (10_000, DISK_FLOOR_BYTES + 1, False, "authorization_expired_or_not_yet_active"),
        (200, DISK_FLOOR_BYTES - 1, False, "disk_floor"),
        (200, DISK_FLOOR_BYTES + 1, True, "operator_plan_binding_mismatch"),
    ],
)
def test_denials_precede_client_construction(tmp_path, monkeypatch, now, free, mutate, expected):
    root, plan, auth = _paths(tmp_path)
    built = []
    if mutate:
        monkeypatch.setattr(bridge, "source_bindings", lambda: {"mutated": "0" * 64})
    with pytest.raises(bridge.BridgeError, match=expected):
        _run(root, plan, auth, lambda campaign: built.append(campaign), now=now, free=free)
    assert built == []
    assert not root.exists()


def test_plan_and_authorization_inputs_require_stable_private_regular_files(tmp_path):
    _root, plan, auth = _paths(tmp_path)
    assert bridge._load_json_file(plan, label="test_plan")[0]["schema"] == bridge.PLAN_SCHEMA
    plan.chmod(0o644)
    with pytest.raises(bridge.BridgeError, match="test_plan_identity_invalid"):
        bridge._load_json_file(plan, label="test_plan")
    plan.chmod(0o600)
    alias = tmp_path / "plan-symlink.json"
    alias.symlink_to(plan)
    with pytest.raises(bridge.BridgeError, match="test_plan_unavailable_or_invalid"):
        bridge._load_json_file(alias, label="test_plan")


def test_clock_regression_between_disk_checks_denies_before_factory(tmp_path):
    root, plan, auth = _paths(tmp_path)
    times = iter((200, 199))
    built = []
    with pytest.raises(bridge.BridgeError, match="clock_or_expiry_changed"):
        bridge.run_injected_once(
            root,
            plan,
            auth,
            client_factory=lambda campaign: built.append(campaign),
            clock_ns=lambda: next(times),
            free_disk_bytes=lambda _path: DISK_FLOOR_BYTES + 1,
        )
    assert built == []
    assert not root.exists()


def test_fresh_source_and_disk_revalidation_immediately_precedes_factory(tmp_path, monkeypatch):
    root, plan, auth = _paths(tmp_path)
    actual_bindings = bridge.source_bindings()
    source_calls = iter((actual_bindings, {"changed_after_reservation": "f" * 64}))
    monkeypatch.setattr(bridge, "source_bindings", lambda: next(source_calls))
    disk_calls = iter((DISK_FLOOR_BYTES + 1, DISK_FLOOR_BYTES + 1))
    built = []
    with pytest.raises(bridge.BridgeError, match="operator_plan_binding_mismatch"):
        bridge.run_injected_once(
            root,
            plan,
            auth,
            client_factory=lambda campaign: built.append(campaign),
            clock_ns=lambda: 200,
            free_disk_bytes=lambda _path: next(disk_calls),
        )
    assert built == []
    assert (
        json.loads((root / "CAMPAIGN_STOP.json").read_text())["reason"]
        == "pre_factory_preflight_denied"
    )


def test_expiry_during_second_preflight_denies_before_factory(tmp_path):
    root, plan, auth = _paths(tmp_path)
    times = iter((200, 200, 9_999, 10_000, 10_000, 10_000))
    built = []
    with pytest.raises(bridge.BridgeError, match="clock_or_expiry_changed"):
        bridge.run_injected_once(
            root,
            plan,
            auth,
            client_factory=lambda campaign: built.append(campaign),
            clock_ns=lambda: next(times),
            free_disk_bytes=lambda _path: DISK_FLOOR_BYTES + 1,
        )
    assert built == []
    assert (
        json.loads((root / "CAMPAIGN_STOP.json").read_text())["reason"]
        == "pre_factory_preflight_denied"
    )


def test_changed_cap_authorization_denies_before_client_factory(tmp_path):
    root, plan, auth = _paths(tmp_path)
    built = []
    changed = json.loads(auth.read_text())
    changed["max_sessions"] = 3
    auth.write_bytes(bridge._canonical(changed))
    plan_row = json.loads(plan.read_text())
    plan_row["authorization_sha256"] = bridge._sha(bridge._canonical(changed))
    plan.write_bytes(bridge._canonical(plan_row))
    with pytest.raises(bridge.BridgeError, match="campaign_authorization_invalid"):
        _run(root, plan, auth, lambda campaign: built.append(campaign))
    assert built == []
    assert not root.exists()


def test_lock_exclusion_denies_before_client_construction(tmp_path):
    root, plan, auth = _paths(tmp_path)
    fd = bridge._lock(tmp_path)
    built = []
    try:
        with pytest.raises(bridge.BridgeError, match="operator_writer_active"):
            _run(root, plan, auth, lambda campaign: built.append(campaign))
    finally:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert built == []
    assert not root.exists()


def test_interruption_during_injected_session_seals_unknown_without_replay(tmp_path):
    root, plan, auth = _paths(tmp_path)
    calls = []

    class InterruptingClient:
        def __init__(self, campaign):
            self.campaign = campaign

        def run_one_shot(self):
            calls.append("called")
            self.campaign.active.record_transport_failure(
                phase="notification",
                reason="notification_io",
                raw_evidence=b"",
                progress_bytes=7,
                at_ns=210,
            )
            raise RuntimeError("synthetic interrupted transport")

    with pytest.raises(bridge.BridgeError, match="operator_run_failed"):
        _run(root, plan, auth, InterruptingClient)
    stop_before = (root / "CAMPAIGN_STOP.json").read_bytes()
    assert calls == ["called"]
    session_stop = json.loads((root / "sessions/01/journal/00000002.json").read_text())
    assert session_stop["kind"] == "transport_failure"
    assert session_stop["partial_input_bytes"] == 7
    receipt_path = tmp_path / "campaign.operator-receipt.json"
    original_receipt = receipt_path.read_bytes()
    second = _run(root, plan, auth, lambda _campaign: pytest.fail("must not replay"))
    assert second["client_factory_called"] is False
    assert second["original_receipt_client_factory_called"] is True
    assert second["original_operator_receipt_sha256"] == bridge._sha(original_receipt)
    assert receipt_path.read_bytes() == original_receipt
    assert (root / "CAMPAIGN_STOP.json").read_bytes() == stop_before


def test_bridge_rejects_second_campaign_window(tmp_path):
    root, plan, auth = _paths(tmp_path)
    observed = []

    class TwoWindowClient:
        def __init__(self, campaign):
            self.campaign = campaign

        def run_one_shot(self):
            self.campaign.stop_session("connection_disconnected", 210)
            with pytest.raises(bridge.BridgeError, match="operator_one_window_cap"):
                self.campaign.start_session(at_ns=220, free_disk_bytes=DISK_FLOOR_BYTES + 1)
            observed.append(len(self.campaign.sessions))

    receipt = _run(root, plan, auth, TwoWindowClient)
    assert observed == [1]
    assert receipt["campaign_stop_reason"] == "one_window_cap"
    assert len(list((root / "sessions").iterdir())) == 1


def test_open_prefix_recovery_after_crash_before_dispatch_never_replays(tmp_path):
    root, plan, auth = _paths(tmp_path)
    script = r"""
import os, sys
from pathlib import Path
from memecoin_collector.wss_campaign import WssReferenceCampaign, default_authorization
from memecoin_collector.wss_reference import DISK_FLOOR_BYTES
root = Path(sys.argv[1])
authorization = default_authorization(c4_stop_sha256='0' * 64,
    account_budget_evidence_sha256='1' * 64, authorized_at_ns=100, expires_at_ns=10000)
campaign = WssReferenceCampaign(root, authorization, opened_ns=200)
campaign.start_session(at_ns=201, free_disk_bytes=DISK_FLOOR_BYTES + 1)
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(root)],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    called = []
    receipt = _run(root, plan, auth, lambda c: called.append(c), now=220)
    assert called == []
    assert receipt["client_factory_called"] is False
    assert receipt["campaign_stop_reason"] == "process_interrupted_no_resume"


def test_ambiguous_prefix_survives_process_crash_and_recovers_without_replay(tmp_path):
    root, plan, auth = _paths(tmp_path)
    script = r"""
import os, sys
from pathlib import Path
from memecoin_collector.wss_campaign import WssReferenceCampaign, default_authorization
from memecoin_collector.wss_reference import DISK_FLOOR_BYTES
root = Path(sys.argv[1])
authorization = default_authorization(c4_stop_sha256='0' * 64,
    account_budget_evidence_sha256='1' * 64, authorized_at_ns=100, expires_at_ns=10000)
campaign = WssReferenceCampaign(root, authorization, opened_ns=200)
session = campaign.start_session(at_ns=201, free_disk_bytes=DISK_FLOOR_BYTES + 1)
session.record_transport_failure(phase='notification', reason='notification_io',
    raw_evidence=b'', progress_bytes=19, at_ns=210)
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(root)],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    built = []
    receipt = _run(root, plan, auth, lambda client: built.append(client), now=220)
    stop = json.loads((root / "CAMPAIGN_STOP.json").read_text())
    assert built == []
    assert receipt["client_factory_called"] is False
    assert stop["reason"] == "process_interrupted_no_resume"
    assert stop["session_stops"][0]["stop"]["partial_input_bytes"] == 19


def test_status_cli_is_read_only_and_execution_is_not_exposed(tmp_path):
    root = tmp_path / "missing"
    command = [
        sys.executable,
        "tools/brain_senses_wss_operator_bridge_v1.py",
        "--root",
        str(root),
        "--status-only",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    status = json.loads(result.stdout)
    assert status["root_exists"] is False
    assert status["activation_authorized"] is False
    assert status["provider_io_performed_by_bridge"] is False
    assert status["provider_io_status_for_existing_tree"] == "not_inferred_from_filesystem"
    assert not root.exists()
    denied = subprocess.run(
        [sys.executable, "tools/brain_senses_wss_operator_bridge_v1.py", "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert denied.returncode != 0
    assert "execution is unavailable" in denied.stderr
    assert not root.exists()
