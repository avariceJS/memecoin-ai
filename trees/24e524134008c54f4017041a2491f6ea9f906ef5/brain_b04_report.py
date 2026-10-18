"""Seal a bounded B04 data/shadow snapshot; background observations after cutoff stay separate."""

import argparse
import time
from pathlib import Path

from memecoin_brain_b04.experiment import reproduce
from memecoin_brain_scale.audit import coverage, safe_corpus, shadow_snapshot
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal

BASE = Path(__file__).resolve().parents[1]


def snapshot(output: Path):
    root = BASE / "work/brain-b04/prospective01"
    now = time.time_ns()
    rows, provenance = safe_corpus(root, now)
    output.mkdir(exist_ok=False)
    write(output / "rows.json", rows)
    write(output / "provenance.json", provenance)
    report = coverage(
        rows,
        read(root / "universe.json"),
        read(BASE / "work/brain-b02/history01/plan.json"),
        provenance,
    )
    write(output / "coverage.json", report)
    roots = tuple(Path(p) for p in read(root / "import.json")["model_roots"])
    shadow_snapshot(root, output / "shadow", rows, now, imported_model_roots=roots)
    write(
        output / "binding.json",
        dict(
            root=str(root),
            as_of_ns=now,
            source_identity=sha(BASE / "src/memecoin_brain_scale/audit.py"),
            import_sha=sha(root / "import.json"),
            quarantined=provenance["quarantined"],
        ),
    )
    seal(output)
    print(dict(bars=len(rows), shadow=read(output / "shadow/report.json")["overall"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["snapshot", "reproduce"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.phase == "snapshot":
        snapshot(args.output)
    else:
        print(
            reproduce(
                BASE / "work/brain-b03/snapshot01/dataset",
                BASE / "work/brain-b04/ablation01",
                args.output,
            )
        )
