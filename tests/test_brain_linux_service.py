"""Linux systemd unit rendering stays inert and bounded to the lab paths."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_operator import linux_service
from memecoin_operator.linux_service import CODE_ROOT, STATE_BASE, render_unit


def test_render_is_manual_sandboxed_and_state_scoped(tmp_path):
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o755)
    unit = render_unit(python, STATE_BASE / "operator", 18792)
    assert "User=brain" in unit
    assert "PrivateNetwork=yes" in unit
    assert "ProtectSystem=strict" in unit
    assert f"WorkingDirectory={CODE_ROOT}" in unit
    assert f"ReadWritePaths={STATE_BASE}" in unit
    assert "Restart=no" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "systemctl enable" not in unit
    assert "systemctl start" not in unit


@pytest.mark.parametrize(
    ("python", "state_root", "port", "reason"),
    [
        ("relative/python", str(STATE_BASE / "operator"), 18792, "invalid_python_path"),
        ("/no/such/python", str(STATE_BASE / "operator"), 18792, "python_not_executable"),
        ("/usr/bin/python3", "/var/lib/brain/operator", 18792, "state_root_outside_lab"),
        ("/usr/bin/python3", str(STATE_BASE), 18792, "invalid_port_or_state_root"),
        ("/usr/bin/python3", str(STATE_BASE / "operator"), 80, "invalid_port_or_state_root"),
        ("/usr/bin/python3", "/var/lib/brain-lab/../brain", 18792, "invalid_state_root"),
    ],
)
def test_rejects_unsafe_paths_and_ports(python, state_root, port, reason):
    with pytest.raises(ValueError, match=reason):
        render_unit(Path(python), Path(state_root), port)


def test_install_only_reloads_and_leaves_unit_disabled(tmp_path, monkeypatch):
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o755)
    unit_path = tmp_path / "memecoin-brain-lab.service"
    monkeypatch.setattr(linux_service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(linux_service.os, "geteuid", lambda: 0)
    calls = []

    statuses = iter(
        [
            "LoadState=not-found\nActiveState=inactive\nUnitFileState=\nMainPID=0\n",
            "LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\nMainPID=0\n",
        ]
    )

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == "show":
            return SimpleNamespace(returncode=0, stdout=next(statuses), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(linux_service.subprocess, "run", run)
    text = render_unit(python, STATE_BASE / "operator", 18792)
    linux_service.install(text, unit_path=unit_path)
    assert unit_path.read_text(encoding="utf-8") == text
    assert unit_path.stat().st_mode & 0o777 == 0o644
    commands = [call[0] for call in calls]
    assert commands == [
        [
            "systemctl",
            "show",
            "--no-pager",
            "--property=LoadState,ActiveState,UnitFileState,MainPID",
            linux_service.UNIT,
        ],
        ["systemctl", "daemon-reload"],
        [
            "systemctl",
            "show",
            "--no-pager",
            "--property=LoadState,ActiveState,UnitFileState,MainPID",
            linux_service.UNIT,
        ],
    ]


def test_install_refuses_to_replace_active_unit(tmp_path, monkeypatch):
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o755)
    unit_path = tmp_path / "memecoin-brain-lab.service"
    monkeypatch.setattr(linux_service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(linux_service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        linux_service.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="LoadState=loaded\nActiveState=active\nUnitFileState=disabled\nMainPID=42\n",
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match="refusing_to_replace_active_unit"):
        linux_service.install("[Service]\n", unit_path=unit_path)
    assert not unit_path.exists()


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (
            "LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\nMainPID=0\n",
            "refusing_to_replace_enabled_unit",
        ),
        (
            "LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\nMainPID=0\nMainPID=0\n",
            "systemctl_status_malformed",
        ),
    ],
)
def test_install_refuses_unsafe_existing_state_before_write(tmp_path, monkeypatch, status, reason):
    path = tmp_path / linux_service.UNIT
    monkeypatch.setattr(linux_service, "UNIT_PATH", path)
    monkeypatch.setattr(linux_service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        linux_service.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=status, stderr=""),
    )
    with pytest.raises(RuntimeError, match=reason):
        linux_service.install("new unit", unit_path=path)
    assert not path.exists()


def test_install_status_command_failure_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / linux_service.UNIT
    monkeypatch.setattr(linux_service, "UNIT_PATH", path)
    monkeypatch.setattr(linux_service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        linux_service.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr="unavailable"),
    )
    with pytest.raises(RuntimeError, match="systemctl_status_failed"):
        linux_service.install("new unit", unit_path=path)
    assert not path.exists()


def test_install_rolls_back_when_post_reload_state_is_enabled(tmp_path, monkeypatch):
    path = tmp_path / linux_service.UNIT
    path.write_text("old unit\n", encoding="utf-8")
    path.chmod(0o640)
    monkeypatch.setattr(linux_service, "UNIT_PATH", path)
    monkeypatch.setattr(linux_service.os, "geteuid", lambda: 0)
    statuses = iter(
        [
            "LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\nMainPID=0\n",
            "LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\nMainPID=0\n",
        ]
    )
    monkeypatch.setattr(
        linux_service.subprocess,
        "run",
        lambda argv, **_k: SimpleNamespace(
            returncode=0, stdout=next(statuses) if argv[1] == "show" else "", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="unit_not_inert_after_install"):
        linux_service.install("new unit", unit_path=path)
    assert path.read_text(encoding="utf-8") == "old unit\n"
    assert path.stat().st_mode & 0o777 == 0o640
