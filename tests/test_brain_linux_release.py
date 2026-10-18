"""Content-addressed Linux release verification is fail-closed."""

import hashlib
import json
import shutil
import sqlite3

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
        f"ExecStart={root}/venv/bin/python -I -m memecoin_operator.runtime "
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
            f"{{ path={root}/venv/bin/python ; argv[]={root}/venv/bin/python -I -m "
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
