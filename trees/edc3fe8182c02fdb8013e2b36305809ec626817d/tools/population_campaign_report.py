"""Reconcile a completed population campaign, without requests, fitting or candidate selection."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from experience_qualification_report import origin_funnel
from population_learning import census, compare, learning_protocol

from memecoin_experience import generations
from memecoin_experience.storage import (
    Snapshot,
    atomic_json,
    digest,
    file_hash,
    lock,
    verify_files,
)
from memecoin_population import corpus


def read(path):
    return json.loads(path.read_text())


def reconcile_capture(campaign):
    plan = read(campaign / "plan.json")
    capture = campaign / "capture"
    if not (capture / "complete.json").exists() or (capture / "STOP.json").exists():
        raise ValueError("population_report_acquisition_incomplete")
    if read(capture / "plan.json") != plan:
        raise ValueError("population_report_plan")
    with lock(capture / ".capture.lock"):
        with sqlite3.connect((capture / "requests.sqlite").as_uri() + "?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            counts, pins = Counter(), []
            total_bytes = 0
            for key, ordinal, raw, status, started, path, result in db.execute(
                "select * from requests order by ordinal"
            ):
                if (
                    ordinal != len(pins)
                    or ordinal >= len(plan["jobs"])
                    or json.loads(raw) != plan["jobs"][ordinal]
                    or key != digest(plan["jobs"][ordinal])
                    or status not in {"received", "unsupported", "transport_failure"}
                ):
                    raise ValueError("population_report_request")
                cap = Path(path)
                if cap != capture / "requests" / f"{ordinal:07d}":
                    raise ValueError("population_report_capture_path")
                verify_files(cap)
                outcome, receipt, request = (
                    read(cap / (n + ".json")) for n in ("outcome", "receipt", "request")
                )
                if (
                    outcome != json.loads(result)
                    or outcome["status"] != status
                    or outcome["ordinal"] != ordinal
                    or outcome["request"] != key
                    or receipt["status"] != status
                    or receipt["response_sha256"] != file_hash(cap / "response.bin")
                    or request["started_ns"] != started
                    or outcome["bytes"] != (cap / "response.bin").stat().st_size
                ):
                    raise ValueError("population_report_outcome")
                counts[status] += 1
                total_bytes += outcome["bytes"]
                pins.append(
                    dict(request=key, status=status, capture=file_hash(cap / "manifest.json"))
                )
            if len(pins) != len(plan["jobs"]):
                raise ValueError("population_report_acquisition_incomplete")
            complete = read(capture / "complete.json")
            if any(
                complete.get(k) != v
                for k, v in dict(
                    completed=True,
                    plan=digest(plan),
                    requests=len(pins),
                    population=len(plan["cases"]),
                    statuses=dict(counts),
                    trading=False,
                ).items()
            ):
                raise ValueError("population_report_completion")
    return dict(
        plan=digest(plan),
        requests=len(pins),
        outcomes=dict(counts),
        bytes=total_bytes,
        prefix=digest(pins),
        completion=file_hash(capture / "complete.json"),
        recoveries={p.name: file_hash(p) for p in sorted((capture / "recoveries").glob("*.json"))},
    )


def report(campaign, root):
    campaign, root = campaign.resolve(), root.resolve()
    plan, result = read(campaign / "plan.json"), read(root / "result.json")
    prepared = read(root / "prepared.json") if (root / "prepared.json").exists() else None
    declaration = (
        read(root / "learning-plan.json") if (root / "learning-plan.json").exists() else None
    )
    protocol = learning_protocol(plan, declaration)
    acquisition = reconcile_capture(campaign)
    if (
        (prepared is not None and result["binding"] != prepared["binding"])
        or result["binding"]["acquisition"] != digest(plan)
        or result["binding"].get("learning_plan") != (digest(declaration) if declaration else None)
    ):
        raise ValueError("population_report_run_binding")
    sources = {}
    for role in ("train", "research_evaluation"):
        path = root / (role + "-source")
        if not path.exists():
            sources[role] = dict(status="not_opened")
            continue
        source = corpus.source(campaign, path, role)
        meta = read(path / "source/meta/source.json")
        # Unbounded decision interval counts every context-ready row persisted by the producer.
        with (path / "source/meta/rows.jsonl").open() as stream:
            funnel = origin_funnel(
                (json.loads(line) for line in stream),
                pools=[c["pool"] for c in plan["cases"] if c["role"] == role],
                context=protocol["context"],
                start_s=0,
                end_s=2**63,
            )
        if funnel["counts"].get("raw_bars", 0) != source["bars"]:
            raise ValueError("population_report_bars")
        sources[role] = dict(accounting=source, coverage=meta["coverage"], funnel=funnel)
    store = root / "store"
    if prepared:
        raw = Snapshot(store, prepared["raw"])
        per_pool = Counter()
        for i in range(len(raw.parts)):
            _, rows = raw.partition(i)
            per_pool.update(r["pool"] for r in rows)
        funnel = sources["train"]["funnel"]
        if raw.manifest["decisions"] != funnel["counts"].get("persistable_origins", 0) or any(
            per_pool[p] != c.get("persistable_origins", 0) for p, c in funnel["per_pool"].items()
        ):
            raise ValueError("population_report_contexts")
    candidates = {}
    for name, outcome in result["training"].items():
        if outcome["status"] != "completed":
            candidates[name] = outcome
            continue
        generation, artifact = generations.get(root / "generations", outcome["generation"])
        fitted = read(artifact / "report.json")
        snapshot = generation["declaration"]["snapshot"]
        counts = census(store, snapshot, protocol["split"])
        if (
            counts != prepared["counts"][name]
            or fitted["rows_seen"] != counts["train_usable"] * fitted["epochs"]
            or fitted["exposure"]["binding"]["rows_seen"] != fitted["rows_seen"]
            or fitted["exposure"]["binding"]["steps"] != fitted["steps"]
            or outcome["artifact"] != generation["artifact"]
        ):
            raise ValueError("population_report_exposure")
        audit_path = root / (name + "-diversity")
        verify_files(audit_path, generation["declaration"]["diversity"])
        candidates[name] = dict(
            generation=outcome["generation"],
            artifact=outcome["artifact"],
            snapshot=snapshot,
            **{k: fitted[k] for k in ("parameters", "epochs", "steps", "rows_seen", "exposure")},
            counts=counts,
            diversity=read(audit_path / "report.json"),
        )
    evaluation = {}
    for stratum, scores in result.get("scores", {}).items():
        outputs = {}
        for name, score in scores.items():
            if name == "paired" or score.get("status") == "infeasible":
                continue
            path = root / "evaluation" / (name + "-" + stratum)
            verify_files(path)
            if (
                read(path / "report.json") != score
                or score["binding"]["candidate"] != candidates[name]["artifact"]
            ):
                raise ValueError("population_report_evaluation")
            outputs[name] = path
        if compare(outputs, plan["cases"]) != scores["paired"]:
            raise ValueError("population_report_comparison")
        evaluation[stratum] = scores
    return dict(
        version="population-campaign-qualification-v1",
        reporter=file_hash(Path(__file__)),
        campaign=str(campaign),
        learning=str(root),
        binding=result["binding"],
        result=file_hash(root / "result.json"),
        prepared=file_hash(root / "prepared.json") if prepared else None,
        acquisition=acquisition,
        protocol=protocol,
        sources=sources,
        raw_snapshot=prepared["raw"] if prepared else None,
        admission={k: prepared[k] for k in ("broad", "narrow")} if prepared else None,
        candidates=candidates,
        evaluation=evaluation,
        evaluation_state=result.get(
            "evaluation",
            "scored"
            if any(v.get("generation") for v in candidates.values())
            else "infeasible_no_candidates",
        ),
        scope=plan["protocol"]["scope"],
        population_bias=plan["protocol"]["population_bias"],
        production_eligible=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("campaign", "root", "output"):
        parser.add_argument("--" + key, required=True, type=Path)
    args = parser.parse_args()
    value = report(args.campaign, args.root)
    atomic_json(args.output, value)
    print(json.dumps(dict(receipt=str(args.output), sha=file_hash(args.output))))


if __name__ == "__main__":
    main()
