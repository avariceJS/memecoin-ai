"""Install a disabled, sandboxed Linux systemd unit for the operator runtime.

Installation only writes the dedicated unit and reloads systemd. It never creates
operator state, enables the unit, or starts a process. Starting the unit is a separate
operator action after an explicit decision to activate the selected root.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

UNIT = "memecoin-brain-lab.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT
STATE_BASE = Path("/var/lib/brain-lab")
STATE_ROOT = STATE_BASE / "operator"
CODE_ROOT = Path("/opt/memecoin-brain-lab")
DEFAULT_PYTHON = CODE_ROOT / "venv/bin/python"
DEFAULT_PORT = 18792
SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")


def _absolute_safe(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not SAFE_PATH.fullmatch(value) or ".." in path.parts:
        raise ValueError(f"invalid_{label}")
    return path


def render_unit(python: Path, state_root: Path, port: int) -> str:
    """Return a fixed-policy unit with only validated path and port substitutions."""
    python = _absolute_safe(str(python), "python_path")
    state_root = _absolute_safe(str(state_root), "state_root")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("python_not_executable")
    try:
        state_root.relative_to(STATE_BASE)
    except ValueError as exc:
        raise ValueError("state_root_outside_lab") from exc
    if state_root == STATE_BASE or not 1024 <= port <= 65535:
        raise ValueError("invalid_port_or_state_root")
    return f"""[Unit]
Description=Disposable Brain operator lab (manual activation only)
After=local-fs.target

[Service]
Type=simple
User=brain
Group=brain
WorkingDirectory={CODE_ROOT}
Environment=HOME=/var/lib/brain-lab
Environment=PYTHONNOUSERSITE=1
UMask=0077
ExecStart={python} -I -m memecoin_operator.runtime --root {state_root} --port {port}
Restart=no
StateDirectory=brain-lab
StateDirectoryMode=0700
PrivateTmp=yes
PrivateNetwork=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
NoNewPrivileges=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadWritePaths={STATE_BASE}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def install(unit_text: str, unit_path: Path = UNIT_PATH) -> None:
    """Install the fixed lab unit without enabling or starting it."""
    if os.geteuid() != 0:
        raise PermissionError("install_requires_root")
    if unit_path != UNIT_PATH:
        raise ValueError("unexpected_unit_path")
    check = subprocess.run(
        ["systemctl", "is-active", "--quiet", UNIT], check=False, capture_output=True
    )
    if check.returncode == 0:
        raise RuntimeError("refusing_to_replace_active_unit")
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{UNIT}.", dir=UNIT_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(unit_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, UNIT_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    enabled = subprocess.run(
        ["systemctl", "is-enabled", UNIT], check=False, capture_output=True, text=True
    )
    if enabled.stdout.strip() not in {"disabled", "static"}:
        raise RuntimeError("unit_not_inert_after_install")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render", help="print the unit without changing host state")
    render.add_argument("--python", default=str(DEFAULT_PYTHON))
    render.add_argument("--state-root", default=str(STATE_ROOT))
    render.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub.add_parser("install", help="install and reload the disabled unit; never start it")
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            print(render_unit(Path(args.python), Path(args.state_root), args.port), end="")
        else:
            install(render_unit(DEFAULT_PYTHON, STATE_ROOT, DEFAULT_PORT))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"linux_service_error: {exc}", file=sys.stderr)
        return 2
    return 0
