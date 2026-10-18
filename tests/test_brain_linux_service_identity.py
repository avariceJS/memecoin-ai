"""Read-only service-account gate tests."""

import hashlib
from types import SimpleNamespace

import pytest

from memecoin_operator import linux_service_identity as identity


def _configure(
    monkeypatch, tmp_path, *, uid=None, gid=241, shell="/usr/sbin/nologin", groups=(241,)
):
    home = tmp_path / "brain-home"
    home.mkdir()
    account_uid = home.stat().st_uid if uid is None else uid
    account = SimpleNamespace(
        pw_name="brain",
        pw_uid=account_uid,
        pw_gid=gid,
        pw_dir=str(home),
        pw_shell=shell,
    )
    monkeypatch.setattr(identity.pwd, "getpwnam", lambda _name: account)
    monkeypatch.setattr(identity.os, "getgrouplist", lambda _name, _gid: list(groups))
    group_names = {241: "brain", 242: "service-extra", 243: "sudo"}
    group_names.update(
        {300 + index: name for index, name in enumerate(sorted(identity.ADMIN_GROUPS))}
    )
    monkeypatch.setattr(
        identity.grp,
        "getgrgid",
        lambda group_id: SimpleNamespace(gr_name=group_names[group_id]),
    )
    monkeypatch.setattr(identity.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(identity.os, "getuid", lambda: 1001)
    monkeypatch.delenv("SUDO_UID", raising=False)
    monkeypatch.setattr(identity.os, "access", lambda _path, _mode: True)
    monkeypatch.setattr(identity, "SSHD", tmp_path / "no-sshd")

    def run(argv):
        if argv[0] == str(identity.PASSWD):
            return SimpleNamespace(returncode=0, stdout="brain L 2026-01-01 0 99999 7 -1\n")
        if argv[0] == str(identity.SUDO):
            return SimpleNamespace(
                returncode=0, stdout="User brain is not allowed to run sudo on host.\n"
            )
        raise AssertionError(argv)

    monkeypatch.setattr(identity, "_run", run)
    return home, account


def test_dedicated_locked_nologin_account_is_sealed(monkeypatch, tmp_path):
    _home, account = _configure(monkeypatch, tmp_path)
    record = identity.snapshot()
    payload = {key: value for key, value in record.items() if key != "sha256"}
    assert record["uid"] == account.pw_uid
    assert record["gid"] == account.pw_gid
    assert record["groups"] == [{"gid": 241, "name": "brain"}]
    assert record["password_locked"] is True
    assert record["sudo_denied"] is True
    assert record["ssh_authorized_keys"] == []
    assert record["operator_uid_distinct"] is True
    assert record["sha256"] == hashlib.sha256(identity._canonical(payload)).hexdigest()
    monkeypatch.setattr(identity, "snapshot", lambda: record)
    assert identity.verify_bound(record) == record


def test_durable_identity_is_stable_across_distinct_operators(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    first = identity.snapshot()
    monkeypatch.setattr(identity.os, "getuid", lambda: 1002)
    second = identity.snapshot()
    assert first == second


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"uid": 0}, "service_identity_root_or_invalid"),
        ({"shell": "/bin/bash"}, "service_login_shell_refused"),
        ({"groups": (241, 243)}, "service_account_in_admin_group"),
    ],
)
def test_identity_refuses_root_login_shell_and_admin_group(monkeypatch, tmp_path, options, reason):
    _configure(monkeypatch, tmp_path, **options)
    with pytest.raises(identity.IdentityError, match=reason):
        identity.snapshot()


@pytest.mark.parametrize("group_name", sorted(identity.ADMIN_GROUPS))
def test_identity_refuses_every_admin_group(monkeypatch, tmp_path, group_name):
    group_id = 300 + sorted(identity.ADMIN_GROUPS).index(group_name)
    _configure(monkeypatch, tmp_path, groups=(241, group_id))
    with pytest.raises(identity.IdentityError, match="service_account_in_admin_group"):
        identity.snapshot()


def test_identity_refuses_uid_shared_with_operator(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, uid=1001)
    with pytest.raises(identity.IdentityError, match="service_identity_shares_operator_uid"):
        identity.snapshot()


def test_identity_refuses_unlocked_password(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        identity,
        "_run",
        lambda argv: (
            SimpleNamespace(
                returncode=0,
                stdout="brain P 2026-01-01 0 99999 7 -1\n",
            )
            if argv[0] == str(identity.PASSWD)
            else SimpleNamespace(
                returncode=0, stdout="User brain is not allowed to run sudo on host.\n"
            )
        ),
    )
    with pytest.raises(identity.IdentityError, match="service_password_unlocked"):
        identity.snapshot()


def test_identity_refuses_authorized_keys_file(monkeypatch, tmp_path):
    home, _account = _configure(monkeypatch, tmp_path)
    ssh = home / ".ssh"
    ssh.mkdir()
    (ssh / "authorized_keys").write_text("ssh-ed25519 AAAA", encoding="ascii")
    with pytest.raises(identity.IdentityError, match="service_ssh_authorized_keys_present"):
        identity.snapshot()


def test_identity_refuses_effective_authorized_keys_command(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    sshd = tmp_path / "sshd"
    sshd.touch()
    monkeypatch.setattr(identity, "SSHD", sshd)
    original_run = identity._run

    def run(argv):
        if argv[0] == str(sshd):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2\n"
                    "authorizedkeyscommand /usr/local/bin/key-helper\n"
                ),
            )
        return original_run(argv)

    monkeypatch.setattr(identity, "_run", run)
    with pytest.raises(identity.IdentityError, match="ssh_authorized_keys_command_present"):
        identity.snapshot()


@pytest.mark.parametrize("authorized_file", ["/srv/keys/*", "/srv/keys/brain?", "/srv/keys/[a]"])
def test_identity_refuses_glob_authorized_keys_path(monkeypatch, tmp_path, authorized_file):
    _configure(monkeypatch, tmp_path)
    sshd = tmp_path / "sshd"
    sshd.touch()
    monkeypatch.setattr(identity, "SSHD", sshd)
    original_run = identity._run

    def run(argv):
        if argv[0] == str(sshd):
            return SimpleNamespace(
                returncode=0,
                stdout=f"authorizedkeysfile {authorized_file}\nauthorizedkeyscommand none\n",
            )
        return original_run(argv)

    monkeypatch.setattr(identity, "_run", run)
    with pytest.raises(identity.IdentityError, match="ssh_authorized_keys_policy_ambiguous"):
        identity.snapshot()


def test_identity_refuses_sudo_capability_and_unavailable_probe(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        identity,
        "_run",
        lambda argv: (
            SimpleNamespace(returncode=0, stdout="User brain may run sudo commands.\n")
            if argv[0] == str(identity.SUDO)
            else SimpleNamespace(returncode=0, stdout="brain L 2026-01-01 0 99999 7 -1\n")
        ),
    )
    with pytest.raises(identity.IdentityError, match="service_sudo_denial_unproven"):
        identity.snapshot()
