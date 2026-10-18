"""Fixed-plan real-data system qualification; no acquisition, recipe search or promotion."""

import argparse
import hashlib
import io
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from memecoin_experience import generations
from memecoin_experience.composition import compose
from memecoin_experience.representations import ohlcv_samples, ohlcv_schema
from memecoin_experience.schema import membership
from memecoin_experience.sources import inputs
from memecoin_experience.storage import (
    Snapshot,
    atomic_json,
    build,
    code_identity,
    digest,
    file_hash,
    safe_path,
    verify_files,
)
from memecoin_experience.transfer import replicate

CASES = ("V1", "V2-warm", "V2-fresh", "V1-recovery")


def read(path):
    return json.loads(Path(path).read_text())


def bind(root, plan):
    root = safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(
        root / "declaration.json",
        dict(plan=plan, code=code_identity(), tool_sha=file_hash(Path(__file__))),
    )
    return root


def source_reference(path, plan, role):
    path = safe_path(path)
    pin = file_hash(path / "meta/manifest.json")
    verify_files(path / "meta", pin)
    report = read(path / "meta/source.json")
    source = report["plan"]
    if (
        source["role"] != role
        or source["pools"] != plan["pools"]
        or source["cutoff_ns"] != plan["source_cutoff_ns"]
    ):
        raise ValueError("qualification_source_binding")
    return dict(kind="continuous", path=str(path), pin=pin), report


def materialize(root, plan, source, interval, role):
    reference, report = source_reference(source, plan, role)
    schema = ohlcv_schema(
        plan["representation"]["context"], tuple(plan["representation"]["horizons"])
    )
    counts = Counter()
    rows = inputs([reference], plan["source_cutoff_ns"], counts, role=role)
    samples = ohlcv_samples(rows, plan["source_cutoff_ns"], schema)
    selected = (r for r in samples if interval[0] * 10**9 <= r["decision_ns"] < interval[1] * 10**9)
    result = build(
        root / "store",
        selected,
        sources=[reference],
        schema=schema,
        cutoff=plan["source_cutoff_ns"],
        source_counts=counts,
        role=role,
        selection=dict(origin_interval_s=interval),
    )
    return result["snapshot"], report


def exposure(snapshot, split, pools):
    counts = Counter()
    by_pool = {p["pool"]: Counter() for p in pools}
    for i in range(len(snapshot.parts)):
        _, rows = snapshot.partition(i)
        for row in rows:
            role, mask = membership(row, split, snapshot.schema)
            counts[role] += 1
            counts[role + "_usable"] += int(any(mask))
            by_pool[row["pool"]][role + "_usable"] += int(any(mask))
    return dict(population=dict(counts), per_pool={k: dict(v) for k, v in by_pool.items()})


def prepare(root, plan, source):
    first, total = plan["first_split"], plan["cumulative_split"]
    base, report = materialize(
        root, plan, source, [first["train_start_s"], first["test_end_s"]], "train"
    )
    increment, _ = materialize(
        root, plan, source, [first["test_end_s"], total["test_end_s"]], "train"
    )
    combined = compose(root / "store", [base, increment])["snapshot"]
    result = dict(source=report, base=base, increment=increment, cumulative=combined, exposure={})
    for name, pin, split in (("base", base, first), ("cumulative", combined, total)):
        snap = Snapshot(root / "store", pin)
        result["exposure"][name] = exposure(snap, split, plan["pools"])
        result["exposure"][name]["persisted_examples"] = snap.manifest["decisions"]
        replicate(root / "store", root / "replica", pin)
        if replicate(root / "store", root / "replica", pin)["copied"] != 0:
            raise ValueError("qualification_replica_not_idempotent")
    a, b, c = (
        Snapshot(root / "store", pin).manifest["decisions"] for pin in (base, increment, combined)
    )
    if a + b != c:
        raise ValueError("qualification_composition_count")
    atomic_json(root / "prepared.json", result)
    return dict(
        base_examples=a, increment_examples=b, cumulative_examples=c, exposure=result["exposure"]
    )


def case_args(root, plan, case):
    prepared = read(root / "prepared.json")
    base = case in {"V1", "V1-recovery"}
    args = dict(
        name=case,
        owner=plan["owner"],
        store=root / "replica",
        snapshot=prepared["base" if base else "cumulative"],
        split=plan["first_split" if base else "cumulative_split"],
        config=plan["config"],
    )
    if case == "V2-warm":
        parent = read(root / "V1.json")
        _, artifact = generations.get(root / "models", parent["generation"])
        args["parent"] = dict(path=str(artifact), pin=parent["artifact"])
    return args


def fit(root, plan, case):
    args = case_args(root, plan, case)
    result = generations.run(root / "models", **args)
    record, artifact = generations.get(root / "models", result["generation"])
    report = read(artifact / "report.json")
    prepared = read(root / "prepared.json")
    key = "base" if case in {"V1", "V1-recovery"} else "cumulative"
    usable = prepared["exposure"][key]["population"]["train_usable"]
    if report["rows_seen"] != usable * plan["config"]["epochs"]:
        raise ValueError("qualification_incomplete_exposure")
    atomic_json(root / (case + ".json"), result)
    return dict(
        case=case,
        **result,
        rows_seen=record["rows_seen"],
        parameters=report["parameters"],
        steps=report["steps"],
    )


def recovery(root, plan, plan_path):
    """Kill only our own child after a durable checkpoint, then compare exact resumed results."""
    import torch

    name = root / "models/names/V1-recovery.json"
    if name.exists():
        raise ValueError("qualification_recovery_requires_new_case")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "fit",
        "--root",
        str(root),
        "--plan",
        str(plan_path.resolve()),
        "--case",
        "V1-recovery",
    ]
    with (root / "recovery-child.log").open("w") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        observed = None
        try:
            while child.poll() is None:
                if time.monotonic() - started > 1200:
                    raise RuntimeError("qualification_checkpoint_deadline")
                if name.exists():
                    identity = read(name)["id"]
                    checkpoint = root / "models/jobs" / identity / "training/checkpoint.pt"
                    if checkpoint.exists():
                        checkpoint_bytes = checkpoint.read_bytes()
                        state = torch.load(
                            io.BytesIO(checkpoint_bytes), map_location="cpu", weights_only=True
                        )
                        if state["steps"] >= plan["config"]["checkpoint_every"]:
                            observed = dict(
                                steps=state["steps"],
                                rows_seen=state["rows_seen"],
                                checkpoint_sha=hashlib.sha256(checkpoint_bytes).hexdigest(),
                            )
                            child.kill()
                            break
                time.sleep(0.2)
            child.wait()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    if observed is None or child.returncode != -9:
        raise ValueError("qualification_no_hard_interruption")
    atomic_json(root / "interruption.json", dict(**observed, exit_code=child.returncode))
    fit(root, plan, "V1-recovery")
    original = read(root / "V1.json")
    resumed = read(root / "V1-recovery.json")
    _, a = generations.get(root / "models", original["generation"])
    _, b = generations.get(root / "models", resumed["generation"])
    wa = torch.load(a / "weights.pt", weights_only=True)
    wb = torch.load(b / "weights.pt", weights_only=True)
    equal = wa.keys() == wb.keys() and all(torch.equal(v, wb[k]) for k, v in wa.items())
    if not equal or read(a / "report.json") != read(b / "report.json"):
        raise ValueError("qualification_recovery_mismatch")
    result = dict(
        interruption=observed,
        weights_equal=True,
        reports_equal=True,
        resumed_generation=resumed["generation"],
    )
    atomic_json(root / "recovery.json", result)
    return result


def register(root, plan):
    from memecoin_experience.evaluation import register as protocol

    result = {}
    for case in CASES[:3]:
        fitted = read(root / (case + ".json"))
        record, artifact = generations.get(root / "models", fitted["generation"])
        step = root / (case + "-protocol.json")
        if step.exists():
            pin = read(step)["protocol"]
            prior = read(root / "protocols" / (pin + ".json"))
            expected = dict(
                candidate=record["artifact"],
                pools=sorted(p["pool"] for p in plan["pools"]),
                **plan["evaluation"],
            )
            if digest(prior) != pin or any(prior[k] != v for k, v in expected.items()):
                raise ValueError("qualification_protocol_binding")
        else:
            pin = protocol(
                root / "protocols",
                artifact,
                record["artifact"],
                pools=[p["pool"] for p in plan["pools"]],
                **plan["evaluation"],
            )["protocol"]
            atomic_json(step, dict(protocol=pin))
        result[case] = pin
    atomic_json(root / "registered.json", result)
    return result


def score(root, plan, source):
    from memecoin_experience.evaluation import score as evaluate

    protocols = read(root / "registered.json")  # All candidates fixed before reading eval source.
    interval = [plan["evaluation"]["start_s"], plan["evaluation"]["end_s"]]
    snapshot, source_report = materialize(root, plan, source, interval, "research_evaluation")
    result = dict(source=source_report, snapshot=snapshot, candidates={})
    for case in CASES[:3]:
        fitted = read(root / (case + ".json"))
        _, artifact = generations.get(root / "models", fitted["generation"])
        result["candidates"][case] = evaluate(
            root / "protocols",
            protocols[case],
            root / "store",
            snapshot,
            artifact,
            root / "evaluation" / case,
        )
    atomic_json(root / "qualification.json", result)
    return {
        k: dict(
            metrics=v["metrics"], supported_pools=v["supported_pools"], enrollment=v["enrollment"]
        )
        for k, v in result["candidates"].items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "fit", "recovery", "register", "score"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    plan = read(args.plan)
    root = bind(args.root, plan)
    if args.phase in {"prepare", "score"}:
        if args.source is None:
            parser.error("--source required")
        result = (prepare if args.phase == "prepare" else score)(root, plan, args.source)
    elif args.phase == "fit":
        if args.case is None:
            parser.error("--case required")
        result = fit(root, plan, args.case)
    elif args.phase == "recovery":
        result = recovery(root, plan, args.plan)
    else:
        result = register(root, plan)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
