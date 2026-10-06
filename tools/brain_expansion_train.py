"""One preregistered eight-fit data-only comparison; immutable inputs and common evaluation."""

import argparse
import json
import sqlite3
import time
from pathlib import Path

from memecoin_brain.data import load_dataset
from memecoin_continuous.store import view
from memecoin_curriculum.admission import compile_corpus, merge_history
from memecoin_curriculum.diversity import compact, delta, support
from memecoin_ml.data import read, sha
from memecoin_operator.learning import RECIPE, partitions, prepare, train
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "work/brain-expansion-20261006"


def admitted(campaigns, output):
    captures, inputs = [], []
    for campaign in campaigns:
        verify(campaign)
        binding = read(campaign / "binding.json")
        cases = {b["pool"]: b for b in binding["cases"]}
        for path in sorted((campaign / "requests").iterdir()):
            if not (path / "receipt.json").exists():
                continue
            pool = read(path / "request.json")["url"].split("/pools/")[1].split("/")[0]
            captures.append((path, cases[pool]))
        inputs.append(dict(path=str(campaign), sha=sha(campaign / "manifest.json")))
    return compile_corpus(captures, output, selection=inputs)


def experiment(corpus, output):
    verify(corpus)
    output.mkdir(exist_ok=False)
    source = BASE / "work/brain-b13/source-repaired01"
    cutoff = time.time_ns()
    backup = output / "source-prefix"
    backup.mkdir()
    with sqlite3.connect(f"file:{source / 'index.sqlite'}?mode=ro", uri=True) as src:
        with sqlite3.connect(backup / "index.sqlite") as dst:
            src.backup(dst)
    src.close()
    dst.close()
    rows, prov = view(backup, cutoff)
    seal(backup)
    reference = dict(path=str(corpus.resolve()), sha=sha(corpus / "manifest.json"))
    broad, provenance = merge_history(rows, prov, [reference])
    publish(
        output / "binding.json",
        dict(
            cutoff_ns=cutoff,
            source=str(source),
            source_config_sha=sha(source / "config.json"),
            source_backup_sha=sha(backup / "manifest.json"),
            historical=reference,
            recipe=RECIPE,
            fits_reserved=8,
            automatic_retry=False,
            plan_sha=sha(BASE / "docs/brain-training-expansion.md"),
        ),
    )
    reports, bundles, parts = {}, {}, {}
    for name, r, p in [("control", rows, prov), ("broad", broad, provenance)]:
        snapshot = output / (name + "-snapshot")
        snapshot.mkdir()
        reports[name] = prepare(r, p, snapshot)
        seal(snapshot)
        bundles[name] = load_dataset(snapshot / "dataset")
        parts[name], _ = partitions(bundles[name], cutoff)
    for role in ("validation", "test"):
        if digest(parts["control"][role]) != digest(parts["broad"][role]):
            raise ValueError("expansion_common_evaluation_changed")
        keys = {k for s in parts["control"][role] for k in s["history"]}
        if any(bundles["control"]["frames"][k] != bundles["broad"]["frames"][k] for k in keys):
            raise ValueError("expansion_evaluation_inputs_changed")
    births = {
        r["pool"]: r["birth"]["event_s"] * 10**9 for r in read(corpus / "admission.json")["records"]
    }
    summaries = {name: compact(bundles[name], parts[name]["train"], births) for name in bundles}
    publish(
        output / "support.json",
        dict(
            control=support(summaries["control"]),
            broad=support(summaries["broad"]),
            delta=delta(summaries["control"], summaries["broad"]),
            identical_validation_test=True,
            common_membership={
                role: digest(parts["control"][role]) for role in ("validation", "test")
            },
        ),
    )
    # Training eligibility and comparison membership are sealed before any model fit.
    result = {}
    for name in ("control", "broad"):
        result[name] = train(
            output / (name + "-snapshot"),
            output / (name + "-model"),
            output / (name + "-progress.json"),
        )
    publish(
        output / "result.json",
        dict(models=result, fits=8, automatic_promotion=False, common_future_exam_required=True),
    )
    seal(output)
    print(
        json.dumps(
            {
                name: dict(
                    selected=c["selected"],
                    train=c["support"]["train"]["pools"],
                    validation={k: v["pool_macro_pinball"] for k, v in c["validation"].items()},
                    test={k: v["pool_macro_pinball"] for k, v in c["test"].items()},
                )
                for name, c in result.items()
            }
        )
    )


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="action", required=True)
    a = sub.add_parser("admit")
    a.add_argument("output", type=Path)
    a.add_argument("campaigns", nargs="+", type=Path)
    a = sub.add_parser("train")
    a.add_argument("corpus", type=Path)
    a.add_argument("output", type=Path)
    args = p.parse_args()
    if args.action == "admit":
        result = admitted([p.resolve() for p in args.campaigns], args.output.resolve())
        print(
            json.dumps(
                {k: v for k, v in result.items() if k not in ("records", "rejected", "per_pool")}
            )
        )
    else:
        experiment(args.corpus.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
