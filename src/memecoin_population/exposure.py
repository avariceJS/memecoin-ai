"""Checkpoint-bound committed optimizer exposure; replayed work is not double-counted."""

import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from memecoin_experience.storage import (
    atomic_json,
    canonical,
    digest,
    file_hash,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)

from .catalog import View
from .diversity import OBJECTIVE, add_counts, classify, counts_table, policy, summary


def validate(reference, snapshot, split, schema):
    if set(reference) != {"path", "pin"}:
        raise ValueError("diversity_reference")
    path = safe_path(reference["path"])
    verify_files(path, reference["pin"])
    report = json.loads((path / "report.json").read_text())
    binding = report["binding"]
    policy(binding["config"])
    allowed = not report["violations"] or binding["config"]["enforcement"] == "report"
    if (
        binding["snapshot"] != snapshot
        or binding["split"] != split
        or report["representation"] != digest(schema)
        or binding["config"]["objective"] != OBJECTIVE
    ):
        raise ValueError("diversity_training_binding")
    if not allowed or report["training_allowed"] != allowed:
        raise ValueError("diversity_training_gate")
    return report


class Journal:
    def __init__(self, reference, output, report):
        self.reference = reference
        self.report = report
        self.root = safe_path(output) / "exposure"
        self.root.mkdir(exist_ok=True)
        sync_dir(self.root.parent)
        self.view = View(safe_path(Path(reference["path"]) / "population.sqlite"), readonly=True)
        self.head = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.view.close()

    def path(self, step):
        return safe_path(self.root / f"{step // 1024:09d}" / f"{step:012d}.json")

    def verify(self, steps, head, rows_seen):
        previous = None
        rows = 0
        for step in range(1, steps + 1):
            row = json.loads(self.path(step).read_text())
            if (
                row["step"] != step
                or row["previous"] != previous
                or row["audit"] != self.reference["pin"]
            ):
                raise ValueError("diversity_exposure_chain")
            previous = digest(row)
            rows += row["rows"]
        if previous != head or rows != rows_seen:
            raise ValueError("diversity_checkpoint_exposure")
        self.head = previous

    def record(self, step, epoch, rows, mask):
        counts = Counter()
        binding = self.report["binding"]
        config = binding["config"]
        hcounts = mask.sum(axis=0)
        active = int((hcounts > 0).sum())
        if not active:
            raise ValueError("diversity_empty_optimizer_batch")
        identities = []
        for row, valids in zip(rows, mask, strict=True):
            at = row.get("decision_ns", row.get("origin_s", 0) * 10**9)
            entity = config["entity_prefix"] + row["pool"]
            identities.append([entity, at, row["id"], valids.tolist()])
            mass = sum(1 / (active * int(hcounts[h])) for h, v in enumerate(valids) if v)
            for axis, value in classify(
                self.view, config, entity, at, binding["cutoff_ns"], self.report["representation"]
            ):
                counts[axis, value, "train", "draws"] += 1
                counts[axis, value, "train", "objective_mass"] += mass
                for h, valid in enumerate(valids):
                    counts[axis, value, "train", f"target:{h}"] += int(valid)
        record = dict(
            version="optimizer-exposure-step-v1",
            audit=self.reference["pin"],
            step=step,
            epoch=epoch,
            previous=self.head,
            rows=len(rows),
            batch=digest(identities),
            counts=[[*key, n] for key, n in sorted(counts.items())],
        )
        path = self.path(step)
        if not path.parent.exists():
            path.parent.mkdir()
            sync_dir(self.root)
        atomic_json(path, record)
        self.head = digest(record)
        return self.head

    def finish(self, steps, rows_seen, output):
        self.verify(steps, self.head, rows_seen)
        binding = dict(
            audit=self.reference["pin"],
            steps=steps,
            rows_seen=rows_seen,
            head=self.head,
            objective=OBJECTIVE,
        )
        output = safe_path(output)
        if output.exists():
            verify_files(output)
            result = json.loads((output / "report.json").read_text())
            if result["binding"] != binding:
                raise ValueError("diversity_exposure_report_binding")
            return dict(pin=file_hash(output / "manifest.json"), **result)
        stage = Path(tempfile.mkdtemp(prefix=".exposure-summary-", dir=output.parent))
        db = sqlite3.connect(stage / "scratch.sqlite")
        try:
            db.execute("pragma cache_size=-8192")
            counts_table(db)
            for step in range(1, steps + 1):
                record = json.loads(self.path(step).read_text())
                add_counts(db, {tuple(row[:4]): row[4] for row in record["counts"]})
                if step % 100 == 0:
                    db.commit()
            db.commit()
            result = dict(
                binding=binding,
                draw_distribution=summary(db, metric="draws"),
                objective_distribution=summary(db, metric="objective_mass"),
                scope=(
                    "committed_optimizer_inputs; excludes rolled-back work; "
                    "not final-checkpoint influence or gradient magnitude"
                ),
            )
            with (stage / "counts.jsonl").open("wb") as f:
                for row in db.execute(
                    "select axis,value,role,metric,n from counts order by axis,value,role,metric"
                ):
                    f.write(
                        canonical(
                            dict(zip(("axis", "value", "role", "metric", "n"), row, strict=True))
                        )
                        + b"\n"
                    )
                f.flush()
                os.fsync(f.fileno())
            db.close()
            (stage / "scratch.sqlite").unlink()
            atomic_json(stage / "report.json", result)
            pin = seal_files(stage)
            os.rename(stage, output)
            sync_dir(output.parent)
            return dict(pin=pin, **result)
        finally:
            db.close()
            if stage.exists():
                shutil.rmtree(stage)
