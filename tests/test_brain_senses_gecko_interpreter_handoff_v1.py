from __future__ import annotations

import os
import plistlib
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
for _entry in (REPO / "src", REPO / "tools"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from tools import brain_senses_gecko_capture_only_runtime_v1 as runtime  # noqa: E402
from tools import brain_senses_gecko_interpreter_handoff_v1 as handoff  # noqa: E402


def _fixture(tmp_path: Path) -> tuple[dict[str, Any], Path, list[str], Path]:
    uid = os.getuid()
    venv = tmp_path / ".venv"
    bindir = venv / "bin"
    bindir.mkdir(parents=True)
    real_cli = bindir / "python-real"
    real_cli.write_bytes(b"synthetic Python CLI\n")
    real_cli.chmod(0o700)
    cli = bindir / "python"
    cli.symlink_to(real_cli.name)
    cfg = venv / "pyvenv.cfg"
    cfg.write_text("home = /synthetic/python/3.13\n")
    cfg.chmod(0o600)
    base = tmp_path / "Python.framework/Versions/3.13"
    app = base / "Resources/Python.app/Contents/MacOS/Python"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"synthetic Python.app executable\n")
    app.chmod(0o700)
    plist_path = tmp_path / "capture.plist"
    label = runtime.LABEL
    capture_id = runtime.CAPTURE_ID
    script = str(REPO / "tools/brain_senses_gecko_capture_only_runtime_v1.py")
    argv = [str(cli), "-B", script, "--scheduled-tick", "--capture-id", capture_id]
    plist_path.write_bytes(
        plistlib.dumps(
            {"Label": label, "ProgramArguments": argv}, fmt=plistlib.FMT_XML, sort_keys=True
        )
    )
    plist_path.chmod(0o600)
    probe = {
        "executable": str(cli),
        "prefix": str(venv),
        "base_prefix": str(base),
        "version": [3, 13, 6],
    }
    receipt = handoff.build_receipt(
        plist_path=str(plist_path),
        expected_label=label,
        expected_argv=argv,
        owner_uid=uid,
        spawn_probe=lambda _program, _pidpath: (probe, str(app)),
        observed_ns=1_800_000_000_000_000_000,
    )
    return receipt, plist_path, argv, app


def test_handoff_receipt_binds_probe_paths_symlinks_and_file_identities(tmp_path: Path) -> None:
    receipt, plist_path, argv, app = _fixture(tmp_path)
    assert receipt["probe"]["proc_pidpath"] == str(app)
    assert receipt["paths"]["cli_executable"].endswith("python-real")
    assert receipt["files"]["program_symlinks"]
    assert receipt["files"]["cli_executable"]["sha256"] == handoff.sha256(b"synthetic Python CLI\n")
    handoff.verify_receipt(
        receipt,
        plist_path=str(plist_path),
        expected_label=receipt["label"],
        expected_argv=argv,
        owner_uid=os.getuid(),
        proc_path=str(app),
    )


@pytest.mark.parametrize("mutation", ["cfg", "app", "symlink", "plist"])
def test_handoff_rejects_changed_chain_config_or_source(tmp_path: Path, mutation: str) -> None:
    receipt, plist_path, argv, app = _fixture(tmp_path)
    if mutation == "cfg":
        (tmp_path / ".venv/pyvenv.cfg").write_text("home = altered\n")
    elif mutation == "app":
        app.write_bytes(b"different executable bytes\n")
    elif mutation == "symlink":
        alternate = tmp_path / ".venv/bin/python-alternate"
        alternate.write_bytes(b"same version, different interpreter\n")
        alternate.chmod(0o700)
        cli = tmp_path / ".venv/bin/python"
        cli.unlink()
        cli.symlink_to(alternate.name)
    else:
        plist_path.write_bytes(plist_path.read_bytes() + b" ")
    with pytest.raises(handoff.HandoffError):
        handoff.verify_receipt(
            receipt,
            plist_path=str(plist_path),
            expected_label=receipt["label"],
            expected_argv=argv,
            owner_uid=os.getuid(),
            proc_path=str(app),
        )


def test_handoff_rejects_alternate_same_version_kernel_binary(tmp_path: Path) -> None:
    receipt, plist_path, argv, _app = _fixture(tmp_path)
    alternate = tmp_path / "other/Python.app/Contents/MacOS/Python"
    alternate.parent.mkdir(parents=True)
    alternate.write_bytes(b"same Python version, alternate binary\n")
    alternate.chmod(0o700)
    with pytest.raises(handoff.HandoffError, match="runtime_path_mismatch"):
        handoff.verify_receipt(
            receipt,
            plist_path=str(plist_path),
            expected_label=receipt["label"],
            expected_argv=argv,
            owner_uid=os.getuid(),
            proc_path=str(alternate),
        )


def test_handoff_requires_plist_program_arguments_to_match(tmp_path: Path) -> None:
    receipt, plist_path, argv, _app = _fixture(tmp_path)
    changed_argv = [*argv[:-1], "0" * 32]
    with pytest.raises(handoff.HandoffError, match="receipt_binding"):
        handoff.verify_receipt(
            receipt,
            plist_path=str(plist_path),
            expected_label=receipt["label"],
            expected_argv=changed_argv,
            owner_uid=os.getuid(),
        )


def test_handoff_probe_fails_closed_when_proc_pidpath_unavailable(tmp_path: Path) -> None:
    receipt, plist_path, argv, _app = _fixture(tmp_path)

    def no_pidpath(_pid: int) -> str:
        raise handoff.HandoffError("handoff_proc_pidpath_unavailable")

    with pytest.raises(handoff.HandoffError, match="proc_pidpath_unavailable"):
        handoff.build_receipt(
            plist_path=str(plist_path),
            expected_label=receipt["label"],
            expected_argv=argv,
            owner_uid=os.getuid(),
            pidpath=no_pidpath,
            spawn_probe=lambda _program, pidpath: (
                {
                    "executable": receipt["probe"]["sys_executable"],
                    "prefix": receipt["probe"]["sys_prefix"],
                    "base_prefix": receipt["probe"]["sys_base_prefix"],
                    "version": receipt["probe"]["python_version"],
                },
                pidpath(123),
            ),
        )


def _launchctl_print(label: str, plist_path: Path, argv: list[str]) -> str:
    lines = [f"gui/{os.getuid()}/{label} = {{", f"\tpath = {plist_path}", "\targuments = {"]
    lines.extend(f"\t\t{item}" for item in argv)
    lines.extend(["\t}", "}"])
    return "\n".join(lines)


def _mock_live_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    list_pid: int | None = None,
    xpc_label: str | None = None,
    launchd_label: str | None = None,
    launchd_argv: list[str] | None = None,
    proc_path: str | None = None,
    process_uid: int | None = None,
) -> tuple[dict[str, Any], list[str], Path]:
    receipt, plist_path, argv, app = _fixture(tmp_path)
    label = receipt["label"]
    raw = handoff.canonical_json(receipt)
    receipt_path = tmp_path / "handoff.json"
    receipt_path.write_bytes(raw)
    receipt_path.chmod(0o600)
    pid = 78901
    monkeypatch.setattr(runtime, "HANDOFF_PATH", receipt_path)
    monkeypatch.setattr(runtime, "PLIST_PATH", plist_path)
    monkeypatch.setattr(runtime, "_expected_argv", lambda _capture_id: argv)
    monkeypatch.setattr(runtime.os, "getpid", lambda: pid)
    monkeypatch.setattr(runtime.sys, "argv", argv[2:])
    monkeypatch.setattr(runtime.sys, "executable", receipt["probe"]["sys_executable"])
    monkeypatch.setattr(runtime.sys, "prefix", receipt["probe"]["sys_prefix"])
    monkeypatch.setattr(runtime.sys, "base_prefix", receipt["probe"]["sys_base_prefix"])
    monkeypatch.setenv("XPC_SERVICE_NAME", xpc_label or label)
    monkeypatch.setattr(
        runtime.interpreter_handoff,
        "_proc_pidpath",
        lambda _pid: proc_path if proc_path is not None else str(app),
    )
    outputs = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout=f"{list_pid if list_pid is not None else pid}\t0\t{label}\n",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=_launchctl_print(launchd_label or label, plist_path, launchd_argv or argv),
            ),
            SimpleNamespace(
                returncode=0,
                stdout=shlex.join([str(app), *argv[1:]]) + "\n",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=f"{process_uid if process_uid is not None else os.getuid()}\n",
            ),
        ]
    )
    monkeypatch.setattr(runtime.subprocess, "run", lambda *_a, **_k: next(outputs))
    return receipt, argv, plist_path


def test_launchd_identity_uses_proc_pidpath_and_loaded_program_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt, argv, _ = _mock_live_identity(monkeypatch, tmp_path)
    assert runtime.launchd_identity(
        runtime.CAPTURE_ID,
        expected_handoff_sha256=handoff.sha256(handoff.canonical_json(receipt)),
    ) == (78901, argv)


def test_launchd_identity_fails_closed_when_proc_pidpath_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_live_identity(monkeypatch, tmp_path)

    def unavailable(_pid: int) -> str:
        raise handoff.HandoffError("handoff_proc_pidpath_unavailable")

    monkeypatch.setattr(runtime.interpreter_handoff, "_proc_pidpath", unavailable)
    with pytest.raises(handoff.HandoffError, match="proc_pidpath_unavailable"):
        runtime.launchd_identity(runtime.CAPTURE_ID)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"list_pid": 78902}, "launchd_list_mismatch"),
        ({"xpc_label": "wrong.label"}, "service_missing"),
        ({"launchd_label": "wrong.label"}, "print_label_mismatch"),
        ({"launchd_argv": ["/other/python"]}, "program_binding"),
        ({"proc_path": "/other/Python"}, "proc_path_mismatch"),
        ({"process_uid": os.getuid() + 1}, "argv_mismatch"),
    ],
)
def test_launchd_identity_rejects_process_or_service_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kwargs: dict[str, Any],
    match: str,
) -> None:
    _mock_live_identity(monkeypatch, tmp_path, **kwargs)
    with pytest.raises(runtime.CaptureRuntimeError, match=match):
        runtime.launchd_identity(runtime.CAPTURE_ID)


def test_launchd_identity_rejects_receipt_digest_and_uid_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt, _argv, _plist = _mock_live_identity(monkeypatch, tmp_path)
    with pytest.raises(runtime.CaptureRuntimeError, match="handoff_changed"):
        runtime.launchd_identity(runtime.CAPTURE_ID, expected_handoff_sha256="0" * 64)
    current_uid = os.getuid()
    monkeypatch.setattr(runtime.os, "getuid", lambda: current_uid + 1)
    with pytest.raises(handoff.HandoffError):
        runtime.launchd_identity(runtime.CAPTURE_ID)
