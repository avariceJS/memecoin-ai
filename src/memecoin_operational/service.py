"""One due-only invocation. CLI cannot mint activation or budget receipts."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from memecoin_collector.qualification import endpoint
from memecoin_collector.scheduler import run_due
from memecoin_sampling.contract import load

from .contract import GateError, authorize, check_bundle, no_symlink
from .supervisor import window


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--bundle-sha", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--receipt-sha", required=True)
    args = parser.parse_args()
    try:
        c = check_bundle(args.bundle, args.bundle_sha)
        if c["mode"] != "prospective":
            raise GateError("fixture_service_forbidden")

        def gate() -> None:
            authorize(
                args.bundle,
                args.bundle_sha,
                args.receipt,
                args.receipt_sha,
                scope="calendar",
                credential=endpoint(Path(c["settings"])),
                now_ns=time.time_ns(),
            )

        gate()  # Before any source-root creation/read or scheduler mutation.
        source = Path(c["source_root"])
        for directory in (source, source / "windows", source / "journal"):
            no_symlink(directory)
            if directory.exists():
                for path in directory.iterdir():
                    no_symlink(path)
        binding, _ = load(args.bundle / "proposal")
        rows = run_due(
            Path(c["source_root"]),
            binding,
            c["proposal_sha"],
            now=time.time_ns,
            runner=lambda path, res: window(
                args.bundle,
                args.bundle_sha,
                path,
                res,
                live_guard=gate,
                receipt=args.receipt,
                receipt_pin=args.receipt_sha,
                scope="calendar",
            ),
        )
        print(
            json.dumps(
                dict(
                    completed=sum(r["status"] == "completed" for r in rows),
                    failed=sum(r["status"] == "failed" for r in rows),
                    missed=sum(r["status"] == "missed" for r in rows),
                    reserved_rpc=sum(r["reserved_rpc"] for r in rows),
                )
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    status="closed",
                    category=str(exc)
                    if isinstance(exc, GateError)
                    else "runtime_or_receipt_invalid",
                    error_type=type(exc).__name__,
                )
            ),
            flush=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
