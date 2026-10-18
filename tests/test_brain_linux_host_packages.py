"""Fail-closed ordinary-host package adapter tests; no real APT transaction."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import brain_linux_host_packages as host


def test_nspawn_host_error_is_precise_and_reuses_lab_adapter(monkeypatch):
    monkeypatch.setattr(host.shared, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    monkeypatch.setattr(host.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        host.pwd, "getpwnam", lambda _name: type("P", (), {"pw_uid": 1000, "pw_gid": 1000})()
    )
    monkeypatch.setattr(host.os, "access", lambda *_args: True)
    monkeypatch.setattr(host, "KEYRING", Path(__file__))
    monkeypatch.setattr(host, "DPKG_STATUS", Path(__file__))
    monkeypatch.setattr(
        host, "_run", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "systemd-nspawn\n", "")
    )
    with pytest.raises(
        host.HostProvisionError,
        match=r"systemd_nspawn_container_refused: use brain_linux_packages.py --lab-only",
    ):
        host._host_identity()


def test_archive_keyring_missing_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(host.shared, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    monkeypatch.setattr(host.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        host.pwd, "getpwnam", lambda _name: type("P", (), {"pw_uid": 1000, "pw_gid": 1000})()
    )
    monkeypatch.setattr(host, "KEYRING", tmp_path / "missing-keyring")
    monkeypatch.setattr(host.os, "access", lambda path, _mode: str(path) != str(host.KEYRING))
    monkeypatch.setattr(host, "_systemd_container", lambda: None)
    with pytest.raises(host.HostProvisionError, match="ubuntu_archive_keyring_missing"):
        host._host_identity()


def test_mutating_package_actions_require_root(monkeypatch):
    monkeypatch.setattr(host.os, "geteuid", lambda: 1000)
    with pytest.raises(host.HostProvisionError, match="root_required"):
        host.refresh_indexes("20261018T120000Z", "a" * 40)
    with pytest.raises(host.HostProvisionError, match="root_required"):
        host.create_plan("source-build", "20261018T120000Z", "a" * 40, Path("/tmp/source"))
    with pytest.raises(host.HostProvisionError, match="root_required"):
        host.apply_plan(Path("/tmp/not-a-plan.json"), "b" * 64)


def test_journal_snapshot_returns_digest_and_signal_without_raw_entries(monkeypatch):
    payload = json.dumps({"MESSAGE": "Started example.service - synthetic secret marker"})
    monkeypatch.setattr(
        host,
        "_run",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 0, payload + "\n", ""),
    )
    digest, service_activity = host._journal_snapshot("now")
    assert digest == host._sha((payload + "\n").encode())
    assert service_activity is True
    assert "synthetic secret marker" not in repr((digest, service_activity))


@pytest.mark.parametrize(
    "unit",
    [
        "brain-worker.service",
        "memecoin-brain-lab.service",
        "memecoin-brain-" + "a" * 64 + ".service",
    ],
)
def test_preexisting_brain_units_are_refused(monkeypatch, unit):
    calls = iter(
        [
            subprocess.CompletedProcess([], 0, f"{unit} loaded active running\n", ""),
            subprocess.CompletedProcess([], 0, f"{unit} enabled enabled\n", ""),
        ]
    )
    monkeypatch.setattr(host, "_run", lambda *_a, **_kw: next(calls))
    with pytest.raises(host.HostProvisionError, match="preexisting_brain_unit_refused"):
        host._unit_inventory()


def test_host_solver_uses_host_source_and_refuses_removals(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "SOURCE", tmp_path / "ordinary.sources")
    monkeypatch.setattr(host, "LISTS", tmp_path / "ordinary-lists")
    seen = []

    def run(argv, **_kwargs):
        seen.append(argv)
        if "policy" in argv:
            return subprocess.CompletedProcess(argv, 0, "Candidate: 1.0\n", "")
        if "depends" in argv:
            return subprocess.CompletedProcess(argv, 0, "git\n", "")
        if "--simulate" in argv:
            return subprocess.CompletedProcess(argv, 0, "Remv unrelated [1]\n", "")
        raise AssertionError(argv)

    monkeypatch.setattr(host, "_run", run)
    assert host._candidate("git") == "1.0"
    assert str(host.SOURCE) in " ".join(seen[0])
    assert str(host.LISTS) in " ".join(seen[0])
    with pytest.raises(host.HostProvisionError, match="package_removal_refused"):
        host._simulate({"git": "1.0"}, {})

    def unknown_inst(argv, **_kwargs):
        if "depends" in argv:
            return subprocess.CompletedProcess(argv, 0, "git\n", "")
        return subprocess.CompletedProcess(argv, 0, "Inst malformed solver output\n", "")

    monkeypatch.setattr(host, "_run", unknown_inst)
    with pytest.raises(host.HostProvisionError, match="unrecognized_solver_transaction"):
        host._simulate({"git": "1.0"}, {})


def test_external_apt_lock_contention_is_refused(monkeypatch, tmp_path):
    lock = tmp_path / "dpkg-lock"
    lock.touch()
    original_path = Path
    monkeypatch.setattr(
        host,
        "Path",
        lambda value: lock if str(value) == "/var/lib/dpkg/lock-frontend" else original_path(value),
    )
    monkeypatch.setattr(
        host.fcntl, "lockf", lambda *_args: (_ for _ in ()).throw(BlockingIOError())
    )
    with pytest.raises(host.HostProvisionError, match="apt_lock_busy"):
        host._require_apt_unlocked()


def test_snapshot_keyring_drift_is_refused(monkeypatch, tmp_path):
    receipt = tmp_path / "indexes-20261018T120000Z.json"
    receipt.write_text(json.dumps({"keyring_sha256": "old"}), encoding="utf-8")
    receipt.chmod(0o600)
    monkeypatch.setattr(host, "RECEIPTS", tmp_path)
    monkeypatch.setattr(host, "KEYRING", Path(__file__))
    monkeypatch.setattr(host, "_read_private", lambda _path: {"keyring_sha256": "old"})
    monkeypatch.setattr(host, "_file_sha", lambda path: "new" if path == host.KEYRING else "unused")
    with pytest.raises(host.HostProvisionError, match="archive_keyring_drift"):
        host._require_snapshot("20261018T120000Z")


def test_snapshot_source_and_index_drift_are_refused(monkeypatch, tmp_path):
    snapshot = "20261018T120000Z"
    source = tmp_path / "ubuntu.sources"
    source.write_bytes(host._sources(snapshot))
    lists = tmp_path / "lists"
    lists.mkdir()
    (lists / "noble_InRelease").write_bytes(b"changed")
    receipt = tmp_path / f"indexes-{snapshot}.json"
    receipt.write_text(
        json.dumps({"keyring_sha256": "key", "source_sha256": "bad", "indexes": {}}),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    monkeypatch.setattr(host, "RECEIPTS", tmp_path)
    monkeypatch.setattr(host, "SOURCE", source)
    monkeypatch.setattr(host, "LISTS", lists)
    monkeypatch.setattr(host, "KEYRING", Path(__file__))
    monkeypatch.setattr(
        host,
        "_read_private",
        lambda _path: {
            "keyring_sha256": "key",
            "source_sha256": "bad",
            "indexes": {},
            "host": {"id": "host"},
            "brain_units": {},
        },
    )
    monkeypatch.setattr(
        host,
        "_file_sha",
        lambda path: "key" if path == host.KEYRING else host._sha(path.read_bytes()),
    )
    monkeypatch.setattr(
        host.pwd, "getpwnam", lambda _name: type("P", (), {"pw_uid": os.getuid()})()
    )
    monkeypatch.setattr(host, "_host_identity", lambda: {"id": "host"})
    monkeypatch.setattr(host, "_unit_inventory", lambda: {})
    with pytest.raises(host.HostProvisionError, match="snapshot_source_drift"):
        host._require_snapshot(snapshot)


def test_snapshot_index_drift_is_refused(monkeypatch, tmp_path):
    snapshot = "20261018T120000Z"
    source = tmp_path / "ubuntu.sources"
    source.write_bytes(host._sources(snapshot))
    lists = tmp_path / "lists"
    lists.mkdir()
    (lists / "noble_InRelease").write_bytes(b"current")
    (tmp_path / f"indexes-{snapshot}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(host, "RECEIPTS", tmp_path)
    monkeypatch.setattr(host, "SOURCE", source)
    monkeypatch.setattr(host, "LISTS", lists)
    monkeypatch.setattr(host, "KEYRING", Path(__file__))
    monkeypatch.setattr(host, "_file_sha", lambda path: host._sha(path.read_bytes()))
    monkeypatch.setattr(
        host,
        "_read_private",
        lambda _path: {
            "keyring_sha256": host._file_sha(host.KEYRING),
            "source_sha256": host._file_sha(source),
            "indexes": {"noble_InRelease": "old"},
            "host": {"id": "host"},
            "brain_units": {},
        },
    )
    monkeypatch.setattr(
        host.pwd, "getpwnam", lambda _name: type("P", (), {"pw_uid": os.getuid()})()
    )
    monkeypatch.setattr(host, "_host_identity", lambda: {"id": "host"})
    monkeypatch.setattr(host, "_unit_inventory", lambda: {})
    with pytest.raises(host.HostProvisionError, match="snapshot_index_drift"):
        host._require_snapshot(snapshot)


def test_interrupted_apply_intent_blocks_replay(monkeypatch, tmp_path):
    monkeypatch.setattr(host.os, "geteuid", lambda: 0)
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    monkeypatch.setattr(host, "ROOT", tmp_path)
    monkeypatch.setattr(host, "RECEIPTS", receipts)
    monkeypatch.setattr(host, "_host_identity", lambda: {"machine_id_sha256": "h"})
    monkeypatch.setattr(host, "_ensure_paths", lambda: None)
    lock = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    monkeypatch.setattr(host, "_locked", lambda: os.dup(lock))
    plan = {"schema": host.SCHEMA, "tool_sha256": "unused"}
    digest = host._sha(host._canonical(plan))
    plan["plan_sha256"] = digest
    (receipts / f"apply-intent-{digest}.json").touch(mode=0o600)
    monkeypatch.setattr(host, "_read_private", lambda _path: plan)
    with pytest.raises(host.HostProvisionError, match="interrupted_apply_requires_manual_review"):
        host.apply_plan(receipts / "plan.json", digest)
    os.close(lock)


@pytest.mark.parametrize(
    "drift, expected",
    [
        ("host", "host_identity_drift"),
        ("package", "installed_package_state_drift"),
    ],
)
def test_apply_rejects_bound_host_or_dpkg_drift(monkeypatch, tmp_path, drift, expected):
    monkeypatch.setattr(host.os, "geteuid", lambda: 0)
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    status = tmp_path / "dpkg-status"
    status.write_text("current", encoding="utf-8")
    monkeypatch.setattr(host, "RECEIPTS", receipts)
    monkeypatch.setattr(host, "DPKG_STATUS", status)
    monkeypatch.setattr(host, "_ensure_paths", lambda: None)
    lock = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    monkeypatch.setattr(host, "_locked", lambda: os.dup(lock))
    host_reads = iter(
        [
            {"machine_id_sha256": "same"},
            {"machine_id_sha256": "changed" if drift == "host" else "same"},
        ]
    )
    monkeypatch.setattr(host, "_host_identity", lambda: next(host_reads))
    monkeypatch.setattr(host, "_unit_inventory", lambda: {})
    monkeypatch.setattr(host, "_active_units", lambda: {})
    monkeypatch.setattr(host, "_require_snapshot", lambda _snap: {})
    monkeypatch.setattr(host, "_read_private", lambda _path: plan)
    plan = {
        "schema": host.SCHEMA,
        "tool_sha256": host._file_sha(Path(host.__file__)),
        "shared_tool_sha256": host._file_sha(Path(host.shared.__file__)),
        "snapshot": "20261018T120000Z",
        "host": {"machine_id_sha256": "same"},
        "active_units": {},
        "dpkg_status_sha256": "wrong" if drift == "package" else host._file_sha(status),
        "installed": {},
        "package_tool_versions": {},
    }
    digest = host._sha(host._canonical(plan))
    plan["plan_sha256"] = digest
    with pytest.raises(host.HostProvisionError, match=expected):
        host.apply_plan(receipts / f"plan-{digest}.json", digest)
    os.close(lock)


def test_mocked_successful_apply_is_digest_bound_and_seals_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(host.os, "geteuid", lambda: 0)
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    monkeypatch.setattr(host, "ROOT", tmp_path)
    monkeypatch.setattr(host, "RECEIPTS", receipts)
    source = tmp_path / "sources"
    source.write_text("sources", encoding="utf-8")
    status = tmp_path / "dpkg-status"
    status.write_text("before", encoding="utf-8")
    keyring = tmp_path / "keyring"
    keyring.write_text("key", encoding="utf-8")
    monkeypatch.setattr(host, "SOURCE", source)
    monkeypatch.setattr(host, "DPKG_STATUS", status)
    monkeypatch.setattr(host, "KEYRING", keyring)
    monkeypatch.setattr(host, "POLICY_RC", tmp_path / "policy-rc.d")
    monkeypatch.setattr(
        host,
        "_host_identity",
        lambda: {"machine_id_sha256": "host", "keyring_sha256": host._sha(b"key")},
    )
    monkeypatch.setattr(host, "_ensure_paths", lambda: None)
    lock = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    monkeypatch.setattr(host, "_locked", lambda: os.dup(lock))
    monkeypatch.setattr(host, "_read_private", lambda _path: plan)
    monkeypatch.setattr(
        host,
        "_write_once",
        lambda path, value: path.write_text(json.dumps(value), encoding="utf-8"),
    )
    monkeypatch.setattr(host, "_require_snapshot", lambda _snap: {})
    monkeypatch.setattr(host, "_unit_inventory", lambda: {})
    monkeypatch.setattr(host, "_active_units", lambda: {})
    requested = {
        name: f"{index + 1}.0" for index, name in enumerate(host.shared.ROLES["inert-service-host"])
    }
    transaction = [
        {"package": name, "from_version": "", "version": version}
        for name, version in requested.items()
    ]
    installed_reads = iter([{}, {}, requested])
    monkeypatch.setattr(host, "_installed", lambda: next(installed_reads))
    monkeypatch.setattr(host, "_tool_versions", lambda: {"apt": "apt", "dpkg": "dpkg"})
    monkeypatch.setattr(host, "_index_inventory", lambda: {"noble_InRelease": "index"})
    monkeypatch.setattr(host, "_capability", lambda *_a: {"fingerprint": {"ready": True}})
    monkeypatch.setattr(host, "_simulate", lambda *_a: (transaction, "solution"))
    monkeypatch.setattr(host, "_journal_snapshot", lambda _since: ("same", False))
    monkeypatch.setattr(
        host, "_run", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "installed", "")
    )
    workspace = tmp_path / "checkout"
    capability_tool = workspace / "src/memecoin_operator/linux_capabilities.py"
    capability_tool.parent.mkdir(parents=True)
    capability_tool.write_text("tool", encoding="utf-8")
    plan = {
        "schema": host.SCHEMA,
        "role": "inert-service-host",
        "snapshot": "20261018T120000Z",
        "source_commit": "a" * 40,
        "workspace": str(workspace),
        "host": {"machine_id_sha256": "host", "keyring_sha256": host._sha(b"key")},
        "tool_sha256": host._file_sha(Path(host.__file__)),
        "shared_tool_sha256": host._file_sha(Path(host.shared.__file__)),
        "source_capability_tool_sha256": host._sha(b"tool"),
        "capability_fingerprint": {"ready": True},
        "package_tool_versions": {"apt": "apt", "dpkg": "dpkg"},
        "source_sha256": host._sha(b"sources"),
        "index_receipt_sha256": "receipt",
        "indexes": {"noble_InRelease": "index"},
        "dpkg_status_sha256": host._sha(b"before"),
        "installed": {},
        "brain_units": {},
        "requested": requested,
        "active_units": {},
        "transaction": transaction,
        "simulation_sha256": host._sha(b"solution"),
    }
    digest = host._sha(host._canonical(plan))
    plan["plan_sha256"] = digest
    result = host.apply_plan(receipts / f"plan-{digest}.json", digest)
    os.close(lock)
    assert result["status"] == "applied"
    assert Path(result["receipt"]).is_file()
    assert not host.POLICY_RC.exists()
