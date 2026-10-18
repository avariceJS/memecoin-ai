from __future__ import annotations

import json
import plistlib
import shlex
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools import brain_senses_c5_forward_pilot_v1 as policy
from tools import brain_senses_c5_http_runner_v1 as runner

AUTH_NS = int(datetime(2026, 10, 12, 0, 58, tzinfo=UTC).timestamp() * 1_000_000_000)
BOOTSTRAP = "20261012T010000Z"
FRAME = "20261012T020000Z"
BOOTSTRAP_TIME = datetime(2026, 10, 12, 1, 6, tzinfo=UTC)
FRAME_TIME = datetime(2026, 10, 12, 2, 6, tzinfo=UTC)
H = "a" * 64
VALID_ENDPOINT = "https://mainnet.helius-rpc.com/?api-key=synthetic-secret"
ORIGINAL_DISK_VERIFY = runner._verify_disk_headroom
ORIGINAL_GATE_VERIFY = runner.verify_activation_gate


def _config_text(endpoint: str = VALID_ENDPOINT) -> str:
    return f"MEMECOIN_HELIUS_RPC_URL={endpoint}\n"


def _inventory() -> list[dict]:
    return [
        {"kind": "namespace", "namespace": f"gui/{runner.os.getuid()}"},
        {"kind": "launchd", "label": runner.C5_LABEL, "pid": 123},
        *[
            {"kind": "disabled", "label": label, "disabled": True}
            for label in sorted(runner.KNOWN_DISABLED_SENSES_LABELS)
        ],
        {
            "kind": "process",
            "label": runner.C5_LABEL,
            "pid": 123,
            "ppid": 1,
            "runner_path": str(runner.REPO_ROOT / "tools/brain_senses_c5_http_runner_v1.py"),
            "argv_matches_expected": True,
        },
    ]


def _launchd_print(*service_rows: str) -> str:
    rows = "\n".join(service_rows)
    return (
        f"gui/{runner.os.getuid()} = {{\n"
        f"  service count = {len(service_rows)}\n"
        f"  services = {{\n{rows}\n  }}\n}}\n"
    )


def _disabled_print() -> str:
    labels = "\n".join(
        f'  "{label}" => disabled' for label in sorted(runner.KNOWN_DISABLED_SENSES_LABELS)
    )
    return f"disabled services = {{\n{labels}\n}}\n"


@pytest.fixture(autouse=True)
def synthetic_disk_headroom(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner, "_verify_disk_headroom", lambda _path, required=runner.DISK_REQUIRED_BYTES: required
    )
    monkeypatch.setattr(runner, "C5_SERVICE_ROOT", tmp_path / "service")
    # Synthetic transition tests inject local predicate evidence only. The production gate
    # remains fail-closed unless official source parsers and a pinned reviewer receipt exist.
    monkeypatch.setattr(
        runner, "verify_activation_gate", runner._verify_activation_gate_evidence_predicates
    )


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _plan(tmp_path: Path) -> dict:
    return runner.build_plan(
        pilot_id="synthetic-c5-01",
        service_id="synthetic-service-01",
        bootstrap_bucket=BOOTSTRAP,
        frame_bucket=FRAME,
        authorized_at_ns=AUTH_NS,
    )


def _gate(
    plan: dict,
    path: Path,
    *,
    account_overrides: dict | None = None,
    disk_overrides: dict | None = None,
    churn_overrides: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expiry_ns = int((policy._utc_bucket(FRAME).timestamp() + 16 * 60) * 1_000_000_000)
    decision_ns = int((policy._utc_bucket(BOOTSTRAP).timestamp() - 60) * 1_000_000_000)
    evidence = {
        "account": {
            "schema": "c5-account-budget-evidence-v1",
            "status": "verified",
            "observed_at_ns": decision_ns - 1,
            "valid_until_ns": expiry_ns,
            "remaining_request_headroom": policy.MAX_REQUESTS,
            "provider_enforced_no_overage": False,
            "single_observation_no_retry": True,
        },
    }
    if account_overrides:
        evidence["account"].update(account_overrides)
    disk = {
        "schema": "c5-disk-budget-evidence-v1",
        "status": "verified",
        "disk_floor_bytes": runner.schedule_v3.DISK_FLOOR_BYTES,
        "full_frame_peak_bytes": (
            runner.schedule_v3.FRAME_RAW_BYTES
            + runner.schedule_v3.FRAME_STORED_BYTES
            + runner.schedule_v3.PEAK_WRITE_MARGIN_BYTES
        ),
        "remaining_pilot_physical_stored_allowance_bytes": (
            policy.BOOTSTRAP_STORED_BYTES + policy.FRAME_STORED_BYTES
        ),
        "adverse_host_churn_margin_bytes": 8_000_000_000,
        "measured_free_bytes": 100_000_000_000,
        "measured_at_ns": decision_ns - 1,
        "valid_until_ns": expiry_ns,
    }
    if disk_overrides:
        disk.update(disk_overrides)
    disk["computed_required_free_bytes"] = max(
        runner.C5_RECORDED_COMPOSITE_DISK_REQUIRED_BYTES,
        disk["disk_floor_bytes"]
        + disk["full_frame_peak_bytes"]
        + disk["remaining_pilot_physical_stored_allowance_bytes"]
        + disk["adverse_host_churn_margin_bytes"],
    )
    churn = {
        "schema": "c5-host-churn-review-v1",
        "status": "reviewed_and_substantiated",
        "reviewer": "synthetic-reviewer",
        "decision_time_ns": decision_ns,
        "margin_bytes": disk["adverse_host_churn_margin_bytes"],
        "method": "synthetic fixture only",
        "basis": "synthetic evidence; not operational substantiation",
    }
    if churn_overrides:
        churn.update(churn_overrides)
    refs = {}
    for key, value in {**evidence, "disk": disk, "host_churn": churn}.items():
        evidence_path = path.parent / f"{path.stem}-{key}.json"
        _write_json(evidence_path, value)
        evidence_path.chmod(0o600)
        refs[f"{key}_evidence"] = {
            "path": str(evidence_path),
            "sha256": runner._sha_file(evidence_path),
        }
    review = {
        "schema": "c5-review-evidence-v1",
        "reviewer": "synthetic-reviewer",
        "decision": "approve_c5_two_bucket_pilot",
        "plan_sha256": plan["plan_sha256"],
        "decision_time_ns": decision_ns,
        "reviewed_evidence_sha256s": {
            key: refs[f"{key}_evidence"]["sha256"] for key in ("account", "disk", "host_churn")
        },
    }
    review_path = path.parent / f"{path.stem}-review.json"
    _write_json(review_path, review)
    review_path.chmod(0o600)
    refs["review_evidence"] = {
        "path": str(review_path),
        "sha256": runner._sha_file(review_path),
    }
    runner.create_activation_receipt(
        path,
        {
            "schema": "c5-independent-activation-gate-v1",
            "status": "activation_approved_after_review",
            "plan_sha256": plan["plan_sha256"],
            "source_bindings": plan["source_bindings"],
            "plist_sha256": plan["source_bindings"][runner.PLIST_TEMPLATE],
            "service_root": plan["external_predecessor_binding"]["service_root"],
            "allowed_buckets": [BOOTSTRAP, FRAME],
            "expires_at_ns": expiry_ns,
            "predecessor": {
                "terminal_receipt_sha256": policy.C4_LINEAGE["terminal_receipt_sha256"],
                "campaign_root": runner.C4_CAMPAIGN_ROOT,
                "stop_sha256": policy.C4_LINEAGE["stop_sha256"],
                "launchagent_label": runner.C4_LABEL,
            },
            "limits": plan["limits"],
            "execution_controls": {
                "no_retry": True,
                "no_backfill": True,
                "no_replacement_bucket": True,
                "no_manual_tick": True,
                "no_third_bucket": True,
                "max_requests": policy.MAX_REQUESTS,
            },
            "reviewer": "synthetic-reviewer",
            "decision": "approve_c5_two_bucket_pilot",
            "decision_time_ns": decision_ns,
            **refs,
        },
    )


def _bootstrap_result(anchor: int = 455430200) -> dict:
    return {
        "status": "bootstrap_complete",
        "anchor_slot": anchor,
        "commitment": "finalized",
        "raw_bytes": 41,
        "stored_bytes": 55,
        "receipt_ns": 1791770700000000001,
        "availability_ns": 1791770700000000002,
        "request_sha256": H,
        "response_sha256": "b" * 64,
        "capture_receipt_sha256": "c" * 64,
        "request_limit": 1,
        "parsed_slot": anchor,
    }


def _frame_result(status: str = "frame_complete") -> dict:
    return {
        "status": status,
        "commitment": "finalized",
        "request_count": 34,
        "raw_bytes": 1000,
        "stored_bytes": 500,
        "anchor_slot": 455431000,
        "start_slot": 455430201,
        "frontier_slot": 455431001,
        "request_journal_sha256": H,
        "capture_manifest_sha256": "b" * 64,
        "summary_sha256": "c" * 64,
    }


def _call(tmp_path: Path, *, now=BOOTSTRAP_TIME, bootstrap=None, frame=None, config=None):
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    root = tmp_path / "service"
    config_path = config or tmp_path / "private.conf"
    config_path.write_text(_config_text(), encoding="utf-8")
    config_path.chmod(0o600)
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    calls: list[str] = []

    def boot(**kwargs):
        calls.append("bootstrap")
        return bootstrap or _bootstrap_result()

    def do_frame(**kwargs):
        calls.append("frame")
        return frame or _frame_result()

    result = runner.run_scheduled_tick(
        plan_path=plan_path,
        gate_path=gate_path,
        root=root,
        config_path=config_path,
        now=now,
        wall_clock=lambda: now,
        lock_root=tmp_path,
        process_inventory=lambda: _inventory(),
        bootstrap_executor=boot,
        frame_executor=do_frame,
    )
    return result, calls, plan, root, plan_path, gate_path, config_path


def test_source_bindings_pin_c4_identity_sources_and_uninstalled_template() -> None:
    bindings = runner.source_bindings()
    assert bindings[runner.C4_TERMINAL_RECEIPT] == policy.C4_LINEAGE["terminal_receipt_sha256"]
    assert (
        bindings["tools/brain_senses_block_epoch_v2.py"]
        == runner.C4_SOURCE_SHA256["tools/brain_senses_block_epoch_v2.py"]
    )
    assert bindings[runner.PLIST_TEMPLATE] == runner._sha_file(
        runner.REPO_ROOT / runner.PLIST_TEMPLATE
    )
    assert bindings["tools/brain_senses_service_v1_runner.py"] == runner._sha_file(
        runner.REPO_ROOT / "tools/brain_senses_service_v1_runner.py"
    )


def test_bootstrap_then_one_later_frame_then_terminal_stop(tmp_path: Path) -> None:
    first, calls, plan, root, plan_path, gate_path, config = _call(tmp_path)
    assert first["status"] == "bootstrap_sealed"
    assert calls == ["bootstrap"]
    lineage = json.loads((root / "lineage.json").read_text())
    assert lineage["last_observed_anchor_slot"] == 455430200
    assert lineage["gap_intervals"][-1]["start"] == 455430160
    assert lineage["service_plan_sha256"] == plan["plan_sha256"]
    assert lineage["controller_lease_expires_ns"] == runner._controller_lease_expiry_ns(plan)
    assert lineage["controller_lease_expires_ns"] > int(
        datetime(2026, 10, 12, 2, 15, 59, tzinfo=UTC).timestamp() * 1_000_000_000
    )
    assert (root / "v3" / "schedule-v3.json").is_file()
    second = runner.run_scheduled_tick(
        plan_path=plan_path,
        gate_path=gate_path,
        root=root,
        config_path=config,
        now=FRAME_TIME,
        wall_clock=lambda: FRAME_TIME,
        lock_root=tmp_path,
        process_inventory=lambda: _inventory(),
        bootstrap_executor=lambda **_: pytest.fail("bootstrap must not replay"),
        frame_executor=lambda **_: _frame_result(),
    )
    assert second["status"] == "two_bucket_pilot_complete"
    with pytest.raises(runner.RunnerError, match="service_stop_is_terminal"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=FRAME_TIME,
            wall_clock=lambda: FRAME_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("third tick forbidden"),
            frame_executor=lambda **_: pytest.fail("third tick forbidden"),
        )
    ledger = runner._read_ledger(root / "request-ledger.jsonl")
    assert [row["kind"] for row in ledger] == ["reservation", "seal", "reservation", "seal"]
    assert sum(row.get("request_count", 0) for row in ledger) == 35


def test_bootstrap_lineage_lease_or_plan_drift_stops_before_frame(tmp_path: Path) -> None:
    _, _, plan, root, plan_path, gate_path, config = _call(tmp_path)
    lineage_path = root / "lineage.json"
    lineage = json.loads(lineage_path.read_text())
    lineage["controller_lease_expires_ns"] -= 1
    _write_json(lineage_path, lineage)
    with pytest.raises(runner.RunnerError, match="bootstrap_lineage_plan_or_lease_mismatch"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=FRAME_TIME,
            wall_clock=lambda: FRAME_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("sealed bootstrap cannot replay"),
            frame_executor=lambda **_: pytest.fail("invalid lineage blocks frame"),
        )
    assert json.loads((root / "STOP.json").read_text())["reason"] == (
        "bootstrap_lineage_plan_or_lease_mismatch"
    )


def test_bootstrap_failure_is_durable_stop_and_never_replayed(tmp_path: Path) -> None:
    # Build explicitly because failed adapter results are converted to a terminal STOP.
    plan = _plan(tmp_path)
    plan_path, gate_path, root, config = (
        tmp_path / "plan.json",
        tmp_path / "gate.json",
        tmp_path / "service",
        tmp_path / "private.conf",
    )
    _write_json(plan_path, plan)
    gate_path = tmp_path / "valid-gate.json"
    _gate(plan, gate_path)
    config.write_text(_config_text())
    config.chmod(0o600)
    calls = []
    with pytest.raises(runner.RunnerError, match="reserved_attempt_failed_or_ambiguous_stopped"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: calls.append("bootstrap") or {"status": "anchor_failed"},
            frame_executor=lambda **_: pytest.fail("frame after failed bootstrap"),
        )
    assert calls == ["bootstrap"]
    assert (
        json.loads((root / "STOP.json").read_text())["reason"]
        == "reserved_attempt_failed_or_ambiguous"
    )
    with pytest.raises(runner.RunnerError, match="service_stop_is_terminal"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("no retry"),
            frame_executor=lambda **_: pytest.fail("no retry"),
        )


def test_reserved_crash_reconstructs_stop_before_config_or_executor(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    root = tmp_path / "service"
    config = tmp_path / "missing-private-config"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    root.mkdir()
    _write_json(root / "manifest.json", {"plan_sha256": plan["plan_sha256"]})
    _write_json(
        root / "state.json",
        {"phase": "awaiting_bootstrap", "request_count": 0, "frontier_slot": 455430160},
    )
    runner._append_ledger(
        root / "request-ledger.jsonl",
        {
            "kind": "reservation",
            "bucket": BOOTSTRAP,
            "phase": "awaiting_bootstrap",
            "reserved_ns": 1,
            "request_count": 1,
        },
    )
    with pytest.raises(runner.RunnerError, match="interrupted_attempt_stopped_without_replay"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("reservation must not be replayed"),
            frame_executor=lambda **_: pytest.fail("reservation must not be replayed"),
        )
    assert (root / "STOP.json").is_file()
    assert json.loads((root / "STOP.json").read_text())["gap"] == {
        "start": 455430160,
        "end_exclusive": None,
        "reason": "interrupted_or_inconsistent_attempt",
    }


def test_crash_after_bootstrap_seal_before_state_update_never_replays(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    root = tmp_path / "service"
    missing_config = tmp_path / "missing-config"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    root.mkdir()
    _write_json(root / "manifest.json", {"plan_sha256": plan["plan_sha256"]})
    _write_json(
        root / "state.json",
        {"phase": "awaiting_bootstrap", "request_count": 0, "frontier_slot": 455430160},
    )
    reservation = runner._append_ledger(
        root / "request-ledger.jsonl",
        {
            "kind": "reservation",
            "bucket": BOOTSTRAP,
            "phase": "awaiting_bootstrap",
            "reserved_ns": 1,
            "request_count": 1,
        },
    )
    runner._append_ledger(
        root / "request-ledger.jsonl",
        {
            "kind": "seal",
            "bucket": BOOTSTRAP,
            "phase": "bootstrap",
            "reservation_sha256": reservation["row_sha256"],
            "result": _bootstrap_result(),
        },
    )
    with pytest.raises(runner.RunnerError, match="interrupted_attempt_stopped_without_replay"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=missing_config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("sealed attempt cannot replay"),
            frame_executor=lambda **_: pytest.fail(
                "frame cannot start before state reconciliation"
            ),
        )
    stop = json.loads((root / "STOP.json").read_text())
    assert stop["reason"] == "state_ledger_bootstrap_mismatch"
    assert stop["gap"]["start"] == 455430160 and stop["gap"]["end_exclusive"] is None


def test_corrupt_ledger_durably_stops_before_config_or_executor(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    root = tmp_path / "service"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    root.mkdir()
    _write_json(root / "manifest.json", {"plan_sha256": plan["plan_sha256"]})
    _write_json(
        root / "state.json",
        {"phase": "awaiting_bootstrap", "request_count": 0, "frontier_slot": 455430160},
    )
    (root / "request-ledger.jsonl").write_text("{broken\n", encoding="utf-8")
    with pytest.raises(runner.RunnerError, match="journal_uncertain_stopped"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=tmp_path / "missing-config",
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("corrupt ledger blocks request"),
            frame_executor=lambda **_: pytest.fail("corrupt ledger blocks request"),
        )
    assert (
        json.loads((root / "STOP.json").read_text())["reason"]
        == "journal_uncertain_requires_review"
    )


def test_frame_gap_or_failed_self_check_stops(tmp_path: Path) -> None:
    _, _, _, root, plan_path, gate_path, config = _call(tmp_path)
    with pytest.raises(runner.RunnerError, match="reserved_attempt_failed_or_ambiguous_stopped"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=FRAME_TIME,
            wall_clock=lambda: FRAME_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("bootstrap no retry"),
            frame_executor=lambda **_: {**_frame_result(), "status": "sampled_gaps"},
        )
    stop = json.loads((root / "STOP.json").read_text())
    assert stop["reason"] == "reserved_attempt_failed_or_ambiguous"


def test_missed_frame_bucket_seals_gap_without_credentials_or_catch_up(tmp_path: Path) -> None:
    _, _, _, root, plan_path, gate_path, config = _call(tmp_path)
    with pytest.raises(runner.RunnerError, match="tick_not_preplanned_or_outside_window"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=FRAME_TIME.replace(hour=3),
            lock_root=tmp_path,
            process_inventory=lambda: pytest.fail("missed bucket is handled offline first"),
            bootstrap_executor=lambda **_: pytest.fail("no bootstrap replay"),
            frame_executor=lambda **_: pytest.fail("no catch-up frame"),
        )
    stop = json.loads((root / "STOP.json").read_text())
    assert stop["reason"] == "missed_pre_pinned_bucket"
    assert stop["gap"] == {
        "start": 455430201,
        "end_exclusive": None,
        "reason": "missed_pre_pinned_bucket_no_replay",
    }


def test_wrong_gate_and_bucket_fail_before_secret_load_or_requests(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    root = tmp_path / "service"
    missing = tmp_path / "does-not-exist"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    bad = json.loads(gate_path.read_text())
    bad["plan_sha256"] = "0" * 64
    _write_json(gate_path, bad)
    with pytest.raises(runner.RunnerError, match="activation_gate_missing_or_mismatched"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=missing,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: pytest.fail("gate first"),
            bootstrap_executor=lambda **_: pytest.fail("no request"),
            frame_executor=lambda **_: pytest.fail("no request"),
        )
    gate_path = tmp_path / "valid-gate.json"
    _gate(plan, gate_path)
    with pytest.raises(runner.RunnerError, match="tick_not_preplanned_or_outside_window"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=missing,
            now=datetime(2026, 10, 12, 1, 20, tzinfo=UTC),
            lock_root=tmp_path,
            process_inventory=lambda: pytest.fail("tick first"),
            bootstrap_executor=lambda **_: pytest.fail("no request"),
            frame_executor=lambda **_: pytest.fail("no request"),
        )
    assert not root.exists()


def test_disk_floor_failure_precedes_credential_read_and_reservation(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "not-created" / "service"
    monkeypatch.setattr(runner, "C5_SERVICE_ROOT", root)
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    missing_config = tmp_path / "missing-private-config"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    monkeypatch.setattr(
        runner,
        "_verify_disk_headroom",
        lambda _path, _required=runner.DISK_REQUIRED_BYTES: (_ for _ in ()).throw(
            runner.RunnerError("disk_floor_plus_full_pilot_envelope_not_met")
        ),
    )
    with pytest.raises(runner.RunnerError, match="disk_floor_plus_full_pilot_envelope_not_met"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=missing_config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("disk gate blocks request"),
            frame_executor=lambda **_: pytest.fail("disk gate blocks request"),
        )
    assert not root.exists()
    assert not root.parent.exists()


def test_disk_gate_keeps_v3_peak_margin_and_recorded_c5_composite() -> None:
    assert runner.V3_DISK_ENVELOPE_BYTES == (
        runner.schedule_v3.DISK_FLOOR_BYTES
        + runner.schedule_v3.FRAME_RAW_BYTES
        + runner.schedule_v3.FRAME_STORED_BYTES
        + runner.schedule_v3.PEAK_WRITE_MARGIN_BYTES
    )
    assert runner.DISK_REQUIRED_BYTES == 61_834_628_800
    assert runner.DISK_REQUIRED_BYTES >= (
        runner.V3_DISK_ENVELOPE_BYTES
        + runner.policy.BOOTSTRAP_RAW_BYTES
        + runner.policy.BOOTSTRAP_STORED_BYTES
    )


def test_fresh_disk_composite_controls_free_space_boundary(tmp_path: Path, monkeypatch) -> None:
    plan = _plan(tmp_path)
    gate_path = tmp_path / "gate.json"
    _gate(plan, gate_path)
    required = runner.verify_activation_gate(
        gate_path, plan, now_ns=int(BOOTSTRAP_TIME.timestamp() * 1_000_000_000)
    )
    assert required > runner.C5_RECORDED_COMPOSITE_DISK_REQUIRED_BYTES
    monkeypatch.setattr(runner, "_verify_disk_headroom", ORIGINAL_DISK_VERIFY)
    monkeypatch.setattr(
        runner.shutil, "disk_usage", lambda _path: type("D", (), {"free": required - 1})()
    )
    with pytest.raises(runner.RunnerError, match="disk_floor_plus_full_pilot_envelope_not_met"):
        runner._verify_disk_headroom(tmp_path, required)
    monkeypatch.setattr(
        runner.shutil, "disk_usage", lambda _path: type("D", (), {"free": required})()
    )
    assert runner._verify_disk_headroom(tmp_path, required) == required


def test_activation_gate_is_separate_and_offline_plan_stays_false(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert plan["activation_gates"]["activation_authorized"] is False
    with pytest.raises(runner.RunnerError, match="activation_gate_missing_or_mismatched"):
        runner.verify_activation_gate(tmp_path / "missing-gate.json", plan, now_ns=AUTH_NS + 2)


def test_synthetic_summaries_cannot_qualify_production_activation(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    gate_path = tmp_path / "synthetic-gate.json"
    _gate(plan, gate_path)
    with pytest.raises(runner.RunnerError, match="activation_gate_source_provenance_unavailable"):
        ORIGINAL_GATE_VERIFY(
            gate_path,
            plan,
            now_ns=int(BOOTSTRAP_TIME.timestamp() * 1_000_000_000),
        )


def test_activation_receipt_is_create_only_private_and_verifies_evidence(tmp_path: Path) -> None:
    receipt = tmp_path / "activation.json"
    runner.create_activation_receipt(receipt, {"reviewed": True})
    assert receipt.stat().st_mode & 0o777 == 0o600
    with pytest.raises(runner.RunnerError, match="activation_receipt_already_exists"):
        runner.create_activation_receipt(receipt, {"reviewed": False})


def test_activation_gate_rejects_tampered_account_evidence_and_expiry(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    gate_path = tmp_path / "gate.json"
    _gate(plan, gate_path)
    gate = runner._read_json(gate_path)
    account_path = Path(gate["account_evidence"]["path"])
    account = runner._read_json(account_path)
    account["remaining_request_headroom"] = 1
    _write_json(account_path, account)
    account_path.chmod(0o600)
    with pytest.raises(runner.RunnerError, match="activation_gate_evidence_hash_mismatch"):
        runner.verify_activation_gate(
            gate_path, plan, now_ns=int(BOOTSTRAP_TIME.timestamp() * 1_000_000_000)
        )
    valid_gate = tmp_path / "valid-gate.json"
    _gate(plan, valid_gate)
    expired_at = int((policy._utc_bucket(FRAME).timestamp() + 17 * 60) * 1_000_000_000)
    with pytest.raises(runner.RunnerError, match="activation_gate_decision_or_expiry_invalid"):
        runner.verify_activation_gate(valid_gate, plan, now_ns=expired_at)


def test_activation_gate_checks_account_and_dynamic_disk_evidence_predicates(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    now_ns = int(BOOTSTRAP_TIME.timestamp() * 1_000_000_000)
    account_gate = tmp_path / "account-low.json"
    _gate(plan, account_gate, account_overrides={"remaining_request_headroom": 34})
    with pytest.raises(runner.RunnerError, match="activation_gate_account_predicate_failed"):
        runner.verify_activation_gate(account_gate, plan, now_ns=now_ns)

    computed_required = max(
        runner.C5_RECORDED_COMPOSITE_DISK_REQUIRED_BYTES,
        runner.schedule_v3.DISK_FLOOR_BYTES
        + runner.schedule_v3.FRAME_RAW_BYTES
        + runner.schedule_v3.FRAME_STORED_BYTES
        + runner.schedule_v3.PEAK_WRITE_MARGIN_BYTES
        + policy.BOOTSTRAP_STORED_BYTES
        + policy.FRAME_STORED_BYTES
        + 8_000_000_000,
    )
    disk_gate = tmp_path / "disk-low.json"
    _gate(plan, disk_gate, disk_overrides={"measured_free_bytes": computed_required - 1})
    with pytest.raises(runner.RunnerError, match="activation_gate_disk_headroom_insufficient"):
        runner.verify_activation_gate(disk_gate, plan, now_ns=now_ns)


def test_activation_gate_fails_closed_without_substantiated_host_churn(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    gate_path = tmp_path / "churn-unsubstantiated.json"
    _gate(
        plan,
        gate_path,
        churn_overrides={"basis": "", "status": "not_substantiated"},
    )
    with pytest.raises(runner.RunnerError, match="activation_gate_churn_margin_unsubstantiated"):
        runner.verify_activation_gate(
            gate_path, plan, now_ns=int(BOOTSTRAP_TIME.timestamp() * 1_000_000_000)
        )


def test_interrupted_root_initialization_fails_before_credentials(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    root = tmp_path / "service"
    missing_config = tmp_path / "missing-config"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    (tmp_path / ".service.initializing").mkdir()
    with pytest.raises(runner.RunnerError, match="interrupted_initialization_requires_review"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=missing_config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("initialization marker prevents request"),
            frame_executor=lambda **_: pytest.fail("initialization marker prevents request"),
        )
    assert not root.exists()
    assert not (tmp_path / ".service.initializing" / "request-ledger.jsonl").exists()


def test_private_config_rejects_symlink_and_wrong_mode(tmp_path: Path) -> None:
    target = tmp_path / "config"
    target.write_text(_config_text())
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(runner.RunnerError, match="private_endpoint_config_invalid"):
        runner._load_endpoint(link)
    target.chmod(0o644)
    with pytest.raises(runner.RunnerError, match="private_endpoint_config_invalid"):
        runner._load_endpoint(target)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://invalid.example/?api-key=x",
        "https://user@mainnet.helius-rpc.com/?api-key=x",
        "https://mainnet.helius-rpc.com/?api-key=x#fragment",
        "https://mainnet.helius-rpc.com:8443/?api-key=x",
        "https://mainnet.helius-rpc.com/",
        "https://mainnet.helius-rpc.com/?api-key=x&api-key=y",
    ],
)
def test_private_endpoint_rejects_unqualified_helius_urls(tmp_path: Path, endpoint: str) -> None:
    config = tmp_path / "private.conf"
    config.write_text(_config_text(endpoint))
    config.chmod(0o600)
    with pytest.raises(runner.RunnerError, match="private_endpoint_config_invalid"):
        runner._load_endpoint(config)


def test_private_endpoint_uses_actual_helius_config_key(tmp_path: Path) -> None:
    config = tmp_path / "private.conf"
    config.write_text(_config_text())
    config.chmod(0o600)
    assert runner._load_endpoint(config) == VALID_ENDPOINT


def test_missing_private_config_stops_without_reservation_or_rpc(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    root = tmp_path / "service"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    with pytest.raises(runner.RunnerError, match="credential_unavailable_no_rpc"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=tmp_path / "missing-private-config",
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("credential gate prevents request"),
            frame_executor=lambda **_: pytest.fail("credential gate prevents request"),
        )
    assert runner._read_ledger(root / "request-ledger.jsonl") == []
    assert json.loads((root / "STOP.json").read_text())["reason"] == "credential_unavailable_no_rpc"


def test_host_lock_contention_prevents_second_request(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    gate_path = tmp_path / "gate.json"
    root = tmp_path / "service"
    config = tmp_path / "private.conf"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    config.write_text(_config_text())
    config.chmod(0o600)
    entered = threading.Event()
    release = threading.Event()
    results: list[str] = []

    def first_exec(**_):
        entered.set()
        release.wait(2)
        return _bootstrap_result()

    def first():
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=first_exec,
            frame_executor=lambda **_: _frame_result(),
        )

    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(2)
    with pytest.raises(runner.RunnerError, match="writer_already_active"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: results.append("second") or _bootstrap_result(),
            frame_executor=lambda **_: _frame_result(),
        )
    release.set()
    thread.join(3)
    assert not thread.is_alive() and results == []


def test_real_clock_recheck_stops_before_reservation(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path, gate_path = tmp_path / "plan.json", tmp_path / "gate.json"
    root, config = tmp_path / "service", tmp_path / "private.conf"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    config.write_text(_config_text())
    config.chmod(0o600)
    values = iter([BOOTSTRAP_TIME, datetime(2026, 10, 12, 1, 16, tzinfo=UTC)])
    with pytest.raises(runner.RunnerError, match="tick_window_expired_before_dispatch"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: next(values),
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("expired window cannot send"),
            frame_executor=lambda **_: pytest.fail("expired window cannot send"),
        )
    assert runner._read_ledger(root / "request-ledger.jsonl") == []
    assert json.loads((root / "STOP.json").read_text())["reason"] == (
        "tick_window_expired_before_reservation"
    )


def test_real_clock_recheck_after_reservation_spends_without_dispatch(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path, gate_path = tmp_path / "plan.json", tmp_path / "gate.json"
    root, config = tmp_path / "service", tmp_path / "private.conf"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    config.write_text(_config_text())
    config.chmod(0o600)
    values = iter(
        [
            BOOTSTRAP_TIME,
            BOOTSTRAP_TIME,
            datetime(2026, 10, 12, 1, 16, tzinfo=UTC),
        ]
    )
    with pytest.raises(runner.RunnerError, match="reserved_attempt_failed_or_ambiguous_stopped"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: next(values),
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("expired window cannot dispatch"),
            frame_executor=lambda **_: pytest.fail("expired window cannot dispatch"),
        )
    rows = runner._read_ledger(root / "request-ledger.jsonl")
    assert [row["kind"] for row in rows] == ["reservation"]
    assert json.loads((root / "STOP.json").read_text())["gap"]["end_exclusive"] is None


def test_disk_headroom_is_refreshed_after_reservation_before_adapter(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _plan(tmp_path)
    plan_path, gate_path = tmp_path / "plan.json", tmp_path / "gate.json"
    root, config = tmp_path / "service", tmp_path / "private.conf"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    config.write_text(_config_text())
    config.chmod(0o600)
    checks = 0

    def disk_check(_path, required=runner.DISK_REQUIRED_BYTES):
        nonlocal checks
        checks += 1
        if checks == 4:
            raise runner.RunnerError("synthetic_disk_drop_before_adapter")
        return required

    monkeypatch.setattr(runner, "_verify_disk_headroom", disk_check)
    with pytest.raises(runner.RunnerError, match="reserved_attempt_failed_or_ambiguous_stopped"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=lambda **_: pytest.fail("disk gate blocks dispatch"),
            frame_executor=lambda **_: pytest.fail("bootstrap tick cannot frame"),
        )
    assert checks == 4
    assert [row["kind"] for row in runner._read_ledger(root / "request-ledger.jsonl")] == [
        "reservation"
    ]


def test_first_durable_stop_is_never_overwritten_by_outer_failure(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path, gate_path = tmp_path / "plan.json", tmp_path / "gate.json"
    root, config = tmp_path / "service", tmp_path / "private.conf"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    config.write_text(_config_text())
    config.chmod(0o600)

    def stop_then_fail(**_):
        runner._atomic_json(root / "STOP.json", {"reason": "first_durable_stop"})
        raise RuntimeError("synthetic failure after STOP")

    with pytest.raises(runner.RunnerError, match="reserved_attempt_failed_or_ambiguous_stopped"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=lambda: _inventory(),
            bootstrap_executor=stop_then_fail,
            frame_executor=lambda **_: pytest.fail("bootstrap tick cannot frame"),
        )
    assert json.loads((root / "STOP.json").read_text())["reason"] == "first_durable_stop"


def test_status_is_read_only_and_missing_root_stays_missing(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    root = tmp_path / "missing-root"
    _write_json(plan_path, plan)
    result = runner.status_only(plan_path, root)
    assert result["status"] == "not_initialized" and result["root_exists"] is False
    assert not root.exists()


def test_process_preflight_rejects_any_other_senses_writer() -> None:
    expected = _inventory()
    runner._preflight_processes({runner.C5_LABEL}, lambda: expected)
    with pytest.raises(runner.RunnerError, match="senses_runtime_preflight_mismatch"):
        runner._preflight_processes(
            {runner.C5_LABEL},
            lambda: [
                *expected,
                {"kind": "process", "label": runner.C5_LABEL},
                {"kind": "process", "label": runner.C5_LABEL},
            ],
        )
    cases = [
        [*expected, {"kind": "launchd", "label": "ai.memecoin.brain.senses.unknown"}],
        [{**row, "ppid": 777} if row.get("kind") == "process" else row for row in expected],
        [row for row in expected if row.get("kind") != "disabled"],
        [row for row in expected if row.get("kind") != "namespace"],
        [*expected, {"kind": "malformed"}],
    ]
    for invalid in cases:
        with pytest.raises(runner.RunnerError):
            runner._preflight_processes({runner.C5_LABEL}, lambda invalid=invalid: invalid)


def test_host_inventory_is_sanitized_and_binds_gui_c4_and_runner_path(monkeypatch) -> None:
    launchd = _launchd_print(f"  123 (pe)\t{runner.C5_LABEL}")
    disabled = _disabled_print()
    command = f"123 1 {shlex.join(runner.C5_EXPECTED_ARGV)}"
    outputs = {"print": launchd, "print-disabled": disabled, "ps": command}

    def fake_run(argv, **kwargs):
        key = "ps" if argv[0].endswith("/ps") else argv[1]
        return subprocess.CompletedProcess(argv, 0, stdout=outputs[key], stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    records = runner._host_process_inventory()
    runner._preflight_processes({runner.C5_LABEL}, lambda: records)
    assert "synthetic-secret" not in repr(records)
    assert all("command" not in row and "argv" not in row for row in records)

    outputs["ps"] = command + " --unexpected-secret hidden-secret"
    records = runner._host_process_inventory()
    assert "hidden-secret" not in repr(records)
    with pytest.raises(runner.RunnerError, match="senses_runtime_preflight_mismatch"):
        runner._preflight_processes({runner.C5_LABEL}, lambda: records)


def test_host_inventory_errors_and_malformed_launchd_fail_closed(monkeypatch) -> None:
    def fail_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(runner.subprocess, "run", fail_run)
    with pytest.raises(runner.RunnerError, match="senses_runtime_inventory_unavailable"):
        runner._host_process_inventory()
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="bad", stderr=""),
    )
    with pytest.raises(runner.RunnerError, match="senses_runtime_inventory_malformed"):
        runner._host_process_inventory()


def test_process_preflight_checks_known_disabled_set_exact_pid_and_path_only_argv() -> None:
    expected = _inventory()
    runner._preflight_processes({runner.C5_LABEL}, lambda: expected)
    cases = [
        [*expected, {"kind": "disabled", "label": runner.C4_LABEL, "disabled": True}],
        [*expected, {"kind": "launchd", "label": runner.C4_LABEL, "pid": 456}],
        [*expected, {"kind": "disabled", "label": runner.C5_LABEL, "disabled": True}],
        [{**row, "pid": 456} if row.get("kind") == "launchd" else row for row in expected],
        [
            {**row, "argv_matches_expected": False} if row.get("kind") == "process" else row
            for row in expected
        ],
        [
            *[
                row
                for row in expected
                if not (row.get("kind") == "disabled" and row.get("label") == runner.C4_LABEL)
            ],
            {
                "kind": "disabled",
                "label": "ai.memecoin.brain.senses.unrecognized",
                "disabled": True,
            },
        ],
    ]
    for invalid in cases:
        with pytest.raises(runner.RunnerError, match="senses_runtime_preflight_mismatch"):
            runner._preflight_processes({runner.C5_LABEL}, lambda invalid=invalid: invalid)


def test_c5_uses_same_host_writer_lock_as_service_v1(tmp_path: Path) -> None:
    assert (
        runner._host_writer_lock_path(runner.REPO_ROOT) == runner.service_v1_runner.HOST_WRITER_LOCK
    )
    assert runner._host_writer_lock_path(tmp_path) == (
        tmp_path / runner.service_v1_runner.HOST_WRITER_LOCK.relative_to(runner.REPO_ROOT)
    )


def test_service_v1_shared_host_lock_blocks_c5_writer(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path, gate_path = tmp_path / "plan.json", tmp_path / "gate.json"
    root, config = tmp_path / "service", tmp_path / "private.conf"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    config.write_text(_config_text())
    config.chmod(0o600)
    shared_lock = runner._host_writer_lock_path(tmp_path)
    with runner.service_v1_runner._exclusive_lock(
        shared_lock, "synthetic_service_v1_writer_active"
    ):
        with pytest.raises(runner.RunnerError, match="writer_already_active"):
            runner.run_scheduled_tick(
                plan_path=plan_path,
                gate_path=gate_path,
                root=root,
                config_path=config,
                now=BOOTSTRAP_TIME,
                wall_clock=lambda: BOOTSTRAP_TIME,
                lock_root=tmp_path,
                process_inventory=lambda: _inventory(),
                bootstrap_executor=lambda **_: pytest.fail("shared lock prevents request"),
                frame_executor=lambda **_: pytest.fail("bootstrap tick cannot frame"),
            )
    assert not root.exists()


def test_writer_appearing_after_initial_check_stops_before_config_or_reservation(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _plan(tmp_path)
    plan_path, gate_path = tmp_path / "plan.json", tmp_path / "gate.json"
    root = tmp_path / "service"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    inventory_calls = 0

    def inventory():
        nonlocal inventory_calls
        inventory_calls += 1
        rows = _inventory()
        if inventory_calls > 1:
            rows.append({"kind": "process", "label": "manual_senses_writer"})
        return rows

    monkeypatch.setattr(
        runner,
        "_load_endpoint",
        lambda _path: pytest.fail("changed runtime must fail before private config"),
    )
    with pytest.raises(runner.RunnerError, match="senses_runtime_preflight_mismatch"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=tmp_path / "private.conf",
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=inventory,
            bootstrap_executor=lambda **_: pytest.fail("runtime mismatch blocks dispatch"),
            frame_executor=lambda **_: pytest.fail("runtime mismatch blocks dispatch"),
        )
    assert inventory_calls == 2
    assert runner._read_ledger(root / "request-ledger.jsonl") == []
    assert (root / "STOP.json").is_file()


def test_writer_appearing_before_adapter_handoff_never_reaches_executor(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan_path, gate_path = tmp_path / "plan.json", tmp_path / "gate.json"
    root, config = tmp_path / "service", tmp_path / "private.conf"
    _write_json(plan_path, plan)
    _gate(plan, gate_path)
    config.write_text(_config_text())
    config.chmod(0o600)
    inventory_calls = 0

    def inventory():
        nonlocal inventory_calls
        inventory_calls += 1
        rows = _inventory()
        if inventory_calls > 4:
            rows.append({"kind": "process", "label": "manual_senses_writer"})
        return rows

    with pytest.raises(runner.RunnerError, match="reserved_attempt_failed_or_ambiguous_stopped"):
        runner.run_scheduled_tick(
            plan_path=plan_path,
            gate_path=gate_path,
            root=root,
            config_path=config,
            now=BOOTSTRAP_TIME,
            wall_clock=lambda: BOOTSTRAP_TIME,
            lock_root=tmp_path,
            process_inventory=inventory,
            bootstrap_executor=lambda **_: pytest.fail("runtime mismatch blocks handoff"),
            frame_executor=lambda **_: pytest.fail("runtime mismatch blocks handoff"),
        )
    assert inventory_calls == 5
    rows = runner._read_ledger(root / "request-ledger.jsonl")
    assert [row["kind"] for row in rows] == ["reservation"]
    stop = runner._read_json(root / "STOP.json")
    assert stop["gap"]["end_exclusive"] is None


def test_template_uses_path_only_args_and_disables_implicit_launch(tmp_path: Path) -> None:
    path = runner.REPO_ROOT / runner.PLIST_TEMPLATE
    document = plistlib.loads(path.read_bytes())
    assert document["Label"] == runner.C5_LABEL
    assert document["RunAtLoad"] is False and document["KeepAlive"] is False
    args = document["ProgramArguments"]
    assert "run" in args and all(
        "https://" not in item and "api-key" not in item.lower() for item in args
    )
    assert args[-1].endswith(".env.local")


def test_production_cli_rejects_overridden_paths_before_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        runner.sys,
        "argv",
        [
            "runner",
            "run",
            "--plan",
            str(tmp_path / "plan.json"),
            "--gate",
            str(tmp_path / "gate.json"),
            "--root",
            str(tmp_path / "root"),
            "--private-config",
            str(tmp_path / "private.conf"),
        ],
    )
    with pytest.raises(runner.RunnerError, match="scheduled_runner_paths_are_fixed"):
        runner._cli()


def test_plan_source_or_external_predecessor_drift_rejected(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    bad = {
        **plan,
        "external_predecessor_binding": {
            **plan["external_predecessor_binding"],
            "launchd_label": "wrong",
        },
    }
    body = {key: value for key, value in bad.items() if key != "plan_sha256"}
    bad["plan_sha256"] = runner._sha_bytes(runner._canonical(body))
    with pytest.raises(runner.RunnerError, match="external_predecessor_binding_mismatch"):
        runner.verify_plan(bad)
