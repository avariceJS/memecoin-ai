"""Independently rescore the immutable shared test; no fitting or provider access."""

import json
from collections import Counter
from pathlib import Path

from memecoin_brain.data import load_dataset
from memecoin_curriculum.design import experiments
from memecoin_curriculum.scoring import paired
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify
from memecoin_runner.ledger import Ledger, publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b12"


def main():
    design, run = ROOT / "design01", ROOT / "run01"
    verify(design)
    verify(run)
    binding = read(design / "binding.json")
    bundle = load_dataset(Path(binding["dataset"]))
    exps = list(experiments(bundle, binding["bounds"]))
    samples = exps[0]["test"]
    assert digest([s["id"] for s in samples]) == binding["test_membership"]
    preds = {e["name"]: read(run / (e["name"] + "-test-predictions.json")) for e in exps}
    rows = []
    variants = [(s, t) for s in samples for t in s["targets"]]
    for i, (s, t) in enumerate(variants):
        losses = {}
        y = t["log_return"]
        for arm, predictions in preds.items():
            for kind, values in predictions.items():
                q = values[i]
                losses[arm + "/" + kind] = (
                    sum(
                        max(level * (y - p), (level - 1) * (y - p))
                        for level, p in zip((0.1, 0.5, 0.9), q, strict=True)
                    )
                    / 3
                )
        rows.append(
            dict(
                pool=s["pool"],
                day=s["event_close_s"] // 86400,
                hour=s["event_close_s"] // 3600,
                horizon=t["horizon"],
                losses=losses,
            )
        )
    result = read(run / "result.json")
    for test in result["test"]:
        for kind, score in test["scores"].items():
            key = test["name"] + "/" + kind
            reproduced = paired(rows, key, key)
            assert abs(reproduced["control"] - score["pool_macro_pinball"]) < 1e-12
    comparisons = {}
    for kind in ("empirical", "linear", "gru"):
        for arm in ("full", "balanced"):
            key = arm + "/" + kind
            comparisons[key] = paired(rows, "recent-100/" + kind, arm + "-100/" + kind)
    selected = result["selection"]["arm"]
    card = read(run / selected / "model-card.json")
    recent = read(run / "recent-100/model-card.json")["selected"]
    comparisons["selected_vs_recent_selected"] = paired(
        rows, "recent-100/" + recent, selected + "/" + card["selected"]
    )
    training_pools = set(read(design / "membership.json")["full-100"]["per_pool"])
    subsets = {
        "seen_pools": [r for r in rows if r["pool"] in training_pools],
        "unseen_pools": [r for r in rows if r["pool"] not in training_pools],
    }
    groups = {
        name: {arm: paired(rr, "recent-100/gru", arm + "-100/gru") for arm in ("full", "balanced")}
        for name, rr in subsets.items()
    }
    horizons = {
        str(h): {
            arm: paired([r for r in rows if r["horizon"] == h], "recent-100/gru", arm + "-100/gru")
            for arm in ("full", "balanced")
        }
        for h in (1, 3)
    }
    with Ledger(run / "ledger", read(run / "ledger/binding.json")) as ledger:
        events = ledger.audit(result["ledger_head"])
        kinds = [e["kind"] for e in events]
        assert kinds.index("development_selection_before_test") < kinds.index(
            "exploratory_test_read"
        )
        ends = [e for e in events if e["kind"] == "attempt_end"]
        assert len(ends) == 36 and all(e["payload"]["status"] == "completed" for e in ends)
        charged = ledger.charged()
    fits = {}
    for arm in ("recent", "full", "balanced"):
        fits[arm] = [
            {
                k: v
                for k, v in read(run / (arm + "-100") / f"{kind}-{seed}" / "fit.json").items()
                if k
                in [
                    "config",
                    "steps",
                    "sampled_decisions",
                    "unique_sampled_decisions",
                    "sampled_pools",
                    "sampled_days",
                    "sampled_per_pool",
                    "best_step",
                ]
            }
            for kind in ("linear", "gru")
            for seed in (7, 19)
        ]
    report = dict(
        design_manifest_sha=sha(design / "manifest.json"),
        run_manifest_sha=sha(run / "manifest.json"),
        selected=result["selection"],
        selected_family=card["selected"],
        comparisons=comparisons,
        by_seen_pool=groups,
        by_horizon=horizons,
        test_decisions=len(samples),
        test_pools=len({r["pool"] for r in rows}),
        test_days=len({r["day"] for r in rows}),
        test_pool_variants=dict(Counter(r["pool"] for r in rows)),
        selection_preceded_test=True,
        independent_rescoring_exact=True,
        charged_seconds=charged,
        process=read(ROOT / "run01-process.json"),
        actual_fit_support=fits,
        membership=read(design / "membership.json"),
        curves=result["test"],
        validation=result["curves"],
        new_fits=36,
        provider_queries=0,
        F01=False,
        edge_established=False,
        execution_model="B07 price proxy only for future paper",
    )
    publish(BASE / "docs/evidence/brain-curriculum-comparison-2026-10-04.json", report)
    print(
        json.dumps(
            dict(selected=selected, family=card["selected"], comparisons=comparisons, groups=groups)
        )
    )


if __name__ == "__main__":
    main()
