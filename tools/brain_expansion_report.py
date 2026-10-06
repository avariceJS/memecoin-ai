"""Reproducible data, descriptive and prospective reports; no training or provider requests."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import torch
from brain_refit_compare import compare

from memecoin_brain.data import load_dataset
from memecoin_curriculum.admission import load_corpus
from memecoin_curriculum.archive import assemble, shards
from memecoin_curriculum.future import compare_exam
from memecoin_ml.data import read, sha
from memecoin_operator.learning import partitions
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish


def economics(campaigns):
    rows = []
    for root in campaigns:
        verify(root)
        status = Counter()
        received, starts, latencies, size = [], [], [], 0
        for path in sorted((root / "requests").iterdir()):
            q = read(path / "request.json")
            starts.append(q["started_ns"])
            if (path / "receipt.json").exists():
                r = read(path / "receipt.json")
                status["success"] += 1
                size += (path / "response.bin").stat().st_size
            else:
                r = read(path / "failure.json")
                status[str(r.get("http_status") or r.get("error_type"))] += 1
            received.append(r["received_ns"])
            latencies.append((r["received_ns"] - q["started_ns"]) / 10**9)
        rows.append(
            dict(
                campaign=str(root),
                manifest_sha=sha(root / "manifest.json"),
                status=dict(status),
                requests=len(starts),
                response_bytes=size,
                wall_span_s=(max(received) - min(starts)) / 10**9 if starts else 0,
                network_seconds=sum(latencies),
                max_latency_s=max(latencies, default=0),
                stop=read(root / "STOP") if (root / "STOP").exists() else None,
            )
        )
    return dict(
        campaigns=rows,
        requests=sum(r["requests"] for r in rows),
        response_bytes=sum(r["response_bytes"] for r in rows),
        paid_budget_usd=0,
        invoice_measured=False,
    )


def report(experiment, exam, corpus, output, unseen=None):
    torch.set_num_threads(1)
    verify(experiment)
    verify(corpus)
    cutoff = time.time_ns()
    refs = read(exam / "config.json")["frozen_exam_models"]
    models = {r["label"]: Path(r["path"]) for r in refs}
    dataset = load_dataset(experiment / "control-snapshot/dataset")
    parts, bounds = partitions(dataset, dataset["provenance"]["as_of_ns"])
    admission = read(corpus / "admission.json")
    result = dict(
        cutoff_ns=cutoff,
        experiment_sha=sha(experiment / "manifest.json"),
        corpus_sha=sha(corpus / "manifest.json"),
        admission=admission,
        support=read(experiment / "support.json"),
        models={
            label: dict(
                manifest_sha=sha(p / "manifest.json"),
                card=read(p / "model-card.json"),
                fits={
                    f.name: read(f / "fit.json") for f in p.glob("*-*") if (f / "fit.json").exists()
                },
            )
            for label, p in models.items()
        },
        common_descriptive_test=compare(models, dataset, parts["test"]),
        bounds=bounds,
        future=compare_exam(exam, cutoff),
        paper_dashboard=read(exam / "dashboard.json"),
        acquisition=economics([Path(ref["path"]) for ref in admission["selection"]]),
        new_fits_here=0,
        promotion=False,
    )
    if unseen:
        verify(unseen)
        if read(unseen / "admission.json")["role"] != "descriptive_unseen_test":
            raise ValueError("unseen_role")
        rows = load_corpus(
            dict(path=str(unseen), sha=sha(unseen / "manifest.json")), cutoff, training=False
        )
        if rows:
            bundle = assemble(shards(rows, dict(as_of_ns=cutoff)), max_decisions=100000)
            samples = [
                s
                for s in bundle["samples"]
                if all(t["log_return"] is not None for t in s["targets"])
            ]
            train_pools = set(
                read(experiment / "broad-snapshot/coverage.json")["retained"]["train"]["per_pool"]
            )
            if {s["pool"] for s in samples} & train_pools:
                raise ValueError("unseen_market_in_train")
            result["unseen_descriptive"] = dict(
                metrics=compare(models, bundle, samples),
                corpus_sha=sha(unseen / "manifest.json"),
                future=False,
                used_for_selection=False,
                historical_online_availability_known=False,
                admission=read(unseen / "admission.json"),
            )
        else:
            result["unseen_descriptive"] = dict(decisions=0, future=False, no_support=True)
    publish(output, result)
    print(
        json.dumps(
            dict(
                path=str(output),
                sha=sha(output),
                train_pools=result["support"]["broad"]["pools"],
                future_common_variants=result["future"]["common_variants"],
            )
        )
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("experiment", type=Path)
    p.add_argument("exam", type=Path)
    p.add_argument("corpus", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--unseen", type=Path)
    a = p.parse_args()
    report(
        a.experiment.resolve(),
        a.exam.resolve(),
        a.corpus.resolve(),
        a.output.resolve(),
        a.unseen.resolve() if a.unseen else None,
    )


if __name__ == "__main__":
    main()
