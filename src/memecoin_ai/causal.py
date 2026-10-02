"""Receipt-causal CPMM snapshots. No reconstruction/backfill becomes a past observation."""

from __future__ import annotations

import copy
import math
from typing import Any

from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.protocol import checkpoint, economic_observation
from memecoin_ai.sources import Capture
from memecoin_ai.tokens import decode_supported_checkpoint

FEATURE_NAMES = (
    "log_price_1_per_0",
    "log_reserve_0_units",
    "log_reserve_1_units",
    "trade_fee_rate",
    "creator_fee_rate",
    "transfer_fee_rate_0",
    "transfer_fee_rate_1",
    "receipt_age_seconds",
    "chain_clock_age_seconds",
    "log_price_change",
    "observation_interval_seconds",
    "slot_delta",
)


def observed_checkpoint(cap: Capture, pool_id: str, available_ns: int) -> dict[str, Any]:
    integer(available_ns, "observation available time")
    if available_ns < cap.received_ns:
        raise ValidationError("observation precedes receipt")
    decoded = decode_supported_checkpoint(cap, pool_id)
    if decoded["schema_version"] != 2:
        raise ValidationError("causal snapshot requires same-bank Clock")
    ctx = checkpoint(cap, pool_id)
    return dict(
        schema_version=1,
        kind="observed_cpmm_snapshot",
        pool_id=pool_id,
        slot=decoded["context_slot"],
        received_ns=cap.received_ns,
        available_ns=available_ns,
        chain_timestamp_seconds=decoded["clock_timestamp"],
        response_sha256=digest(cap.response),
        source_alias=cap.source_alias,
        economic=economic_observation(ctx, ctx.state, list(ctx.withheld), decoded["context_slot"]),
        mint_flags=decoded["mint_flags"],
    )


def _price(obs: dict[str, Any]) -> float:
    e = obs["economic"]
    r0, r1 = e["effective_reserves"]
    d0, d1 = e["pool"]["decimals"]
    return float(math.log(r1) - math.log(r0) + (d0 - d1) * math.log(10))


class CausalBook:
    def __init__(
        self, pools: list[str], *, max_receipt_age_ns: int, max_chain_age_ns: int, max_gap_ns: int
    ) -> None:
        if not pools or len(set(pools)) != len(pools):
            raise ValidationError("fixed cohort must be nonempty and unique")
        for bound in (max_receipt_age_ns, max_chain_age_ns, max_gap_ns):
            integer(bound, "freshness bound", 1)
        self.pools = list(pools)
        self.receipt_age = max_receipt_age_ns
        self.chain_age = max_chain_age_ns
        self.max_gap = max_gap_ns
        self.last: dict[str, dict[str, Any]] = {}
        self.previous: dict[str, dict[str, Any]] = {}
        self.gaps: dict[str, str] = {}
        self.segments = dict.fromkeys(pools, 0)
        self.now = 0

    def _advance(self, pool: str, now: int) -> None:
        integer(now, "observation clock")
        if pool not in self.pools or now < self.now:
            raise ValidationError("unknown pool or observation clock regression")
        self.now = now

    def gap(self, pool: str, available_ns: int, reason: str) -> None:
        self._advance(pool, available_ns)
        self.gaps[pool] = reason
        self.previous.pop(pool, None)
        self.segments[pool] += 1

    def ingest(self, obs: dict[str, Any]) -> str:
        if obs.get("kind") != "observed_cpmm_snapshot" or obs.get("schema_version") != 1:
            raise ValidationError("only validated observed snapshots enter causal book")
        pool, now = obs["pool_id"], obs["available_ns"]
        integer(obs["received_ns"], "receipt")
        integer(obs["slot"], "slot")
        if obs["received_ns"] > now:
            raise ValidationError("observation precedes receipt")
        self._advance(pool, now)
        old = self.last.get(pool)
        if old is not None:
            if obs["slot"] < old["slot"]:
                self.gap(pool, now, "regressed_slot")
                return "regressed_slot"
            if obs["slot"] == old["slot"]:
                keys = ("economic", "chain_timestamp_seconds", "mint_flags", "source_alias")
                if any(canonical(obs[k]) != canonical(old[k]) for k in keys):
                    self.gap(pool, now, "conflicting_same_bank")
                    return "conflicting_same_bank"
                # Duplicate banks do not refresh age or establish a new return interval.
                return "duplicate"
            discontinuity = (
                now - old["available_ns"] > self.max_gap
                or now - old["received_ns"] > self.receipt_age
                or now - old["chain_timestamp_seconds"] * 10**9 > self.chain_age
                or old["chain_timestamp_seconds"] * 10**9 > old["available_ns"]
                or min(old["economic"]["effective_reserves"]) <= 0
                or bool(old["economic"]["pool"]["status"] & 4)
                or old["chain_timestamp_seconds"] < old["economic"]["pool"]["open_time"]
            )
            if pool in self.gaps or discontinuity:
                self.previous.pop(pool, None)
                if pool not in self.gaps:
                    self.segments[pool] += 1
            else:
                self.previous[pool] = old
        self.last[pool] = copy.deepcopy(obs)
        self.gaps.pop(pool, None)
        return "advanced"

    def features(self, as_of_ns: int) -> list[dict[str, Any]]:
        integer(as_of_ns, "feature as-of")
        if as_of_ns < self.now:
            raise ValidationError("feature request predates ingested evidence")
        self.now = as_of_ns
        rows = []
        for pool in self.pools:
            obs, prev = self.last.get(pool), self.previous.get(pool)
            reasons = [self.gaps[pool]] if pool in self.gaps else []
            values: dict[str, float | None] = dict.fromkeys(FEATURE_NAMES)
            if obs is None:
                reasons.append("missing_snapshot")
            else:
                receipt_age = as_of_ns - obs["received_ns"]
                chain_age = as_of_ns - obs["chain_timestamp_seconds"] * 10**9
                if receipt_age > self.receipt_age:
                    reasons.append("stale_receipt")
                if chain_age < 0:
                    reasons.append("chain_clock_ahead")
                elif chain_age > self.chain_age:
                    reasons.append("stale_chain_clock")
                e = obs["economic"]
                if min(e["effective_reserves"]) <= 0 or e["pool"]["status"] & 4:
                    reasons.append("pool_unavailable")
                if obs["chain_timestamp_seconds"] < e["pool"]["open_time"]:
                    reasons.append("pre_open")
                if not reasons:
                    d0, d1 = e["pool"]["decimals"]
                    values.update(
                        log_price_1_per_0=_price(obs),
                        log_reserve_0_units=math.log(e["effective_reserves"][0])
                        - d0 * math.log(10),
                        log_reserve_1_units=math.log(e["effective_reserves"][1])
                        - d1 * math.log(10),
                        trade_fee_rate=e["fees"]["trade_ppm"] / 10**6,
                        creator_fee_rate=e["fees"]["creator_ppm"] / 10**6,
                        transfer_fee_rate_0=e["token_fees"][0]["bps"] / 10**4,
                        transfer_fee_rate_1=e["token_fees"][1]["bps"] / 10**4,
                        receipt_age_seconds=receipt_age / 10**9,
                        chain_clock_age_seconds=chain_age / 10**9,
                    )
                    if prev is not None and min(prev["economic"]["effective_reserves"]) > 0:
                        values.update(
                            log_price_change=_price(obs) - _price(prev),
                            observation_interval_seconds=(
                                obs["available_ns"] - prev["available_ns"]
                            )
                            / 10**9,
                            slot_delta=float(obs["slot"] - prev["slot"]),
                        )
                    else:
                        reasons.append("history_warmup")
            if any(v is not None and not math.isfinite(v) for v in values.values()):
                raise ValidationError("nonfinite causal feature")
            rows.append(
                dict(
                    pool_id=pool,
                    as_of_ns=as_of_ns,
                    segment=self.segments[pool],
                    input_available_ns=obs["available_ns"] if obs else None,
                    input_response_sha256=obs["response_sha256"] if obs else None,
                    previous_response_sha256=prev["response_sha256"] if prev else None,
                    slot=obs["slot"] if obs else None,
                    values=values,
                    missing={k: v is None for k, v in values.items()},
                    usable=not reasons,
                    reasons=reasons,
                )
            )
        return rows


def publish_features(rows: list[dict[str, Any]], available_ns: int) -> list[dict[str, Any]]:
    integer(available_ns, "feature publication time")
    result = copy.deepcopy(rows)
    for row in result:
        if available_ns < row["as_of_ns"]:
            raise ValidationError("feature publication predates computation inputs")
        row.update(
            feature_available_ns=available_ns,
            decision_ns=available_ns,
            sample_id=f"{row['pool_id']}:{available_ns}",
        )
        validate_feature_row(row)
    return result


def validate_feature_row(row: dict[str, Any]) -> None:
    for name in ("as_of_ns", "feature_available_ns", "decision_ns"):
        integer(row[name], name)
    if not row["as_of_ns"] <= row["feature_available_ns"] <= row["decision_ns"]:
        raise ValidationError("feature temporal leakage")
    if row["input_available_ns"] is not None and row["input_available_ns"] > row["as_of_ns"]:
        raise ValidationError("input unavailable at feature as-of")
    if set(row["values"]) != set(FEATURE_NAMES) or row["missing"] != {
        k: v is None for k, v in row["values"].items()
    }:
        raise ValidationError("feature schema/masks differ")
    if row["usable"] and (row["reasons"] or any(row["missing"].values())):
        raise ValidationError("usable features contain missing inputs")


def observation_targets(
    rows: list[dict[str, Any]],
    *,
    horizon_ns: int,
    tolerance_ns: int,
    calibration_end_ns: int,
    target_schema: int = 2,
) -> list[dict[str, Any]]:
    """Future observed log-price change, not an executable return or a strategy target."""
    integer(horizon_ns, "target horizon", 1)
    integer(tolerance_ns, "target tolerance")
    integer(calibration_end_ns, "calibration cutoff")
    if type(target_schema) is not int or target_schema not in (1, 2):
        raise ValidationError("unsupported observation target schema")
    by_pool: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        validate_feature_row(row)
        history = by_pool.setdefault(row["pool_id"], [])
        if history and row["decision_ns"] <= history[-1]["decision_ns"]:
            raise ValidationError("feature sequence must be increasing per pool")
        if row["feature_available_ns"] > row["decision_ns"]:
            raise ValidationError("feature leakage at decision")
        history.append(row)
    targets = []
    for history in by_pool.values():
        coverage_end = (
            history[-1]["decision_ns"]
            if target_schema == 1
            else max(r["input_available_ns"] or 0 for r in history)
        )
        for i, start in enumerate(history):
            end = start["decision_ns"] + horizon_ns
            reason, value, selected = None, None, None
            if not start["usable"]:
                reason = "invalid_start_observation"
            elif end > coverage_end:
                reason = "right_censored"
            else:
                for future in history[i + 1 :]:
                    if future["segment"] != start["segment"] or not future["usable"]:
                        reason = "observation_gap"
                        break
                    if future["decision_ns"] >= end:
                        if target_schema == 2 and (
                            future["input_available_ns"] < end or future["slot"] <= start["slot"]
                        ):
                            continue  # Publication of a frame is not receipt of a new snapshot.
                        if future["decision_ns"] - end > tolerance_ns:
                            reason = "target_late"
                        else:
                            selected = future
                            value = (
                                future["values"]["log_price_1_per_0"]
                                - start["values"]["log_price_1_per_0"]
                            )
                        break
                if selected is None and reason is None:
                    reason = "no_fresh_target_snapshot"
            partition = (
                "calibration"
                if start["decision_ns"] < calibration_end_ns
                else "engineering_evaluation"
            )
            if reason is not None:
                partition = "censored"
            elif (
                partition == "calibration"
                and selected is not None
                and selected["decision_ns"] >= calibration_end_ns
            ):
                partition = "purged"
            targets.append(
                dict(
                    sample_id=start["sample_id"],
                    horizon_end_ns=end,
                    target_observed_ns=selected["decision_ns"] if selected else None,
                    target_available_ns=selected["feature_available_ns"]
                    if selected
                    else history[-1]["decision_ns"],
                    value=value,
                    censor_reason=reason,
                    partition=partition,
                    definition="future_observed_log_reserve_price_change; not executable return",
                )
            )
            if target_schema == 2:
                targets[-1].update(
                    target_schema=2,
                    target_input_available_ns=selected["input_available_ns"] if selected else None,
                    target_slot=selected["slot"] if selected else None,
                )
    return targets
