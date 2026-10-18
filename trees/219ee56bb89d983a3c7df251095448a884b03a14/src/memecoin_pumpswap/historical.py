"""Exact historical ABI alternatives, preserving strict transfer/vault reconciliation."""

from memecoin_registry.contracts import digest

from .decoder import decode
from .historical_schema import PROFILES


def decode_historical(tx):
    accepted = {}
    reasons = set()
    for profile in PROFILES:
        result = decode(tx, profile)
        if result["status"] == "reconciled_candidate":
            # Distinct wire profiles must not yield conflicting economic facts.
            economic = [
                dict(path=s["path"], kind=s["kind"], roles=s["roles"], movement=s["movement"])
                for s in result["swaps"]
            ]
            accepted.setdefault(digest(economic), dict(result, profiles=[]))["profiles"].append(
                profile["profile_sha"]
            )
        else:
            reasons.add(result["status"])
    if len(accepted) > 1:
        raise ValueError("historical_profile_ambiguity")
    if accepted:
        return next(iter(accepted.values()))
    return dict(
        status="quarantined",
        swaps=[],
        profiles=[],
        reasons=sorted(reasons),
        training_eligible=False,
        execution_supported=False,
    )
