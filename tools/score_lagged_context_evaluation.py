#!/usr/bin/env python3
"""Freeze and score the evaluation-only continuation for a completed lag assay fit."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from memecoin_experience import evaluation  # noqa: E402
from memecoin_experience.predict import Predictor  # noqa: E402
from memecoin_experience.storage import Snapshot, digest, file_hash  # noqa: E402
from tools import lagged_context_retrieval as assay  # noqa: E402

RUN_ID = "2026-10-15T0610Z"
ORIGINAL_PREREG = ROOT / "docs/evidence/brain-lagged-context-retrieval-prereg-2026-10-15T0610Z.json"
WORK = ROOT / "work/lagged-context" / RUN_ID
ARTIFACT = WORK / "training-job/artifact"
EVALUATION_START_S = 320
EVALUATION_END_S = 400


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_hashes() -> dict[str, str]:
    return {
        name: sha(ROOT / "src/memecoin_experience" / name)
        for name in (
            "evaluation.py",
            "learning.py",
            "observations.py",
            "qualification.py",
            "sensing.py",
            "storage.py",
            "predict.py",
        )
    }


def prepare() -> Path:
    if not ORIGINAL_PREREG.is_file():
        raise ValueError("original_preregistration_missing")
    original = json.loads(ORIGINAL_PREREG.read_text())
    if original["experiment_id"] != RUN_ID:
        raise ValueError("original_preregistration_identity")
    if original["generator"]["script_sha256"] != sha(ROOT / "tools/lagged_context_retrieval.py"):
        raise ValueError("original_generator_changed")
    if code_hashes() != original["code"]:
        raise ValueError("experience_code_changed_since_fit")
    prepared_path = WORK / "prepared.json"
    prepared = json.loads(prepared_path.read_text())
    if sha(ORIGINAL_PREREG) != prepared["receipt_sha256"]:
        raise ValueError("original_preregistration_pin")
    frozen_prepared = original["prepared"]
    if (
        prepared["training"]["snapshot"] != frozen_prepared["training_snapshot"]
        or prepared["training"]["source_pin"] != frozen_prepared["sensing_source"]
        or prepared["training"]["schedule_pin"] != frozen_prepared["schedule"]
        or prepared["census"] != frozen_prepared["census"]
    ):
        raise ValueError("frozen_training_inputs_changed")
    if any(
        (WORK / name).exists()
        for name in ("evaluation-source", "evaluation-build", "evaluation-score")
    ):
        raise ValueError("evaluation_stage_already_started")
    protocol_root = ROOT / "work/lagged-context/protocols"
    if protocol_root.exists() and any(protocol_root.iterdir()):
        raise ValueError("evaluation_protocol_already_registered")
    train_snapshot = Snapshot(prepared["training"]["store"], prepared["training"]["snapshot"])
    artifact_pin = file_hash(ARTIFACT / "manifest.json")
    model = Predictor(ARTIFACT, artifact_pin)
    report = model.report
    expected_config = {
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
    if (
        report["binding"]["snapshot"] != prepared["training"]["snapshot"]
        or report["binding"]["plan"] != assay.SPLIT
        or any(report["binding"]["config"].get(k) != v for k, v in expected_config.items())
        or report["steps"] != 640
        or report["epochs"] != 80
        or report["production_eligible"] is not False
        or model.spec["schema"] != train_snapshot.schema
    ):
        raise ValueError("completed_candidate_binding")
    target_name = "target/events_1000000000ns"
    expected_denominators = {
        "train": assay.ROLE_COUNTS["train"],
        "validation": assay.ROLE_COUNTS["validation"],
        "test": assay.ROLE_COUNTS["internal_test"],
    }
    if any(
        report["statistics"]["denominators"].get(f"{role}/{target_name}/usable") != expected
        for role, expected in expected_denominators.items()
    ):
        raise ValueError("completed_candidate_denominators")
    roles = {role: assay.roster(role) for role in assay.ROLE_COUNTS}
    evaluation_entities = [row["entity"] for row in roles["research_evaluation"]]
    if set(evaluation_entities) & {
        row["entity"] for role in ("train", "validation", "internal_test") for row in roles[role]
    }:
        raise ValueError("evaluation_roster_overlap")
    original_roles = original["roles"]
    for role, rows in roles.items():
        roster_pin = hashlib.sha256("\n".join(row["entity"] for row in rows).encode()).hexdigest()
        if roster_pin != original_roles[role]["entity_roster_sha256"]:
            raise ValueError(f"frozen_roster_changed:{role}")
    scorer_path = Path(__file__)
    continuation = {
        "version": "brain-lagged-context-evaluation-continuation-prereg-v1",
        "experiment_id": RUN_ID,
        "stage": "evaluation_only_no_fit",
        "original_preregistration": ORIGINAL_PREREG.relative_to(ROOT).as_posix(),
        "original_preregistration_sha256": sha(ORIGINAL_PREREG),
        "scorer_sha256": sha(scorer_path),
        "experience_code": code_hashes(),
        "candidate_artifact_manifest_sha256": artifact_pin,
        "candidate_report_sha256": digest(report),
        "candidate_training_snapshot": prepared["training"]["snapshot"],
        "candidate_steps": report["steps"],
        "candidate_epochs": report["epochs"],
        "candidate_exact_denominators": expected_denominators,
        "candidate_production_eligible": report["production_eligible"],
        "evaluation": {
            "role": "research_evaluation",
            "cohort_size": assay.ROLE_COUNTS["research_evaluation"],
            "entity_roster_sha256": original_roles["research_evaluation"]["entity_roster_sha256"],
            "start_s": EVALUATION_START_S,
            "end_s": EVALUATION_END_S,
            "scope": "retrospective",
            "source_creation": "after protocol registration only",
            "metrics": "target/events_1000000000ns raw-count pinball over q10/q50/q90",
        },
        "inherited_baselines": original["baselines"],
        "inherited_quantile_tie_rule": original["quantile_tie_rule"],
        "inherited_thresholds": original["thresholds"],
        "inherited_failure_policy": original["failure_policy"],
        "fit_repeated": False,
        "evaluation_attempts": 1,
        "resource_gate_note": (
            "The original controller failed after artifact publication. Its tool-level process "
            "wall time is recorded separately; peak scratch use was not measured and remains "
            "an unverified frozen resource gate."
        ),
        "claim_limit": original["claim_limit"],
    }
    path = (
        ROOT
        / "docs/evidence/brain-lagged-context-evaluation-continuation-prereg-2026-10-15T0610Z.json"
    )
    if path.exists():
        raise FileExistsError("continuation_preregistration_already_exists")
    path.write_text(json.dumps(continuation, sort_keys=True, indent=2) + "\n")
    state = {
        "continuation_path": str(path),
        "continuation_sha256": sha(path),
        "artifact": str(ARTIFACT),
        "artifact_pin": artifact_pin,
    }
    (WORK / "evaluation-continuation.json").write_text(
        json.dumps(state, sort_keys=True, indent=2) + "\n"
    )
    return path


def score() -> Path:
    state_path = WORK / "evaluation-continuation.json"
    state = json.loads(state_path.read_text())
    prereg_path = Path(state["continuation_path"])
    if sha(prereg_path) != state["continuation_sha256"]:
        raise ValueError("continuation_preregistration_pin")
    prereg = json.loads(prereg_path.read_text())
    if prereg["scorer_sha256"] != sha(Path(__file__)):
        raise ValueError("continuation_scorer_changed")
    if code_hashes() != prereg["experience_code"]:
        raise ValueError("experience_code_changed_after_continuation_prereg")
    forbidden = [
        WORK / name for name in ("evaluation-source", "evaluation-build", "evaluation-score")
    ]
    if any(path.exists() for path in forbidden):
        raise ValueError("evaluation_stage_already_started")
    artifact = Path(state["artifact"])
    artifact_pin = file_hash(artifact / "manifest.json")
    if artifact_pin != prereg["candidate_artifact_manifest_sha256"]:
        raise ValueError("candidate_artifact_changed")
    model = Predictor(artifact, artifact_pin)
    report = model.report
    if digest(report) != prereg["candidate_report_sha256"] or report["steps"] != 640:
        raise ValueError("candidate_report_changed")

    eval_people = assay.roster("research_evaluation")
    protocol_root = ROOT / "work/lagged-context/protocols"
    protocol = evaluation.register(
        protocol_root,
        artifact,
        artifact_pin,
        start_s=EVALUATION_START_S,
        end_s=EVALUATION_END_S,
        pools=[row["entity"] for row in eval_people],
        scope="retrospective",
    )
    evaluation_started = time.monotonic()
    eval_source = assay._build_source(
        WORK / "evaluation-source", "research_evaluation", eval_role=True
    )
    eval_snapshot = assay._materialize(
        eval_source, "research_evaluation", WORK / "evaluation-build"
    )
    scored = evaluation.score(
        protocol_root,
        protocol["protocol"],
        eval_snapshot["store"],
        eval_snapshot["snapshot"],
        artifact,
        WORK / "evaluation-score",
    )
    census = assay._structural_census(
        Snapshot(eval_snapshot["store"], eval_snapshot["snapshot"]), "research_evaluation"
    )
    eval_snapshot_obj = Snapshot(eval_snapshot["store"], eval_snapshot["snapshot"])
    eval_x = assay._flatten_x(eval_snapshot_obj)
    eval_rows, eval_labels = assay._snapshot_rows(eval_snapshot_obj)
    train_state = json.loads((WORK / "prepared.json").read_text())
    train_snapshot = Snapshot(train_state["training"]["store"], train_state["training"]["snapshot"])
    train_rows, train_labels = assay._snapshot_rows(train_snapshot)
    feature_idx = next(
        i for i, f in enumerate(model.spec["schema"]["features"]) if f["name"] == "signal/bit"
    )
    target_idx = next(
        i
        for i, target in enumerate(model.spec["schema"]["targets"])
        if target["name"] == "target/events_1000000000ns"
    )
    exact_train = [
        row
        for row in train_rows
        if 0 <= row["decision_ns"] // assay.NS < 100
        and row["target_status"][target_idx] == "observed"
    ]
    unconditional = [
        assay._quantile([train_labels[row["id"]][target_idx] for row in exact_train], q)
        for q in (0.1, 0.5, 0.9)
    ]
    conditional = {}
    train_x = assay._flatten_x(train_snapshot)
    for bit in (0, 1):
        values = [
            train_labels[row["id"]][target_idx]
            for row in exact_train
            if int(train_x[row["pool"]][3][feature_idx]) == bit
        ]
        if not values:
            raise ValueError("conditional_baseline_empty_group")
        conditional[bit] = [assay._quantile(values, q) for q in (0.1, 0.5, 0.9)]

    actual, latest, predictions = [], [], []
    for row in eval_rows:
        actual.append(eval_labels[row["id"]][target_idx])
        latest.append(int(eval_x[row["pool"]][3][feature_idx]))
        frames = [
            [None if not math.isfinite(value) else value for value in frame]
            for frame in eval_x[row["pool"]]
        ]
        predictions.append([float(value) for value in model.predict(frames)[0][target_idx]])
    model_loss = assay._pinball(actual, predictions)
    unconditional_loss = assay._pinball(actual, [unconditional] * len(actual))
    conditional_loss = assay._pinball(actual, [conditional[bit] for bit in latest])
    elapsed = time.monotonic() - evaluation_started
    scratch_bytes = assay._tree_bytes(WORK)
    if scratch_bytes > assay.LIMITS["max_scratch_bytes"]:
        raise ValueError(f"scratch_budget:{scratch_bytes}")
    thresholds = prereg["inherited_thresholds"]
    exact_rows = sum(row["target_status"][target_idx] == "observed" for row in eval_rows)
    status = (
        "pass"
        if exact_rows == thresholds["evaluation_rows_exact"]
        and model_loss <= thresholds["max_raw_count_pinball"]
        and model_loss <= thresholds["max_loss_ratio_to_each_baseline"] * unconditional_loss
        and model_loss <= thresholds["max_loss_ratio_to_each_baseline"] * conditional_loss
        else "fail"
    )
    result = {
        "version": "brain-lagged-context-retrieval-result-v1",
        "experiment_id": RUN_ID,
        "original_preregistration_sha256": prereg["original_preregistration_sha256"],
        "continuation_preregistration_sha256": sha(prereg_path),
        "training_snapshot": prereg["candidate_training_snapshot"],
        "artifact_manifest_sha256": artifact_pin,
        "candidate_report_sha256": prereg["candidate_report_sha256"],
        "protocol": protocol["protocol"],
        "evaluation_source": eval_source["wrapper"]["source_pin"],
        "evaluation_snapshot": eval_snapshot["snapshot"],
        "registered_score": scored,
        "structural_census": census,
        "evaluation": {
            "exact_rows": exact_rows,
            "target_index": target_idx,
            "raw_count_macro_pinball": model_loss,
            "unconditional_baseline": {"quantiles": unconditional, "loss": unconditional_loss},
            "latest_frame_conditional_baseline": {
                "quantiles_by_bit": {str(bit): values for bit, values in conditional.items()},
                "loss": conditional_loss,
            },
            "ratio_to_unconditional": model_loss / unconditional_loss
            if unconditional_loss
            else None,
            "ratio_to_latest_frame_conditional": model_loss / conditional_loss
            if conditional_loss
            else None,
            "elapsed_seconds": elapsed,
            "scratch_bytes": scratch_bytes,
        },
        "thresholds": thresholds,
        "fit_repeated": False,
        "status": status,
        "claim_limit": prereg["claim_limit"],
    }
    result_path = ROOT / "docs/evidence/brain-lagged-context-retrieval-result-2026-10-15T0610Z.json"
    if result_path.exists():
        raise FileExistsError("evaluation_result_already_exists")
    result_path.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return result_path


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"prepare", "score"}:
        raise SystemExit("usage: score_lagged_context_evaluation.py prepare|score")
    result = prepare() if sys.argv[1] == "prepare" else score()
    print(result)
