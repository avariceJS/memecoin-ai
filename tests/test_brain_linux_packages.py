"""Safety contracts for opt-in Ubuntu lab package provisioning."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from tools import brain_linux_packages as provisioner


def test_apt_cache_files_are_isolated_from_null_device():
    args = provisioner._apt_base()
    cache_targets = [
        args[index + 1].split("=", 1)[1]
        for index, item in enumerate(args[:-1])
        if item == "-o"
        and args[index + 1].startswith(("Dir::Cache::pkgcache=", "Dir::Cache::srcpkgcache="))
    ]
    assert cache_targets == [
        str(provisioner.APT_CACHE / "pkgcache.bin"),
        str(provisioner.APT_CACHE / "srcpkgcache.bin"),
    ]
    assert all(target.startswith(f"{provisioner.APT_CACHE}/") for target in cache_targets)
    assert "/dev/null" not in cache_targets


def test_roles_are_fixed_and_do_not_include_database_test_control():
    assert provisioner.ROLES["source-build"] == (
        "ca-certificates",
        "git",
        "openssh-client",
        "make",
        "python3",
        "python3.12",
        "python3.12-venv",
    )
    assert provisioner.ROLES["inert-service-host"] == (
        "bsdutils",
        "iproute2",
        "sudo",
        "systemd",
        "systemd-sysv",
        "udev",
        "util-linux",
    )
    assert "dbus" not in sum(provisioner.ROLES.values(), ())


def test_bootstrap_preflight_requires_installer_substrate(tmp_path):
    assert provisioner._bootstrap_prerequisites_missing(tmp_path, (3, 12)) == [
        "python3.12",
        "apt_get",
        "dpkg",
        "systemd_detect_virt",
        "ubuntu_archive_keyring",
        "systemd_pid1",
        "systemd_nspawn",
    ]
    (tmp_path / "usr/bin/python3.12").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr/bin/python3.12").touch()
    assert "python3.12" in provisioner._bootstrap_prerequisites_missing(tmp_path, (3, 12))

    for path in provisioner.BOOTSTRAP_EXECUTABLES:
        target = tmp_path / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        target.chmod(0o755)
    keyring = tmp_path / provisioner.BOOTSTRAP_KEYRING.lstrip("/")
    keyring.parent.mkdir(parents=True, exist_ok=True)
    keyring.touch()
    pid1 = tmp_path / "proc/1/comm"
    pid1.parent.mkdir(parents=True, exist_ok=True)
    pid1.write_text("systemd\n", encoding="utf-8")
    (tmp_path / "run/systemd/system").mkdir(parents=True)
    marker = tmp_path / "run/systemd/container"
    marker.write_text("systemd-nspawn\n", encoding="utf-8")
    assert provisioner._bootstrap_prerequisites_missing(tmp_path, (3, 12)) == []
    assert provisioner._bootstrap_prerequisites_missing(tmp_path, (3, 13)) == ["python3.12_runtime"]


def test_lab_guard_reports_missing_substrate_before_running_virtualization_probe(monkeypatch):
    monkeypatch.setattr(provisioner.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provisioner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(provisioner, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    monkeypatch.setattr(
        provisioner, "_bootstrap_prerequisites_missing", lambda: ["systemd_detect_virt"]
    )
    monkeypatch.setattr(
        provisioner,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected probe")),
    )
    with pytest.raises(
        provisioner.ProvisionError, match="bootstrap_prerequisite_missing:systemd_detect_virt"
    ):
        provisioner._require_lab(mutating=True)


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


def test_dependency_closure_includes_package_breaks_and_conflicts(monkeypatch):
    output = (
        "util-linux\n"
        "  Pre-Depends: libblkid1 (>= 2.37)\n"
        "  Breaks: bsdutils (< 1:2.39)\n"
        "  Conflicts: old-tool\n"
    )
    monkeypatch.setattr(
        provisioner,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, output, ""),
    )
    assert provisioner._dependency_closure({"util-linux": "2.39"}) == {
        "util-linux",
        "libblkid1",
        "bsdutils",
        "old-tool",
    }


def test_lab_guard_requires_systemd_nspawn_even_with_root(monkeypatch, tmp_path):
    monkeypatch.setattr(provisioner.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provisioner.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(provisioner, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    monkeypatch.setattr(
        provisioner,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "systemd-nspawn\n", ""),
    )
    monkeypatch.setattr(provisioner, "_bootstrap_prerequisites_missing", lambda: [])

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


def test_index_inventory_tracks_auxiliary_files_and_ignores_partial(monkeypatch, tmp_path):
    lists = tmp_path / "lists"
    (lists / "auxfiles").mkdir(parents=True)
    (lists / "partial").mkdir()
    (lists / "noble_InRelease").write_bytes(b"signed metadata")
    (lists / "auxfiles" / "hashes").write_bytes(b"auxiliary")
    (lists / "partial" / "download").write_bytes(b"incomplete")
    monkeypatch.setattr(provisioner, "LISTS", lists)
    monkeypatch.setattr(provisioner.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=-1))
    inventory = provisioner._index_inventory()
    assert inventory == {
        "auxfiles/hashes": provisioner._sha(b"auxiliary"),
        "noble_InRelease": provisioner._sha(b"signed metadata"),
    }


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


def test_source_checkout_may_be_pending_git_only_for_source_role_bootstrap():
    pending = {"checks": {"source_checkout": {"status": "absent", "reason": "git_missing"}}}
    provisioner._validate_source_checkout(pending, allow_git_missing=True)
    with pytest.raises(provisioner.ProvisionError, match="brain_source_checkout_not_clean"):
        provisioner._validate_source_checkout(pending, allow_git_missing=False)
    dirty = {"checks": {"source_checkout": {"status": "absent", "reason": "worktree_dirty"}}}
    with pytest.raises(provisioner.ProvisionError, match="brain_source_checkout_not_clean"):
        provisioner._validate_source_checkout(dirty, allow_git_missing=True)


def test_post_apply_capability_failure_is_returned_for_durable_receipt(monkeypatch, tmp_path):
    def fail_probe(_workspace, _source_commit, *, allow_git_missing=False):
        raise provisioner.ProvisionError("brain_source_checkout_not_clean_at_requested_commit")

    monkeypatch.setattr(provisioner, "_capability_report", fail_probe)
    report, error = provisioner._post_apply_capability_report(tmp_path, "a" * 40)

    assert report["schema"] == "brain-linux-capability-probe-failed-v1"
    assert report["source_commit"] == "a" * 40
    assert error == "brain_source_checkout_not_clean_at_requested_commit"


def test_unit_state_recognizes_memecoin_brain_release_unit(monkeypatch):
    unit = "memecoin-brain-" + "a" * 64 + ".service loaded inactive dead"
    monkeypatch.setattr(
        provisioner,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, unit + "\n", ""),
    )
    assert provisioner._unit_state() == [unit]


def test_refresh_cli_has_no_source_workspace_dependency(monkeypatch, capsys):
    monkeypatch.setattr(
        provisioner,
        "refresh_indexes",
        lambda snapshot, source_commit: {"status": snapshot, "source_commit": source_commit},
    )
    result = provisioner.main(
        [
            "refresh-indexes",
            "--lab-only",
            "--snapshot",
            "20261018T120000Z",
            "--source-commit",
            "a" * 40,
        ]
    )
    assert result == 0
    assert '"status": "20261018T120000Z"' in capsys.readouterr().out
