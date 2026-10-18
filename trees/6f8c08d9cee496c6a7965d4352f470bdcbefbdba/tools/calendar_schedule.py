"""Finite due-only local schedule. Frozen runtime; reservations are never retried."""

import argparse
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path

from memecoin_ai.domain import ValidationError, digest
from memecoin_ai.prospective import create_campaign, hashes, read, verify_campaign, write
from memecoin_ai.sources import Capture


def due_action(due_ns, now_ns, lateness_seconds):
    if now_ns < due_ns:
        return "pending"
    if now_ns > due_ns + lateness_seconds * 10**9:
        return "missed"
    return "collect"


def snapshot_hashes(root):
    return {str(p.relative_to(root)): digest(p.read_bytes()) for p in sorted(root.rglob("*.py"))}


def check_runtime(root):
    if snapshot_hashes(root / "runtime") != read(root / "runtime-manifest.json"):
        raise ValidationError("pinned runtime differs; no network")
    if digest((root / "plan.json").read_bytes()) != read(root / "plan-sha.json"):
        raise ValidationError("schedule plan differs; no network")
    if hashes(root / "references") != read(root / "references-manifest.json"):
        raise ValidationError("schedule reference captures differ; no network")
    if read(root / "plan.json")["python_version"] != sys.version:
        raise ValidationError("schedule Python version differs; no network")


def run_child(root, window, tool, args, timeout):
    with (window / "worker.log").open("a") as log:
        try:
            p = subprocess.run(
                [sys.executable, str(root / "runtime/tools" / tool), *map(str, args)],
                stdout=log,
                stderr=log,
                timeout=timeout,
                check=False,
            )
            return p.returncode
        except subprocess.TimeoutExpired:
            return 124


def run_window(root, index, plan):
    spec = plan["spec"]
    window = root / "windows" / f"{index:03}"
    window.mkdir()
    write(
        window / "reservation.json",
        dict(
            index=index,
            due_ns=plan["due_ns"][index],
            started_ns=time.time_ns(),
            reserved_rpc=192,
            plan_sha256=digest((root / "plan.json").read_bytes()),
        ),
    )
    campaign = window / "campaign"
    refs = [Capture.load(p) for p in sorted((root / "references").iterdir())]
    create_campaign(
        campaign,
        refs,
        source_alias=spec["source_alias"],
        endpoint_origin=spec["endpoint_origin"],
        transport_profile=spec["transport_profile"],
        segments=2,
        rounds=30,
        interval_seconds=5,
    )
    write(campaign / "stage-plan.json", dict(selected="alchemy"))
    write(campaign / "stage-provenance.json", dict(code_commit=plan["code_commit"], seed=None))
    code = 0
    for _ in range(2):
        if (root / "STOP").exists():
            code = 125
            break
        code = run_child(
            root, window, "corpus_windows.py", ["collect", campaign], spec["segment_wall_seconds"]
        )
        if code:
            break
    corpus_code = code
    if code == 0 and not (root / "STOP").exists():
        code = run_child(
            root,
            window,
            "observed_execution.py",
            ["collect", window / "execution", "--plan", root / "plan.json", "--campaign", campaign],
            spec["execution_wall_seconds"],
        )
    qa_code = None
    if all((p / "seal.json").exists() for p in (campaign / "segments").iterdir()):
        qa_code = run_child(root, window, "qualified_http_corpus.py", ["report", campaign], 60)
        if qa_code == 0:
            qa_code = run_child(
                root,
                window,
                "execution_uncertainty.py",
                ["export", campaign, window / "campaign-targets", window / "uncertainty"],
                60,
            )
    state = verify_campaign(campaign) if qa_code == 0 else None
    status = (
        "completed" if code == 0 and qa_code == 0 and not (root / "STOP").exists() else "failed"
    )
    outcome = dict(
        index=index,
        status=status,
        corpus_exit_code=corpus_code,
        execution_or_collection_exit_code=code,
        qa_exit_code=qa_code,
        snapshots=None if state is None else state["successful_snapshots"],
        finished_ns=time.time_ns(),
    )
    write(window / "seal.json", dict(outcome=outcome, files=hashes(window)))
    return outcome


def run_due(root):
    with (root / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        check_runtime(root)
        if (root / "STOP").exists():
            return
        plan = read(root / "plan.json")
        outcomes = root / "outcomes"
        outcomes.mkdir(exist_ok=True)
        previous = [read(p) for p in sorted(outcomes.glob("*.json"))]
        if sum(r["status"] == "failed" for r in previous) >= plan["spec"]["max_failed_windows"]:
            if not (root / "closed.json").exists():
                write(
                    root / "closed.json",
                    dict(reason="failed window budget", time_ns=time.time_ns()),
                )
            return
        for index, due in enumerate(plan["due_ns"]):
            target = outcomes / f"{index:03}.json"
            if target.exists():
                continue
            directory = root / "windows" / f"{index:03}"
            if directory.exists():
                # Preserve crash bytes and reserve the full window budget. Never resume them.
                result = dict(
                    index=index,
                    status="failed",
                    reason="interrupted reservation; no retry",
                    finished_ns=time.time_ns(),
                )
            else:
                action = due_action(due, time.time_ns(), plan["spec"]["lateness_seconds"])
                if action == "pending":
                    break
                if action == "missed":
                    result = dict(index=index, status="missed", finished_ns=time.time_ns())
                else:
                    result = run_window(root, index, plan)
            write(target, result)
            print(json.dumps(result), flush=True)
            if result["status"] == "failed":
                break
        if (
            len(list(outcomes.glob("*.json"))) == len(plan["due_ns"])
            and not (root / "closed.json").exists()
        ):
            write(root / "closed.json", dict(reason="schedule exhausted", time_ns=time.time_ns()))


def status(root):
    plan = read(root / "plan.json")
    rows = []
    for i, due in enumerate(plan["due_ns"]):
        p = root / "outcomes" / f"{i:03}.json"
        rows.append(read(p) if p.exists() else dict(index=i, status="pending", due_ns=due))
    return dict(
        plan=plan,
        windows=rows,
        closed=(root / "closed.json").exists(),
        stop_requested=(root / "STOP").exists(),
        training_ready=False,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["run-due", "status"])
    p.add_argument("root", type=Path)
    a = p.parse_args()
    if a.action == "run-due":
        run_due(a.root)
    else:
        print(json.dumps(status(a.root), indent=2))


if __name__ == "__main__":
    main()
