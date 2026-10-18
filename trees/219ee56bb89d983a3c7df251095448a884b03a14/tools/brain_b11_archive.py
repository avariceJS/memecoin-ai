"""Create one immutable full-history textbook and measure what deployed training omits."""

import gc
import json
import shutil
import sqlite3
from pathlib import Path

from memecoin_brain.data import save_dataset
from memecoin_continuous.store import audit, view
from memecoin_curriculum.archive import (
    assemble,
    catalogue,
    funnel,
    path_labels,
    revision_audit,
    rolling,
    shards,
)
from memecoin_ml.data import read, sha
from memecoin_paper.health import operational
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]


def main():
    source = BASE / "work/brain-b09/source01"
    paper = BASE / "work/brain-b10/paper01"
    output = BASE / "work/brain-b11/textbook02"
    output.mkdir(parents=True, exist_ok=False)
    parent = BASE / "work/brain-b11/textbook01"
    verify(parent)
    shutil.copyfile(parent / "index.sqlite", output / "index.sqlite")
    cutoff = read(parent / "seed.json")["as_of_ns"]
    publish(
        output / "seed.json",
        dict(parent=str(source), as_of_ns=cutoff, prior_snapshot_sha=sha(parent / "manifest.json")),
    )
    rows, prov = view(output, cutoff)
    raw = audit(output, cutoff)
    (output / "shards").mkdir()

    def prepared():
        for i, part in enumerate(shards(rows, prov)):
            save_dataset(output / "shards" / f"{i:06d}", part)
            yield part

    bundle = assemble(prepared())
    bundle, full = funnel(rows, prov, bundle)
    save_dataset(output / "dataset-full", bundle)
    _, deployed = funnel(rolling(rows, cutoff), prov)
    with sqlite3.connect(output / "index.sqlite") as db:
        publish(output / "catalogue.json", catalogue(db))
        revisions = revision_audit(db)
    db.close()
    counts = {}
    with (output / "auxiliary-targets.jsonl").open("x") as f:
        for label in path_labels(rows, cutoff):
            counts[label["status"]] = counts.get(label["status"], 0) + 1
            f.write(json.dumps(label, sort_keys=True, allow_nan=False) + "\n")
    shutil.copyfile(source / "universe.json", output / "universe.json")
    result = dict(
        cutoff_ns=cutoff,
        full_archive=full,
        deployed_rolling=deployed,
        raw_audit=raw,
        revisions=revisions,
        auxiliary_target_counts=counts,
        actual_operating_health=operational(paper),
        new_fits=0,
        materialization_limit=100000,
        shards=len(list((output / "shards").iterdir())),
        source_pin=read(source / "deployment.json")["pin"],
        paper_pin=read(paper / "deployment.json")["pin"],
        metadata=dict(
            historical_universe_known=False,
            survivors_only_not_representative=True,
            liquidity_and_buy_sell_flow_historical=False,
            no_new_provider_requests=True,
        ),
    )
    publish(output / "result.json", result)
    gc.collect()
    seal(output)
    verify(output)
    publish(
        BASE / "docs/evidence/brain-archive-curriculum-2026-10-04.json",
        dict(**result, artifact=str(output), manifest_sha=sha(output / "manifest.json")),
    )
    print(json.dumps(dict(full=full, deployed=deployed, targets=counts, revisions=revisions)))


if __name__ == "__main__":
    main()
