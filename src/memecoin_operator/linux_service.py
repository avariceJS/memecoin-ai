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


def _unit_status() -> tuple[str, str, str, int]:
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "--no-pager",
            "--property=LoadState,ActiveState,UnitFileState,MainPID",
            UNIT,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError("systemctl_status_failed")
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if (
            not separator
            or key not in {"LoadState", "ActiveState", "UnitFileState", "MainPID"}
            or key in fields
        ):
            raise RuntimeError("systemctl_status_malformed")
        fields[key] = value
    if set(fields) != {"LoadState", "ActiveState", "UnitFileState", "MainPID"}:
        raise RuntimeError("systemctl_status_malformed")
    if fields["LoadState"] not in {"not-found", "loaded"}:
        raise RuntimeError("systemctl_status_unrecognized")
    if fields["ActiveState"] not in {"inactive", "active", "activating", "deactivating", "failed"}:
        raise RuntimeError("systemctl_status_unrecognized")
    if fields["UnitFileState"] not in {
        "",
        "disabled",
        "static",
        "enabled",
        "enabled-runtime",
        "masked",
        "masked-runtime",
    }:
        raise RuntimeError("systemctl_status_unrecognized")
    if not fields["MainPID"].isdecimal():
        raise RuntimeError("systemctl_status_malformed")
    return (
        fields["LoadState"],
        fields["ActiveState"],
        fields["UnitFileState"],
        int(fields["MainPID"]),
    )


def install(unit_text: str, unit_path: Path = UNIT_PATH) -> None:
    """Install the fixed lab unit without enabling or starting it."""
    if os.geteuid() != 0:
        raise PermissionError("install_requires_root")
    if unit_path != UNIT_PATH:
        raise ValueError("unexpected_unit_path")
    load, active, file_state, pid = _unit_status()
    if active != "inactive" or pid != 0:
        raise RuntimeError("refusing_to_replace_active_unit")
    if load == "loaded" and file_state not in {"disabled", "static"}:
        raise RuntimeError("refusing_to_replace_enabled_unit")
    if load == "not-found" and (file_state or active != "inactive"):
        raise RuntimeError("refusing_to_replace_unknown_unit")
    old_bytes = UNIT_PATH.read_bytes() if UNIT_PATH.exists() else None
    old_mode = UNIT_PATH.stat().st_mode & 0o777 if UNIT_PATH.exists() else None
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
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
        load, active, file_state, pid = _unit_status()
        if (load, active, file_state, pid) != ("loaded", "inactive", "disabled", 0):
            raise RuntimeError("unit_not_inert_after_install")
    except (OSError, subprocess.SubprocessError, RuntimeError):
        if old_bytes is None:
            UNIT_PATH.unlink(missing_ok=True)
        else:
            UNIT_PATH.write_bytes(old_bytes)
            UNIT_PATH.chmod(old_mode or 0o644)
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        raise


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
