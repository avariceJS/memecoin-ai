"""Content-addressed Linux release verification is fail-closed."""

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_operator import linux_release


def _verified_fixture(tmp_path, monkeypatch):
    wheel_sha = "b" * 64
    interpreter = {
        "path": "/usr/bin/python3.12",
        "version": "3.12.3",
        "soabi": "cpython-312-x86_64-linux-gnu",
    }
    release_id = linux_release._identity(wheel_sha, interpreter)
    unit_name = f"memecoin-brain-{release_id}.service"
    root = tmp_path / "releases" / release_id
    venv = root / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("synthetic interpreter", encoding="utf-8")
    (venv / "package.py").write_text("synthetic package", encoding="utf-8")
    inventory, inventory_sha = linux_release._inventory(venv)
    unit = (
        f"ExecStart={root}/venv/bin/python -B -I -m memecoin_operator.runtime "
        "--root /var/lib/brain-lab/releases/test/operator --port 18792\n"
    )
    (tmp_path / "systemd").mkdir()
    (tmp_path / "systemd" / unit_name).write_text(unit, encoding="utf-8")
    state_root = tmp_path / "state" / release_id / "operator"
    state_root.mkdir(parents=True)
    (state_root / "config.json").write_text(
        json.dumps(
            {
                "mode": "operator",
                "real_execution_enabled": False,
                "paid_budget_usd": 0,
            }
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(state_root / "operator.sqlite") as db:
        db.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY)")
    manifest = {
        "schema": linux_release.SCHEMA,
        "release_id": release_id,
        "wheel_sha256": wheel_sha,
        "interpreter": interpreter,
        "source_commit": "c" * 40,
        "package_version": "0.1.0",
        "installed_inventory": inventory,
        "installed_inventory_sha256": inventory_sha,
        "unit_name": unit_name,
        "unit_sha256": hashlib.sha256(unit.encode()).hexdigest(),
    }
    (root / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pin_dir = tmp_path / "releases" / ".pins"
    pin_dir.mkdir()
    manifest_sha = hashlib.sha256((root / "release-manifest.json").read_bytes()).hexdigest()
    (pin_dir / f"{release_id}.sha256").write_text(
        f"{manifest_sha}  release-manifest.json\n", encoding="ascii"
    )
    monkeypatch.setattr(linux_release, "RELEASE_BASE", tmp_path / "releases")
    monkeypatch.setattr(linux_release, "UNIT_BASE", tmp_path / "systemd")
    monkeypatch.setattr(linux_release, "STATE_BASE", tmp_path / "state")
    monkeypatch.setattr(linux_release, "_verify_permissions", lambda _root, _unit: None)
    monkeypatch.setattr(linux_release, "_interpreter", lambda _python: interpreter)
    monkeypatch.setattr(
        linux_release.linux_service,
        "_unit_status",
        lambda _unit: ("loaded", "inactive", "disabled", 0),
    )
    properties = {
        "FragmentPath": str(tmp_path / "systemd" / unit_name),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "ExecStart": (
            f"{{ path={root}/venv/bin/python ; argv[]={root}/venv/bin/python -B -I -m "
            "memecoin_operator.runtime --root "
            f"{state_root} --port 18792 ; ignore_errors=no ; start_time=[n/a] }}"
        ),
        "User": "brain",
        "Group": "brain",
        "PrivateNetwork": "yes",
        "WorkingDirectory": str(root),
        "ReadWritePaths": str(state_root),
        "StateDirectory": "brain-lab",
        "ProtectSystem": "strict",
        "Restart": "no",
    }
    monkeypatch.setattr(linux_release, "_unit_properties", lambda _unit: properties)
    return release_id, root


def test_release_identity_binds_wheel_and_interpreter():
    wheel = "b" * 64
    py312 = {
        "path": "/usr/bin/python3.12",
        "version": "3.12.3",
        "soabi": "cpython-312-x86_64-linux-gnu",
    }
    py313 = {
        "path": "/usr/bin/python3.13",
        "version": "3.13.1",
        "soabi": "cpython-313-x86_64-linux-gnu",
    }
    assert linux_release._identity(wheel, py312) != linux_release._identity(wheel, py313)
    assert linux_release._identity(wheel, py312) != linux_release._identity("c" * 64, py312)


def _stage_attempt_record():
    wheel_sha = "b" * 64
    interpreter = {
        "path": "/usr/bin/python3.12",
        "version": "3.12.3",
        "soabi": "cpython-312-x86_64-linux-gnu",
    }
    release_id = linux_release._identity(wheel_sha, interpreter)
    attempt_id = f"{release_id[:12]}-{'a' * 16}"
    return attempt_id, {
        "schema": linux_release.STAGE_ATTEMPT_SCHEMA,
        "attempt_id": attempt_id,
        "release_id": release_id,
        "wheel_sha256": wheel_sha,
        "source_commit": "c" * 40,
        "interpreter": interpreter,
        "builder_name": f"bs-{attempt_id}",
        "builder_uid": 1000,
        "staging_name": f".stage-{attempt_id}",
        "staging_dev": 1,
        "staging_ino": 2,
        "slice_name": linux_release._stage_slice_name(attempt_id),
        "phase": "building",
    }


def test_stage_attempt_record_binds_builder_and_release_identity():
    attempt_id, record = _stage_attempt_record()
    assert linux_release._validate_stage_attempt_record(attempt_id, record) == record

    forged = {**record, "builder_name": "nobody"}
    with pytest.raises(RuntimeError, match="stage_attempt_record_invalid"):
        linux_release._validate_stage_attempt_record(attempt_id, forged)

    forged = {**record, "builder_uid": 0}
    with pytest.raises(RuntimeError, match="stage_attempt_record_invalid"):
        linux_release._validate_stage_attempt_record(attempt_id, forged)

    forged = {**record, "release_id": "f" * 64}
    with pytest.raises(RuntimeError, match="stage_attempt_record_invalid"):
        linux_release._validate_stage_attempt_record(attempt_id, forged)


def test_publish_intent_pins_fixed_destinations_and_artifact_policy(tmp_path, monkeypatch):
    wheel_sha = "d" * 64
    interpreter = {
        "path": "/usr/bin/python3.12",
        "version": "3.12.3",
        "soabi": "cpython-312-x86_64-linux-gnu",
    }
    release_id = linux_release._identity(wheel_sha, interpreter, linux_release.SCHEMA_V2)
    attempt_id = f"{release_id[:12]}-{'e' * 16}"
    release_base = tmp_path / "opt" / "releases"
    unit_base = tmp_path / "etc" / "systemd"
    state_base = tmp_path / "var" / "state"
    prepared_base = tmp_path / "var" / "prepared"
    for path in (release_base, release_base / ".pins", unit_base, state_base, prepared_base):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(linux_release, "RELEASE_BASE", release_base)
    monkeypatch.setattr(linux_release, "UNIT_BASE", unit_base)
    monkeypatch.setattr(linux_release, "STATE_BASE_V2", state_base)
    monkeypatch.setattr(linux_release, "STATE_PREPARED_BASE", prepared_base)
    monkeypatch.setattr(
        linux_release.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=1001, pw_gid=1001)
    )
    policies = {
        "release": (0, 1001, 0o555),
        "pin": (0, 0, 0o444),
        "state": (0, 0, 0o711),
        "unit": (0, 0, 0o644),
    }
    artifacts = []
    device = os.stat(release_base).st_dev
    for spec in linux_release._publish_artifact_paths(attempt_id, release_id):
        uid, gid, mode = policies[spec["name"]]
        artifacts.append(
            {
                **spec,
                "identity": {
                    "dev": device,
                    "ino": len(artifacts) + 10,
                    "uid": uid,
                    "gid": gid,
                    "mode": mode,
                    "kind": spec["kind"],
                    "content_sha256": "a" * 64,
                },
            }
        )
    intent = {
        "schema": linux_release.PUBLISH_INTENT_SCHEMA,
        "attempt_id": attempt_id,
        "release_id": release_id,
        "source_commit": "c" * 40,
        "wheel_sha256": wheel_sha,
        "interpreter": interpreter,
        "artifacts": artifacts,
    }
    assert linux_release._validate_publish_intent(attempt_id, intent) == artifacts
    forged = {
        **intent,
        "artifacts": [{**artifacts[0], "destination_name": "foreign"}, *artifacts[1:]],
    }
    with pytest.raises(RuntimeError, match="release_publish_intent_invalid"):
        linux_release._validate_publish_intent(attempt_id, forged)
    forged = {
        **intent,
        "artifacts": [
            {**artifacts[0], "identity": {**artifacts[0]["identity"], "mode": 0o777}},
            *artifacts[1:],
        ],
    }
    with pytest.raises(RuntimeError, match="release_publish_artifact_policy_mismatch"):
        linux_release._validate_publish_intent(attempt_id, forged)


def test_completed_publish_rechecks_release_without_pinning_mutable_runtime_state(monkeypatch):
    release_id = "e" * 64
    wheel_sha = "d" * 64
    interpreter = {
        "path": "/usr/bin/python3.12",
        "version": "3.12.3",
        "soabi": "cpython-312-x86_64-linux-gnu",
    }
    source_commit = "c" * 40
    attempt_id = f"{release_id[:12]}-{'f' * 16}"
    record = {
        "phase": "complete",
        "release_id": release_id,
        "wheel_sha256": wheel_sha,
        "source_commit": source_commit,
        "interpreter": interpreter,
    }
    intent = {
        "release_id": release_id,
        "wheel_sha256": wheel_sha,
        "source_commit": source_commit,
        "interpreter": interpreter,
    }
    monkeypatch.setattr(linux_release, "_validate_publish_intent", lambda _id, _record: [])
    sealed = []
    monkeypatch.setattr(
        linux_release,
        "_record_stage_phase",
        lambda path, _record, phase, *, seal=False: sealed.append((path, phase, seal)),
    )
    monkeypatch.setattr(
        linux_release,
        "verify",
        lambda _release: {
            "source_commit": source_commit,
            "wheel_sha256": wheel_sha,
        },
    )
    result = linux_release._finish_publish_locked(attempt_id, record, intent)
    assert result["release_id"] == release_id
    assert result["release_path"] == str(linux_release.RELEASE_BASE / release_id)
    assert sealed == [(linux_release._stage_attempt_path(attempt_id), "complete", True)]


def test_discard_stage_replays_after_interrupted_quarantine_rename(tmp_path, monkeypatch):
    attempt_id, record = _stage_attempt_record()
    artifacts = []
    for index, name in enumerate(("release", "pin", "state", "unit")):
        parent = tmp_path / name
        parent.mkdir()
        source = parent / f"source-{index}"
        if name in {"release", "state"}:
            source.mkdir()
            (source / "payload").write_text(name, encoding="utf-8")
            kind = "directory"
        else:
            source.write_text(name, encoding="utf-8")
            kind = "file"
        artifacts.append(
            {
                "name": name,
                "source_parent": str(parent),
                "source_name": source.name,
                "destination_parent": str(parent),
                "destination_name": f"destination-{index}",
                "kind": kind,
                "identity": linux_release._artifact_identity(source, kind=kind),
            }
        )
    intent = {"source_commit": record["source_commit"]}
    monkeypatch.setattr(linux_release.os, "geteuid", lambda: 0)
    monkeypatch.setattr(linux_release, "_read_stage_attempt", lambda _id: (Path("record"), record))
    monkeypatch.setattr(
        linux_release, "_acquire_stage_lock", lambda _id: os.open("/dev/null", os.O_RDONLY)
    )
    monkeypatch.setattr(
        linux_release, "_discard_preflight", lambda _id: (record, intent, artifacts)
    )
    sealed = {}
    monkeypatch.setattr(
        linux_release,
        "_read_sealed_stage_json",
        lambda path, *, schema: sealed.get(path.name),
    )
    monkeypatch.setattr(
        linux_release,
        "_write_sealed_stage_json",
        lambda path, payload: sealed.__setitem__(path.name, payload),
    )

    def rename(source_parent, source_name, destination_parent, destination_name):
        os.rename(source_parent / source_name, destination_parent / destination_name)

    calls = 0

    def fail_after_first_rename(source_parent, source_name, destination_parent, destination_name):
        nonlocal calls
        calls += 1
        rename(source_parent, source_name, destination_parent, destination_name)
        if calls == 1:
            raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(linux_release, "_rename_noreplace", fail_after_first_rename)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        linux_release.discard_stage(attempt_id)
    monkeypatch.setattr(linux_release, "_rename_noreplace", rename)
    result = linux_release.discard_stage(attempt_id)
    assert result["status"] == "quarantined-retained"
    assert len(result["quarantined"]) == 4
    for item in artifacts:
        assert not (Path(item["source_parent"]) / item["source_name"]).exists()
        assert linux_release._artifact_location(
            linux_release._discard_quarantine_path(attempt_id, item),
            item["identity"],
            item["kind"],
        )


def test_process_census_fails_closed_on_permission_error(tmp_path, monkeypatch):
    status = tmp_path / "proc" / "123" / "status"
    status.parent.mkdir(parents=True)
    status.write_text("Name:\ttest\nUid:\t1000\t1000\t1000\t1000\n", encoding="ascii")
    original_read_text = Path.read_text

    def unreadable_status(path, *args, **kwargs):
        if path == status:
            raise PermissionError("synthetic unreadable proc status")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable_status)
    with pytest.raises(RuntimeError, match="stage_process_census_unavailable"):
        linux_release._uid_has_processes(1000, proc_root=status.parent.parent)


def test_process_census_fails_closed_on_malformed_status(tmp_path):
    status = tmp_path / "proc" / "123" / "status"
    status.parent.mkdir(parents=True)
    status.write_text("Name:\ttest\n", encoding="ascii")
    with pytest.raises(RuntimeError, match="stage_process_census_unavailable"):
        linux_release._uid_has_processes(1000, proc_root=status.parent.parent)


def test_verify_binds_installed_tree_and_versioned_unit(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    result = linux_release.verify(release_id)
    assert result["release_id"] == release_id
    assert result["unit"] == f"memecoin-brain-{release_id}.service"
    assert result["unit_state"] == ("loaded", "inactive", "disabled", 0)


def test_verify_rejects_installed_file_drift(tmp_path, monkeypatch):
    release_id, root = _verified_fixture(tmp_path, monkeypatch)
    (root / "venv" / "package.py").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="release_inventory_mismatch"):
        linux_release.verify(release_id)


def test_verify_rejects_unit_drift(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    unit = tmp_path / "systemd" / f"memecoin-brain-{release_id}.service"
    unit.write_text("ExecStart=/tmp/wrong-python\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="release_unit_mismatch"):
        linux_release.verify(release_id)


def test_verify_rejects_non_inert_unit(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        linux_release.linux_service,
        "_unit_status",
        lambda _unit: ("loaded", "active", "disabled", 42),
    )
    with pytest.raises(RuntimeError, match="release_unit_not_inert"):
        linux_release.verify(release_id)


def test_verify_rejects_effective_systemd_dropin(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    properties = linux_release._unit_properties(release_id)
    monkeypatch.setattr(
        linux_release,
        "_unit_properties",
        lambda _unit: {**properties, "DropInPaths": "/etc/systemd/system/override.conf"},
    )
    with pytest.raises(RuntimeError, match="release_effective_unit_mismatch"):
        linux_release.verify(release_id)


def test_verify_rejects_effective_execstart_redirect(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    properties = linux_release._unit_properties(release_id)
    monkeypatch.setattr(
        linux_release,
        "_unit_properties",
        lambda _unit: {
            **properties,
            "ExecStart": properties["ExecStart"].replace("--port 18792", "--port 9000"),
        },
    )
    with pytest.raises(RuntimeError, match="release_effective_execstart_mismatch"):
        linux_release.verify(release_id)


def test_verify_rejects_manifest_and_pin_drift(tmp_path, monkeypatch):
    release_id, root = _verified_fixture(tmp_path, monkeypatch)
    manifest = json.loads((root / "release-manifest.json").read_text())
    manifest["source_commit"] = "d" * 40
    (root / "release-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="release_manifest_pin_mismatch"):
        linux_release.verify(release_id)


def test_verify_rejects_unsafe_lab_config(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    config_path = tmp_path / "state" / release_id / "operator" / "config.json"
    config = json.loads(config_path.read_text())
    config["real_execution_enabled"] = True
    config_path.write_text(json.dumps(config))
    with pytest.raises(RuntimeError, match="release_lab_config_unsafe"):
        linux_release.verify(release_id)


def test_verify_allows_uncreated_state_until_explicit_activation(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    state_root = tmp_path / "state" / release_id / "operator"
    shutil.rmtree(state_root)
    result = linux_release.verify(release_id)
    assert result["release_id"] == release_id


def test_verify_allows_safe_config_before_runtime_database_exists(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    state_root = tmp_path / "state" / release_id / "operator"
    (state_root / "operator.sqlite").unlink()
    result = linux_release.verify(release_id)
    assert result["release_id"] == release_id


def test_verify_rejects_existing_job_state(tmp_path, monkeypatch):
    release_id, _root = _verified_fixture(tmp_path, monkeypatch)
    state_root = tmp_path / "state" / release_id / "operator"
    with sqlite3.connect(state_root / "operator.sqlite") as db:
        db.execute("INSERT INTO jobs(job_id) VALUES ('synthetic-job')")
    with pytest.raises(RuntimeError, match="release_lab_state_not_idle"):
        linux_release.verify(release_id)


@pytest.mark.parametrize(
    ("stdout", "stderr", "free_blocks", "free_inodes", "reason"),
    [
        ("", "OSError: [Errno 28] No space left on device", 1, 1, "stage_builder_no_space"),
        ("", "builder returned nonzero", 0, 1, "stage_builder_no_space"),
        ("", "builder returned nonzero", 1, 1, "stage_builder_command_failed"),
    ],
)
def test_run_builder_reports_bounded_failure_reason(
    monkeypatch, stdout, stderr, free_blocks, free_inodes, reason
):
    monkeypatch.setattr(
        linux_release.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=stdout, stderr=stderr),
    )
    monkeypatch.setattr(
        linux_release.os,
        "statvfs",
        lambda _path: SimpleNamespace(f_bavail=free_blocks, f_favail=free_inodes),
    )
    with pytest.raises(RuntimeError, match=reason):
        linux_release._run_builder(
            ["/bin/false"],
            attempt_id="a" * 12 + "-" + "b" * 16,
            phase="venv",
        )
