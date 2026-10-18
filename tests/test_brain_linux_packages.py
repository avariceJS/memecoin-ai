"""Safety contracts for opt-in Ubuntu lab package provisioning."""

from __future__ import annotations

import subprocess

import pytest

from tools import brain_linux_packages as provisioner


def test_roles_are_fixed_and_do_not_include_database_test_control():
    assert provisioner.ROLES["source-build"] == (
        "ca-certificates",
        "git",
        "make",
        "python3",
        "python3.12",
        "python3.12-venv",
    )
    assert "dbus" not in sum(provisioner.ROLES.values(), ())


@pytest.mark.parametrize("snapshot", ["20261018", "20261018T1200Z", "20260230T120000Z", "latest"])
def test_snapshot_id_must_be_exact_utc_form(snapshot):
    with pytest.raises(provisioner.ProvisionError, match="invalid_snapshot_id"):
        provisioner._validate_snapshot(snapshot)


def test_snapshot_sources_are_fixed_to_signed_official_noble_archives():
    text = provisioner._sources("20261018T120000Z").decode()
    assert text.count("Snapshot: 20261018T120000Z") == 2
    assert "https://archive.ubuntu.com/ubuntu" in text
    assert "https://security.ubuntu.com/ubuntu" in text
    assert "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg" in text
    assert "ppa:" not in text


def test_simulation_refuses_removals(monkeypatch):
    result = subprocess.CompletedProcess([], 0, "Remv old-package [1]\n", "")
    monkeypatch.setattr(provisioner, "_run", lambda *_args, **_kwargs: result)
    with pytest.raises(provisioner.ProvisionError, match="package_removal_refused"):
        provisioner._simulate({"git": "1"}, {})


def test_simulation_allows_required_dependency_upgrade(monkeypatch):
    output = "Inst git [1.0] (2.0 Ubuntu:24.04/noble)\n"
    monkeypatch.setattr(provisioner, "_dependency_closure", lambda _requested: {"git"})

    def run(argv, **_kwargs):
        if argv[0] == "/usr/bin/dpkg":
            return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(provisioner, "_run", run)
    transactions, _ = provisioner._simulate({"git": "2.0"}, {"git": "1.0"})
    assert transactions == [{"package": "git", "from_version": "1.0", "version": "2.0"}]


def test_simulation_refuses_unrelated_package_upgrade(monkeypatch):
    output = "Inst git [1.0] (2.0 Ubuntu:24.04/noble)\n"
    monkeypatch.setattr(
        provisioner, "_dependency_closure", lambda _requested: {"python3", "python3-minimal"}
    )
    monkeypatch.setattr(
        provisioner,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, output, ""),
    )
    with pytest.raises(provisioner.ProvisionError, match="unrelated_package_change_refused:git"):
        provisioner._simulate({"python3": "3.12"}, {"git": "1.0"})


def test_simulation_refuses_dependency_downgrade(monkeypatch):
    output = "Inst git [2.0] (1.0 Ubuntu:24.04/noble)\n"
    monkeypatch.setattr(provisioner, "_dependency_closure", lambda _requested: {"git"})

    def run(argv, **_kwargs):
        if argv[0] == "/usr/bin/dpkg":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(provisioner, "_run", run)
    with pytest.raises(provisioner.ProvisionError, match="package_downgrade_refused:git"):
        provisioner._simulate({"git": "1.0"}, {"git": "2.0"})


def test_simulation_records_only_new_dependency_closure(monkeypatch):
    output = (
        "Inst ca-certificates (2024.2.69ubuntu0.1 Ubuntu:24.04/noble)\n"
        "Inst libexample1:amd64 (1.4 Ubuntu:24.04/noble)\n"
    )
    monkeypatch.setattr(
        provisioner,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, output, ""),
    )
    result, raw = provisioner._simulate({"ca-certificates": "2024.2.69ubuntu0.1"}, {})
    assert result == [
        {"package": "ca-certificates", "from_version": "", "version": "2024.2.69ubuntu0.1"},
        {"package": "libexample1", "from_version": "", "version": "1.4"},
    ]
    assert raw == output


def test_lab_guard_requires_systemd_nspawn_even_with_root(monkeypatch, tmp_path):
    monkeypatch.setattr(provisioner.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provisioner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(provisioner, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    monkeypatch.setattr(
        provisioner,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "systemd-nspawn\n", ""),
    )

    class Marker:
        def exists(self):
            return True

        def read_text(self, encoding=None):
            return "docker\n"

    monkeypatch.setattr(
        provisioner, "Path", lambda value: Marker() if value == "/run/systemd/container" else value
    )
    with pytest.raises(provisioner.ProvisionError, match="systemd_nspawn_lab_required"):
        provisioner._require_lab(mutating=False)


def test_private_directory_rejects_symlink(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(provisioner.ProvisionError, match="unsafe_directory"):
        provisioner._ensure_private_dir(link)


def test_capability_fingerprint_ignores_only_observation_time_and_free_bytes():
    first = {
        "observed_at": "today",
        "host": {"architecture": "x86_64"},
        "checks": {"free_space": {"status": "available", "available_bytes": 1000}},
        "roles": {"source_build": {"status": "available"}},
        "unit": None,
    }
    second = {
        **first,
        "observed_at": "tomorrow",
        "checks": {"free_space": {"status": "available", "available_bytes": 999}},
    }
    assert provisioner._capability_fingerprint(first) == provisioner._capability_fingerprint(second)
