#!/usr/bin/env python3
"""Read-only arithmetic over the one sealed synthetic lag assay evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from memecoin_experience.storage import Snapshot, digest, file_hash, verify_files  # noqa: E402
from tools import lagged_context_retrieval as assay  # noqa: E402

RUN_ID = "2026-10-15T0610Z"
WORK = ROOT / "work/lagged-context" / RUN_ID
SCORE = WORK / "evaluation-score"
EVIDENCE = ROOT / "docs/evidence"
PREREG = EVIDENCE / f"brain-lagged-context-postscore-addendum-prereg-{RUN_ID}.json"
RESULT = EVIDENCE / f"brain-lagged-context-postscore-addendum-{RUN_ID}.json"
TARGET = "target/events_1000000000ns"
QUANTILES = (0.1, 0.5, 0.9)
FLOAT_TOLERANCE = 1e-12


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_snapshot_rows(snapshot: Snapshot) -> tuple[list[dict], dict[str, list[float]]]:
    import struct

    rows: list[dict] = []
    labels: dict[str, list[float]] = {}
    width = len(snapshot.schema["targets"])
    for part_index in range(len(snapshot.parts)):
        path, part_rows = snapshot.partition(part_index)
        values = struct.unpack(f"<{len(part_rows) * width}d", (path / "y.f64").read_bytes())
        for row_index, row in enumerate(part_rows):
            if row["id"] in labels:
                raise ValueError("duplicate_snapshot_id")
            rows.append(row)
            labels[row["id"]] = list(values[row_index * width : (row_index + 1) * width])
    return rows, labels


def main() -> Path:
    if not PREREG.is_file() or RESULT.exists():
        raise ValueError("addendum_prereg_missing_or_result_exists")
    prereg = json.loads(PREREG.read_text())
    if prereg["derivation_code_sha256"] != sha(Path(__file__)):
        raise ValueError("derivation_code_pin")

    original_path = ROOT / prereg["original_preregistration"]
    continuation_path = ROOT / prereg["continuation_preregistration"]
    if sha(original_path) != prereg["original_preregistration_sha256"]:
        raise ValueError("original_preregistration_pin")
    if sha(continuation_path) != prereg["continuation_preregistration_sha256"]:
        raise ValueError("continuation_preregistration_pin")
    original = json.loads(original_path.read_text())
    continuation = json.loads(continuation_path.read_text())
    if continuation["evaluation_attempts"] != 1 or continuation["fit_repeated"]:
        raise ValueError("evaluation_or_fit_count")

    score_manifest_pin = sha(SCORE / "manifest.json")
    if score_manifest_pin != prereg["sealed_score_manifest_sha256"]:
        raise ValueError("sealed_score_manifest_pin")
    score_files = verify_files(SCORE, score_manifest_pin)
    if score_files != prereg["sealed_score_files"]:
        raise ValueError("sealed_score_tree_changed")
    protocol = json.loads((SCORE / "protocol.json").read_text())
    report = json.loads((SCORE / "report.json").read_text())
    if (
        file_hash(SCORE / "protocol.json") != prereg["protocol_pin"]
        or report.get("binding", {}).get("protocol") != prereg["protocol_pin"]
        or report.get("binding", {}).get("snapshot") != prereg["evaluation_snapshot"]
        or report.get("binding", {}).get("candidate") != prereg["candidate_artifact_manifest_sha256"]
        or report.get("status") != "completed"
        or report.get("production_eligible") is not False
    ):
        raise ValueError("registered_score_binding")
    target_metric = [item for item in report["metrics"] if item["target"] == TARGET]
    if len(target_metric) != 1:
        raise ValueError("registered_target_metric_cardinality")

    prepared = json.loads((WORK / "prepared.json").read_text())
    eval_snapshot = Snapshot(
        WORK / "evaluation-build/snapshot", prereg["evaluation_snapshot"]
    )
    train_snapshot = Snapshot(prepared["training"]["store"], prereg["training_snapshot"])
    eval_rows, eval_labels = read_snapshot_rows(eval_snapshot)
    train_rows, train_labels = read_snapshot_rows(train_snapshot)
    eval_x = assay._flatten_x(eval_snapshot)
    train_x = assay._flatten_x(train_snapshot)
    schema = eval_snapshot.schema
    target_index = next(i for i, target in enumerate(schema["targets"]) if target["name"] == TARGET)
    feature_index = next(
        i for i, feature in enumerate(schema["features"]) if feature["name"] == "signal/bit"
    )
    eval_eligible = [row for row in eval_rows if row["target_status"][target_index] == "observed"]
    train_eligible = [
        row
        for row in train_rows
        if 0 <= row["decision_ns"] // assay.NS < 100
        and row["target_status"][target_index] == "observed"
    ]
    if len(eval_eligible) != 128 or len(train_eligible) != 256:
        raise ValueError("exact_role_denominator")

    expected = {}
    for row in eval_eligible:
        key = (row["entity"], row["id"], row["decision_ns"], row["input_available_ns"])
        if key in expected:
            raise ValueError("duplicate_eval_snapshot_key")
        expected[key] = row

    actual_values: list[float] = []
    latest_bits: list[int] = []
    model_quantiles: list[list[float]] = []
    seen = set()
    with (SCORE / "predictions.jsonl").open() as stream:
        for line in stream:
            item = json.loads(line)
            key = (item["entity"], item["id"], item["decision_ns"], item["input_available_ns"])
            if key not in expected or key in seen:
                raise ValueError("prediction_transcript_missing_extra_or_duplicate_key")
            seen.add(key)
            row = expected[key]
            if item["target_mask"] != [status == "observed" for status in row["target_status"]]:
                raise ValueError("prediction_target_mask_mismatch")
            prediction = item["prediction"]
            if len(prediction) != len(schema["targets"]):
                raise ValueError("prediction_target_width")
            values = [float(value) for value in prediction[target_index]]
            if len(values) != 3 or not all(math.isfinite(value) for value in values):
                raise ValueError("prediction_quantile_shape_or_finiteness")
            if values != sorted(values):
                raise ValueError("prediction_quantile_order")
            model_quantiles.append(values)
            actual_values.append(float(eval_labels[row["id"]][target_index]))
            latest_bits.append(int(eval_x[row["pool"]][3][feature_index]))
    if seen != set(expected):
        raise ValueError("prediction_transcript_cardinality")

    unconditional = [
        assay._quantile([float(train_labels[row["id"]][target_index]) for row in train_eligible], q)
        for q in QUANTILES
    ]
    conditional = {}
    for bit in (0, 1):
        ys = [
            float(train_labels[row["id"]][target_index])
            for row in train_eligible
            if int(train_x[row["pool"]][3][feature_index]) == bit
        ]
        if not ys:
            raise ValueError("empty_train_conditional_group")
        conditional[bit] = [assay._quantile(ys, q) for q in QUANTILES]

    # The frozen assay loss sums the three pinball terms per row; official scoring
    # averages over quantiles, so its matching value is exactly one third here.
    model_loss = assay._pinball(actual_values, model_quantiles)
    unconditional_loss = assay._pinball(actual_values, [unconditional] * len(actual_values))
    conditional_loss = assay._pinball(
        actual_values, [conditional[bit] for bit in latest_bits]
    )
    official_loss = float(target_metric[0]["pinball"])
    crosscheck_difference = abs(model_loss / 3.0 - official_loss)
    if crosscheck_difference > FLOAT_TOLERANCE:
        raise ValueError("official_pinball_crosscheck")

    thresholds = original["thresholds"]
    exact_rows = len(actual_values)
    predictive_gate = (
        exact_rows == thresholds["evaluation_rows_exact"]
        and model_loss <= thresholds["max_raw_count_pinball"]
        and model_loss <= thresholds["max_loss_ratio_to_each_baseline"] * unconditional_loss
        and model_loss <= thresholds["max_loss_ratio_to_each_baseline"] * conditional_loss
    )
    receipt = {
        "version": "brain-lagged-context-postscore-readonly-metric-addendum-v1",
        "experiment_id": RUN_ID,
        "status": "predictive_thresholds_met_resource_gate_unverified"
        if predictive_gate
        else "predictive_thresholds_not_met_resource_gate_unverified",
        "evaluation_semantics": (
            "Post-exposure arithmetic over bytes from the sole completed registered evaluation. "
            "Eval labels and predictions were already exposed by evaluation.score; this is not "
            "a second preregistered evaluation, new holdout, or prospective result."
        ),
        "original_preregistration_sha256": sha(original_path),
        "continuation_preregistration_sha256": sha(continuation_path),
        "addendum_preregistration_sha256": sha(PREREG),
        "derivation_code_sha256": prereg["derivation_code_sha256"],
        "candidate_artifact_manifest_sha256": prereg["candidate_artifact_manifest_sha256"],
        "candidate_report_sha256": prereg["candidate_report_sha256"],
        "training_snapshot": prereg["training_snapshot"],
        "protocol_pin": prereg["protocol_pin"],
        "evaluation_source_pin": prereg["evaluation_source_pin"],
        "evaluation_snapshot": prereg["evaluation_snapshot"],
        "sealed_score": {
            "manifest_sha256": score_manifest_pin,
            "files": score_files,
            "report_sha256": sha(SCORE / "report.json"),
            "transcript_sha256": sha(SCORE / "predictions.jsonl"),
        },
        "denominators": {
            "evaluation_exact_target_rows": exact_rows,
            "training_exact_target_rows": len(train_eligible),
            "evaluation_pools": len({row["entity"] for row in eval_eligible}),
            "evaluation_score_target_observed": target_metric[0]["n"],
            "evaluation_score_target_usable": report["denominators"][f"{TARGET}/usable"],
        },
        "target": TARGET,
        "metrics": {
            "frozen_assay_model_raw_count_macro_pinball_sum_q_per_row": model_loss,
            "official_scorer_mean_q_pinball": official_loss,
            "model_loss_divided_by_three_minus_official_absolute_difference": crosscheck_difference,
            "unconditional_train_only_quantiles": unconditional,
            "unconditional_train_only_loss": unconditional_loss,
            "latest_frame_bit_conditional_train_only_quantiles": {
                str(bit): values for bit, values in conditional.items()
            },
            "latest_frame_bit_conditional_train_only_loss": conditional_loss,
            "ratio_to_unconditional": model_loss / unconditional_loss if unconditional_loss else None,
            "ratio_to_latest_frame_conditional": model_loss / conditional_loss if conditional_loss else None,
        },
        "thresholds": thresholds,
        "fit_repeated": False,
        "evaluation_score_repeated": False,
        "predictor_reinvoked": False,
        "evaluation_source_rebuilt": False,
        "peak_scratch_gate": "unverified; no peak measurement was captured during the original fit/evaluation process",
        "overall_assay_qualification": "incomplete_due_to_unverified_resource_gate",
        "claim_limit": continuation["claim_limit"],
    }
    RESULT.write_text(json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return RESULT


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("usage: derive_lagged_context_addendum.py")
    print(main())
