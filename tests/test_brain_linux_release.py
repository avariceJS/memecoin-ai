"""Content-addressed Linux release verification is fail-closed."""

import hashlib
import json

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
    monkeypatch.setattr(linux_release, "RELEASE_BASE", tmp_path / "releases")
    monkeypatch.setattr(linux_release, "UNIT_BASE", tmp_path / "systemd")
    monkeypatch.setattr(linux_release, "_verify_permissions", lambda _root, _unit: None)
    monkeypatch.setattr(linux_release, "_interpreter", lambda _python: interpreter)
    monkeypatch.setattr(
        linux_release.linux_service,
        "_unit_status",
        lambda _unit: ("loaded", "inactive", "disabled", 0),
    )
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
