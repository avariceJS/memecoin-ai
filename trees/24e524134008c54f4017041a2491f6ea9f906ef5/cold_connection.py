"""Bounded phase observation of the unchanged stdlib transport; no credentials in logs."""

import argparse
import json
import socket
import ssl
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from memecoin_ai.domain import digest
from memecoin_ai.prospective import hashes, read, write
from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_ai.tokens import decode_supported_checkpoint


def settings():
    result = {}
    for line in Path(".env.local").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("\"'")
    return result


def observed(phase, function, emit):
    def call(*args, **kwargs):
        start = time.monotonic_ns()
        emit(dict(phase=phase, event="start", available_ns=time.time_ns()))
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            emit(
                dict(
                    phase=phase,
                    event="error",
                    available_ns=time.time_ns(),
                    elapsed_ns=time.monotonic_ns() - start,
                    error_type=type(exc).__name__,
                    errno=getattr(exc, "errno", None),
                )
            )
            raise
        emit(
            dict(
                phase=phase,
                event="end",
                available_ns=time.time_ns(),
                elapsed_ns=time.monotonic_ns() - start,
            )
        )
        return result

    return call


@contextmanager
def phase_observer(emit):
    with ExitStack() as stack:
        for owner, name, phase in [
            (socket, "getaddrinfo", "dns"),
            (socket.socket, "connect", "tcp"),
            (ssl.SSLContext, "wrap_socket", "tls"),
        ]:
            stack.enter_context(
                patch.object(owner, name, observed(phase, getattr(owner, name), emit))
            )
        yield


def probe(root, alias, plan):
    root.mkdir(parents=True, exist_ok=False)
    write(
        root / "reservation.json",
        dict(alias=alias, origin=plan["origins"][alias], started_ns=time.time_ns(), max_requests=2),
    )
    endpoint = (
        settings().get("MEMECOIN_ALCHEMY_HTTP_URL")
        if alias == "alchemy"
        else plan["origins"][alias]
    )
    if not endpoint:
        write(
            root / "result.json", dict(complete=False, category="missing_local_setting", requests=0)
        )
        return
    cap = Capture.load(Path(plan["reference"]))
    req = json.loads(cap.request_json)
    pool = req["params"][0][0]
    client = ReadOnlyRPC(
        endpoint,
        "cold-" + alias,
        timeout=plan["rpc_timeout_seconds"],
        max_bytes=1_000_000,
        keep_alive=True,
    )
    if client.endpoint_origin != plan["origins"][alias]:
        raise ValueError("origin differs from frozen plan")
    result = dict(complete=False, requests=0, accepted=0, attempts=[])
    with (root / "phases.jsonl").open("x") as log:

        def emit(row):
            log.write(json.dumps(row) + "\n")
            log.flush()

        try:
            with phase_observer(emit):
                for i in range(plan["max_rpc_per_connection"]):
                    result["requests"] += 1
                    row = dict(index=i, started_ns=time.time_ns())
                    try:
                        new = client.fetch(req["method"], req["params"])
                        new.save(root / f"capture-{i}")
                        decoded = decode_supported_checkpoint(new, pool, config_layout_version=2)
                        row.update(status="accepted", slot=decoded["context_slot"])
                        result["accepted"] += 1
                    except Exception as exc:
                        row.update(
                            status="error",
                            error_type=type(exc).__name__,
                            category=getattr(exc, "reason", "validation_or_local"),
                            http_status=getattr(exc, "status", None),
                        )
                    row.update(
                        available_ns=time.time_ns(), transport=client.transport_measurement()
                    )
                    result["attempts"].append(row)
                    emit(dict(event="rpc_result", **row))
                    if row["status"] == "error":
                        print(json.dumps(dict(alias=alias, **row)), flush=True)
                        break
        finally:
            client.close()
    result["complete"] = result["accepted"] == plan["max_rpc_per_connection"]
    write(root / "result.json", result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "probe"])
    parser.add_argument("root", type=Path)
    parser.add_argument("--alias", choices=["alchemy", "public"])
    args = parser.parse_args()
    plan = read(Path("configs/cold-connection-v1.json"))
    if args.action == "probe":
        probe(args.root, args.alias, plan)
        return
    root = args.root
    root.mkdir(parents=True, exist_ok=False)
    write(root / "plan.json", plan)
    write(
        root / "code.json",
        dict(
            commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            tool_sha256=digest(Path(__file__).read_bytes()),
            seed=None,
        ),
    )
    results = []
    for i, alias in enumerate(plan["order"]):
        if i == 2:
            print("Second diagnostic round in 60 seconds", flush=True)
            time.sleep(plan["round_gap_seconds"])
        trial = root / f"trial-{i}"
        try:
            proc = subprocess.run(
                [sys.executable, __file__, "probe", str(trial), "--alias", alias],
                timeout=plan["child_wall_seconds"],
                check=False,
            )
            if proc.returncode or not (trial / "result.json").exists():
                result = dict(
                    complete=False, category="local_child_failure", exit_code=proc.returncode
                )
            else:
                result = read(trial / "result.json")
        except subprocess.TimeoutExpired:
            result = dict(complete=False, category="child_wall_deadline")
        write(trial / "seal.json", dict(files=hashes(trial), result=result))
        results.append(dict(alias=alias, path=str(trial), **result))
        print(json.dumps(results[-1]), flush=True)
        if sum(not r["complete"] for r in results) >= plan["max_failed_trials"]:
            break
    eligible = []
    for alias in plan["origins"]:
        trials = [r for r in results if r["alias"] == alias]
        if trials and trials[-1]["complete"]:
            eligible.append((sum(r["complete"] for r in trials), alias == "alchemy", alias))
    chosen = max(eligible)[2] if eligible else None
    write(
        root / "decision.json", dict(selected=chosen, trials=results, production_reliability=False)
    )
    write(root / "manifest.json", hashes(root))
    print(json.dumps(dict(selected=chosen, diagnostic_finished=True)), flush=True)


if __name__ == "__main__":
    main()
