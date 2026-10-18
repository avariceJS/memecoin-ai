"""Reproduce OLD/NEW support and persisted future forecasts without changing runtime."""

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from memecoin_curriculum.archive import revision_audit, shards
from memecoin_curriculum.diversity import compact, delta, support
from memecoin_ml.data import read, sha
from memecoin_operator.history import build_report
from memecoin_operator.learning import partitions
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish


def dataset(entry, births):
    root = Path(entry["artifact"])
    verify(root)
    cutoff = entry["as_of_ns"]
    with sqlite3.connect(f"file:{root / 'index.sqlite'}?mode=ro&immutable=1", uri=True) as db:
        rows = [
            json.loads(r[0])
            for r in db.execute(
                "select payload from firsts where available<=? order by pool,open_s", (cutoff,)
            )
        ]
        revisions = revision_audit(db)
        captures = dict(db.execute("select status,count(*) from captures group by status"))
    parts = defaultdict(list)
    for part in shards(rows, dict(as_of_ns=cutoff)):
        roles, _ = partitions(part, cutoff)
        for name, samples in roles.items():
            if name != "purged":
                parts[name].extend(compact(part, samples, births))
    return parts, dict(revisions=revisions, captures=captures, index_sha=sha(root / "index.sqlite"))


def forecasts(root, cutoff):
    groups = defaultdict(list)
    for exam in sorted((root / "exams").glob("*.json")):
        info = read(exam)
        lanes = Path(info["exam_root"]) / "candidate-shadows"
        for lane in sorted(lanes.iterdir()):
            if lane.is_symlink() or not lane.is_dir():
                continue
            for path in sorted((lane / "shadow/predictions").glob("*.json")):
                p = read(path)
                if p["predicted_ns"] > cutoff:
                    continue
                outcome = lane / "shadow/outcomes" / path.name
                if not outcome.exists():
                    continue
                o = read(outcome)
                if o["status"] != "scored" or o["scored_ns"] > cutoff:
                    continue
                if o["prediction_sha"] != sha(path) or p["model_sha"] != lane.name:
                    raise ValueError("forecast_identity")
                if (
                    p["predicted_ns"] < info["activation_ns"]
                    or p["input_available_ns"] > p["predicted_ns"]
                ):
                    raise ValueError("forecast_availability")
                for family, metrics in o["candidate_metrics"].items():
                    groups[(info["id"], lane.name, family, p["horizon"])].append((p, metrics))
    result = []
    for (exam, model, family, horizon), rows in sorted(groups.items()):
        pool, day = defaultdict(list), defaultdict(list)
        for p, m in rows:
            pool[p["pool"]].append(m["pinball"])
            day[(p["pool"], p["event_close_s"] // 86400)].append(m["pinball"])
        result.append(
            dict(
                exam=exam,
                model_sha=model,
                family=family,
                minutes=horizon * 5,
                n=len(rows),
                pool_macro_pinball=sum(sum(v) / len(v) for v in pool.values()) / len(pool),
                pool_day_macro_pinball=sum(sum(v) / len(v) for v in day.values()) / len(day),
                pinball=sum(m["pinball"] for _, m in rows) / len(rows),
                coverage=[sum(m["coverage"][i] for _, m in rows) / len(rows) for i in range(3)],
                per_pool={p: dict(n=len(v), pinball=sum(v) / len(v)) for p, v in pool.items()},
                days=len({p["event_close_s"] // 86400 for p, _ in rows}),
                ci=None,
            )
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff-ns", type=int, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("immutable_output_exists")
    entries = sorted(
        (read(p) for p in (args.root / "datasets").glob("*.json")), key=lambda r: r["as_of_ns"]
    )
    if len(entries) != 2:
        raise ValueError("audit_requires_exactly_two_snapshots")
    births = {p["pool"]: p.get("pool_created_ns") for p in read(args.universe)["pools"]}
    old, old_meta = dataset(entries[0], births)
    new, new_meta = dataset(entries[1], births)
    report = dict(
        schema="brain-experience-audit-v1",
        cutoff_ns=args.cutoff_ns,
        runtime_mutated=False,
        fits=0,
        network_requests=0,
        protected_holdout_read=False,
        snapshots=entries,
        old={k: support(v) for k, v in old.items()},
        new={k: support(v) for k, v in new.items()},
        train_delta=delta(old["train"], new["train"]),
        genuinely_new_train_delta=delta(sum(old.values(), []), new["train"]),
        eligible_delta=delta(sum(old.values(), []), sum(new.values(), [])),
        raw_audit=dict(old=old_meta, new=new_meta),
        forecasts=forecasts(args.root, args.cutoff_ns),
        operator=build_report(args.root, args.cutoff_ns),
        definitions=dict(
            age_edges_seconds=[0, 86400, 604800, 2592000, 7776000],
            volatility_edges_rms=[0.005, 0.02, 0.1],
            dollar_volume_edges=[100, 1000, 10000, 100000],
            denominator="all eligible decisions before cap; whole maturity purge",
            dependence=(
                "Adjacent contexts share 7/8 frames; 15m labels share 2/3 intervals. "
                "Counts are not ESS."
            ),
            model_comparison=(
                "Different exams and rolling tests are unpaired; no OLD/NEW improvement claim."
            ),
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    publish(args.output, report)
    print(
        json.dumps(
            dict(
                output=str(args.output),
                sha=sha(args.output),
                train_added=report["train_delta"]["added"]["decisions"],
                eligible_added=report["eligible_delta"]["added"]["decisions"],
            )
        )
    )


if __name__ == "__main__":
    main()
