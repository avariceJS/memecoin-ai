"""Isolated fit process; parent enforces a hard wall deadline."""

from __future__ import annotations

import sys
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_runner.trainer import fit


def main() -> None:
    job = read(Path(sys.argv[1]))
    path = Path(job["dataset"])
    if sha(path) != job["dataset_sha256"]:
        raise ValueError("worker_dataset_changed")
    bundle = read(path)
    samples = {s["id"]: s for s in bundle["samples"]}
    fit(
        bundle,
        [samples[k] for k in job["train"]],
        [samples[k] for k in job["validation"]],
        job["config"],
        Path(job["output"]),
        identity=job["identity"],
        pools=job["pools"],
        max_epochs=job["epochs"],
        fixed_epochs=job["fixed_epochs"],
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never serialize arbitrary exception/provider/data text.
        print(f"fit_failure:{type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
