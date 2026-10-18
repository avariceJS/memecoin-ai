"""Reconcile a fixed qualification run without selecting candidates or changing artifacts."""

import argparse
import json
import platform
import subprocess
import time
from collections import Counter
from pathlib import Path

from memecoin_experience.storage import Snapshot, atomic_json, digest, file_hash, verify_files


def read(path):
    return json.loads(Path(path).read_text())


def origin_funnel(rows, *, pools, context, start_s, end_s):
    """Independent accounting of the shipped producer's contiguous-context eligibility."""
    counts = Counter()
    per_pool = {p: Counter() for p in pools}
    previous, run, reason = None, 0, "initial_context"
    for row in rows:
        pool, at = row["pool"], row["open_s"]
        if pool not in per_pool or (previous is not None and (pool, at) <= previous):
            raise ValueError("qualification_funnel_membership_or_order")
        if previous is None or previous[0] != pool:
            run, reason = 0, "initial_context"
        elif at != previous[1] + 300:
            run, reason = 0, "gap_context"
        previous = pool, at
        run += 1
        key = (
            "outside_origin_interval"
            if not start_s <= at + 300 < end_s
            else reason
            if run < context
            else "persistable_origins"
        )
        counts["raw_bars"] += 1
        counts[key] += 1
        per_pool[pool]["raw_bars"] += 1
        per_pool[pool][key] += 1
    return dict(counts=dict(counts), per_pool={p: dict(v) for p, v in per_pool.items()})


def report(root, source, before, after):
    import torch

    from memecoin_experience.generations import get

    declaration = read(root / "declaration.json")
    plan = declaration["plan"]
    prepared = read(root / "prepared.json")
    scored = read(root / "qualification.json")
    source_pin = file_hash(source / "meta/manifest.json")
    verify_files(source / "meta", source_pin)
    base = Snapshot(root / "store", prepared["base"])
    if base.manifest["binding"]["sources"] != [dict(kind="continuous", pin=source_pin)]:
        raise ValueError("qualification_report_source_pin")
    if read(source / "meta/source.json") != prepared["source"]:
        raise ValueError("qualification_report_source")
    with (source / "meta/rows.jsonl").open() as stream:
        funnel = origin_funnel(
            (json.loads(line) for line in stream),
            pools=[p["pool"] for p in plan["pools"]],
            context=plan["representation"]["context"],
            start_s=plan["cumulative_split"]["train_start_s"],
            end_s=plan["cumulative_split"]["test_end_s"],
        )
    total = prepared["exposure"]["cumulative"]
    if funnel["counts"].get("persistable_origins", 0) != total["persisted_examples"]:
        raise ValueError("qualification_funnel_reconciliation")
    candidates = {}
    protocols = read(root / "registered.json")
    for case in ("V1", "V2-warm", "V2-fresh"):
        fitted = read(root / (case + ".json"))
        generation, artifact = get(root / "models", fitted["generation"])
        training = read(artifact / "report.json")
        evaluation_path = root / "evaluation" / case
        evaluation_pin = file_hash(evaluation_path / "manifest.json")
        verify_files(evaluation_path, evaluation_pin)
        evaluation = read(evaluation_path / "report.json")
        if (
            evaluation != scored["candidates"][case]
            or evaluation["binding"]["candidate"] != fitted["artifact"]
        ):
            raise ValueError("qualification_report_evaluation")
        cohort = "base" if case == "V1" else "cumulative"
        expected = (
            prepared["exposure"][cohort]["population"]["train_usable"] * plan["config"]["epochs"]
        )
        if training["rows_seen"] != expected:
            raise ValueError("qualification_report_exposure")
        candidates[case] = dict(
            generation=fitted["generation"],
            artifact=fitted["artifact"],
            parent=generation["declaration"]["parent"],
            snapshot=generation["declaration"]["snapshot"],
            parameters=training["parameters"],
            epochs=training["epochs"],
            steps=training["steps"],
            rows_seen=training["rows_seen"],
            population=training["statistics"]["population"],
            denominators=training["statistics"]["denominators"],
            protocol=protocols[case],
            evaluation_pin=evaluation_pin,
            evaluation=evaluation,
        )
    stable = (
        "universe_sha",
        "source_config_sha",
        "operator_config_sha",
        "deployment",
        "source_stop",
    )
    owner_fields_equal = {k: before[k] == after[k] for k in stable}
    owner_fields_equal["champion"] = (
        before["operator_summary"]["champion"] == after["operator_summary"]["champion"]
    )
    if not all(owner_fields_equal.values()):
        raise ValueError("qualification_owner_drift")
    return dict(
        version="brain-system-qualification-receipt-v1",
        at_ns=time.time_ns(),
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        reporter_sha=file_hash(Path(__file__)),
        declaration=declaration,
        declaration_sha=digest(declaration),
        raw_source_pin=source_pin,
        source_counts=prepared["source"]["counts"],
        source_coverage=prepared["source"]["coverage"],
        origin_funnel=funnel,
        composition={k: prepared[k] for k in ("base", "increment", "cumulative")},
        exposure=prepared["exposure"],
        candidates=candidates,
        recovery=read(root / "recovery.json"),
        evaluation_source=scored["source"],
        owner=dict(
            before_ns=before["at_ns"],
            after_ns=after["at_ns"],
            fields_equal=owner_fields_equal,
            status_after=after["operator_summary"],
        ),
        compute=dict(
            machine=platform.machine(),
            torch=str(torch.__version__),
            cuda_available=torch.cuda.is_available(),
            cuda_qualified=False,
            distributed_qualified=False,
            fit_wall_time="not_instrumented",
        ),
        limitations=[
            "Existing selected development history; not an untouched or prospective benchmark",
            "Overlapping origins; no independent uncertainty or execution-quality evidence",
            "CPU reference model; no large-model, CUDA, DDP or remote-store acceptance",
            "Indexed-capture scope; unindexed transport failures remain unknown",
            "Expected slots include pre-birth periods; not a provider failure rate",
        ],
        production_eligible=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "source", "owner-before", "owner-after", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = report(args.root, args.source, read(args.owner_before), read(args.owner_after))
    atomic_json(args.output, result)
    print(
        json.dumps(
            dict(
                receipt=str(args.output),
                sha=file_hash(args.output),
                funnel=result["origin_funnel"]["counts"],
            )
        )
    )


if __name__ == "__main__":
    main()
