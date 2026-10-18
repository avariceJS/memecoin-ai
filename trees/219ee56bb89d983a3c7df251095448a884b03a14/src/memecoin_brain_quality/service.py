"""Due-only continuous development data, supervised refresh and genuine forecast shadow."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import time
from pathlib import Path

from memecoin_brain.data import build, fingerprint, save_dataset
from memecoin_brain.source import NS, SourceFailure, host_guard
from memecoin_brain_scale.audit import safe_corpus as corpus
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import Watch, child
from memecoin_operational.worker import lease_watchdog
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

from .collection import collect, stop
from .runtime import verify_runtime


def models(root: Path) -> list[Path]:
    return sorted(
        [p for p in (root / "models").iterdir() if (p / "manifest.json").exists()],
        key=lambda p: read(p / "model-card.json")["finished_ns"],
    )


def cooldown(root: Path) -> int:
    for path in reversed(sorted((root / "requests").iterdir())):
        if (path / "failure.json").exists():
            r = read(path / "failure.json")
            if r.get("http_status") in (401, 403):
                return 2**63 - 1
            if r.get("http_status") == 429:
                return r["received_ns"] + max(900, r.get("retry_after_seconds", 0)) * NS
            break
    return 0


def worker(root: Path, output: Path, phase: str) -> None:
    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    verify_runtime(root)
    host_guard(root)
    if phase == "collect":
        existing = models(root)
        collect(root, output, model=existing[-1] if existing else None)
    elif phase == "history":
        from .history import run

        run(root, output)
    elif phase == "train":
        from memecoin_brain.model import train

        rows, provenance = corpus(root, time.time_ns())
        bundle = build(rows, provenance)
        existing = models(root)
        mark = fingerprint(bundle)
        if existing and read(existing[-1] / "model-card.json")["data_fingerprint"] == mark:
            publish(output / "training.json", dict(status="no_new_data"))
            return
        dataset = output / "dataset"
        save_dataset(dataset, bundle)
        target = root / "models" / output.name
        card = train(dataset, target)
        publish(
            output / "training.json",
            dict(
                status="completed",
                model=str(target),
                model_sha=sha(target / "manifest.json"),
                selected=card["selected"],
                data_fingerprint=mark,
            ),
        )
    else:
        raise ValueError("unknown_phase")


def command(root: Path, output: Path, phase: str, seconds: int) -> list[str]:
    runtime = Path(read(root / "deployment.json")["bundle"]) / "runtime"
    script = (
        "import os,sys,time; sys.path.insert(0,sys.argv.pop(1)); "
        'os.environ["MEMECOIN_BRAIN_DEADLINE_NS"]=str(time.time_ns()+int(sys.argv.pop(1))*10**9); '
        "from memecoin_brain_quality.service import safe_worker; from pathlib import Path; "
        "safe_worker(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3])"
    )
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        script,
        str(runtime),
        str(seconds),
        str(root),
        str(output),
        phase,
    ]


def safe_worker(root: Path, output: Path, phase: str) -> None:
    try:
        worker(root, output, phase)
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    status="failed",
                    category=str(exc)
                    if isinstance(exc, SourceFailure)
                    else "Brain_worker_integrity",
                    error_type=type(exc).__name__,
                )
            ),
            flush=True,
        )
        raise SystemExit(1) from None


def guard(root: Path) -> None:
    c = read(root / "config.json")
    if (
        (root / "STOP").exists()
        or time.time_ns() >= c["expires_ns"]
        or shutil.disk_usage(root).free < c["free_floor_bytes"]
    ):
        raise SourceFailure("stop_expiry_or_disk")


def run_phase(root: Path, output: Path, phase: str, seconds: int) -> dict:
    watch = Watch(seconds)

    def fenced():
        guard(root)
        if watch.remaining() <= 0:
            raise SourceFailure("phase_deadline")

    result = child(command(root, output, phase, seconds), output / "worker.log", seconds, fenced)
    try:
        fenced()
        verify_runtime(root)
    except Exception as exc:
        result.update(category="post_phase_guard", exit_code=1, error_type=type(exc).__name__)
    return result


def tick(root: Path, pin: str) -> dict:
    verify_runtime(root, pin)
    guard(root)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cycles = sorted((root / "cycles").iterdir())
        # Crash after reservation consumes its cycle. Never resume/replay its requests.
        for cycle in cycles:
            if not (cycle / "result.json").exists():
                publish(
                    cycle / "result.json",
                    dict(
                        status="failed", category="interrupted_no_retry", finished_ns=time.time_ns()
                    ),
                )
                seal(cycle)
        failures = 0
        for cycle in reversed(cycles):
            if (
                read(cycle / "result.json")["status"] == "completed"
                and read(cycle / "collection.json")["attempted"] > 0
            ):
                break
            failures += 1
        if len(cycles) == failures:
            # A failed bounded historical acquisition immediately before deployment also counts.
            if read(root / "activation.json")["prior_consecutive_acquisition_failures"]:
                failures += 1
        if failures >= 2:
            if not (root / "STOP").exists():
                publish(root / "STOP", dict(reason="two_consecutive_acquisition_failures"))
            return dict(status="stopped", failures=failures)
        until = cooldown(root)
        if time.time_ns() < until:
            return dict(status="provider_cooldown", until_ns=until)
        due = (time.time_ns() // (300 * NS)) * 300
        output = root / "cycles" / str(due)
        if output.exists():
            return dict(status="already_reserved")
        output.mkdir()
        publish(
            output / "reservation.json", dict(due_s=due, started_ns=time.time_ns(), max_requests=12)
        )
        result = run_phase(root, output, "collect", 240)
        successful = result["category"] == "completed" and result["exit_code"] == 0
        publish(
            output / "result.json",
            dict(
                status="completed" if successful else "failed", finished_ns=time.time_ns(), **result
            ),
        )
        seal(output)
        if not successful:
            attempts = sorted((root / "requests").iterdir())
            last_failure = (
                read(attempts[-1] / "failure.json")
                if attempts and (attempts[-1] / "failure.json").exists()
                else {}
            )
            if last_failure.get("http_status") in (401, 403, 429):
                stop(root, "global_provider_auth_or_rate")
            elif not last_failure or result["category"] != "worker_failed":
                stop(root, "global_runtime_integrity")
            elif failures + 1 >= 2:
                stop(root, "two_consecutive_global_failures")
            return dict(status="failed", phase="collection", category=result["category"])
        if not read(output / "collection.json")["attempted"]:
            stop(root, "no_acquisition_support")
            return dict(status="stopped", reason="no_acquisition_support")
        if not (root / "qualified.json").exists():
            return dict(status="completed", training="withheld_until_live_qualification")
        qualification = read(root / "qualified.json")
        if qualification.get("decision") != "approved" or qualification.get("root") != str(
            root.resolve()
        ):
            raise ValueError("training_qualification_binding")
        existing = models(root)
        tickets = sorted((root / "training").iterdir())
        c = read(root / "config.json")
        if len(tickets) < c["max_training_cycles"] and (
            not existing
            or time.time_ns() - read(existing[-1] / "model-card.json")["finished_ns"]
            >= c["train_interval_seconds"] * NS
        ):
            # Failed/incomplete training disables training retries; data collection continues.
            if any(
                not (p / "result.json").exists() or read(p / "result.json")["status"] != "completed"
                for p in tickets
            ):
                return dict(status="completed", training="blocked_after_previous_failure")
            ticket = root / "training" / f"{len(tickets):06d}"
            ticket.mkdir()
            publish(
                ticket / "reservation.json",
                dict(started_ns=time.time_ns(), fits=4, max_seconds=600),
            )
            trained = run_phase(root, ticket, "train", 600)
            publish(
                ticket / "result.json",
                dict(
                    status="completed"
                    if trained["category"] == "completed" and trained["exit_code"] == 0
                    else "failed",
                    **trained,
                ),
            )
            seal(ticket)
        return dict(status="completed", collection=read(output / "collection.json"))


def main() -> None:
    try:
        print(json.dumps(tick(Path(sys.argv[1]), sys.argv[2])), flush=True)
    except BlockingIOError:
        print(json.dumps(dict(status="busy_no_overlap")), flush=True)
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    status="closed",
                    category=str(exc) if isinstance(exc, SourceFailure) else "Brain_runtime_guard",
                    error_type=type(exc).__name__,
                )
            ),
            flush=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
