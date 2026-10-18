#!/usr/bin/env python3
"""One-shot synthetic GRU lag retrieval capability assay.

This is research plumbing only. It creates qualified synthetic observation streams,
trains one frozen CPU recipe, then registers and opens a distinct synthetic evaluation
cohort. It does not establish market skill or prospective performance.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memecoin_experience import (  # noqa: E402
    evaluation,
    learning,
    observations,
    qualification,
    sensing,
)
from memecoin_experience.predict import Predictor  # noqa: E402
from memecoin_experience.storage import Snapshot, digest  # noqa: E402
from memecoin_population import catalog  # noqa: E402

NS = 1_000_000_000
BITS = tuple(itertools.product((0, 1), repeat=4))
ROLE_COUNTS = {"train": 256, "validation": 64, "internal_test": 64, "research_evaluation": 128}
DECISIONS = {"train": 60, "validation": 160, "internal_test": 260, "research_evaluation": 360}
PLAN = {"context": 5, "max_history_ns": 40 * NS, "horizons_ns": [NS], "price_targets": []}
SPLIT = {"train_start_s": 0, "train_end_s": 100, "validation_end_s": 200, "test_end_s": 300}
RECIPE = {
    "device": "cpu",
    "cpu_threads": 1,
    "width": 16,
    "batch": 32,
    "epochs": 80,
    "seed": 47,
    "lr": 0.01,
    "objective": "partial_count_lower_bound_pinball_v1",
    "sampler": "role_isolated_partition_v1",
}
LIMITS = {
    "max_optimizer_steps": 640,
    "max_wall_seconds": 900,
    "max_scratch_bytes": 2 * 1024**3,
    "reruns": 0,
}
THRESHOLDS = {
    "evaluation_rows_exact": 128,
    "max_loss_ratio_to_each_baseline": 0.25,
    "max_raw_count_pinball": 0.25,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bits_for(index: int) -> tuple[int, ...]:
    return BITS[index % len(BITS)]


def roster(role: str) -> list[dict]:
    if role == "training":
        return [
            person
            for cohort in ("train", "validation", "internal_test")
            for person in roster(cohort)
        ]
    n = ROLE_COUNTS[role]
    return [
        {
            "entity": f"chain/market/synth-{role}-{i:04d}",
            "bits": list(bits_for(i)),
            "decision_s": DECISIONS[role],
        }
        for i in range(n)
    ]


def _process(store: Path, stream: str, field: str) -> tuple[dict, str]:
    schema_pin = qualification.publish_payload_schema(
        store,
        {
            "version": "payload-schema-v1",
            "fields": [{"name": field, "unit": "records", "denomination": "records"}],
        },
    )
    rules = {
        key: qualification.publish_rule(store, key, definition)
        for key, definition in {
            "event_identity_rule": "key-v1",
            "event_time_basis": "block-time-v1",
            "interval_convention": "half-open-start-inclusive-end-exclusive-v1",
            "revision_policy": "append-revisions-v1",
        }.items()
    }
    process = {
        "version": "process-contract-v1",
        "name": f"synthetic-{stream}",
        "stream": stream,
        "semantics": f"synthetic {stream} events in a half-open interval",
        "unit": "events",
        "source_kind": "synthetic_closed_fixture",
        "entity_namespace": "chain/market/",
        "modality": stream,
        "payload_schema_pin": schema_pin,
        "event_identity_rule": rules["event_identity_rule"],
        "event_time_basis": rules["event_time_basis"],
        "timestamp_precision_ns": None,
        "interval_convention": rules["interval_convention"],
        "revision_policy": rules["revision_policy"],
    }
    return process, qualification.process_contract_pin(process, store=store)


def _member(
    store: Path,
    process: dict,
    process_pin: str,
    entity: str,
    start: int,
    end: int,
    available: int,
    events: list[dict],
) -> dict:
    fixture = {
        "version": "synthetic-qualification-fixture-v1",
        "entity": entity,
        "start_ns": start,
        "end_ns": end,
        "available_ns": available,
        "closed_through_ns": end,
        "events": events,
        "unresolved_events": [],
        "rejected_events": [],
    }
    fixture_pin = qualification.publish_synthetic_fixture(
        store, fixture, process=process, process_pin=process_pin
    )
    count = len(events)
    member = {
        "entity": entity,
        "start_ns": start,
        "end_ns": end,
        "available_ns": available,
        "status": "complete",
        "counts": {
            "captured": count,
            "decoded": count,
            "rejected": 0,
            "qualified": count,
            "unresolved": 0,
        },
        "capabilities": ["complete_support", "observation_support", "population_coverage"],
        "evidence": sorted(["a" * 64, fixture_pin]),
        "verification": None,
    }
    member["verification"] = qualification.execute_verifier(
        store, "synthetic_closed_fixture_v1", fixture_pin, process, process_pin, member
    )
    return member


def _receipt(
    store: Path,
    population_pin: str,
    process: dict,
    process_pin: str,
    scope_pin: str,
    members: list[dict],
    role: str,
) -> str:
    source_pin = qualification.synthetic_source_pin(process_pin, scope_pin)
    code_pin = qualification._verifier_code_pin("synthetic_closed_fixture_v1")
    return qualification.publish_receipt(
        store,
        {
            "version": "qualification-receipt-v1",
            "process_contract": process,
            "process_contract_pin": process_pin,
            "inputs": sorted(
                {
                    source_pin,
                    qualification.SYNTHETIC_DECODER_PIN,
                    code_pin,
                    process_pin,
                    population_pin,
                    scope_pin,
                }
            ),
            "source_pin": source_pin,
            "decoder_pin": qualification.SYNTHETIC_DECODER_PIN,
            "qualification_code_pin": code_pin,
            "population_pin": population_pin,
            "scope_pin": scope_pin,
            "role": role,
            "accounting_unit": "candidate_process_records",
            "available_ns": max(member["available_ns"] for member in members),
            "counts": qualification._sum_counts(members),
            "capabilities": ["complete_support", "observation_support", "population_coverage"],
        },
    )


def _destination(process: dict, process_pin: str, field: str) -> dict:
    return {
        "version": "observation-process-binding-v1",
        "process_name": process["name"],
        "stream": process["stream"],
        "process_contract_pin": process_pin,
        "payload_schema_pin": process["payload_schema_pin"],
        "modality": process["modality"],
        "features": [{"name": field, "unit": "records", "modality": process["modality"]}],
    }


def _build_source(base: Path, role: str, *, eval_role: bool = False) -> dict:
    """Build one fully qualified role cohort. Eval callers invoke this only post-register."""
    people = sorted(roster(role), key=lambda person: person["entity"])
    population_store = base / "population"
    role_binding = "research_evaluation" if eval_role else "train"
    pop_rows = []
    for person in people:
        decision = person["decision_s"] * NS
        pop_rows.append(
            {
                "entity": person["entity"],
                "start_ns": decision - 60 * NS,
                "end_ns": decision + 10 * NS,
                "known_ns": decision - 60 * NS,
                "attributes": {},
                "evidence": ["a" * 64],
            }
        )
    pop_pin = catalog.publish(
        population_store,
        "population",
        pop_rows,
        cutoff_ns=(400 if eval_role else 300) * NS,
        definition={
            "role": role_binding,
            "selection": f"frozen synthetic lagged-context {role} roster",
        },
    )
    receipt_store = base / "qualification"
    streams, receipt_pins, contracts = {}, [], []
    for stream, field in (("signal", "bit"), ("target", "event")):
        process, process_pin = _process(receipt_store, stream, field)
        members = []
        for person in people:
            decision = person["decision_s"] * NS
            if stream == "signal":
                history_events = [
                    {
                        "id": f"{field}-{idx}",
                        "event_ns": decision - (4 - idx) * 10 * NS,
                        "values": [float(value)],
                    }
                    for idx, value in enumerate(person["bits"])
                ]
                members.append(
                    _member(
                        receipt_store,
                        process,
                        process_pin,
                        person["entity"],
                        decision - 40 * NS,
                        decision,
                        decision,
                        history_events,
                    )
                )
                members.append(
                    _member(
                        receipt_store,
                        process,
                        process_pin,
                        person["entity"],
                        decision,
                        decision + NS + 1,
                        decision + NS + 1,
                        [],
                    )
                )
            else:
                events = (
                    []
                    if person["bits"][1] == 0
                    else [
                        {
                            "id": f"{field}-{idx}",
                            "event_ns": decision + (idx + 1) * NS // 5,
                            "values": [1.0],
                        }
                        for idx in range(4)
                    ]
                )
                members.append(
                    _member(
                        receipt_store,
                        process,
                        process_pin,
                        person["entity"],
                        decision,
                        decision + NS + 1,
                        decision + NS + 1,
                        events,
                    )
                )
        scope_pin = qualification.publish_scope(
            receipt_store,
            members,
            population_store=population_store,
            population_pin=pop_pin,
            role=role_binding,
            process=process,
            process_pin=process_pin,
        )
        receipt_pin = _receipt(
            receipt_store, pop_pin, process, process_pin, scope_pin, members, role_binding
        )
        receipt_pins.append(receipt_pin)
        contracts.append((process, process_pin, field, members))
        streams[stream] = {
            "process": process["name"],
            "features": [{"name": field, "unit": "records", "modality": stream}],
        }
    obs_rows = [
        {
            "kind": "enrollment",
            "entity": p["entity"],
            "start_ns": p["decision_s"] * NS - 60 * NS,
            "end_ns": p["decision_s"] * NS + 10 * NS,
            "available_ns": p["decision_s"] * NS - 60 * NS,
            "evidence": [pop_pin],
        }
        for p in people
    ]
    for receipt_pin, (process, process_pin, field, members) in zip(
        receipt_pins, contracts, strict=True
    ):
        for member in members:
            fixture_pin = member["verification"]["input_pin"]
            fixture = json.loads(
                (
                    receipt_store / "qualification-synthetic-fixtures" / f"{fixture_pin}.json"
                ).read_text()
            )
            assertion = {
                "receipt_pin": receipt_pin,
                "scope_pin": qualification.load_receipt(receipt_store, receipt_pin)["scope_pin"],
                "population_pin": pop_pin,
                "entity": member["entity"],
                "start_ns": member["start_ns"],
                "end_ns": member["end_ns"],
            }
            obs_rows.append(
                qualification.observation_fact(
                    assertion,
                    receipt_store=receipt_store,
                    population_store=population_store,
                    stream=process["stream"],
                    destination_schema=_destination(process, process_pin, field),
                )
            )
            for event in fixture["events"]:
                obs_rows.append(
                    {
                        "kind": "event",
                        "entity": member["entity"],
                        "stream": process["stream"],
                        "id": event["id"],
                        "event_ns": event["event_ns"],
                        "available_ns": fixture["available_ns"],
                        "evidence": sorted([receipt_pin, fixture_pin]),
                        "values": event["values"],
                    }
                )
    observation_store = base / "observations"
    obs_pin = observations.publish(
        observation_store,
        sorted(obs_rows, key=observations.key),
        schema={
            "version": "market-observations-v1",
            "role": role_binding,
            "availability": "point_in_time",
            "streams": streams,
        },
        cutoff=(400 if eval_role else 300) * NS,
        sources=[{"kind": "synthetic", "pin": "b" * 64}],
    )
    sensing_store = base / "sensing"
    wrapper = sensing.publish_source(
        sensing_store,
        observation_store=observation_store,
        observation_source_pin=obs_pin,
        qualification_store=receipt_store,
        population_store=population_store,
        population_pin=pop_pin,
        role=role_binding,
        receipt_pins=receipt_pins,
    )
    return {
        "base": base,
        "people": people,
        "population_store": population_store,
        "population_pin": pop_pin,
        "receipt_store": receipt_store,
        "observation_store": observation_store,
        "observation_pin": obs_pin,
        "sensing_store": sensing_store,
        "wrapper": wrapper,
        "role": role_binding,
    }


def _materialize(source: dict, role: str, base: Path) -> dict:
    schedule_store = base / "schedule"
    schedule_pin = sensing.publish_schedule(
        schedule_store,
        ({"entity": p["entity"], "decision_ns": p["decision_s"] * NS} for p in source["people"]),
        population_store=source["population_store"],
        population_pin=source["population_pin"],
        role=source["role"],
    )
    result = sensing.materialize_episode(
        source["observation_store"],
        source["wrapper"]["source_pin"],
        sensing_store=source["sensing_store"],
        schedule_store=schedule_store,
        schedule_pin=schedule_pin,
        qualification_store=source["receipt_store"],
        population_store=source["population_store"],
        output=base / "snapshot",
        plan=PLAN,
        role=source["role"],
        chunk=128,
    )
    return {
        "store": str(base / "snapshot"),
        "snapshot": result["snapshot"],
        "schedule_pin": schedule_pin,
        "source_pin": source["wrapper"]["source_pin"],
        "result": result,
    }


def _flatten_x(snapshot: Snapshot) -> dict[str, list[list[float | None]]]:
    import struct

    names = [f["name"] for f in snapshot.schema["features"]]
    width = len(names)
    result = {}
    for part_idx in range(len(snapshot.parts)):
        path, rows = snapshot.partition(part_idx)
        raw = struct.unpack(
            f"<{len(rows) * snapshot.schema['context'] * width}d", (path / "x.f64").read_bytes()
        )
        for i, row in enumerate(rows):
            vals = raw[
                i * snapshot.schema["context"] * width : (i + 1)
                * snapshot.schema["context"]
                * width
            ]
            result[row["pool"]] = [
                list(vals[j * width : (j + 1) * width]) for j in range(snapshot.schema["context"])
            ]
    return result


def _structural_census(snapshot: Snapshot, role: str) -> dict:
    matrix = _flatten_x(snapshot)
    rows, labels = _snapshot_rows(snapshot)
    expected = (
        sum(ROLE_COUNTS[r] for r in ("train", "validation", "internal_test"))
        if role == "training"
        else ROLE_COUNTS[role]
    )
    if len(rows) != expected:
        raise ValueError(f"{role}_row_count:{len(rows)}")
    feature_idx = next(
        i for i, f in enumerate(snapshot.schema["features"]) if f["name"] == "signal/bit"
    )
    target_idx = next(
        i
        for i, t in enumerate(snapshot.schema["targets"])
        if t["name"] == "target/events_1000000000ns"
    )
    lag_counts = [0, 0, 0, 0]
    patterns = {}
    for row in rows:
        frames = matrix[row["pool"]]
        history = tuple(int(frames[i][feature_idx]) for i in range(4))
        if math.isfinite(frames[4][feature_idx]):
            raise ValueError("decision_frame_not_empty")
        patterns[history] = patterns.get(history, 0) + 1
        if (
            row["input_start_ns"] != row["decision_ns"] - 40 * NS
            or row["input_available_ns"] > row["decision_ns"]
        ):
            raise ValueError("input_clock_or_extent")
        if (
            row["target_status"][target_idx] != "observed"
            or row["target_maturity_ns"][target_idx] != row["decision_ns"] + NS
        ):
            raise ValueError("target_support_or_maturity")
        if labels[row["id"]][target_idx] != 4 * history[1]:
            raise ValueError("lag_target_relation")
        for i in range(4):
            lag_counts[i] += 1
    if len(patterns) != 16 or set(patterns.values()) != {expected // 16}:
        raise ValueError(f"unbalanced_history_patterns:{patterns}")
    return {
        "rows": len(rows),
        "pattern_counts": {"".join(map(str, k)): v for k, v in sorted(patterns.items())},
        "feature_names": [f["name"] for f in snapshot.schema["features"]],
        "context": snapshot.schema["context"],
        "exact_count_labels": sum(r["target_status"][target_idx] == "observed" for r in rows),
        "max_input_available_after_decision": max(
            r["input_available_ns"] - r["decision_ns"] for r in rows
        ),
        "min_input_start_offset_ns": min(r["input_start_ns"] - r["decision_ns"] for r in rows),
        "lag_index": 1,
    }


def _snapshot_rows(snapshot: Snapshot) -> tuple[list[dict], dict[str, list[float]]]:
    import struct

    rows, labels = [], {}
    width = len(snapshot.schema["targets"])
    for part_idx in range(len(snapshot.parts)):
        path, part_rows = snapshot.partition(part_idx)
        raw = struct.unpack(f"<{len(part_rows) * width}d", (path / "y.f64").read_bytes())
        for i, row in enumerate(part_rows):
            rows.append(row)
            labels[row["id"]] = list(raw[i * width : (i + 1) * width])
    return rows, labels


def _prereg_receipt(prepared: dict, *, script_sha: str, run_id: str) -> dict:
    return {
        "version": "brain-lagged-context-retrieval-prereg-v1",
        "experiment_id": run_id,
        "qualification": "synthetic_engineering_only",
        "generator": {
            "script_sha256": script_sha,
            "data_function": "y=4*b(t-3); exact zero when b(t-3)=0",
            "roster_rule": (
                "lexically named disjoint entities; each role cycles all 16 four-bit "
                "histories equally"
            ),
            "history_order": ["t-4", "t-3", "t-2", "t-1"],
            "decision_frame": "empty reserved frame t",
            "future_process": (
                "four uniquely identified events in (t,t+1s] iff b(t-3)=1; complete "
                "support otherwise exact zero"
            ),
        },
        "code": {
            name: sha(ROOT / "src/memecoin_experience" / name)
            for name in (
                "sensing.py",
                "observations.py",
                "qualification.py",
                "learning.py",
                "evaluation.py",
                "storage.py",
                "predict.py",
            )
        },
        "roles": {
            role: {
                "entities": ROLE_COUNTS[role],
                "decision_s": DECISIONS[role],
                "patterns": 16,
                "per_pattern": ROLE_COUNTS[role] // 16,
                "entity_roster_sha256": hashlib.sha256(
                    "\n".join(row["entity"] for row in roster(role)).encode()
                ).hexdigest(),
            }
            for role in ROLE_COUNTS
        },
        "isolation": {
            "training_snapshot_roles": ["train", "validation", "internal_test"],
            "evaluation_source_role": "research_evaluation",
            "evaluation_source_opened_after_candidate_registration": True,
            "overlapping_entities": 0,
        },
        "schema_plan": PLAN,
        "split": SPLIT,
        "recipe": RECIPE,
        "baselines": [
            "train_only_unconditional_empirical_q10_q50_q90",
            "train_only_last_frame_bit_conditional_empirical_q10_q50_q90",
        ],
        "quantile_tie_rule": (
            "linear interpolation at index q*(n-1), then exact floating values; pinball "
            "summed over q10/q50/q90 and averaged per row"
        ),
        "thresholds": THRESHOLDS,
        "resource_limits": LIMITS,
        "failure_policy": (
            "one fit and one registered evaluation only; preserve structural, denominator, "
            "budget, or loss failures; no repair, tuning, or rerun"
        ),
        "claim_limit": (
            "Pass establishes historical-frame retrieval for this frozen synthetic episode "
            "only; no market, calibration, transfer, general temporal-learning, or "
            "architecture-superiority claim."
        ),
        "prepared": prepared,
    }


def prepare(root: Path, run_id: str) -> Path:
    work = root / "work" / "lagged-context" / run_id
    if work.exists():
        raise FileExistsError(f"run directory exists: {work}")
    work.mkdir(parents=True)
    source = _build_source(work / "training-source", "training")
    prepared = _materialize(source, "train", work / "training-build")
    census = _structural_census(Snapshot(prepared["store"], prepared["snapshot"]), "training")
    if (
        census["rows"]
        != ROLE_COUNTS["train"] + ROLE_COUNTS["validation"] + ROLE_COUNTS["internal_test"]
    ):
        raise ValueError("training_roster_census")
    receipt = _prereg_receipt(
        {
            "training_snapshot": prepared["snapshot"],
            "sensing_source": prepared["source_pin"],
            "schedule": prepared["schedule_pin"],
            "census": census,
        },
        script_sha=sha(
            Path(__file__),
        ),
        run_id=run_id,
    )
    receipt_path = (
        ROOT / "docs" / "evidence" / f"brain-lagged-context-retrieval-prereg-{run_id}.json"
    )
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    (work / "prepared.json").write_text(
        json.dumps(
            {
                "receipt_path": str(receipt_path),
                "receipt_sha256": sha(receipt_path),
                "training": prepared,
                "census": census,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    return receipt_path


def _quantile(values: list[float], q: float) -> float:
    values = sorted(values)
    position = q * (len(values) - 1)
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def _pinball(actual: list[float], predictions: list[list[float]]) -> float:
    quantiles = (0.1, 0.5, 0.9)
    return sum(
        max(q * (y - row[j]), (q - 1) * (y - row[j]))
        for y, row in zip(actual, predictions, strict=True)
        for j, q in enumerate(quantiles)
    ) / len(actual)


def _tree_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def execute(root: Path, run_id: str) -> Path:
    work = root / "work" / "lagged-context" / run_id
    prepared_file = work / "prepared.json"
    prepared = json.loads(prepared_file.read_text())
    prereg_path = Path(prepared["receipt_path"])
    if sha(prereg_path) != prepared["receipt_sha256"]:
        raise ValueError("prereg_receipt_changed")
    prereg = json.loads(prereg_path.read_text())
    if prereg["generator"]["script_sha256"] != sha(Path(__file__)):
        raise ValueError("generator_code_changed_after_preregistration")
    code_now = {name: sha(ROOT / "src/memecoin_experience" / name) for name in prereg["code"]}
    if code_now != prereg["code"]:
        raise ValueError("experience_code_changed_after_preregistration")
    snapshot = Snapshot(prepared["training"]["store"], prepared["training"]["snapshot"])
    (work / "training-job").mkdir(parents=True, exist_ok=True)
    split_audit = learning.prepare(
        snapshot, SPLIT, work / "training-job", RECIPE["batch"], objective=RECIPE["objective"]
    )
    target_name = "target/events_1000000000ns"
    if (
        split_audit["denominators"].get(f"train/{target_name}/usable") != ROLE_COUNTS["train"]
        or split_audit["denominators"].get(f"validation/{target_name}/usable")
        != ROLE_COUNTS["validation"]
        or split_audit["denominators"].get(f"test/{target_name}/usable")
        != ROLE_COUNTS["internal_test"]
    ):
        raise ValueError("training_split_denominator")
    started = time.monotonic()
    training = learning.train(
        prepared["training"]["store"],
        prepared["training"]["snapshot"],
        work / "training-job",
        plan=SPLIT,
        config=RECIPE,
    )
    elapsed = time.monotonic() - started
    report = training["report"]
    if report.get("steps") != LIMITS["max_optimizer_steps"] or elapsed > LIMITS["max_wall_seconds"]:
        raise ValueError(f"training_budget:{report.get('steps')}:{elapsed:.3f}")
    scratch_bytes_after_training = _tree_bytes(work)
    if scratch_bytes_after_training > LIMITS["max_scratch_bytes"]:
        raise ValueError(f"scratch_budget:{scratch_bytes_after_training}")
    artifact = work / "training-job" / "artifact"
    artifact_pin = training["artifact"]
    predictor = Predictor(artifact, artifact_pin)

    # Create evaluation population/labels only after sealing this candidate-bound protocol.
    eval_people = roster("research_evaluation")
    protocol = evaluation.register(
        ROOT / "work" / "lagged-context" / "protocols",
        artifact,
        artifact_pin,
        start_s=320,
        end_s=400,
        pools=[p["entity"] for p in eval_people],
        scope="retrospective",
    )
    eval_source = _build_source(work / "evaluation-source", "research_evaluation", eval_role=True)
    eval_snapshot = _materialize(eval_source, "research_evaluation", work / "evaluation-build")
    scored = evaluation.score(
        ROOT / "work" / "lagged-context" / "protocols",
        protocol["protocol"],
        eval_snapshot["store"],
        eval_snapshot["snapshot"],
        artifact,
        work / "evaluation-score",
    )
    census = _structural_census(
        Snapshot(eval_snapshot["store"], eval_snapshot["snapshot"]), "research_evaluation"
    )
    denominators = scored["denominators"]
    # Read exact truth and predictions from the registered, sealed prediction transcript.
    truth, last_bits = [], []
    # Derive baselines from train only and labels from the sealed evaluation snapshot.
    feature_idx = next(
        i for i, f in enumerate(snapshot.schema["features"]) if f["name"] == "signal/bit"
    )
    target_idx = next(
        i
        for i, t in enumerate(snapshot.schema["targets"])
        if t["name"] == "target/events_1000000000ns"
    )
    eval_x = _flatten_x(Snapshot(eval_snapshot["store"], eval_snapshot["snapshot"]))
    eval_rows, eval_labels = _snapshot_rows(
        Snapshot(eval_snapshot["store"], eval_snapshot["snapshot"])
    )
    for row in eval_rows:
        truth.append(float(eval_labels[row["id"]][target_idx]))
        last_bits.append(int(eval_x[row["pool"]][3][feature_idx]))
    train_rows, train_labels = _snapshot_rows(snapshot)
    train_rows = [
        row
        for row in train_rows
        if SPLIT["train_start_s"] <= row["decision_ns"] // NS < SPLIT["train_end_s"]
        and row["target_status"][target_idx] == "observed"
    ]
    unconditional = [
        _quantile([float(train_labels[r["id"]][target_idx]) for r in train_rows], q)
        for q in (0.1, 0.5, 0.9)
    ]
    conditional = {}
    train_x = _flatten_x(snapshot)
    for bit in (0, 1):
        ys = [
            float(train_labels[r["id"]][target_idx])
            for r in train_rows
            if int(train_x[r["pool"]][3][feature_idx]) == bit
        ]
        if not ys:
            raise ValueError("conditional_baseline_empty_group")
        conditional[bit] = [_quantile(ys, q) for q in (0.1, 0.5, 0.9)]
    # Use the same Predictor implementation and frozen candidate on the evaluation rows.
    model_predictions = []
    for row in eval_rows:
        frames = [
            [None if not math.isfinite(v) else v for v in frame] for frame in eval_x[row["pool"]]
        ]
        outputs = predictor.predict(frames)
        model_predictions.append([float(v) for v in outputs[0]])
    base_unconditional = [unconditional for _ in truth]
    base_conditional = [conditional[b] for b in last_bits]
    model_loss = _pinball(truth, model_predictions)
    unconditional_loss = _pinball(truth, base_unconditional)
    conditional_loss = _pinball(truth, base_conditional)
    report_fields = {
        "protocol": protocol["protocol"],
        "artifact": artifact_pin,
        "evaluation_snapshot": eval_snapshot["snapshot"],
        "scores": scored,
    }
    result = {
        "version": "brain-lagged-context-retrieval-result-v1",
        "experiment_id": run_id,
        "prereg_sha256": sha(prereg_path),
        "training_snapshot": prepared["training"]["snapshot"],
        "evaluation_source": eval_source["wrapper"]["source_pin"],
        "evaluation_snapshot": eval_snapshot["snapshot"],
        "artifact": artifact_pin,
        "protocol": protocol["protocol"],
        "report_sha256": digest(report_fields),
        "census": census,
        "training": {
            "steps": report["steps"],
            "epochs": report["epochs"],
            "elapsed_seconds": elapsed,
            "scratch_bytes_after_training": scratch_bytes_after_training,
            "train_denominator": split_audit["denominators"].get(f"train/{target_name}/usable"),
            "validation_denominator": split_audit["denominators"].get(
                f"validation/{target_name}/usable"
            ),
            "internal_test_denominator": split_audit["denominators"].get(
                f"test/{target_name}/usable"
            ),
        },
        "evaluation": {
            "exact_rows": len(truth),
            "scored_population": scored["population"],
            "denominators": denominators,
            "model_raw_count_macro_pinball": model_loss,
            "unconditional_baseline": {"quantiles": unconditional, "loss": unconditional_loss},
            "latest_frame_conditional_baseline": {
                "quantiles_by_bit": {str(k): v for k, v in conditional.items()},
                "loss": conditional_loss,
            },
            "model_ratio_to_unconditional": model_loss / unconditional_loss
            if unconditional_loss
            else None,
            "model_ratio_to_latest_frame_conditional": model_loss / conditional_loss
            if conditional_loss
            else None,
        },
        "thresholds": THRESHOLDS,
        "status": "pass"
        if len(truth) == THRESHOLDS["evaluation_rows_exact"]
        and model_loss <= THRESHOLDS["max_raw_count_pinball"]
        and model_loss <= THRESHOLDS["max_loss_ratio_to_each_baseline"] * unconditional_loss
        and model_loss <= THRESHOLDS["max_loss_ratio_to_each_baseline"] * conditional_loss
        else "fail",
        "claim_limit": prereg["claim_limit"],
    }
    result_path = (
        ROOT / "docs" / "evidence" / f"brain-lagged-context-retrieval-result-{run_id}.json"
    )
    result_path.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return result_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "execute"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    path = (
        prepare(args.root, args.run_id)
        if args.phase == "prepare"
        else execute(args.root, args.run_id)
    )
    print(path)


if __name__ == "__main__":
    main()
