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
from memecoin_curriculum.diversity import bucket
from memecoin_curriculum.future import compare_exam
from memecoin_ml.data import read, sha
from memecoin_operator.learning import partitions
from memecoin_registry.contracts import digest, verify
from memecoin_runner.ledger import publish


def economics(campaigns):
    rows = []
    for root in campaigns:
        verify(root)
        status = Counter()
        received, starts, latencies, size = [], [], [], 0
        for path in sorted((root / "requests").iterdir()):
            if not path.is_dir():
                continue
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


def source_operations(source, since, cutoff):
    """Read a hash-checked immutable journal prefix without taking its writer lock."""
    folder = source / "journal"
    previous = digest(read(folder / "binding.json"))
    counts, failures = Counter(), []
    head = previous
    for index, path in enumerate(sorted(folder.glob("event-*.json"))):
        event = read(path)
        checksum = event.pop("sha256")
        if (
            path.name != f"event-{index:06d}.json"
            or event["sequence"] != index
            or event["previous"] != previous
            or digest(event) != checksum
        ):
            raise ValueError("source_journal_prefix_changed")
        previous = checksum
        if event["time_ns"] > cutoff:
            break
        head = checksum
        if event["time_ns"] < since or event["kind"] != "outcome":
            continue
        item = event["payload"]
        counts[item["status"]] += 1
        capture = Path(item["capture"])
        if (capture / "failure.json").exists():
            verify(capture)
            if sha(capture / "manifest.json") != item["capture_sha"]:
                raise ValueError("source_failure_capture_changed")
            failures.append(
                dict(at_ns=event["time_ns"], event_sha=checksum, **read(capture / "failure.json"))
            )
    return dict(
        since_ns=since,
        cutoff_ns=cutoff,
        journal_head=head,
        outcome_counts=dict(counts),
        failures=failures,
        attribution_to_historical_worker_established=False,
        config_sha=sha(source / "config.json"),
        source_stop=(source / "STOP").exists(),
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
    campaigns = [Path(ref["path"]) for ref in admission["selection"]]
    broad_coverage = read(experiment / "broad-snapshot/coverage.json")
    train_counts = broad_coverage["retained"]["train"]["per_pool"]
    old_pools = {s["pool"] for s in parts["train"]}
    new_pools = set(train_counts) - old_pools
    source = Path(read(experiment / "binding.json")["source"])
    universe = read(source / "universe.json")
    births = {p["pool"]: p.get("pool_created_ns") for p in universe["pools"]}
    births.update({r["pool"]: r["birth"]["event_s"] * 10**9 for r in admission["records"]})
    age_support = {}
    for arm in ("control", "broad"):
        arm_bundle = (
            dataset if arm == "control" else load_dataset(experiment / "broad-snapshot/dataset")
        )
        arm_parts, _ = partitions(arm_bundle, arm_bundle["provenance"]["as_of_ns"])
        age_support[arm] = dict(
            Counter(
                bucket(
                    s["event_close_s"] - births[s["pool"]] / 10**9
                    if births.get(s["pool"]) is not None
                    else None,
                    [0, 86400, 7 * 86400, 30 * 86400, 90 * 86400],
                )
                for s in arm_parts["train"]
            )
        )
    result = dict(
        cutoff_ns=cutoff,
        experiment_sha=sha(experiment / "manifest.json"),
        corpus_sha=sha(corpus / "manifest.json"),
        admission=admission,
        support=read(experiment / "support.json"),
        training_distribution=dict(
            retained_new_pool_decisions=sum(train_counts[p] for p in new_pools),
            new_pool_sampling_mass=len(new_pools) / len(train_counts),
            pools_below_16_decisions=sum(n < 16 for n in train_counts.values()),
            sparse_pool_sampling_mass=sum(n < 16 for n in train_counts.values())
            / len(train_counts),
            pool_sampling="B13 equal pools, then equal pool-days; unchanged",
            validation_universe_expanded=False,
        ),
        training_age_support=dict(
            metadata_sha=sha(source / "universe.json"),
            age_buckets=age_support,
            note=(
                "Supplement original support audit with existing source birth metadata; "
                "no feature or fit change."
            ),
        ),
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
        acquisition=economics(campaigns),
        live_source_operations=source_operations(
            Path(read(exam / "config.json")["source"]),
            min(read(p / "config.json")["created_ns"] for p in campaigns),
            cutoff,
        ),
        new_fits_here=0,
        promotion=False,
    )
    if unseen:
        verify(unseen)
        unseen_admission = read(unseen / "admission.json")
        if unseen_admission["role"] != "descriptive_unseen_test":
            raise ValueError("unseen_role")
        result["unseen_acquisition"] = economics(
            [Path(ref["path"]) for ref in unseen_admission["selection"]]
        )
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
                admission=unseen_admission,
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
