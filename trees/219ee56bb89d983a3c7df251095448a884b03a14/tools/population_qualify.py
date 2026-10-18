"""Synthetic diversity capacity and sealed development-data audits; no requests or fitting."""

import argparse
import json
import resource
import sqlite3
import subprocess
import time
from pathlib import Path

from memecoin_experience.continuous import NAMESPACE
from memecoin_experience.storage import Snapshot, atomic_json, build, code_identity, file_hash
from memecoin_population import adapters, catalog, diversity, planning

NS = 10**9


def policy(prefix):
    return dict(
        entity_prefix=prefix,
        knowledge="at_decision",
        axes=[["@entity"], ["venue"], ["lifecycle"], ["@period"], ["venue", "lifecycle"]],
        time_bucket_ns=30 * NS,
        selection=dict(salt="qualification-fixed", numerator=1, denominator=1),
        enforcement="report",
        min_train_entities=1,
        max_entity_share=1,
        max_unknown_fraction=1,
        objective=diversity.OBJECTIVE,
    )


def scale(root, entities, per_entity, fit=False):
    end = per_entity + 5
    facts = root / "facts"
    cfg = policy("synthetic/market/")

    def members():
        for p in range(entities):
            for a, b, phase in ((0, end // 2, "early"), (end // 2, end, "late")):
                yield dict(
                    entity=f"synthetic/market/{p:08d}",
                    start_ns=a * NS,
                    end_ns=b * NS,
                    known_ns=0,
                    attributes=dict(venue=str(p % 8) if p % 7 else None, lifecycle=phase),
                    evidence=["a" * 64],
                )

    started = time.monotonic()
    pop = catalog.publish(
        facts,
        "population",
        members(),
        cutoff_ns=end * NS,
        definition=dict(role="train", selection="synthetic fixed census includes 20% unsupported"),
    )
    stream = dict(
        name="events",
        source="synthetic",
        modality="order_flow",
        representation="events-v1",
        unit="events",
        cadence_ns=None,
        tile_ns=5 * NS,
    )
    plan = dict(
        start_ns=0,
        end_ns=end * NS,
        cutoff_ns=end * NS,
        selection=cfg["selection"],
        partitions=64,
        streams=[stream, dict(stream, name="state", modality="liquidity", cadence_ns=NS)],
    )
    planned = planning.create(facts, pop, root / "work-plan", plan)
    plan_seconds = time.monotonic() - started
    print(json.dumps(dict(stage="plan", seconds=plan_seconds, **planned)), flush=True)
    schema = dict(
        schema="brain-experience-v2",
        layout="dense-sequence-f64-v1",
        context=2,
        features=[
            dict(name="flow", unit="units", modality="order_flow"),
            dict(name="depth", unit="units", modality="liquidity"),
        ],
        targets=[dict(name="change", unit="ratio", kind="synthetic_regression")],
        representation=dict(name="synthetic-diversity", version="1", code_sha="c" * 64),
        availability="point_in_time",
    )

    def samples():
        for p in range(entities):
            if p % 5 == 0:
                continue
            for i in range(per_entity):
                t = (i + 1) * NS
                yield dict(
                    id=f"{p:08d}:{i:08d}",
                    pool=f"{p:08d}",
                    decision_ns=t,
                    input_start_ns=t - NS,
                    input_available_ns=t,
                    input_dependencies=[
                        dict(artifact_sha="a" * 64, event_ns=t - NS, available_ns=t)
                    ],
                    values=[[p % 8, None], [i % 11, p % 7]],
                    targets=[(i % 5) * 0.01],
                    target_status=["observed"],
                    target_maturity_ns=[t + NS // 2],
                    target_available_ns=[t + NS // 2],
                    target_captures=[["b" * 64]],
                )

    started = time.monotonic()
    snapshot = build(
        root / "experience",
        samples(),
        sources=[dict(synthetic=True)],
        schema=schema,
        cutoff=end * NS,
        chunk=1024,
    )["snapshot"]
    build_seconds = time.monotonic() - started
    print(json.dumps(dict(stage="build", seconds=build_seconds, snapshot=snapshot)), flush=True)
    split = dict(
        train_start_s=0, train_end_s=end * 6 // 10, validation_end_s=end * 8 // 10, test_end_s=end
    )
    started = time.monotonic()
    result = diversity.audit(
        root / "experience",
        snapshot,
        facts,
        pop,
        root / "audit",
        split=split,
        config=cfg,
        coverage_plan=dict(
            path=str(root / "work-plan"), pin=file_hash(root / "work-plan/manifest.json")
        ),
    )
    actual = Snapshot(root / "experience", snapshot).manifest["decisions"]
    assert actual == (entities - (entities + 4) // 5) * per_entity
    assert result["enrolled_entities"] == entities
    assert result["unmapped_rows"] == 0
    assert (
        result["train_distribution"]['["@entity"]']["supported_values"]
        == entities - (entities + 4) // 5
    )
    audit_seconds = time.monotonic() - started
    training = None
    if fit:
        import torch

        from memecoin_experience.learning import train

        train_config = dict(
            width=8,
            epochs=1,
            batch=1024,
            seed=42,
            model_kind="temporal-mlp-quantiles-v1",
            checkpoint_every=50,
        )
        started = time.monotonic()
        audited = train(
            root / "experience",
            snapshot,
            root / "audited-fit",
            plan=split,
            config=train_config,
            diversity=dict(path=str(root / "audit"), pin=file_hash(root / "audit/manifest.json")),
        )
        audited_seconds = time.monotonic() - started
        started = time.monotonic()
        plain = train(
            root / "experience", snapshot, root / "plain-fit", plan=split, config=train_config
        )
        plain_seconds = time.monotonic() - started
        left = torch.load(root / "audited-fit/artifact/weights.pt", weights_only=True)
        right = torch.load(root / "plain-fit/artifact/weights.pt", weights_only=True)
        assert all(torch.equal(v, right[k]) for k, v in left.items())
        assert audited["test"] == plain["test"]
        assert (
            audited["exposure"]["draw_distribution"]['["@entity"]']["total"] == audited["rows_seen"]
        )
        training = dict(
            rows_seen=audited["rows_seen"],
            steps=audited["steps"],
            audited_seconds=audited_seconds,
            plain_seconds=plain_seconds,
            identical_weights_and_metrics=True,
            exposure=audited["exposure"],
        )
    return dict(
        kind="synthetic_diversity_capacity",
        entities=entities,
        rows=actual,
        plan=planned,
        population=pop,
        snapshot=snapshot,
        audit=result,
        audit_pin=file_hash(root / "audit/manifest.json"),
        training=training,
        seconds=dict(plan=plan_seconds, build=build_seconds, audit=audit_seconds),
    )


def real(root, inputs):
    original = json.loads(Path("configs/brain-system-qualification-20261008.json").read_text())
    prepared = json.loads((inputs / "run01/prepared.json").read_text())
    store = inputs / "run01/store"
    # The previous qualification's source locator is explicit and its pin is verified by Snapshot.
    if not store.exists():
        raise ValueError("qualification_experience_store_missing")
    snap = prepared["cumulative"]
    data = Snapshot(store, snap)
    cutoff = data.manifest["binding"]["cutoff_ns"]
    facts = root / "facts"
    pop = adapters.universe(
        facts,
        inputs / "owner-before.json",
        file_hash(inputs / "owner-before.json"),
        role="train",
        cutoff_ns=cutoff,
        age_edges_ns=[86400 * NS, 3 * 86400 * NS, 7 * 86400 * NS],
    )
    split = original["cumulative_split"]
    cfg = policy("solana/pool/")
    cfg["time_bucket_ns"] = 7 * 86400 * NS
    stream = dict(
        name="qualified-bars",
        source=NAMESPACE,
        modality="ohlcv",
        representation="qualified-ohlcv-v1",
        unit="unique_bar_opens",
        cadence_ns=300 * NS,
        tile_ns=86400 * NS,
    )
    plan = dict(
        start_ns=split["train_start_s"] * NS,
        end_ns=split["test_end_s"] * NS,
        cutoff_ns=cutoff,
        streams=[
            stream,
            dict(
                stream,
                name="swaps",
                source="unassigned",
                modality="order_flow",
                representation="unassigned",
                unit="events",
                cadence_ns=None,
            ),
        ],
        selection=cfg["selection"],
        partitions=64,
    )
    planning.create(facts, pop, root / "initial-plan", plan)
    ref = dict(
        path=str(inputs / "train-source"), pin=file_hash(inputs / "train-source/meta/manifest.json")
    )
    coverage = adapters.continuous_coverage(
        facts, ref, root / "initial-plan", file_hash(root / "initial-plan/manifest.json")
    )
    revised = planning.create(facts, pop, root / "coverage-plan", plan, coverage=coverage)
    audits = {}
    for knowledge in ("at_decision", "at_cutoff"):
        out = root / knowledge
        report = diversity.audit(
            store,
            snap,
            facts,
            pop,
            out,
            split=split,
            config=dict(cfg, knowledge=knowledge),
            coverage_plan=dict(
                path=str(root / "coverage-plan"),
                pin=file_hash(root / "coverage-plan/manifest.json"),
            ),
        )
        with sqlite3.connect(out / "counts.sqlite") as db:
            query = "select n from counts where axis=? and role='train' and metric='usable'"
            n = db.execute(
                "select sum(n) from (" + query + " order by n desc limit 4)", ('["@entity"]',)
            ).fetchone()[0]
            total = db.execute("select sum(n) from (" + query + ")", ('["@entity"]',)).fetchone()[0]
        audits[knowledge] = dict(
            pin=file_hash(out / "manifest.json"), report=report, top_four_share=n / total
        )
    assert audits["at_cutoff"]["report"]["population"]["train_usable"] == 74680
    return dict(
        kind="sealed_existing_development_diversity_not_new_benchmark",
        population=pop,
        coverage=coverage,
        planning=revised,
        audits=audits,
        source=ref["pin"],
        snapshot=snap,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("scale", "real"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument(
        "--fit",
        action="store_true",
        help="scale only: compare audited vs plain synthetic CPU fitting",
    )
    p.add_argument("--entities", type=int, default=10000)
    p.add_argument("--per-entity", type=int, default=125)
    p.add_argument("--inputs", type=Path, default=Path("work/brain-system-qualification-20261008"))
    args = p.parse_args()
    if args.entities < 10 or args.per_entity < 30:
        p.error("scale inputs must allow meaningful train/validation/test partitions")
    args.root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = (
        scale(args.root, args.entities, args.per_entity, args.fit)
        if args.mode == "scale"
        else real(args.root, args.inputs)
    )
    result.update(
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        tool_sha=file_hash(Path(__file__)),
        code=code_identity(),
        elapsed_seconds=time.monotonic() - started,
        max_rss_platform_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        rss_note="bytes on macOS; KiB on Linux; includes torch only with --fit; no GPU claim",
    )
    atomic_json(args.root / "receipt.json", result)
    print(
        json.dumps(
            dict(receipt=str(args.root / "receipt.json"), elapsed_seconds=result["elapsed_seconds"])
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
