"""One sealed phase per process; safe logs, per-RPC fencing, no descendants."""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical
from memecoin_ai.execution_targets import export_execution_targets, verify_execution_targets
from memecoin_ai.prospective import create_campaign, resume_campaign, verify_campaign
from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_collector.qualification import endpoint
from memecoin_collector.scheduler import durable
from memecoin_ml.data import read
from memecoin_sampling.contract import NS

from .contract import GateError, authorize, check_attempt, check_bundle
from .supervisor import Watch, disk_guard

Row = dict[str, Any]


def lease_watchdog(fd: int) -> None:
    def watch() -> None:
        try:
            os.read(fd, 1)  # Parent never writes. EOF is death/revocation.
        finally:
            os._exit(125)

    threading.Thread(target=watch, daemon=True).start()


class Fixture:
    def __init__(self, start: int, refs: list[Capture]) -> None:
        self.now = start
        self.calls = 0
        self.refs = {json.loads(c.request_json)["params"][0][0]: c for c in refs}

    def clock(self) -> int:
        self.now += 100000
        return self.now

    def mono(self) -> float:
        return self.now / NS

    def sleep(self, seconds: float) -> None:
        self.now += int(seconds * NS)

    def transport(self, endpoint: str, request: bytes, timeout: float, limit: int) -> bytes:
        self.calls += 1
        cap = self.refs[json.loads(request)["params"][0][0]]
        payload = json.loads(cap.response)
        slot = payload["result"]["context"]["slot"] + self.now // NS
        payload["result"]["context"]["slot"] = slot
        raw = bytearray(base64.b64decode(payload["result"]["value"][6]["data"][0]))
        struct.pack_into("<Q", raw, 0, slot)
        struct.pack_into("<q", raw, 32, self.now // NS - 2)
        payload["result"]["value"][6]["data"][0] = base64.b64encode(raw).decode()
        return canonical(payload).encode()


def execute(bundle: Path, output: Path, jobpath: Path) -> None:
    job = read(jobpath)
    if os.getppid() != job["parent_pid"]:
        raise GateError("orphan_worker")
    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    c = check_bundle(bundle, job["bundle_sha"])
    phase = job["phase"]
    if phase not in ("init", "segment0", "segment1", "qa"):
        raise GateError("unknown_phase")
    marker = output / f"{phase}-started.json"
    durable(marker, dict(pid=os.getpid(), host_ns=time.time_ns()))
    if c["mode"] == "synthetic_fixture":

        def deny_network(*args: Any, **kwargs: Any) -> Any:
            raise GateError("fixture_network_forbidden")

        socket.socket.connect = deny_network  # type: ignore[method-assign]

        def audit(event: str, args: Any) -> None:
            if event == "socket.connect":
                raise GateError("fixture_network_forbidden")

        sys.addaudithook(audit)
    refs = [Capture.load(bundle / "references" / f"{i:03}") for i in range(3)]
    fixture = Fixture(job["logical_ns"], refs) if c["mode"] == "synthetic_fixture" else None
    clock = fixture.clock if fixture else time.time_ns
    mono = fixture.mono if fixture else time.monotonic
    sleep = fixture.sleep if fixture else time.sleep
    watch = Watch(210 if phase.startswith("segment") else 600)
    campaign = output / "campaign"

    def guard() -> None:
        if os.getppid() != job["parent_pid"]:
            raise GateError("orphan_worker")
        if watch.remaining() <= 0 or time.time_ns() >= job["host_deadline_ns"]:
            raise GateError("worker_deadline")
        check_bundle(bundle, job["bundle_sha"])
        disk_guard(output, floor=c["free_floor"], limit=c["window_bytes"])
        if (output.parent.parent / "STOP").exists():
            raise GateError("stop_requested")
        if fixture is None:
            decision = authorize(
                bundle,
                job["bundle_sha"],
                Path(job["receipt"]) if job["receipt"] else None,
                job["receipt_pin"],
                scope=job["scope"],
                credential=endpoint(Path(c["settings"])),
                now_ns=time.time_ns(),
            )

            check_attempt(bundle, c, decision, output, job["scope"])

    guard()
    requests = 0
    if phase == "init":
        if fixture:
            durable(
                output / "fixture-disclosure.json",
                dict(
                    data_kind="synthetic_fixture",
                    transformations="old D40 public bytes; synthetic Clock/slot/receipt",
                    real_rpc=0,
                ),
            )
        create_campaign(
            campaign,
            refs,
            source_alias="calendar-alchemy",
            endpoint_origin="https://solana-mainnet.g.alchemy.com",
            transport_profile="stdlib_persistent_v1",
            segments=2,
            rounds=30,
            interval_seconds=5,
            clock=clock,
        )
    elif phase.startswith("segment"):
        index = int(phase[-1])
        state = verify_campaign(campaign)
        if len(state["segments"]) != index or any(
            not s["report"]["complete"] for s in state["segments"]
        ):
            raise GateError("segment_restart_or_previous_failure")
        allowed = [json.loads(x.request_json)["params"] for x in refs]

        class GuardedRPC(ReadOnlyRPC):
            def fetch(self, method: str, params: list[object]) -> Capture:
                nonlocal requests
                try:
                    guard()
                except GateError as exc:
                    print(json.dumps(dict(status="failed", category=str(exc))), flush=True)
                    raise
                if requests >= 90 or method != "getMultipleAccounts" or params not in allowed:
                    raise GateError("rpc_budget_or_scope")
                requests += 1  # Charge even ambiguous transport errors.
                return super().fetch(method, params)

        value = "https://solana-mainnet.g.alchemy.com" if fixture else endpoint(Path(c["settings"]))
        client = (
            GuardedRPC(
                value,
                "calendar-alchemy",
                timeout=15,
                max_bytes=1000000,
                transport=fixture.transport,
                clock=clock,
            )
            if fixture
            else GuardedRPC(
                value, "calendar-alchemy", timeout=15, max_bytes=1000000, keep_alive=True
            )
        )
        if fixture:
            client.transport_profile = "stdlib_persistent_v1"
        try:
            state = resume_campaign(
                campaign,
                client,
                clock=clock,
                monotonic=mono,
                sleep=sleep,
                emit=lambda row: print(
                    json.dumps(
                        {
                            k: row.get(k)
                            for k in ("status", "category", "error_type", "http_status", "rpc_code")
                        }
                    ),
                    flush=True,
                ),
            )
        finally:
            client.close()
        if not state["segments"][-1]["report"]["complete"]:
            raise GateError("first_rpc_error_closes_window")
    else:
        state = verify_campaign(campaign)
        if not state["complete"] or state["successful_snapshots"] != 180:
            raise GateError("incomplete_raw_window")
        export_execution_targets(campaign, output / "campaign-targets")
        verify_execution_targets(campaign, output / "campaign-targets")
    guard()
    durable(
        output / f"{phase}-result.json",
        dict(
            status="completed",
            finished_ns=clock(),
            attempted_rpc=requests,
            fake_rpc=requests if fixture else 0,
        ),
    )
    print(json.dumps(dict(phase=phase, status="completed", attempted_rpc=requests)), flush=True)


def main() -> None:
    try:
        execute(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    status="failed",
                    category=str(exc)
                    if isinstance(exc, GateError)
                    else "worker_integrity_or_protocol",
                    error_type=type(exc).__name__,
                )
            ),
            flush=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
