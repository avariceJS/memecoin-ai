"""Conditional two-leg snapshot probes, with unknown inclusion/costs kept unknown."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from memecoin_ai.causal import observed_checkpoint, validate_feature_row
from memecoin_ai.cpmm import EconomicState, Fees
from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.forward import _read
from memecoin_ai.prospective import hashes, read, source, verify_campaign, write
from memecoin_ai.sources import Capture
from memecoin_ai.tokens import TransferFee, transfer_swap

NS = 10**9


def execution_spec() -> dict[str, Any]:
    return dict(
        schema_version=1,
        kind="conditional_snapshot_execution_targets",
        sizes_ppm=[100, 1000],
        input_indices=[0, 1],
        assumed_delays_ns=[0, 5 * NS],
        holding_ns=20 * NS,
        tolerance_ns=15 * NS,
        slippage_bps=100,
        entry_sizing="gross wallet input from decision reserve; fixed before future states",
        continuation="exit entire net acquired amount; hold from entry observation publication",
        execution_model="independent_future_snapshot_reset_v1",
        impact="integer local quote impact; no persistent own impact or intervening activity",
        network_cost_input_atomic=None,
        native_network_cost_lamports=None,
        admission_probability=None,
        training_ready=False,
        execution_qualified=False,
        label_definition="conditional on snapshot fills; net of pool/transfer fees only",
    )


def quote(obs: dict[str, Any], direction: int, amount: int) -> int:
    e = obs["economic"]
    if e["pool"]["status"] & 4 or obs["chain_timestamp_seconds"] < e["pool"]["open_time"]:
        raise ValidationError("swap disabled or pre-open")
    fees = tuple(TransferFee(**v) for v in e["token_fees"])
    if len(fees) != 2:
        raise ValidationError("two token fee schedules required")
    _, _, net, _, _ = transfer_swap(
        EconomicState(**e["state"]),
        Fees(**e["fees"]),
        direction,
        amount,
        0,
        exact_input=True,
        creator_fee_on=e["pool"]["creator_fee_on"],
        creator_enabled=e["pool"]["enable_creator_fee"],
        token_fees=(fees[0], fees[1]),
    )
    return net


def _endpoint(
    history: list[dict[str, Any]], index: int, deadline: int, tolerance: int
) -> tuple[int | None, str | None]:
    start = history[index]
    for j in range(index + 1, len(history)):
        future = history[j]
        if not future["usable"] or future["segment"] != start["segment"]:
            return None, "observation_gap"
        if future["input_available_ns"] < deadline or future["slot"] <= start["slot"]:
            continue
        if future["decision_ns"] > deadline + tolerance:
            return None, "endpoint_late"
        return j, None
    return None, "right_censored"


def condition_segment(
    features: list[dict[str, Any]],
    observations: dict[str, dict[str, Any]],
    *,
    calibration_end_ns: int,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    if spec != execution_spec():
        raise ValidationError("unsupported execution specification")
    by_pool: dict[str, list[dict[str, Any]]] = {}
    for row in features:
        validate_feature_row(row)
        if row["usable"]:
            value = observations.get(row["input_response_sha256"])
            if (
                value is None
                or value["pool_id"] != row["pool_id"]
                or value["slot"] != row["slot"]
                or value["available_ns"] != row["input_available_ns"]
            ):
                raise ValidationError("execution observation provenance mismatch")
        history = by_pool.setdefault(row["pool_id"], [])
        if history and row["decision_ns"] <= history[-1]["decision_ns"]:
            raise ValidationError("execution feature order invalid")
        history.append(row)
    results = []
    for history in by_pool.values():
        for i, start in enumerate(history):
            for direction in spec["input_indices"]:
                for size in spec["sizes_ppm"]:
                    for delay in spec["assumed_delays_ns"]:
                        result: dict[str, Any] = dict(
                            sample_id=start["sample_id"],
                            pool_id=start["pool_id"],
                            decision_ns=start["decision_ns"],
                            input_index=direction,
                            size_ppm=size,
                            assumed_delay_ns=delay,
                            holding_ns=spec["holding_ns"],
                            status="censored",
                            reason=None,
                            gross_input=None,
                            entry_net_output=None,
                            exit_net_output=None,
                            pnl_before_network_costs=None,
                            return_before_network_costs=None,
                            net_pnl=None,
                            residual_token_units=None,
                            target_available_ns=history[-1]["decision_ns"],
                            legs=[],
                            partition="censored",
                        )
                        if not start["usable"]:
                            result["reason"] = "invalid_start_observation"
                            results.append(result)
                            continue

                        def obs(row: dict[str, Any]) -> dict[str, Any]:
                            value = observations[row["input_response_sha256"]]
                            if (
                                value["pool_id"] != row["pool_id"]
                                or value["slot"] != row["slot"]
                                or value["available_ns"] != row["input_available_ns"]
                            ):
                                raise ValidationError("execution observation provenance mismatch")
                            return value

                        try:
                            initial = obs(start)
                            result.update(
                                input_mint=initial["economic"]["pool"]["mints"][direction],
                                output_mint=initial["economic"]["pool"]["mints"][1 - direction],
                                input_decimals=initial["economic"]["pool"]["decimals"][direction],
                                initial_response_sha256=start["input_response_sha256"],
                            )
                            amount = (
                                initial["economic"]["effective_reserves"][direction]
                                * size
                                // 1_000_000
                            )
                            integer(amount, "sized gross input", 1)
                            entry_minimum = (
                                quote(initial, direction, amount)
                                * (10000 - spec["slippage_bps"])
                                // 10000
                            )
                            result.update(gross_input=amount, entry_min_output=entry_minimum)
                            entry, reason = _endpoint(
                                history, i, start["decision_ns"] + delay, spec["tolerance_ns"]
                            )
                            if entry is None:
                                result["reason"] = "entry_" + str(reason)
                            else:
                                entry_row = history[entry]
                                acquired = quote(obs(entry_row), direction, amount)
                                result["legs"].append(
                                    dict(
                                        kind="entry",
                                        sample_id=entry_row["sample_id"],
                                        slot=entry_row["slot"],
                                        input_available_ns=entry_row["input_available_ns"],
                                        response_sha256=entry_row["input_response_sha256"],
                                        decision_ns=entry_row["decision_ns"],
                                    )
                                )
                                result.update(target_available_ns=entry_row["feature_available_ns"])
                                if acquired < entry_minimum:
                                    result.update(
                                        status="entry_unfilled",
                                        reason="entry_slippage",
                                        residual_token_units=0,
                                    )
                                else:
                                    result.update(
                                        entry_net_output=acquired, residual_token_units=acquired
                                    )
                                    exit_decision, reason = _endpoint(
                                        history,
                                        entry,
                                        entry_row["decision_ns"] + spec["holding_ns"],
                                        spec["tolerance_ns"],
                                    )
                                    if exit_decision is None:
                                        result["reason"] = "exit_decision_" + str(reason)
                                    else:
                                        exit_row = history[exit_decision]
                                        minimum = (
                                            quote(obs(exit_row), 1 - direction, acquired)
                                            * (10000 - spec["slippage_bps"])
                                            // 10000
                                        )
                                        result["exit_min_output"] = minimum
                                        result["legs"].append(
                                            dict(
                                                kind="exit_decision",
                                                sample_id=exit_row["sample_id"],
                                                slot=exit_row["slot"],
                                                input_available_ns=exit_row["input_available_ns"],
                                                response_sha256=exit_row["input_response_sha256"],
                                                decision_ns=exit_row["decision_ns"],
                                            )
                                        )
                                        exit_fill, reason = _endpoint(
                                            history,
                                            exit_decision,
                                            exit_row["decision_ns"] + delay,
                                            spec["tolerance_ns"],
                                        )
                                        if exit_fill is None:
                                            result["reason"] = "exit_fill_" + str(reason)
                                        else:
                                            end = history[exit_fill]
                                            back = quote(obs(end), 1 - direction, acquired)
                                            result["legs"].append(
                                                dict(
                                                    kind="exit_fill",
                                                    sample_id=end["sample_id"],
                                                    slot=end["slot"],
                                                    input_available_ns=end["input_available_ns"],
                                                    response_sha256=end["input_response_sha256"],
                                                    decision_ns=end["decision_ns"],
                                                )
                                            )
                                            result["target_available_ns"] = end[
                                                "feature_available_ns"
                                            ]
                                            if back < minimum:
                                                result.update(
                                                    status="exit_unfilled", reason="exit_slippage"
                                                )
                                            else:
                                                result.update(
                                                    status="conditional_roundtrip",
                                                    exit_net_output=back,
                                                    residual_token_units=0,
                                                    pnl_before_network_costs=back - amount,
                                                    return_before_network_costs=(back - amount)
                                                    / amount,
                                                )
                        except ValidationError:
                            result.update(status="censored", reason="unsupported_quote")
                        if result["status"] == "censored":
                            result["target_available_ns"] = history[-1]["decision_ns"]
                        else:
                            result["partition"] = (
                                "engineering_evaluation"
                                if start["decision_ns"] >= calibration_end_ns
                                else "purged"
                                if result["target_available_ns"] >= calibration_end_ns
                                else "calibration"
                            )
                        results.append(result)
    return results


def _result(parent: Path, spec: dict[str, Any]) -> dict[str, Any]:
    campaign = verify_campaign(parent)
    targets = []
    for seal in campaign["segments"]:
        if seal["interrupted"]:
            continue
        corpus = parent / "segments" / f"{seal['index']:03d}" / "corpus"
        observations = {}
        for attempt in _read(corpus / "attempts.jsonl"):
            if attempt["status"] != "advanced":
                continue
            cap = Capture.load(corpus / "captures" / f"{attempt['index']:03d}")
            obs = observed_checkpoint(cap, attempt["pool_id"], attempt["available_ns"])
            observations[obs["response_sha256"]] = obs
        rows = condition_segment(
            _read(corpus / "features.jsonl"),
            observations,
            calibration_end_ns=campaign["plan"]["calibration_end_ns"],
            spec=spec,
        )
        for row in rows:
            row["campaign_segment"] = seal["index"]
        targets.extend(rows)
    return dict(
        targets=targets,
        report=dict(
            kind=spec["kind"],
            targets=len(targets),
            feature_decisions=len({r["sample_id"] for r in targets}),
            mature_decisions=len({r["sample_id"] for r in targets if r["status"] != "censored"}),
            evaluation_decisions=len(
                {r["sample_id"] for r in targets if r["partition"] == "engineering_evaluation"}
            ),
            variants_per_decision=8,
            overlapping_labels_are_not_independent=True,
            statuses=dict(Counter(r["status"] for r in targets)),
            partitions=dict(Counter(r["partition"] for r in targets)),
            reasons=dict(Counter(r["reason"] for r in targets if r["reason"])),
            training_ready=False,
            execution_qualified=False,
            calibrated_network_costs=False,
            calibrated_inclusion=False,
            persistent_own_impact=False,
            complete_net_pnl_labels=0,
            seed=None,
        ),
    )


def _sources() -> dict[str, str]:
    return {**source(), "execution_targets.py": Path(__file__).read_text()}


def export_execution_targets(parent: Path, output: Path) -> dict[str, Any]:
    spec = execution_spec()
    result = _result(parent, spec)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "spec.json", spec)
    write(output / "source.json", _sources())
    write(
        output / "parents.json",
        dict(
            plan_sha256=digest((parent / "plan.json").read_bytes()),
            seals={
                p.parent.name: digest(p.read_bytes())
                for p in sorted((parent / "segments").glob("*/seal.json"))
            },
        ),
    )
    (output / "targets.jsonl").write_text("".join(canonical(r) + "\n" for r in result["targets"]))
    write(output / "report.json", result["report"])
    write(output / "manifest.json", hashes(output))
    report: dict[str, Any] = result["report"]
    return report


def verify_execution_targets(parent: Path, output: Path) -> dict[str, Any]:
    files = hashes(output)
    files.pop("manifest.json")
    if files != read(output / "manifest.json") or read(output / "source.json") != _sources():
        raise ValidationError("execution target checksum/source mismatch")
    parents = dict(
        plan_sha256=digest((parent / "plan.json").read_bytes()),
        seals={
            p.parent.name: digest(p.read_bytes())
            for p in sorted((parent / "segments").glob("*/seal.json"))
        },
    )
    if parents != read(output / "parents.json"):
        raise ValidationError("execution target parent campaign changed; export new version")
    result = _result(parent, read(output / "spec.json"))
    if result["targets"] != _read(output / "targets.jsonl") or result["report"] != read(
        output / "report.json"
    ):
        raise ValidationError("execution target reproduction differs")
    report: dict[str, Any] = result["report"]
    return report
