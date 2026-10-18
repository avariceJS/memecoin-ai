"""Frozen independent birth cohort and isolated keyless source campaign. No promotion."""

import argparse
import datetime
import json
import sqlite3
import subprocess
import time
from pathlib import Path

from memecoin_brain.source import BASE_EXCLUDED, EXCLUDED
from memecoin_experience.storage import atomic_json, digest, file_hash, verify_files
from memecoin_population.acquisition import capture, validate

NS = 10**9


def prepare(root, inventory, catalogue, prior_plans):
    inv = json.loads(inventory.read_text())
    verify_files(catalogue, inv["catalogue_sha"])
    with sqlite3.connect(
        (catalogue / "catalogue.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    ) as db:
        births = {
            row["pool"]: row
            for (payload,) in db.execute("select payload from births")
            for row in [json.loads(payload)]
        }
    if births != {row["pool"]: row for row in inv["births"]} or len(births) != len(inv["births"]):
        raise ValueError("campaign_birth_inventory_mismatch")
    blocked, mints = set(EXCLUDED), set(BASE_EXCLUDED)
    for entry in inv["metadata"].values():
        value = entry["value"]
        for row in value.get("cases", []) + value.get("pools", []):
            blocked.add(row["pool"])
            mints.add(row["base_mint"])
        for row in value.get("cohort", []):
            blocked.add(row["pool_id"])
            mints.update(row["params"][0])
    for path in prior_plans:
        for row in json.loads(path.read_text())["cases"]:
            blocked.add(row["pool"])
            mints.add(row["base_mint"])
    for row in inv["births"]:
        if row["pool"] in blocked:
            mints.add(row["base_mint"])
    cases, excluded = [], []
    for birth in inv["births"]:
        month = datetime.datetime.fromtimestamp(birth["event_s"], datetime.UTC).strftime("%Y-%m")
        if not "2026-05" <= month <= "2026-09":
            reason = "outside_registered_period"
        elif birth["pool"] in blocked or birth["base_mint"] in mints:
            reason = "prior_or_protected_cohort"
        else:
            reason = None
        if reason:
            excluded.append(dict(pool=birth["pool"], reason=reason))
        else:
            cases.append(
                dict(
                    birth,
                    month=month,
                    role="research_evaluation" if month == "2026-09" else "train",
                )
            )
    training_mints = {c["base_mint"] for c in cases if c["role"] == "train"}
    overlap = {
        c["pool"]
        for c in cases
        if c["role"] == "research_evaluation" and c["base_mint"] in training_mints
    }
    excluded.extend(dict(pool=p, reason="cross_role_mint_overlap") for p in sorted(overlap))
    cases = [c for c in cases if c["pool"] not in overlap]
    baseline = []
    for month in ("2026-05", "2026-06", "2026-07", "2026-08"):
        eligible = sorted(
            (c for c in cases if c["month"] == month),
            key=lambda c: digest(["diversity-baseline-20261008", c["base_mint"], c["pool"]]),
        )
        baseline.extend(c["pool"] for c in eligible[:4])

    def epoch(day):
        return int(datetime.datetime.fromisoformat(day + "T00:00:00+00:00").timestamp())

    protocol = dict(
        scope="independent_mint_historical_replay_not_prospective",
        baseline_pools=baseline,
        split=dict(
            train_start_s=epoch("2026-05-01"),
            train_end_s=epoch("2026-08-08"),
            validation_end_s=epoch("2026-08-11"),
            test_end_s=epoch("2026-08-15"),
        ),
        evaluation=dict(start_s=epoch("2026-09-01"), end_s=epoch("2026-09-15")),
        models=["narrow_fresh", "broad_fresh", "narrow_compute_control"],
        config=dict(
            width=128, epochs=5, batch=512, seed=20261008, checkpoint_every=20, cpu_threads=2
        ),
        compute_control=(
            "ceil(5 * broad_train_usable / narrow_train_usable) epochs; at "
            "most 500; otherwise report infeasible"
        ),
        context=32,
        horizons=[1, 3, 6],
        normalization="constant_feature_scale_one_v1",
        quality=dict(
            name="causal_activity_v1", min_positive_volume_fraction=0.25, min_context_volume_usd=100
        ),
        quality_scope=(
            "input only; no target-return, future survival or current "
            "liquidity filter; original enrollment retained"
        ),
        primary=(
            "per-horizon paired pinball on fixed September origins; report "
            "micro and equal-mint descriptive means"
        ),
        uncertainty=(
            "paired whole-mint bootstrap, 2000 draws, seed 20261008; "
            "conditional on one trainer seed"
        ),
        acceptance=(
            "no empty horizon; compare all structural origins and "
            "input-quality strata; report every model and failures; "
            "no promotion or trading-edge claim"
        ),
        population_bias=(
            "existing authority-selected calendar-page prefix, graduation and "
            "decoder support bias; not a complete market census"
        ),
    )
    jobs = [
        dict(
            pool=c["pool"],
            start_s=c["event_s"] + i * 3 * 86400,
            end_s=c["event_s"] + (i + 1) * 3 * 86400,
        )
        for c in cases
        for i in range(4)
    ]
    jobs.sort(key=lambda j: digest(["diversity-acquisition-order-20261008", j]))
    now = time.time_ns()
    plan = dict(
        version="population-public-capture-v1",
        created_ns=now,
        expires_ns=now + 8 * 3600 * NS,
        spacing_s=3,
        max_requests=len(jobs),
        max_bytes=4 * 1024**3,
        metadata=dict(
            inventory=file_hash(inventory),
            catalogue=inv["catalogue_sha"],
            previous_plans=[file_hash(p) for p in prior_plans],
            known_ns=inv["at_ns"],
        ),
        cases=cases,
        jobs=jobs,
        protocol=protocol,
    )
    validate(plan)
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(
        root / "population-selection.json",
        dict(
            enrolled=len(cases),
            excluded=excluded,
            blocked_pools=sorted(blocked),
            blocked_mints=sorted(mints),
            plan=digest(plan),
        ),
    )
    atomic_json(root / "plan.json", plan)
    return dict(
        plan=digest(plan),
        cases=len(cases),
        jobs=len(jobs),
        roles={r: sum(c["role"] == r for c in cases) for r in ("train", "research_evaluation")},
    )


def run(root, bridge_command):
    plan = json.loads((root / "plan.json").read_text())
    process = subprocess.Popen(
        bridge_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )

    def guard():
        process.stdin.write("check\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line or json.loads(line).get("ready") is not True:
            raise ValueError("acquisition_owner_fence")

    try:
        return capture(root / "capture", plan, guard=guard)
    finally:
        process.stdin.close()
        process.wait()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("prepare", "run"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--bridge", type=Path)
    args = p.parse_args()
    if args.action == "prepare":
        result = prepare(
            args.root,
            Path("work/development-inventory-v2-20261007.json"),
            args.root / "inputs/monthly-births01-catalogue",
            sorted(Path("work/brain-development-20261007").glob("*/frozen/plan.json")),
        )
    else:
        result = run(args.root, json.loads(args.bridge.read_text()))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
