"""Pure standard-library quota admission and recovery decisions."""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime


def _timestamp(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return None
        return dt.timestamp()
    if isinstance(value, (int, float)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp() if dt.tzinfo is not None else None
        except (ValueError, OverflowError):
            return None
    return None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0 <= value <= 100 else None


def _limits(rate_limits):
    if not isinstance(rate_limits, Mapping) or rate_limits.get("ordinaryUsageAllowed") is not True:
        return None
    raw = rate_limits.get("rateLimitsByLimitId")
    if not isinstance(raw, (Mapping, list, tuple)) or not raw:
        return None
    items = (
        list(raw.items()) if isinstance(raw, Mapping) else [(str(i), v) for i, v in enumerate(raw)]
    )
    buckets = []
    for limit_id, limit in items:
        if not isinstance(limit, Mapping) or limit_id is None or str(limit_id) == "":
            return None
        if not {
            "primary",
            "secondary",
            "credits",
            "rateLimitReachedType",
            "spendControlReached",
        }.issubset(limit):
            return None
        if type(limit["spendControlReached"]) is not bool:
            return None
        primary = limit.get("primary")
        if not isinstance(primary, Mapping):
            return None
        if any(primary.get(k) is True for k in ("limitReached", "reached")):
            return None
        primary_pct = _number(primary.get("usedPercent"))
        if primary_pct is None:
            return None
        buckets.append((f"{limit_id}:primary", primary_pct, primary.get("resetsAt")))
        secondary = limit.get("secondary")
        if secondary is not None:
            if not isinstance(secondary, Mapping):
                return None
            if any(secondary.get(k) is True for k in ("limitReached", "reached")):
                return None
            secondary_pct = _number(secondary.get("usedPercent"))
            if secondary_pct is None:
                return None
            buckets.append((f"{limit_id}:secondary", secondary_pct, secondary.get("resetsAt")))
        reached = limit.get("rateLimitReachedType")
        if (
            reached is not None
            or limit.get("spendControlReached") is True
            or limit.get("providerReached") is True
        ):
            return None
        credits = limit.get("credits")
        if not isinstance(credits, Mapping):
            return None
        balance = credits.get("balance")
        if credits.get("hasCredits") is not False or credits.get("unlimited") is not False:
            return None
        if isinstance(balance, bool) or balance not in (None, "0", 0, 0.0):
            return None
    if not buckets or len({identity for identity, _, _ in buckets}) != len(buckets):
        return None
    if rate_limits.get("spendControlReached") is True or rate_limits.get("providerReached") is True:
        return None
    return sorted(buckets)


def _historical_limits(rate_limits):
    """Read historical bucket identities and percentages without access eligibility."""
    if not isinstance(rate_limits, Mapping):
        return None
    raw = rate_limits.get("rateLimitsByLimitId")
    if not isinstance(raw, (Mapping, list, tuple)) or not raw:
        return None
    items = (
        list(raw.items()) if isinstance(raw, Mapping) else [(str(i), v) for i, v in enumerate(raw)]
    )
    buckets = []
    for limit_id, limit in items:
        if not isinstance(limit, Mapping) or limit_id is None or str(limit_id) == "":
            return None
        primary = limit.get("primary")
        if not isinstance(primary, Mapping):
            return None
        primary_pct = _number(primary.get("usedPercent"))
        if primary_pct is None:
            return None
        buckets.append((f"{limit_id}:primary", primary_pct, primary.get("resetsAt")))
        secondary = limit.get("secondary")
        if secondary is not None:
            if not isinstance(secondary, Mapping):
                return None
            secondary_pct = _number(secondary.get("usedPercent"))
            if secondary_pct is None:
                return None
            buckets.append((f"{limit_id}:secondary", secondary_pct, secondary.get("resetsAt")))
    if not buckets or len({identity for identity, _, _ in buckets}) != len(buckets):
        return None
    return sorted(buckets)


def _decision(rate_limits, observed_at, now, active_teams, stop, reserve_points, reserve_evidence):
    if type(stop) is not bool:
        return None, "invalid_stop_evidence", 300
    n = _timestamp(now)
    observed = _timestamp(observed_at)
    if n is None or observed is None or observed > n:
        return None, "invalid_observation", 300
    if (
        isinstance(active_teams, bool)
        or not isinstance(active_teams, int)
        or not 0 <= active_teams <= 2
    ):
        return None, "invalid_active_teams", 300
    if (
        isinstance(reserve_points, bool)
        or not isinstance(reserve_points, int)
        or reserve_points not in (1, 2)
    ):
        return None, "invalid_reserve", 300
    buckets = _limits(rate_limits)
    if buckets is None:
        return None, "invalid_or_missing_quota_evidence", 300
    usage = max(pct for _, pct, _ in buckets)
    freshness = max(0, n - observed)
    poll = 60 if usage >= 95 else 300
    if freshness > poll:
        return None, "stale_quota_evidence", poll
    if stop:
        return {"slots": 0, "reason": "stop", "poll_seconds": poll, "reduced": True}, None, poll
    if usage >= 99:
        slots = 0
        reason = "quota_critical"
    elif usage >= 97:
        slots = max(0, 1 - active_teams)
        reason = "quota_high"
    else:
        slots = 2 - active_teams
        reason = "capacity_available"
    if reserve_points == 2 and not (isinstance(reserve_evidence, str) and reserve_evidence.strip()):
        slots = 0
        reason = "reserve_not_evidenced"
    elif usage >= 100 - reserve_points:
        slots = 0
        reason = "reserve_threshold"
    return (
        {
            "slots": slots,
            "reason": reason,
            "poll_seconds": poll,
            "reduced": slots < 2 - active_teams,
        },
        None,
        poll,
    )


def choose_quota_capacity(
    rate_limits,
    *,
    observed_at,
    now,
    active_teams=0,
    stop=False,
    reserve_points=1,
    reserve_evidence=None,
):
    """Return a conservative slot decision from one quota observation."""
    result, error, poll = _decision(
        rate_limits, observed_at, now, active_teams, stop, reserve_points, reserve_evidence
    )
    if result is not None:
        return result
    return {"slots": 0, "reason": error, "poll_seconds": poll, "reduced": True}


def reset_recovery_decision(
    paused_reading, readings, *, now, state_reconciled=False, other_stop_reasons=()
):
    """Require a confirmed two sample restoration before permitting caller resume."""

    def denied(reason):
        return {"ready": False, "reason": reason}

    if state_reconciled is not True:
        return denied("state_not_reconciled")
    if other_stop_reasons:
        return denied("other_stop_reasons")
    if (
        not isinstance(paused_reading, Mapping)
        or not isinstance(readings, Sequence)
        or isinstance(readings, (str, bytes))
        or len(readings) != 2
    ):
        return denied("insufficient_readings")
    now_ts = _timestamp(now)
    paused = _historical_limits(paused_reading.get("rate_limits"))
    paused_at = _timestamp(paused_reading.get("observed_at"))
    if now_ts is None or paused is None or paused_at is None or paused_at > now_ts:
        return denied("invalid_paused_evidence")
    first, last = readings[0], readings[1]
    if not isinstance(first, Mapping) or not isinstance(last, Mapping):
        return denied("invalid_reading")
    validated = []
    for reading in (first, last):
        at = _timestamp(reading.get("observed_at"))
        buckets = _limits(reading.get("rate_limits"))
        if at is None or at > now_ts or buckets is None:
            return denied("invalid_reading")
        usage = max(pct for _, pct, _ in buckets)
        poll = 60 if usage >= 95 else 300
        if now_ts - at > poll + 60:
            return denied("stale_reading")
        validated.append((at, buckets, usage, poll))
    (t1, b1, u1, poll1), (t2, b2, u2, _poll2) = validated
    if t2 - t1 < 60:
        return denied("readings_too_close")
    if t2 - t1 > poll1 + 60 or now_ts - t2 > validated[1][3]:
        return denied("stale_reading")
    if t1 < paused_at or u1 >= 99 or u2 >= 99:
        return denied("capacity_not_restored")
    ids_paused = {i for i, _, _ in paused}
    ids1 = {i for i, _, _ in b1}
    ids2 = {i for i, _, _ in b2}
    if ids_paused != ids1 or ids1 != ids2:
        return denied("bucket_identity_changed")
    pmap = {i: pct for i, pct, _ in paused}
    m1 = {i: pct for i, pct, _ in b1}
    m2 = {i: pct for i, pct, _ in b2}
    restored = any(m1[i] < pmap[i] and m2[i] < pmap[i] for i in ids1)
    if not restored:
        return denied("no_observed_restoration")
    # A changed reset window signature is corroborating evidence only when usage
    # also fell; reset timestamp prediction by itself never grants recovery.
    if t2 <= t1:
        return denied("restoration_not_persistent")
    return {"ready": True, "reason": "capacity_restored"}
