"""Offline coverage/maturity/latency accounting, without training or edge claims."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from memecoin_ai.domain import ValidationError, digest
from memecoin_ai.execution_targets import _sources, verify_execution_targets
from memecoin_ai.forward import _read
from memecoin_ai.prospective import hashes, read, verify_campaign, write

NS = 10**9


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if any(not math.isfinite(v) for v in ordered):
        raise ValidationError("non-finite quality measurement")
    return dict(
        count=len(ordered),
        minimum=ordered[0] if ordered else None,
        median=statistics.median(ordered) if ordered else None,
        p95=ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else None,
        maximum=ordered[-1] if ordered else None,
    )


def summarize_quality(
    segments: list[list[dict[str, Any]]],
    labels: list[dict[str, Any]],
    *,
    cutoff_ns: int,
) -> dict[str, Any]:
    """Pure independent invariants and coverage accounting over already verified rows."""
    features: dict[str, dict[str, Any]] = {}
    boundaries = []
    spans = []
    prior_end: int | None = None
    runs: dict[str, list[float]] = defaultdict(list)
    for index, rows in enumerate(segments):
        if not rows:
            continue
        start, end = min(r["decision_ns"] for r in rows), max(r["decision_ns"] for r in rows)
        if prior_end is not None:
            if start <= prior_end:
                raise ValidationError("overlapping campaign segment time")
            boundaries.append(
                dict(
                    after_segment=index - 1,
                    before_segment=index,
                    gap_seconds=(start - prior_end) / NS,
                )
            )
        prior_end = end
        spans.append((end - start) / NS)
        by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["sample_id"] in features:
                raise ValidationError("duplicate feature sample")
            features[row["sample_id"]] = dict(row, campaign_segment=index)
            by_pool[row["pool_id"]].append(row)
        for pool, history in by_pool.items():
            run_start: int | None = None
            last: dict[str, Any] | None = None
            for row in history:
                if last is not None and row["decision_ns"] <= last["decision_ns"]:
                    raise ValidationError("unordered pool observations")
                if not row["usable"] or (last and row["segment"] != last["segment"]):
                    if run_start is not None and last is not None:
                        runs[pool].append((last["decision_ns"] - run_start) / NS)
                    run_start = None
                if row["usable"] and run_start is None:
                    run_start = row["decision_ns"]
                last = row
            if run_start is not None and last is not None:
                runs[pool].append((last["decision_ns"] - run_start) / NS)
    seen = set()
    entry_delays, exit_delays, held, outcome_ages = [], [], [], []
    mature_decisions: dict[str, set[str]] = defaultdict(set)
    for label in labels:
        key = (
            label["sample_id"],
            label["input_index"],
            label["size_ppm"],
            label["assumed_delay_ns"],
        )
        if key in seen:
            raise ValidationError("duplicate execution scenario")
        seen.add(key)
        start = features[label["sample_id"]]
        if label["campaign_segment"] != start["campaign_segment"]:
            raise ValidationError("target start crosses campaign boundary")
        if label["net_pnl"] is not None:
            raise ValidationError("unknown execution cost cannot become net PnL")
        previous = start
        for leg in label["legs"]:
            current = features[leg["sample_id"]]
            if (
                current["pool_id"] != start["pool_id"]
                or not current["usable"]
                or current["campaign_segment"] != start["campaign_segment"]
                or current["segment"] != start["segment"]
                or current["slot"] <= previous["slot"]
                or current["decision_ns"] <= previous["decision_ns"]
                or leg["response_sha256"] != current["input_response_sha256"]
            ):
                raise ValidationError("execution leg crosses gap or has invalid provenance")
            deadline = previous["decision_ns"] + (
                label["holding_ns"] if leg["kind"] == "exit_decision" else label["assumed_delay_ns"]
            )
            if current["input_available_ns"] < deadline:
                raise ValidationError("execution endpoint precedes required availability")
            previous = current
        if label["status"] == "censored":
            if label["partition"] != "censored" or label["pnl_before_network_costs"] is not None:
                raise ValidationError("censored execution outcome treated as known")
            continue
        expected = (
            "engineering_evaluation"
            if start["decision_ns"] >= cutoff_ns
            else "purged"
            if previous["feature_available_ns"] >= cutoff_ns
            else "calibration"
        )
        if (
            label["partition"] != expected
            or label["target_available_ns"] != previous["feature_available_ns"]
        ):
            raise ValidationError("execution target maturity/purge mismatch")
        mature_decisions[expected].add(label["sample_id"])
        if (
            label["status"] == "exit_unfilled"
            and label["residual_token_units"] != label["entry_net_output"]
        ):
            raise ValidationError("failed exit erased inventory")
        if label["status"] == "entry_unfilled" and label["residual_token_units"] != 0:
            raise ValidationError("failed entry invented inventory")
        if label["status"] == "conditional_roundtrip":
            if (
                label["residual_token_units"] != 0
                or label["pnl_before_network_costs"]
                != label["exit_net_output"] - label["gross_input"]
            ):
                raise ValidationError("conditional roundtrip accounting mismatch")
        if label["legs"]:
            entry_delays.append((label["legs"][0]["decision_ns"] - start["decision_ns"]) / NS)
        if len(label["legs"]) == 3:
            held.append((label["legs"][1]["decision_ns"] - label["legs"][0]["decision_ns"]) / NS)
            exit_delays.append(
                (label["legs"][2]["decision_ns"] - label["legs"][1]["decision_ns"]) / NS
            )
        outcome_ages.append((label["target_available_ns"] - start["decision_ns"]) / NS)
    rows = list(features.values())
    usable = [r for r in rows if r["usable"]]
    return dict(
        feature_rows=len(rows),
        usable_features=len(usable),
        distinct_pools=len({r["pool_id"] for r in rows}),
        summed_segment_span_seconds=sum(spans),
        segment_spans_seconds=spans,
        boundaries=boundaries,
        longest_usable_run_seconds_by_pool={p: max(v, default=0) for p, v in sorted(runs.items())},
        feature_masks=dict(Counter(reason for r in rows for reason in r["reasons"])),
        receipt_age_seconds=distribution([r["values"]["receipt_age_seconds"] for r in usable]),
        chain_clock_age_seconds=distribution(
            [r["values"]["chain_clock_age_seconds"] for r in usable]
        ),
        feature_compute_seconds=distribution(
            [(r["feature_available_ns"] - r["as_of_ns"]) / NS for r in rows]
        ),
        execution_statuses=dict(Counter(r["status"] for r in labels)),
        target_partitions=dict(Counter(r["partition"] for r in labels)),
        distinct_mature_decisions_by_partition={
            p: len(v) for p, v in sorted(mature_decisions.items())
        },
        censor_reasons=dict(Counter(r["reason"] for r in labels if r["status"] == "censored")),
        conditional_entry_observation_delay_seconds=distribution(entry_delays),
        conditional_exit_observation_delay_seconds=distribution(exit_delays),
        conditional_observed_holding_seconds=distribution(held),
        conditional_label_maturity_seconds=distribution(outcome_ages),
        temporal_and_accounting_checks_passed=True,
        latency_semantics="snapshot publication delays; not transaction inclusion latency",
        training_ready=False,
        execution_qualified=False,
        edge_established=False,
        blockers=[
            "fixed cohort selection is not a qualified market universe",
            "minutes of correlated snapshots do not establish regime or out-of-period coverage",
            "scenario variants and overlapping horizons are not independent observations",
            "admission/inclusion/network costs/rent/FX/persistent own impact uncalibrated",
            "engineering evaluation is not a protected final holdout",
        ],
    )


def _result(campaign: Path, targets: Path) -> dict[str, Any]:
    state = verify_campaign(campaign)
    verify_execution_targets(campaign, targets)
    segments: list[list[dict[str, Any]]] = []
    attempts: list[dict[str, Any]] = []
    for seal in state["segments"]:
        if seal["interrupted"]:
            segments.append([])
            continue
        path = campaign / "segments" / f"{seal['index']:03d}" / "corpus"
        segments.append(_read(path / "features.jsonl"))
        attempts.extend(_read(path / "attempts.jsonl"))
    result = summarize_quality(
        segments, _read(targets / "targets.jsonl"), cutoff_ns=state["plan"]["calibration_end_ns"]
    )
    result.update(
        collection_complete=state["complete"],
        attempted_requests=len(attempts),
        request_errors=dict(Counter(r.get("category") for r in attempts if r["status"] == "error")),
        http_response_time_seconds=distribution(
            [r["response_ns"] / NS for r in attempts if r["http_status"] == 200]
        ),
        response_bytes=sum(r.get("response_bytes", 0) for r in attempts),
        calibration_end_ns=state["plan"]["calibration_end_ns"],
    )
    return result


def _provenance(campaign: Path, targets: Path) -> dict[str, Any]:
    return dict(
        campaign_plan_sha256=digest((campaign / "plan.json").read_bytes()),
        target_manifest_sha256=digest((targets / "manifest.json").read_bytes()),
        source={**_sources(), "corpus_quality.py": Path(__file__).read_text()},
    )


def export_quality(campaign: Path, targets: Path, output: Path) -> dict[str, Any]:
    result = _result(campaign, targets)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "provenance.json", _provenance(campaign, targets))
    write(output / "report.json", result)
    write(output / "manifest.json", hashes(output))
    return result


def verify_quality(campaign: Path, targets: Path, output: Path) -> dict[str, Any]:
    files = hashes(output)
    files.pop("manifest.json")
    if files != read(output / "manifest.json") or read(output / "provenance.json") != _provenance(
        campaign, targets
    ):
        raise ValidationError("quality artifact provenance/checksum mismatch")
    result = _result(campaign, targets)
    if result != read(output / "report.json"):
        raise ValidationError("quality report reproduction mismatch")
    return result
