"""One reviewed bounded attempt using D50 runtime; never issues a receipt or installs a job."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from memecoin_collector.qualification import endpoint
from memecoin_collector.scheduler import durable, sync_dir
from memecoin_operational.contract import (
    GateError,
    authorize,
    check_attempt,
    check_bundle,
    no_symlink,
)
from memecoin_operational.supervisor import disk_guard, window
from memecoin_sampling.contract import NS, paths_disjoint


def preflight(bundle, pin, receipt, receipt_pin, output):
    c = check_bundle(bundle, pin)
    r = authorize(
        bundle,
        pin,
        receipt,
        receipt_pin,
        scope="bounded_qualification",
        credential=endpoint(Path(c["settings"])),
        now_ns=time.time_ns(),
    )
    no_symlink(output)
    if output.resolve() != Path(r["attempt_root"]).resolve():
        raise GateError("qualification_attempt_reuse")
    # Reject protected destinations before reading or creating anything below them.
    work = Path(c["source_root"]).parent
    protected = [
        bundle,
        Path(c["source_root"]),
        work / "calendar-execution-01",
        work / "corpus-registry-01",
        work / "sampling-d48",
        work / "collector-d50",
        Path(c["settings"]),
        receipt,
        Path(r["budget_review"]["mapping_evidence_path"]),
        Path(r["budget_review"]["billing_evidence_path"]),
    ]
    for p in protected:
        paths_disjoint(output, p)
    if output.exists():
        raise GateError("attempt_exists_no_retry")
    if not output.parent.is_dir():
        raise GateError("attempt_parent_missing")
    if (output.parent.parent / "STOP").exists():
        raise GateError("stop_requested")
    if r["budget_review"]["valid_until_ns"] < time.time_ns() + 600 * NS:
        raise GateError("review_does_not_cover_window")
    # Empty candidate parent: no recursive inspection of unrelated data.
    import shutil

    if shutil.disk_usage(output.parent).free < c["free_floor"] + c["window_bytes"]:
        raise GateError("disk_headroom")
    return c


def run(bundle, pin, receipt, receipt_pin, output):
    c = preflight(bundle, pin, receipt, receipt_pin, output)
    output.mkdir()  # Exclusive claim also fences simultaneous launch and crash-before-reservation.
    sync_dir(output.parent)
    started = time.time_ns()
    reservation = dict(
        index=0,  # Local bounded index, never an actual F01 calendar index/admission.
        due_ns=started,
        started_ns=started,
        reserved_rpc=180,
        plan_sha256=c["proposal_sha"],
    )
    durable(output / "reservation.json", reservation)

    def guard():
        r = authorize(
            bundle,
            pin,
            receipt,
            receipt_pin,
            scope="bounded_qualification",
            credential=endpoint(Path(c["settings"])),
            now_ns=time.time_ns(),
        )
        check_attempt(bundle, c, r, output, "bounded_qualification")
        disk_guard(output, floor=c["free_floor"], limit=c["window_bytes"])

    try:
        guard()
        return window(
            bundle,
            pin,
            output,
            reservation,
            live_guard=guard,
            receipt=receipt,
            receipt_pin=receipt_pin,
            scope="bounded_qualification",
        )
    except Exception as exc:
        durable(
            output / "launcher-failure.json",
            dict(
                status="failed",
                category="launcher_or_integrity_failure",
                error_type=type(exc).__name__,
                reserved_rpc=180,
                no_retry=True,
            ),
        )
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--bundle-sha", required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--receipt-sha")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Validate only; no reservation or RPC")
    args = parser.parse_args(argv)
    try:
        call = preflight if args.check else run
        result = call(
            args.bundle, args.bundle_sha, args.receipt, args.receipt_sha, args.output.absolute()
        )
        status = "preflight_passed" if args.check else result["status"]
        print(json.dumps(dict(status=status, calendar_activation=False, research_training=False)))
        return 0 if status in ("completed", "preflight_passed") else 1
    except Exception as exc:
        # Never print endpoint, provider payload, raw exception text or receipt contents.
        print(
            json.dumps(
                dict(
                    status="closed",
                    category=str(exc)
                    if isinstance(exc, GateError)
                    else "qualification_gate_or_runtime_failure",
                    error_type=type(exc).__name__,
                )
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
