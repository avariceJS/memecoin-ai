#!/usr/bin/env python3
"""Path-only LaunchAgent entrypoint for a reviewed finite external Senses lease."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_ai.sources import PersistentHTTPTransport, ReadOnlyRPC  # noqa: E402
from memecoin_research_storage import StorageError, writer_guard  # noqa: E402
from tools import brain_senses_external_clock_v1 as clock  # noqa: E402
from tools import brain_senses_external_orchestrator_v1 as engine  # noqa: E402
from tools import brain_senses_external_verify_v1 as verify  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402
from tools.brain_senses_service_v1_runner import _load_private_endpoint  # noqa: E402

C4_ROOT = REPO / "work/brain-senses-live-helius-inventory-v3-20261010-4"
C4_TERMINAL = REPO / "docs/evidence/helius-inventory-v3-c4-abnormal-terminal-2026-10-11.json"
HANDOFF = REPO / "docs/evidence/senses-external-storage-handoff-2026-10-11.json"
QUALIFICATION_PATHS = (
    REPO / "docs/evidence/senses-storage-detach-qualification-2026-10-12-v5.json",
    REPO / "docs/evidence/senses-kingston-writer-qualification-2026-10-12-v4.json",
    REPO / "docs/evidence/senses-external-recovery-qualification-2026-10-12-v5.json",
)


def _require(value: bool, reason: str) -> None:
    if not value:
        raise StorageError(reason)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _open_absolute(path: Path) -> int:
    _require(path.is_absolute() and ".." not in path.parts, "unsafe_absolute_directory")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(path: Path, *, private: bool = False) -> bytes:
    parent = _open_absolute(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == os.getuid()
            and before.st_nlink == 1
            and before.st_size <= 4_000_000,
            "unsafe_binding_file",
        )
        if private:
            _require(stat.S_IMODE(before.st_mode) in (0o400, 0o600), "binding_not_private")
        out = bytearray()
        while chunk := os.read(fd, 65536):
            out.extend(chunk)
            _require(len(out) <= 4_000_000, "binding_file_grew")
        after = os.fstat(fd)
        _require(
            (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "binding_changed",
        )
        return bytes(out)
    finally:
        os.close(fd)


@contextlib.contextmanager
def host_lock() -> Iterator[None]:
    """Same inode as legacy runner lock, anchored to the internal work descriptor."""
    parent = _open_absolute(REPO / "work")
    try:
        fd = os.open(
            ".brain-senses-host-writer-v1.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        entry = os.fstat(fd)
        _require(
            stat.S_ISREG(entry.st_mode)
            and entry.st_uid == os.getuid()
            and entry.st_nlink == 1
            and stat.S_IMODE(entry.st_mode) == 0o600,
            "host_lock_identity",
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def open_service(guard: Any, relative: str, *, create: bool = False) -> int:
    _require(bool(re.fullmatch(r"services/[A-Za-z0-9_-]+", relative)), "invalid_service_path")
    guard.revalidate()
    fd = os.dup(guard.mount_fd)
    try:
        pieces = [*guard.config.data_root.split("/"), *relative.split("/")]
        for index, part in enumerate(pieces):
            if create and index >= len(pieces) - 2:
                guard.revalidate()
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    _require(index != len(pieces) - 1, "service_id_already_exists")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                entry = os.fstat(child)
                _require(
                    entry.st_dev == os.fstat(guard.mount_fd).st_dev
                    and entry.st_uid == os.getuid()
                    and stat.S_IMODE(entry.st_mode) == 0o700,
                    "service_directory_identity",
                )
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)


def runtime_gate(label: str, plist: dict[str, Any], *, running: bool) -> None:
    domain = f"gui/{os.getuid()}"
    table = _command(["/bin/launchctl", "list"])
    disabled = _command(["/bin/launchctl", "print-disabled", domain])
    _require(table.returncode == disabled.returncode == 0, "launchd_inventory_unavailable")
    labels = {line.split()[-1] for line in table.stdout.splitlines()[1:] if line.split()}
    retired = json.loads(_read(HANDOFF))["runtime"]["retired_senses_agents"]
    for entry in retired:
        old = entry["label"]
        _require(
            old not in labels
            and re.search(r'"' + re.escape(old) + r'"\s*=>\s*(?:true|disabled)\b', disabled.stdout)
            is not None,
            "retired_agent_not_disabled",
        )
    _require(
        not any(item.startswith("ai.memecoin.brain.senses.") and item != label for item in labels),
        "another_senses_agent_loaded",
    )
    if running:
        info = _command(["/bin/launchctl", "print", f"{domain}/{label}"])
        _require(
            info.returncode == 0
            and re.search(r"\bpid = " + str(os.getpid()) + r"\b", info.stdout) is not None
            and os.getppid() == 1,
            "not_reviewed_launchd_process",
        )
        _require(
            plist.get("ProgramArguments")
            == [str(REPO / ".venv/bin/python"), str(Path(__file__).resolve()), *sys.argv[1:]],
            "launchd_argv_mismatch",
        )


def activation_gate(
    activation_path: Path, config_path: Path, *, running: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    activation = verify.decode(_read(activation_path, private=True))
    _require(
        isinstance(activation, dict)
        and activation.get("schema") == "brain-senses-external-launch-v1",
        "activation_schema",
    )
    plan_path = Path(activation["plan_path"])
    plan_bytes = _read(plan_path, private=True)
    _require(_sha(plan_bytes) == activation["plan_sha256"], "activation_plan_hash")
    plan = verify.decode(plan_bytes)
    verify.verify_plan(plan, engine.source_bindings())
    _require(
        _sha(_read(config_path, private=True)) == activation["storage_config_sha256"],
        "activation_storage_config",
    )
    review_bytes = _read(Path(activation["review_receipt_path"]))
    _require(_sha(review_bytes) == activation["review_receipt_sha256"], "review_receipt_hash")
    review = verify.decode(review_bytes)
    _require(
        review.get("decision") == "approved_for_finite_activation"
        and review.get("plan_sha256") == activation["plan_sha256"]
        and review.get("source_sha256") == plan["code_sha256"],
        "review_scope",
    )
    receipts = review.get("qualification_receipts")
    _require(
        isinstance(receipts, list)
        and len(receipts) == len(QUALIFICATION_PATHS)
        and {entry.get("path") for entry in receipts}
        == {str(path) for path in QUALIFICATION_PATHS},
        "required_qualification_receipts",
    )
    for receipt in review["qualification_receipts"]:
        _require(
            _sha(_read(Path(receipt["path"]))) == receipt["sha256"], "qualification_receipt_hash"
        )
    _require(review.get("expires_ns") == plan["schedule"]["expires_ns"], "review_expiry")
    _require(
        _sha(_read(C4_ROOT / "STOP")) == plan["predecessor"]["stop_sha256"]
        and _sha(_read(C4_TERMINAL)) == plan["predecessor"]["terminal_receipt_sha256"],
        "c4_terminal_changed",
    )
    prior = json.loads(_read(HANDOFF))["runtime"]["c4"]
    _require(
        plan["predecessor"]["committed_frontier"] == prior["observed_frontier"]
        and plan["predecessor"]["gap_intervals"] == prior["gap_intervals"],
        "predecessor_lineage",
    )
    for name, digest in prior["pins"].items():
        _require(_sha(_read(C4_ROOT / name)) == digest, "c4_state_changed")
    plist_bytes = _read(Path(activation["plist_path"]), private=True)
    _require(_sha(plist_bytes) == activation["plist_sha256"], "activation_plist_hash")
    plist = plistlib.loads(plist_bytes)
    label = activation["label"]
    _require(
        label == f"ai.memecoin.brain.senses.{plan['service_id']}" and plist.get("Label") == label,
        "launchd_label",
    )
    _require(
        plist.get("StandardOutPath") == "/dev/null"
        and plist.get("StandardErrorPath") == "/dev/null"
        and not plist.get("EnvironmentVariables")
        and not plist.get("RunAtLoad")
        and not plist.get("KeepAlive"),
        "unsafe_launchd_configuration",
    )
    expected_argv = [
        str(REPO / ".venv/bin/python"),
        str(Path(__file__).resolve()),
        "run",
        "--activation",
        str(activation_path),
        "--storage-config",
        str(config_path),
    ]
    _require(
        plist.get("ProgramArguments") == expected_argv
        and plist.get("WorkingDirectory") == str(REPO),
        "plist_entrypoint",
    )
    calendar = []
    for phase in ("bootstrap", "frame"):
        start = plan["schedule"][phase]["start_ns"]
        _require(start % 60_000_000_000 == 0, "launch_bucket_not_minute_aligned")
        local = datetime.fromtimestamp(start // 1_000_000_000, UTC).astimezone()
        calendar.append(
            {"Month": local.month, "Day": local.day, "Hour": local.hour, "Minute": local.minute}
        )
    _require(plist.get("StartCalendarInterval") == calendar, "plist_calendar_binding")
    runtime_gate(label, plist, running=running)
    return activation, plan


def _client(
    plan: dict[str, Any], writer: DescriptorWriter, guard: Any, bucket: str
) -> tuple[ReadOnlyRPC, Callable[[], None]]:
    endpoint = _load_private_endpoint()
    _require(endpoint is not None, "private_endpoint_unavailable")
    assert endpoint is not None
    phase = "bootstrap" if bucket == plan["schedule"]["bootstrap"]["bucket"] else "frame"
    window = plan["schedule"][phase]
    _require("SSLKEYLOGFILE" not in os.environ, "tls_key_logging_forbidden")
    backend = PersistentHTTPTransport()
    last_start = 0.0

    def transport(url: str, body: bytes, timeout: float, max_bytes: int) -> bytes:
        nonlocal last_start
        _require(url == endpoint, "transport_endpoint_changed")
        delay = 0.25 - (time.monotonic() - last_start)
        if delay > 0:
            time.sleep(delay)
        guard.revalidate(
            min_free_bytes=plan["caps"]["disk_floor_bytes"]
            + plan["caps"]["stored_bytes_total"]
            + plan["caps"]["peak_write_margin_bytes"]
        )
        requests = sorted(
            path for path in writer.list_files() if path.startswith(f"buckets/{bucket}/requests/")
        )
        _require(bool(requests), "transport_without_intent")
        intent = verify.decode(writer.read_bytes(requests[-1]))
        _require(intent["wire_request_sha256"] == _sha(body), "transport_intent_mismatch")
        observation_bytes = writer.read_bytes(intent["clock_check_path"])
        _require(_sha(observation_bytes) == intent["clock_check_sha256"], "transport_clock_hash")
        now, mono = time.time_ns(), time.monotonic_ns()
        clock.validate_progress(verify.decode(observation_bytes), wall_ns=now, monotonic_ns=mono)
        _require(
            window["start_ns"] + 5_000_000_000
            <= now
            <= min(window["end_ns"] - 5_000_000_000, plan["schedule"]["expires_ns"]),
            "transport_outside_window",
        )
        last_start = time.monotonic()
        return backend(url, body, min(timeout, max(0.1, (window["end_ns"] - now) / 1e9)), max_bytes)

    client = ReadOnlyRPC(
        endpoint,
        plan["provider"],
        timeout=20,
        max_bytes=plan["caps"]["max_response_bytes"],
        transport=transport,
    )
    return client, backend.close


def execute(command: str, *, activation_path: Path, config_path: Path) -> dict[str, Any]:
    activation, plan = activation_gate(activation_path, config_path, running=command == "run")
    required = (
        plan["caps"]["disk_floor_bytes"]
        + plan["caps"]["stored_bytes_total"]
        + plan["caps"]["peak_write_margin_bytes"]
    )
    with host_lock(), writer_guard(config_path, min_free_bytes=required) as guard:
        current_activation, current_plan = activation_gate(
            activation_path, config_path, running=command == "run"
        )
        _require(
            current_activation == activation and current_plan == plan,
            "activation_changed_under_lock",
        )
        _require(
            plan["storage"]["root_id"] == guard.root_id
            and plan["storage"]["volume_uuid"].lower() == guard.volume_uuid.lower(),
            "plan_storage_identity",
        )
        fd = open_service(guard, plan["storage"]["relative_root"], create=command == "init")
        try:

            def before_write() -> None:
                guard.revalidate(min_free_bytes=required)

            with DescriptorWriter(fd, before_write=before_write) as writer:
                if command == "init":
                    _require(
                        time.time_ns() + 60_000_000_000
                        <= plan["schedule"]["bootstrap"]["start_ns"],
                        "init_too_late",
                    )
                    return engine.initialize(writer, plan)
                with verify.DescriptorReader(fd) as reader:
                    state = verify.verify(
                        reader,
                        expected_plan_sha256=verify.sha(verify.canonical(plan)),
                        expected_sources=engine.source_bindings(),
                    )
                if command == "status" or state["status"] == "stopped":
                    return state
                unfinished = [
                    phase
                    for phase in ("bootstrap", "frame")
                    if state["buckets"][plan["schedule"][phase]["bucket"]]["status"] != "sealed"
                ]
                phase = unfinished[0] if unfinished else "frame"
                bucket = plan["schedule"][phase]["bucket"]
                window = plan["schedule"][phase]
                delay_ns = window["start_ns"] + 10_000_000_000 - time.time_ns()
                if 0 < delay_ns <= 15_000_000_000:
                    # launchd fires on the minute; wait into the reviewed margin.
                    # All dispatch gates run again after this bounded wait.
                    time.sleep(delay_ns / 1_000_000_000)
                closers: list[Callable[[], None]] = []

                def loader() -> ReadOnlyRPC:
                    client, close = _client(plan, writer, guard, bucket)
                    closers.append(close)
                    return client

                try:
                    return engine.run_bucket(
                        writer, bucket=bucket, client_loader=loader, guard=guard
                    )
                finally:
                    for close in closers:
                        close()

        finally:
            os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "run", "status"))
    parser.add_argument("--activation", type=Path, required=True)
    parser.add_argument("--storage-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = execute(
            args.command, activation_path=args.activation, config_path=args.storage_config
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_class": type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
