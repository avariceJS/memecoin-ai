"""Linux systemd unit rendering stays inert and bounded to the lab paths."""

from pathlib import Path

import pytest

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
