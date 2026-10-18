"""Frozen sparse conversion/load/CPU plumbing qualification; no network or evaluation cohort IO."""

import argparse
import json
import resource
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from memecoin_experience import observation_adapters, observations, sparse
from memecoin_experience.schema import membership
from memecoin_experience.storage import Snapshot, atomic_json, code_identity, digest, file_hash
from memecoin_population import catalog, diversity

NS = 10**9


def checkpoint(path, report):
    """Keep first-run measurements on resume; refuse any change to semantic results."""
    if path.exists():
        previous = json.loads(path.read_text())

        def stable(value):
            return {
                k: v for k, v in value.items() if k not in {"seconds", "process_peak_rss_bytes"}
            }

        if stable(previous) != stable(report):
            raise ValueError("sparse_qualification_checkpoint_mismatch")
        return previous
    atomic_json(path, report)
    return report


def synthetic(entities, events):
    for i in range(entities):
        start = (i % 3) * 100000
        common = dict(entity=f"synthetic/market/{i:09d}", evidence=["a" * 64])
        rows = [
            dict(
                common,
                kind="enrollment",
                start_ns=start * NS,
                end_ns=(start + 10000) * NS,
                available_ns=start * NS,
            ),
            dict(
                common,
                kind="lifecycle",
                id="birth",
                transition="birth",
                event_ns=start * NS,
                available_ns=start * NS,
            ),
            dict(
                common,
                kind="support",
                stream="ticks",
                start_ns=start * NS,
                end_ns=(start + 10000) * NS,
                available_ns=(start + 10000) * NS,
                status="unknown" if i % 5 == 0 else "complete",
            ),
        ]
        # Explicit zero-event entities, short histories, irregular spacing, non-price values.
        for j in range(0 if i % 17 == 0 else (3 if i % 13 == 0 else events)):
            at = (start + 10 + 3 * j + j // 5) * NS
            rows.append(
                dict(
                    common,
                    kind="event",
                    stream="ticks",
                    id=str(j),
                    event_ns=at,
                    available_ns=at,
                    values=[float(j % 11)],
                )
            )
        yield from sorted(rows, key=observations.key)


def census(store, pin, split):
    snapshot = Snapshot(store, pin)
    counts, reasons = Counter(), Counter()
    with tempfile.TemporaryDirectory(prefix="sparse-census-") as temp:
        db = sqlite3.connect(Path(temp) / "entities.sqlite")
        try:
            db.execute(
                "create table entities(role text,entity text,n integer,primary key(role,entity))"
            )
            for i in range(len(snapshot.parts)):
                _, rows = snapshot.partition(i)
                for row in rows:
                    role, mask = membership(row, split, snapshot.schema)
                    counts[role] += 1
                    counts[role + "_usable"] += int(any(mask))
                    reasons.update(d["reason"] for d in row["target_details"])
                    roles = ["all", role + "_origins"]
                    if any(mask):
                        roles += [role + "_usable"]
                    for name in roles:
                        db.execute(
                            "insert into entities values(?,?,1) on conflict(role,entity) "
                            "do update set n=n+1",
                            (name, row["pool"]),
                        )
                db.commit()
            by_entity = {
                role: dict(entities=n, max_rows=largest, rows=total)
                for role, n, largest, total in db.execute(
                    "select role,count(*),max(n),sum(n) from entities group by role"
                )
            }
        finally:
            db.close()
    return dict(population=dict(counts), by_entity=by_entity, target_reasons=dict(reasons))


def run(job, root):
    root.mkdir(parents=True, exist_ok=True)
    binding = dict(job=job, code=code_identity(), harness=file_hash(Path(__file__)))
    atomic_json(root / "declaration.json", binding)
    started = time.monotonic()
    store = root / "observations"
    if job["mode"] == "synthetic":
        pin = observations.publish(
            store,
            synthetic(job["entities"], job["events"]),
            schema=dict(
                version="market-observations-v1",
                role="train",
                availability="historical_replay",
                streams=dict(
                    ticks=dict(
                        process="synthetic qualified flow observations",
                        features=[dict(name="flow", unit="units", modality="flow")],
                    )
                ),
            ),
            cutoff=400000 * NS,
            sources=[dict(synthetic=job)],
            chunk=256,
        )
    elif job["mode"] == "continuous":
        acquisition = Path(job["acquisition"])
        if file_hash(acquisition) != job["acquisition_file_sha"]:
            raise ValueError("sparse_qualification_acquisition_pin")
        declaration = json.loads(acquisition.read_text())
        if digest(declaration) != job["acquisition_pin"]:
            raise ValueError("sparse_qualification_acquisition_identity")
        # Only enrollment control metadata is inspected; no evaluation payload paths are opened.
        lifecycle = sorted(
            (
                dict(
                    kind="lifecycle",
                    entity="solana/pool/" + case["pool"],
                    id="birth",
                    transition="birth",
                    event_ns=case["event_s"] * NS,
                    available_ns=declaration["created_ns"],
                    evidence=[job["acquisition_pin"]],
                )
                for case in declaration["cases"]
                if case["role"] == "train"
            ),
            key=observations.key,
        )
        pin = observation_adapters.from_continuous(job["reference"], store, lifecycle=lifecycle)
    else:
        raise ValueError("sparse_qualification_mode")
    published = time.monotonic()
    source = observations.Source(store, pin, role="train")
    built = sparse.materialize(store, pin, root / "experience", plan=job["projection"], chunk=256)
    materialized = time.monotonic()
    report = dict(
        declaration=digest(binding),
        source=pin,
        snapshot=built["snapshot"],
        source_partitions=len(source.parts),
        source_paged="partition_pages" in source.manifest,
        experience_partitions=built["partition_count"]
        if "partition_count" in built
        else len(built["partitions"]),
        experience_paged="partition_pages" in built,
        source_counts=built["source_counts"],
        target_statuses=built["target_statuses"],
        census=census(root / "experience", built["snapshot"], job["split"]),
        seconds=dict(publish=published - started, materialize=materialized - published),
        production_eligible=False,
        qualification="engineering_only_not_learning_gain",
    )
    report = checkpoint(root / "conversion.json", report)
    if job.get("train"):
        from memecoin_experience import generations

        def members():
            for row in source.rows():
                if row["kind"] == "enrollment":
                    yield dict(
                        entity=row["entity"],
                        start_ns=row["start_ns"],
                        end_ns=row["end_ns"],
                        known_ns=row["available_ns"],
                        attributes=dict(status="enrolled"),
                        evidence=row["evidence"],
                    )

        population = catalog.publish(
            root / "population",
            "population",
            members(),
            cutoff_ns=source.manifest["binding"]["cutoff_ns"],
            definition=dict(role="train", observation_source=pin),
        )
        policy = dict(
            entity_prefix="",
            knowledge="at_cutoff",
            axes=[["@entity"]],
            time_bucket_ns=86400 * NS,
            selection=dict(salt="sparse-engineering-v1", numerator=1, denominator=1),
            enforcement="require",
            min_train_entities=1,
            max_entity_share=1,
            max_unknown_fraction=0,
            objective=diversity.OBJECTIVE,
        )
        audit = diversity.audit(
            root / "experience",
            built["snapshot"],
            root / "population",
            population,
            root / "audit",
            split=job["split"],
            config=policy,
        )
        trained = generations.run(
            root / "models",
            name="engineering",
            owner="sparse-qualification",
            store=root / "experience",
            snapshot=built["snapshot"],
            split=job["split"],
            config=job["train"],
            diversity=dict(path=str(root / "audit"), pin=file_hash(root / "audit/manifest.json")),
        )
        _, artifact = generations.get(root / "models", trained["generation"])
        fitted = json.loads((artifact / "report.json").read_text())
        report["training"] = dict(
            generation={k: trained[k] for k in ("status", "generation", "artifact")},
            rows_seen=fitted["rows_seen"],
            steps=fitted["steps"],
            parameters=fitted["parameters"],
            exposure=fitted["exposure"],
            train_distribution=audit["train_distribution"],
        )
    report["seconds"]["total"] = time.monotonic() - started
    report["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if sys.platform == "darwin" else 1024
    )
    return checkpoint(root / "result.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    report = run(json.loads(args.job.read_text()), args.root)
    print(
        json.dumps(
            {k: report[k] for k in ("source", "snapshot", "seconds", "census")}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
