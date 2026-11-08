"""Synthetic attachment runtime receipts accept only pinned safe local state."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from memecoin_operator import control, linux_attachment
from memecoin_operator.linux_attachment import (
    EXPECTED_CONFIG,
    ROOT_ACTION_ERROR_SCHEMA,
    RUNTIME_DIRECTORIES,
    _prepare_runtime_directories,
    _publish_attachment_directory,
    _rename_noreplace,
    _root_action_error_code,
    _root_call,
    _runtime_inventory,
    _schema_fingerprint,
    _verify_idle_database,
)
from memecoin_research_storage import StorageError


def _root_error(action, code):
    return subprocess.CompletedProcess(
        args=["python", "-m", "memecoin_operator.linux_attachment"],
        returncode=2,
        stdout=json.dumps(
            {"schema": ROOT_ACTION_ERROR_SCHEMA, "action": action, "code": code},
            sort_keys=True,
        ),
        stderr="private path and secret-like text must never be forwarded",
    )


def test_root_action_error_protocol_accepts_a_known_code_without_stderr():
    result = _root_error("verify-root", "attachment_activation_incomplete")
    assert _root_action_error_code(result, "verify-root") == "attachment_activation_incomplete"


@pytest.mark.parametrize(
    "result,action",
    [
        (
            subprocess.CompletedProcess(
                [], 2, "", "linux_attachment_error: attachment_unit_failed"
            ),
            "verify-root",
        ),
        (_root_error("finalize-root", "attachment_unit_failed"), "verify-root"),
        (_root_error("verify-root", "arbitrary_path_or_secret"), "verify-root"),
        (subprocess.CompletedProcess([], 1, "", "sudo: a password is required"), "verify-root"),
        (subprocess.CompletedProcess([], 2, "not json", ""), "verify-root"),
    ],
)
def test_root_action_error_protocol_rejects_untrusted_or_malformed_output(result, action):
    assert _root_action_error_code(result, action) == "root_action_failed"


def test_root_action_error_protocol_rejects_oversized_output():
    result = subprocess.CompletedProcess([], 2, " " * 513, "")
    assert _root_action_error_code(result, "verify-root") == "root_action_failed"


def test_root_call_maps_only_the_allowlisted_failure(monkeypatch):
    result = _root_error("verify-root", "attachment_activation_incomplete")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: result)
    with pytest.raises(
        StorageError,
        match="attachment_root_action_failed:attachment_activation_incomplete",
    ):
        _root_call("a" * 64, ["verify-root"])


def test_root_command_emits_only_the_static_error_envelope(monkeypatch, capsys):
    monkeypatch.setattr(linux_attachment.os, "geteuid", lambda: 0)

    def fail(*_args):
        raise StorageError("attachment_unit_failed")

    monkeypatch.setattr(linux_attachment, "verify_attached", fail)
    result = linux_attachment.main(
        [
            "verify-root",
            "--attachment-id",
            "fixture",
            "--release-id",
            "a" * 64,
            "--release-manifest-sha256",
            "b" * 64,
            "--restore-manifest-sha256",
            "c" * 64,
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "schema": ROOT_ACTION_ERROR_SCHEMA,
        "action": "verify-root",
        "code": "attachment_unit_failed",
    }


def test_root_call_rejects_invalid_release_id_before_dispatch(monkeypatch):
    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("invalid release id reached subprocess dispatch")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    with pytest.raises(StorageError, match="attachment_release_id_invalid"):
        _root_call("../not-a-release", ["verify-root"])


def _runtime_tree(root):
    (root / "config.json").write_bytes(b"{}\n")
    (root / "config.json").chmod(0o600)
    (root / "operator.sqlite").write_bytes(b"sqlite-fixture")
    (root / "operator.sqlite").chmod(0o600)
    for directory in (root / "background", root / "operator"):
        directory.mkdir(mode=0o700)


def test_runtime_inventory_hashes_a_stopped_attachment_tree(tmp_path):
    _runtime_tree(tmp_path)
    inventory = _runtime_inventory(tmp_path, os.getuid(), os.getgid())
    assert [entry["path"] for entry in inventory] == [
        "background",
        "config.json",
        "operator",
        "operator.sqlite",
    ]
    assert next(entry for entry in inventory if entry["path"] == "config.json")["mode"] == "0600"


def test_runtime_inventory_rejects_sqlite_sidecars(tmp_path):
    _runtime_tree(tmp_path)
    (tmp_path / "operator.sqlite-wal").write_bytes(b"unqualified")
    (tmp_path / "operator.sqlite-wal").chmod(0o600)
    with pytest.raises(StorageError, match="attachment_runtime_root_members_mismatch"):
        _runtime_inventory(tmp_path, os.getuid(), os.getgid())


def test_initial_runtime_directory_layout_is_complete_and_private(tmp_path):
    root = tmp_path / "operator"
    root.mkdir(mode=0o700)
    _prepare_runtime_directories(root, os.getuid(), os.getgid())
    for relative in (
        "config.json",
        "operator.sqlite",
        "operator/owner.lock",
        "operator/logs/operator.log",
    ):
        path = root / relative
        path.write_bytes(b"synthetic")
        path.chmod(0o600)
    inventory = _runtime_inventory(root, os.getuid(), os.getgid())
    assert {item["path"] for item in inventory if item["kind"] == "directory"} == (
        RUNTIME_DIRECTORIES
    )


@pytest.mark.skipif(sys.platform != "linux", reason="renameat2 is Linux-specific")
def test_attachment_publication_never_replaces_existing_directory(tmp_path):
    source = tmp_path / ".pending"
    destination = tmp_path / "attachment"
    source.mkdir()
    destination.mkdir()
    (destination / "preserve").write_text("existing")
    with pytest.raises(StorageError, match="attachment_id_already_exists"):
        _rename_noreplace(tmp_path, source.name, destination.name)
    assert source.is_dir()
    assert (destination / "preserve").read_text() == "existing"


@pytest.mark.skipif(sys.platform != "linux", reason="renameat2 is Linux-specific")
def test_attachment_collision_removes_only_its_staging_directory(tmp_path):
    source = tmp_path / ".pending"
    destination = tmp_path / "attachment"
    source.mkdir()
    (source / "payload").write_text("temporary")
    destination.mkdir()
    (destination / "preserve").write_text("existing")
    with pytest.raises(StorageError, match="attachment_id_already_exists"):
        _publish_attachment_directory(source, destination)
    assert not source.exists()
    assert (destination / "preserve").read_text() == "existing"


def test_post_start_verifier_accepts_only_the_expected_idle_maintenance_event(tmp_path):
    path = tmp_path / "operator.sqlite"
    database = sqlite3.connect(path)
    try:
        control._schema(database)
        state = {
            "mode": "idle",
            "phase": "idle",
            "collect_profile": "balanced",
            "selected_candidate_id": None,
            "active_exam_id": None,
            "resume_mode": None,
            "auto_reason": None,
            "cycle": control._cycle_default(),
        }
        control._push(database, state, "initialized", 1)
        database.execute("INSERT INTO meta(key,value) VALUES('last_maintain_ns','2')")
        database.execute(
            "INSERT INTO events(kind,reason,payload_json,created_ns) "
            "VALUES('incident','adapter_error','{}',3)"
        )
        database.commit()
    finally:
        database.close()
    payload = path.read_bytes()
    expected_schema = _schema_fingerprint()
    with pytest.raises(StorageError, match="attachment_state_has_pending_metadata"):
        _verify_idle_database(payload, expected_schema)
    _verify_idle_database(payload, expected_schema, allow_startup_maintenance=True)


def test_linux_synthetic_idle_maintenance_is_a_bounded_noop(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    monkeypatch.setattr(control.sys, "platform", "linux")

    def unexpected_adapter():
        raise AssertionError("Linux synthetic idle must not load the Mac-oriented adapter")

    monkeypatch.setattr(control, "load_adapter", unexpected_adapter)
    assert control._maintain(root, {"mode": "idle", "phase": "idle"}) == {
        "ok": True,
        "reason": "synthetic_idle_noop",
    }
    assert control._maintain(root, {"mode": "collect", "phase": "collect"}) == {
        "ok": False,
        "reason": "linux_synthetic_idle_only",
    }


def test_linux_synthetic_idle_rejects_mutating_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "linux")
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)
    result = control.command(root, "synthetic-collect", "collect", {"profile": "balanced"})
    assert result["ok"] is False
    assert result["reason"] == "linux_synthetic_idle_only"


def test_linux_tick_and_status_never_load_adapter_after_config_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "linux")
    root = tmp_path / "state"
    root.mkdir()
    config_path = root / "config.json"
    config_path.write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)

    def unexpected_adapter():
        raise AssertionError("Linux operator path must not load the generic adapter")

    monkeypatch.setattr(control, "load_adapter", unexpected_adapter)
    assert control._load_view(root)["warning"] == "linux_synthetic_idle_only"
    assert control.tick(root)["mode"] == "idle"
    assert control.status(root)["mode"] == "idle"

    config_path.write_text(
        json.dumps(
            {key: value for key, value in EXPECTED_CONFIG.items() if key != "maintenance_policy"}
        )
    )
    assert control._load_view(root)["warning"] == "linux_unsupported_mode"
    assert control.tick(root)["mode"] == "idle"
    rejected = control.command(root, "drifted-collect", "collect", {"profile": "balanced"})
    assert rejected["reason"] == "linux_unsupported_mode"
    assert (
        control.command(root, "synthetic-collect", "collect", {"profile": "balanced"})["reason"]
        == "linux_unsupported_mode"
    )


def test_linux_policy_string_does_not_change_macos_adapter_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "darwin")
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)
    assert control._load_view(root)["warning"] == "linux_policy_platform_mismatch"
    assert control._maintain(root, {"mode": "idle", "phase": "idle"}) == {
        "ok": False,
        "reason": "linux_policy_platform_mismatch",
    }


def test_linux_tick_does_not_advance_a_nonidle_database(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "linux")
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)
    db = control._connect(root)
    try:
        db.execute("UPDATE revisions SET mode='collect',phase='collect' WHERE revision=1")
        db.commit()
    finally:
        db.close()

    def unexpected_adapter():
        raise AssertionError("unsupported Linux state must not load the generic adapter")

    monkeypatch.setattr(control, "load_adapter", unexpected_adapter)
    assert control.tick(root)["mode"] == "collect"


def test_linux_synthetic_stop_is_local_and_does_not_load_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "linux")
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)

    def unexpected_adapter():
        raise AssertionError("Linux synthetic stop must not load the generic adapter")

    monkeypatch.setattr(control, "load_adapter", unexpected_adapter)
    result = control.command(root, "idle-stop", "stop", {"confirm": True})
    assert result["ok"] is True
    assert result["mode"] == "stopped"


def _mock_provision_environment(tmp_path, monkeypatch):
    parent = tmp_path / "var" / "lib"
    parent.mkdir(parents=True)
    parent.chmod(0o755)
    lab_base = parent / "brain-lab"
    monkeypatch.setattr(linux_attachment, "LAB_BASE", lab_base)
    monkeypatch.setattr(linux_attachment, "_validate_lab_parent", lambda _parent: None)
    monkeypatch.setattr(linux_attachment.os, "geteuid", lambda: 0)
    monkeypatch.setattr(linux_attachment, "_is_mountpoint", lambda _path: False)
    monkeypatch.setattr(
        linux_attachment.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()),
    )
    return parent, lab_base


@pytest.mark.skipif(sys.platform != "linux", reason="renameat2 is Linux-specific")
def test_provision_base_creates_private_brain_directory_and_is_idempotent(tmp_path, monkeypatch):
    _parent, lab_base = _mock_provision_environment(tmp_path, monkeypatch)
    assert linux_attachment.provision_base() == {
        "state": "created",
        "path": str(lab_base),
        "mode": "0700",
    }
    metadata = lab_base.lstat()
    assert stat.S_IMODE(metadata.st_mode) == 0o700
    assert metadata.st_uid == os.getuid()
    assert metadata.st_gid == os.getgid()
    assert linux_attachment.provision_base()["state"] == "present"
    assert list(lab_base.iterdir()) == []


@pytest.mark.parametrize("conflict", ["symlink", "mode", "owner"])
def test_provision_base_rejects_conflicts_without_changing_them(tmp_path, monkeypatch, conflict):
    _parent, lab_base = _mock_provision_environment(tmp_path, monkeypatch)
    if conflict == "symlink":
        target = lab_base.parent / "other"
        target.mkdir()
        lab_base.symlink_to(target, target_is_directory=True)
    else:
        lab_base.mkdir(mode=0o700)
        if conflict == "mode":
            lab_base.chmod(0o750)
        else:

            def reject_owner(*_args):
                raise StorageError("attachment_directory_unsafe")

            monkeypatch.setattr(linux_attachment, "_check_private_directory", reject_owner)
    before = (lab_base.is_symlink(), lab_base.lstat().st_mode, lab_base.lstat().st_uid)
    with pytest.raises(StorageError, match="attachment_lab_base_conflict"):
        linux_attachment.provision_base()
    after = (lab_base.is_symlink(), lab_base.lstat().st_mode, lab_base.lstat().st_uid)
    assert after == before


def test_provision_base_rejects_mountpoint(tmp_path, monkeypatch):
    _parent, lab_base = _mock_provision_environment(tmp_path, monkeypatch)
    lab_base.mkdir(mode=0o700)
    monkeypatch.setattr(linux_attachment, "_is_mountpoint", lambda path: path == lab_base)
    with pytest.raises(StorageError, match="attachment_lab_base_mountpoint"):
        linux_attachment.provision_base()


def test_mountinfo_detection_catches_same_filesystem_bind_mount(tmp_path, monkeypatch):
    lab_base = tmp_path / "var" / "lib" / "brain-lab"
    escaped = os.fspath(lab_base).replace(" ", r"\040")
    monkeypatch.setattr(
        linux_attachment.Path,
        "read_text",
        lambda *_args, **_kwargs: f"31 22 0:42 / {escaped} rw,relatime - ext4 /dev/loop0 rw\n",
    )
    assert linux_attachment._is_mountpoint(lab_base) is True


@pytest.mark.skipif(sys.platform != "linux", reason="renameat2 is Linux-specific")
def test_provision_base_cleans_its_staging_directory_after_collision(tmp_path, monkeypatch):
    _parent, lab_base = _mock_provision_environment(tmp_path, monkeypatch)
    observed_staging_roots = []

    def collide(source_parent, _source_name, _destination_parent, _destination_name):
        observed_staging_roots.append(source_parent)
        lab_base.mkdir(mode=0o700)
        raise StorageError("attachment_id_already_exists")

    monkeypatch.setattr(linux_attachment, "_rename_noreplace_between", collide)
    assert linux_attachment.provision_base()["state"] == "present"
    assert len(observed_staging_roots) == 1
    assert not observed_staging_roots[0].exists()


def test_provision_base_requires_root(tmp_path, monkeypatch):
    _mock_provision_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(linux_attachment.os, "geteuid", lambda: 1000)
    with pytest.raises(StorageError, match="attachment_provision_base_requires_root"):
        linux_attachment.provision_base()


@pytest.mark.parametrize(
    "uid,gid",
    [(0, 0), (1000, 0)],
)
def test_provision_base_rejects_brain_mapped_to_root(tmp_path, monkeypatch, uid, gid):
    _mock_provision_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(
        linux_attachment.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=uid, pw_gid=gid),
    )
    with pytest.raises(StorageError, match="attachment_lab_base_account_unsafe"):
        linux_attachment.provision_base()
