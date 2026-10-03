"""Two preregistered calendar windows, existing corpus math, no failed-window retries."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from cold_connection import settings
from qualified_http_corpus import require_clean_stage_boundary

from memecoin_ai.domain import digest
from memecoin_ai.prospective import create_campaign, hashes, read, resume_campaign, write
from memecoin_ai.sources import Capture, ReadOnlyRPC


def window_due_ns(stage_start, first_created, index, separation_seconds):
    if index == 0:
        return stage_start
    if first_created is None:
        raise ValueError("first campaign creation time required before another window")
    return first_created + index * separation_seconds * 10**9


def collect(root):
    require_clean_stage_boundary(root)
    plan = read(root / "plan.json")
    alias = read(root / "stage-plan.json")["selected"]
    endpoint = (
        settings()["MEMECOIN_ALCHEMY_HTTP_URL"] if alias == "alchemy" else plan["endpoint_origin"]
    )
    client = ReadOnlyRPC(
        endpoint,
        plan["source_alias"],
        timeout=plan["timeout_seconds"],
        max_bytes=plan["max_response_bytes"],
        keep_alive=True,
    )
    try:
        state = resume_campaign(
            root,
            client,
            emit=lambda row: (
                print(json.dumps(row), flush=True) if row.get("status") == "error" else None
            ),
        )
    finally:
        client.close()
    complete = state["segments"][-1]["report"]["complete"]
    if not complete:
        write(
            root / "closure.json",
            dict(reason="D42 first failure stops this window", time_ns=time.time_ns()),
        )
    print(
        json.dumps(dict(segment_complete=complete, snapshots=state["successful_snapshots"])),
        flush=True,
    )
    return complete


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "collect"])
    parser.add_argument("root", type=Path)
    parser.add_argument("--diagnostic", type=Path, default=Path("work/cold-connection-01"))
    args = parser.parse_args()
    if args.action == "collect":
        sys.exit(0 if collect(args.root) else 1)
    root = args.root
    decision = read(args.diagnostic / "decision.json")
    if decision["selected"] is None:
        raise SystemExit("No eligible source; no corpus requests")
    expected = read(args.diagnostic / "manifest.json")
    actual = hashes(args.diagnostic)
    actual.pop("manifest.json")
    if expected != actual:
        raise SystemExit("Diagnostic checksum mismatch; no network")
    plan = read(Path("configs/cold-connection-v1.json"))
    root.mkdir(parents=True, exist_ok=False)
    start = time.time_ns()
    stage = dict(
        selected=decision["selected"],
        plan=plan,
        started_ns=start,
        diagnostic_manifest_sha256=digest((args.diagnostic / "manifest.json").read_bytes()),
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        seed=None,
        segment_child_wall_seconds=210,
    )
    write(root / "plan.json", stage)
    refs = [
        Capture.load(Path(p))
        for p in read(Path("configs/qualified-http-corpus-v1.json"))["references"]
    ]
    outcomes = []
    first_created = None
    for index in range(plan["corpus"]["windows"]):
        due = window_due_ns(
            start, first_created, index, plan["corpus"]["minimum_start_separation_seconds"]
        )
        while time.time_ns() < due:
            time.sleep(min(30, (due - time.time_ns()) / 1e9))
        window = root / f"window-{index}"
        campaign_plan = create_campaign(
            window,
            refs,
            source_alias="calendar-" + stage["selected"],
            endpoint_origin=plan["origins"][stage["selected"]],
            transport_profile="stdlib_persistent_v1",
            segments=2,
            rounds=30,
            interval_seconds=5,
        )
        if index == 0:
            first_created = campaign_plan["created_ns"]
        write(window / "stage-plan.json", stage)
        write(window / "stage-provenance.json", dict(code_commit=stage["code_commit"], seed=None))
        print(
            json.dumps(dict(window=index, event="start", available_ns=time.time_ns())), flush=True
        )
        for segment in range(2):
            try:
                p = subprocess.run(
                    [sys.executable, __file__, "collect", str(window)], timeout=210, check=False
                )
                code = p.returncode
            except subprocess.TimeoutExpired:
                code = 124
                write(
                    window / "closure.json",
                    dict(
                        reason="parent wall deadline; original bytes preserved",
                        time_ns=time.time_ns(),
                    ),
                )
            if code:
                print(
                    json.dumps(
                        dict(
                            window=index, segment=segment, category="window_stopped", exit_code=code
                        )
                    ),
                    flush=True,
                )
                break
        outcomes.append(
            dict(
                window=index, closed=(window / "closure.json").exists(), finished_ns=time.time_ns()
            )
        )
        # Existing reports refuse interrupted unsealed artifacts; preserve those for separate audit.
        if all((p / "seal.json").exists() for p in (window / "segments").iterdir()):
            with (root / f"qa-{index}.json").open("x") as log:
                subprocess.run(
                    [sys.executable, "tools/qualified_http_corpus.py", "report", str(window)],
                    stdout=log,
                    check=True,
                )
        print(json.dumps(outcomes[-1]), flush=True)
        if sum(r["closed"] for r in outcomes) >= plan["corpus"]["stop_all_after_failed_windows"]:
            break
    write(root / "result.json", outcomes)
    write(root / "manifest.json", hashes(root))


if __name__ == "__main__":
    main()
