"""Versioned shadow information-support trigger; never consumes quota or promotes models."""

import math

from memecoin_registry.contracts import digest

POLICY = dict(
    version="information-support-v1",
    normal=dict(seconds=3600, decisions=1000, effective_pools=8, new_pool_days=8, disjoint=128),
    fast=dict(seconds=600, decisions=20000, effective_pools=50, new_pool_days=50, disjoint=1000),
    max_age_s=600,
    error_driven_selection=False,
    promotion=False,
)


def evaluate(evidence, now_ns, last_reservation_ns, *, quota_ok, source_ok, domain_stop):
    reasons = []
    if not quota_ok:
        reasons.append("quota")
    if not source_ok or domain_stop:
        reasons.append("source_or_domain_stop")
    required = ("as_of_ns", "previous_snapshot_sha", "current_snapshot_sha", "new_train_support")
    if not isinstance(evidence, dict) or any(k not in evidence for k in required):
        return dict(
            eligible=False,
            reasons=reasons + ["missing_support"],
            version=POLICY["version"],
            policy_sha=digest(POLICY),
            shadow_only=True,
        )
    age = now_ns - evidence["as_of_ns"]
    if not 0 <= age <= POLICY["max_age_s"] * 10**9:
        reasons.append("stale_or_future_support")
    for key in ("previous_snapshot_sha", "current_snapshot_sha"):
        pin = evidence[key]
        if (
            not isinstance(pin, str)
            or len(pin) != 64
            or any(c not in "0123456789abcdef" for c in pin)
        ):
            reasons.append("unbound_snapshot")
    if evidence["previous_snapshot_sha"] == evidence["current_snapshot_sha"]:
        reasons.append("unchanged_snapshot")
    s = evidence["new_train_support"]
    try:
        values = [
            s["new_pool_days"],
            s["added"]["decisions"],
            s["added"]["effective_pool_count"],
            s["added"]["disjoint_context_and_target_windows"],
        ]
        valid = all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values)
    except (KeyError, TypeError):
        valid = False
    if not valid:
        return dict(
            eligible=False,
            reasons=reasons + ["invalid_support"],
            version=POLICY["version"],
            policy_sha=digest(POLICY),
            shadow_only=True,
        )
    elapsed = (now_ns - last_reservation_ns) / 10**9
    branches = {}
    for name in ("normal", "fast"):
        limit = POLICY[name]
        checks = dict(
            interval=elapsed >= limit["seconds"],
            decisions=s["added"]["decisions"] >= limit["decisions"],
            effective_pools=s["added"]["effective_pool_count"] >= limit["effective_pools"],
            new_pool_days=s["new_pool_days"] >= limit["new_pool_days"],
            disjoint=s["added"]["disjoint_context_and_target_windows"] >= limit["disjoint"],
        )
        branches[name] = dict(eligible=all(checks.values()), checks=checks)
    if not any(v["eligible"] for v in branches.values()):
        reasons.append("insufficient_new_support")
    return dict(
        eligible=not reasons,
        reasons=reasons,
        branches=branches,
        version=POLICY["version"],
        policy_sha=digest(POLICY),
        evidence_sha=digest(evidence),
        shadow_only=True,
    )
