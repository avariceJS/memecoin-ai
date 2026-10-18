"""Pinned population, coverage and diversity research jobs; never dispatches provider requests."""

import argparse
import json
from pathlib import Path

from memecoin_experience.storage import canonical

from . import adapters, catalog, diversity, planning


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "publish",
            "compact",
            "replicate",
            "plan",
            "work",
            "audit",
            "universe",
            "continuous-coverage",
            "reconcile-transport-stop",
        ),
    )
    parser.add_argument(
        "--job",
        type=Path,
        required=True,
        help="JSON keyword arguments; paths are local transport references",
    )
    args = parser.parse_args()
    job = json.loads(args.job.read_text())
    if args.action == "publish":
        with Path(job.pop("rows_path")).open() as f:
            result = catalog.publish(rows=(json.loads(line) for line in f), **job)
    elif args.action == "reconcile-transport-stop":
        from .recovery import reconcile_transport_stop

        result = reconcile_transport_stop(**job)
    elif args.action == "work":
        for row in planning.work(**job):
            print(canonical(row).decode())
        return
    else:
        operation = {
            "compact": catalog.compact,
            "replicate": catalog.replicate,
            "plan": planning.create,
            "audit": diversity.audit,
            "universe": adapters.universe,
            "continuous-coverage": adapters.continuous_coverage,
        }[args.action]
        result = operation(**job)
    print(canonical(result).decode())


if __name__ == "__main__":
    main()
