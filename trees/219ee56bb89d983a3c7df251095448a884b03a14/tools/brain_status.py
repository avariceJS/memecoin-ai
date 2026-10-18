"""Read-only Brain Development metadata snapshot; never opens D43/F01 holdout."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from memecoin_ml.data import read, sha, write

ROOT = Path(__file__).resolve().parents[1] / "work/brain-b01/dev01"


def status():
    config = read(ROOT / "config.json")
    requests = sorted((ROOT / "requests").iterdir())
    failures = [read(p / "failure.json") for p in requests if (p / "failure.json").exists()]
    cycles = [
        dict(cycle=p.name, **read(p / "result.json"))
        for p in sorted((ROOT / "cycles").iterdir())
        if (p / "result.json").exists()
    ]
    models = []
    for path in sorted((ROOT / "models").iterdir()):
        if (path / "manifest.json").exists():
            card = read(path / "model-card.json")
            models.append(
                dict(
                    version=path.name,
                    sha256=sha(path / "manifest.json"),
                    selected=card["selected"],
                    finished_ns=card["finished_ns"],
                    training_cutoff_ns=card["training_as_of_ns"],
                )
            )
    predictions = list((ROOT / "shadow/predictions").glob("*.json"))
    outcomes = [read(p) for p in (ROOT / "shadow/outcomes").glob("*.json")]
    losses = {}
    for outcome in outcomes:
        if outcome["status"] == "scored":
            for name, metrics in outcome["candidate_metrics"].items():
                losses.setdefault(name, []).append(metrics["pinball"])
    quality = None
    if (ROOT / "quality-seed.json").exists():
        from memecoin_brain_quality.policy import states

        pools, head = states(ROOT)
        quality = dict(pools=pools, ledger_head=head)
    return dict(
        quality=quality,
        track="brain_development",
        observed_ns=time.time_ns(),
        root=str(ROOT),
        runtime=read(ROOT / "deployment.json"),
        expires_ns=config["expires_ns"],
        stopped=(ROOT / "STOP").exists(),
        universe_pools=len(read(ROOT / "universe.json")["pools"]),
        total_requests_reserved=len(requests)
        + read(ROOT / "external-request-debit.json")["requests"],
        max_requests=config["max_requests"],
        successful_requests=sum((p / "receipt.json").exists() for p in requests),
        failures=failures,
        cycles=cycles,
        models=models,
        predictions=len(predictions),
        outcomes=dict(Counter(o["status"] for o in outcomes)),
        pending_predictions=len(predictions) - len(outcomes),
        shadow_descriptive_pinball={
            name: dict(mean=sum(v) / len(v), variants=len(v)) for name, v in losses.items()
        },
        shadow_statistics_independent=False,
        F01=False,
        trade_orders=0,
        paid_budget_usd=0,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    allowed = [
        ROOT.resolve(),
        (ROOT.parents[1] / "brain-b02/prospective01").resolve(),
        (ROOT.parents[1] / "brain-b03/prospective01").resolve(),
        (ROOT.parents[1] / "brain-b04/prospective01").resolve(),
    ]
    if args.root.resolve() not in allowed:
        parser.error("Only B01/B02/B03/B04 Brain roots are supported")
    ROOT = args.root.resolve()
    result = status()
    if args.output:
        write(args.output, result)
    print(json.dumps(result, indent=2))
