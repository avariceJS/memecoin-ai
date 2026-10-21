"""Owner-facing local Engineering Desk."""

import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=[
            "bootstrap",
            "chief",
            "desk",
            "status",
            "supervise",
            "attempt",
            "stop",
            "restore-reconcile",
        ],
    )
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--attempt", type=int)
    p.add_argument("--max-seconds", type=int, default=1800)
    p.add_argument("--source", type=Path)
    p.add_argument("--python")
    args = p.parse_args()
    if args.command == "bootstrap":
        from .bootstrap import bootstrap

        if not args.source or not args.python:
            p.error("bootstrap requires --source and --python")
        print(json.dumps(bootstrap(args.state, args.source, args.python), indent=2))
    elif args.command == "chief":
        from .chief import chief

        chief(args.state)
    elif args.command == "restore-reconcile":
        from .service import reconcile_stopped_restore

        print(json.dumps(reconcile_stopped_restore(args.state), sort_keys=True, indent=2))
    elif args.command == "status":
        from .supervisor import status

        print(json.dumps(status(args.state), indent=2))
    elif args.command == "desk":
        from .supervisor import render_desk

        print(render_desk(args.state))
    elif args.command == "supervise":
        from .supervisor import supervise

        supervise(args.state, args.max_seconds)
    elif args.command == "attempt":
        if args.attempt is None:
            p.error("--attempt required")
        from .supervisor import run_attempt

        run_attempt(args.state, args.attempt)
    else:
        (args.state / "STOP").touch(exist_ok=True)


if __name__ == "__main__":
    main()
