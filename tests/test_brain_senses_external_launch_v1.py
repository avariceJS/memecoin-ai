from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_research_storage import StorageError
from tools import brain_senses_external_launch_v1 as launch


def test_read_binding_rejects_links_and_broad_private_mode(tmp_path: Path) -> None:
    item = tmp_path / "activation.json"
    item.write_bytes(b"{}")
    item.chmod(0o600)
    assert launch._read(item, private=True) == b"{}"
    (tmp_path / "alias").symlink_to(item)
    with pytest.raises(OSError):
        launch._read(tmp_path / "alias", private=True)
    item.chmod(0o644)
    with pytest.raises(StorageError, match="binding_not_private"):
        launch._read(item, private=True)
    item.chmod(0o600)
    os.link(item, tmp_path / "hard")
    with pytest.raises(StorageError, match="unsafe_binding_file"):
        launch._read(item, private=True)


def test_service_creation_bound_to_held_descriptor_after_path_replacement(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    mount.mkdir(mode=0o700)
    (mount / "BrainData").mkdir(mode=0o700)
    fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    replaced = False

    def revalidate() -> None:
        nonlocal replaced
        if not replaced:
            mount.rename(tmp_path / "detached")
            mount.mkdir(mode=0o700)
            replaced = True

    guard = SimpleNamespace(
        mount_fd=fd, config=SimpleNamespace(data_root="BrainData"), revalidate=revalidate
    )
    try:
        service = launch.open_service(guard, "services/test", create=True)
        os.close(service)
        assert list(mount.iterdir()) == []
        assert (tmp_path / "detached/BrainData/services/test").is_dir()
        with pytest.raises(StorageError, match="service_id_already_exists"):
            launch.open_service(guard, "services/test", create=True)
        with pytest.raises(StorageError, match="invalid_service_path"):
            launch.open_service(guard, "services/../../escape", create=True)
    finally:
        os.close(fd)


@pytest.mark.parametrize("failure", ["parallel", "retired_enabled", "manual_pid"])
def test_live_runtime_gate_rejects_parallel_or_unbound_process(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    label = "ai.memecoin.brain.senses.test"
    retired = "ai.memecoin.brain.senses.old"
    handoff = {"runtime": {"retired_senses_agents": [{"label": retired}]}}
    monkeypatch.setattr(launch, "_read", lambda _: json.dumps(handoff).encode())

    def command(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[1] == "list":
            out = "PID Status Label\n- 0 " + label + "\n"
            if failure == "parallel":
                out += "- 0 ai.memecoin.brain.senses.another\n"
        elif args[1] == "print-disabled":
            out = '"' + retired + '" => ' + ("false" if failure == "retired_enabled" else "true")
        else:
            out = "pid = 999999999"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(launch, "_command", command)
    with pytest.raises(StorageError):
        launch.runtime_gate(label, {}, running=True)


def test_activation_requires_exact_qualification_set_and_immutable_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import plistlib
    from datetime import UTC, datetime

    from tools.brain_senses_external_orchestrator_v1 import build_plan
    from tools.brain_senses_external_verify_v1 import canonical, sha

    def save(name: str, value: object) -> Path:
        path = tmp_path / name
        path.write_bytes(canonical(value))
        path.chmod(0o600)
        return path

    at = 1_800_000_000_000_000_000
    c4 = tmp_path / "c4"
    c4.mkdir()
    for name in ("STOP", "frontier-v3.json", "active-frame-reservation.json"):
        (c4 / name).write_bytes(b"{}\n")
    terminal = save("terminal.json", {"status": "terminal"})
    predecessor = {
        "campaign_id": "c4",
        "stop_sha256": sha((c4 / "STOP").read_bytes()),
        "terminal_receipt_sha256": sha(terminal.read_bytes()),
        "committed_frontier": 80,
        "gap_intervals": [],
        "last_inventoried_interval": None,
    }
    p = build_plan(
        service_id="activation-test",
        root_id="rootid",
        volume_uuid="volumeid",
        relative_root="services/activation-test",
        predecessor=predecessor,
        clock_preflight_ns=at - 120 * 10**9,
        bootstrap_start_ns=at,
        frame_start_ns=at + 300 * 10**9,
        expires_ns=at + 480 * 10**9,
        endpoint_origin="https://mainnet.helius-rpc.com",
    )
    plan_path = save("plan.json", p)
    cfg = save("config.json", {})
    handoff = save(
        "handoff.json",
        {
            "runtime": {
                "c4": {
                    "observed_frontier": 80,
                    "gap_intervals": [],
                    "pins": {
                        name: sha((c4 / name).read_bytes())
                        for name in ("STOP", "frontier-v3.json", "active-frame-reservation.json")
                    },
                }
            }
        },
    )
    qualification_paths = tuple(
        save(
            f"qualification-{i}.json",
            {
                "status": "passed",
                "source_sha256": p["code_sha256"],
                "provenance": {
                    "source_sha256": {
                        "descriptor_writer": p["code_sha256"][
                            "tools/brain_senses_external_writer_v1.py"
                        ],
                        "storage_library": p["code_sha256"][
                            "src/memecoin_research_storage/__init__.py"
                        ],
                    }
                },
            },
        )
        for i in range(3)
    )
    monkeypatch.setattr(launch, "QUALIFICATION_PATHS", qualification_paths)
    monkeypatch.setattr(launch, "C4_ROOT", c4)
    monkeypatch.setattr(launch, "C4_TERMINAL", terminal)
    monkeypatch.setattr(launch, "HANDOFF", handoff)
    monkeypatch.setattr(launch, "runtime_gate", lambda *a, **kw: None)
    receipts = [
        {"path": str(path), "sha256": sha(path.read_bytes())} for path in qualification_paths
    ]
    review = save(
        "review.json",
        {
            "decision": "approved_for_finite_activation",
            "plan_sha256": sha(plan_path.read_bytes()),
            "source_sha256": p["code_sha256"],
            "qualification_receipts": receipts,
            "expires_ns": p["schedule"]["expires_ns"],
        },
    )
    activation_path = tmp_path / "activation.json"
    plist_path = tmp_path / "launch.plist"
    calendar = []
    for phase in ("bootstrap", "frame"):
        local = datetime.fromtimestamp(p["schedule"][phase]["start_ns"] // 10**9, UTC).astimezone()
        calendar.append(
            {"Month": local.month, "Day": local.day, "Hour": local.hour, "Minute": local.minute}
        )
    label = "ai.memecoin.brain.senses.activation-test"
    plist = {
        "Label": label,
        "ProgramArguments": [
            str(launch.REPO / ".venv/bin/python"),
            str(Path(launch.__file__).resolve()),
            "run",
            "--activation",
            str(activation_path),
            "--storage-config",
            str(cfg),
        ],
        "WorkingDirectory": str(launch.REPO),
        "StartCalendarInterval": calendar,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "RunAtLoad": False,
        "KeepAlive": False,
    }
    plist_path.write_bytes(plistlib.dumps(plist))
    plist_path.chmod(0o600)
    activation = {
        "schema": "brain-senses-external-launch-v1",
        "plan_path": str(plan_path),
        "plan_sha256": sha(plan_path.read_bytes()),
        "storage_config_sha256": sha(cfg.read_bytes()),
        "review_receipt_path": str(review),
        "review_receipt_sha256": sha(review.read_bytes()),
        "plist_path": str(plist_path),
        "plist_sha256": sha(plist_path.read_bytes()),
        "label": label,
    }
    save("activation.json", activation)
    assert launch.activation_gate(activation_path, cfg, running=False)[1] == p
    review_value = json.loads(review.read_bytes())
    review_value["qualification_receipts"] = []
    save("review.json", review_value)
    activation["review_receipt_sha256"] = sha(review.read_bytes())
    save("activation.json", activation)
    with pytest.raises(StorageError, match="required_qualification_receipts"):
        launch.activation_gate(activation_path, cfg, running=False)


@pytest.mark.parametrize("disabled_token", ["true", "disabled"])
def test_runtime_gate_accepts_only_bound_launchd_pid_and_argv(
    monkeypatch: pytest.MonkeyPatch,
    disabled_token: str,
) -> None:
    import sys

    label = "ai.memecoin.brain.senses.test"
    retired = "ai.memecoin.brain.senses.old"
    monkeypatch.setattr(
        launch,
        "_read",
        lambda _: json.dumps({"runtime": {"retired_senses_agents": [{"label": retired}]}}).encode(),
    )
    monkeypatch.setattr(launch.os, "getppid", lambda: 1)

    def command(args: list[str]) -> subprocess.CompletedProcess[str]:
        output = (
            "PID Status Label\n- 0 " + label
            if args[1] == "list"
            else f'"{retired}" => {disabled_token}'
            if args[1] == "print-disabled"
            else f"pid = {os.getpid()}"
        )
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(launch, "_command", command)
    plist = {
        "ProgramArguments": [
            str(launch.REPO / ".venv/bin/python"),
            str(Path(launch.__file__).resolve()),
            *sys.argv[1:],
        ]
    }
    launch.runtime_gate(label, plist, running=True)
    plist["ProgramArguments"].append("--unexpected")
    with pytest.raises(StorageError, match="argv_mismatch"):
        launch.runtime_gate(label, plist, running=True)


def test_bound_live_transport_rejects_redirect_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from memecoin_ai import sources
    from tools import brain_senses_external_verify_v1 as verify

    calls = []

    class Connection:
        def __init__(self, host: str, *args: object, **kwargs: object) -> None:
            calls.append(("connection", host))
            self.sock = SimpleNamespace(settimeout=lambda _: None)

        def connect(self) -> None:
            pass

        def request(self, *args: object, **kwargs: object) -> None:
            calls.append(("request", args[0]))

        def getresponse(self) -> object:
            return SimpleNamespace(status=302)

        def close(self) -> None:
            self.sock = None

    monkeypatch.setattr(sources.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(
        launch,
        "_load_private_endpoint",
        lambda: "https://mainnet.helius-rpc.com/?api-key=synthetic-test",
    )
    monkeypatch.setattr(launch.clock, "validate_progress", lambda *args, **kwargs: None)
    now = time.time_ns()
    p = {
        "provider": "helius-free",
        "schedule": {
            "bootstrap": {"bucket": "b", "start_ns": now - 10 * 10**9, "end_ns": now + 50 * 10**9},
            "frame": {"bucket": "f"},
            "expires_ns": now + 100 * 10**9,
        },
        "caps": {
            "disk_floor_bytes": 50 * 2**30,
            "stored_bytes_total": 288000000,
            "peak_write_margin_bytes": 290000000,
            "max_response_bytes": 32000000,
        },
    }
    body = sources.canonical(
        {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": [{"commitment": "finalized"}]}
    ).encode()
    clock_bytes = b"{}\n"
    intent = {
        "wire_request_sha256": verify.sha(body),
        "clock_check_path": "clock/b-00.json",
        "clock_check_sha256": verify.sha(clock_bytes),
    }
    writer = SimpleNamespace(
        list_files=lambda: ["buckets/b/requests/00.json"],
        read_bytes=lambda path: (
            clock_bytes if path.startswith("clock/") else verify.canonical(intent)
        ),
    )
    guard = SimpleNamespace(revalidate=lambda **kwargs: None)
    client, close = launch._client(p, writer, guard, "b")
    try:
        with pytest.raises(sources.HTTPSourceError) as error:
            client.fetch("getSlot", [{"commitment": "finalized"}])
        assert error.value.status == 302
        assert calls == [("connection", "mainnet.helius-rpc.com"), ("request", "POST")]
    finally:
        close()
