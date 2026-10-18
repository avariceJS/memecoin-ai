"""Offline support audit: scenario variants and overlapping labels are not sample size."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from memecoin_ai.corpus_quality import _provenance, _result
from memecoin_ai.domain import ValidationError
from memecoin_ai.execution_targets import execution_spec
from memecoin_ai.forward import _read
from memecoin_ai.prospective import hashes, read, write


def support_report(features: list[dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Require every planned scenario to mature in one partition before counting a window."""
    by_id = {r["sample_id"]: r for r in features}
    if len(by_id) != len(features):
        raise ValidationError("duplicate feature sample")
    spec = execution_spec()
    grid = {
        (direction, size, delay)
        for direction in spec["input_indices"]
        for size in spec["sizes_ppm"]
        for delay in spec["assumed_delays_ns"]
    }
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for label in labels:
        if label["sample_id"] not in by_id:
            raise ValidationError("target without feature")
        groups[label["sample_id"]].append(label)
    if set(groups) != set(by_id):
        raise ValidationError("missing decision scenario group")
    windows = []
    excluded: Counter[str] = Counter()
    for sample_id, variants in groups.items():
        keys = {(r["input_index"], r["size_ppm"], r["assumed_delay_ns"]) for r in variants}
        if keys != grid or len(variants) != len(grid):
            raise ValidationError("scenario grid missing, duplicate or unplanned")
        start = by_id[sample_id]
        if any(
            r["pool_id"] != start["pool_id"] or r["decision_ns"] != start["decision_ns"]
            for r in variants
        ):
            raise ValidationError("scenario decision differs from feature")
        if not start["usable"]:
            excluded["masked_start"] += 1
            continue
        if any(r["status"] == "censored" for r in variants):
            excluded["at_least_one_censored_variant"] += 1
            continue
        partitions = {r["partition"] for r in variants}
        if len(partitions) != 1 or partitions == {"purged"}:
            excluded["purged_or_mixed_partition"] += 1
            continue
        partition = next(iter(partitions))
        if partition not in ("calibration", "engineering_evaluation"):
            raise ValidationError("unexpected mature partition")
        end = max(r["target_available_ns"] for r in variants)
        if end <= start["decision_ns"]:
            raise ValidationError("nonpositive target window")
        windows.append(
            dict(
                sample_id=sample_id,
                pool_id=start["pool_id"],
                partition=partition,
                start_ns=start["decision_ns"],
                end_ns=end,
            )
        )

    def count_nonoverlap(rows: list[dict[str, Any]]) -> int:
        # Fixed chronological thinning, not an optimization on returns/duration.
        end = -1
        count = 0
        for row in sorted(rows, key=lambda r: (r["start_ns"], r["pool_id"], r["sample_id"])):
            if row["start_ns"] > end:  # Shared boundary endpoint remains dependent.
                count += 1
                end = row["end_ns"]
        return count

    per_pool: dict[str, Any] = {}
    for pool in sorted({r["pool_id"] for r in features}):
        usable = [r for r in features if r["pool_id"] == pool and r["usable"]]
        per_pool[pool] = dict(
            usable_rows=len(usable),
            distinct_slots=len({r["slot"] for r in usable}),
            distinct_observed_log_prices=len({r["values"]["log_price_1_per_0"] for r in usable}),
            zero_observed_price_changes=sum(r["values"]["log_price_change"] == 0 for r in usable),
            nonoverlapping_complete_windows_by_partition={
                p: count_nonoverlap(
                    [w for w in windows if w["pool_id"] == pool and w["partition"] == p]
                )
                for p in ("calibration", "engineering_evaluation")
            },
        )
    return dict(
        kind="conditional_target_support_audit",
        feature_decisions=len(features),
        scenario_rows=len(labels),
        complete_decisions_by_partition=dict(Counter(w["partition"] for w in windows)),
        excluded_decisions=dict(excluded),
        per_pool=per_pool,
        all_pool_nonoverlapping_windows_by_partition={
            p: count_nonoverlap([w for w in windows if w["partition"] == p])
            for p in ("calibration", "engineering_evaluation")
        },
        selection="all planned scenarios mature in one partition; chronological nonoverlap",
        caveats=[
            "nonoverlap counts are not statistically independent effective sample sizes",
            "flat observed prices do not prove absence of intervening trades",
            "report uses future labels only for offline QA; never an online feature/filter",
            "engineering evaluation is not a protected holdout",
        ],
        training_ready=False,
        execution_qualified=False,
        seed=None,
    )


def _support(campaign: Path, targets: Path) -> dict[str, Any]:
    _result(campaign, targets)  # Reproduce raw/features/labels and independent quality invariants.
    features = []
    for segment in sorted((campaign / "segments").iterdir()):
        if not read(segment / "seal.json")["interrupted"]:
            features.extend(_read(segment / "corpus/features.jsonl"))
    return support_report(features, _read(targets / "targets.jsonl"))


def _support_provenance(campaign: Path, targets: Path) -> dict[str, Any]:
    parent = _provenance(campaign, targets)
    parent["source"]["coverage.py"] = Path(__file__).read_text()
    parent["source"]["corpus_quality.py"] = (
        Path(__file__).with_name("corpus_quality.py").read_text()
    )
    return parent


def export_support(campaign: Path, targets: Path, output: Path) -> dict[str, Any]:
    result = _support(campaign, targets)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "provenance.json", _support_provenance(campaign, targets))
    write(output / "report.json", result)
    write(output / "manifest.json", hashes(output))
    return result


def verify_support(campaign: Path, targets: Path, output: Path) -> dict[str, Any]:
    files = hashes(output)
    files.pop("manifest.json")
    if files != read(output / "manifest.json") or read(
        output / "provenance.json"
    ) != _support_provenance(campaign, targets):
        raise ValidationError("support artifact provenance/checksum mismatch")
    result = _support(campaign, targets)
    if result != read(output / "report.json"):
        raise ValidationError("support report reproduction mismatch")
    return result
