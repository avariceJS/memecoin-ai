"""Coverage of an explicit identity roster across declared providers.

Input rows and identity alignment are caller claims. This diagnostic counts
provider/identity presence, shared absence, provider-specific absence and
repeated provider/identity rows; it does not collect or verify observations.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from typing import Any


_PAYLOAD_KEYS = {"expected_ids", "providers", "observations"}


def _strings(values: Any, label: str) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
        raise ValueError(f"provider_roster_coverage_{label}")
    if len(set(values)) != len(values):
        raise ValueError(f"provider_roster_coverage_duplicate_{label}")
    return values


def _canonical(payload: dict) -> bytes:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("provider_roster_coverage_json_value") from exc


def analyze(payload: dict) -> dict:
    """Analyze presence over the caller supplied expected identity roster."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("provider_roster_coverage_payload")
    expected_ids = _strings(payload["expected_ids"], "expected_ids")
    providers = _strings(payload["providers"], "providers")
    observations = payload["observations"]
    if not isinstance(observations, list):
        raise ValueError("provider_roster_coverage_observations")
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    expected, declared = set(expected_ids), set(providers)
    present: dict[str, set[str]] = {provider: set() for provider in providers}
    rows: dict[tuple[str, str], int] = defaultdict(int)
    observation_ids: set[str] = set()
    for row in observations:
        if not isinstance(row, dict) or set(row) != {"provider", "id", "observation_id"}:
            raise ValueError("provider_roster_coverage_observation")
        provider, identity, observation_id = row["provider"], row["id"], row["observation_id"]
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider_roster_coverage_provider")
        if provider not in declared:
            raise ValueError("provider_roster_coverage_unknown_provider")
        if not isinstance(identity, str) or not identity:
            raise ValueError("provider_roster_coverage_id")
        if identity not in expected:
            raise ValueError("provider_roster_coverage_unknown_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError("provider_roster_coverage_observation_id")
        if observation_id in observation_ids:
            raise ValueError("provider_roster_coverage_duplicate_observation_id")
        observation_ids.add(observation_id)
        present[provider].add(identity)
        rows[(provider, identity)] += 1

    absent_by_provider = {p: sorted(expected - present[p]) for p in providers}
    shared_absent = sorted(identity for identity in expected_ids
                           if providers and all(identity not in present[p] for p in providers))
    provider_specific_absent = sorted(identity for identity in expected_ids
                                      if any(identity not in present[p] for p in providers)
                                      and not all(identity not in present[p] for p in providers))
    details = []
    for provider in sorted(providers):
        duplicates = sorted(identity for (p, identity), count in rows.items() if p == provider and count > 1)
        absent = absent_by_provider[provider]
        details.append({
            "provider": provider, "expected_count": len(expected_ids),
            "present_count": len(present[provider]), "absent_count": len(absent),
            "coverage_rate": len(present[provider]) / len(expected_ids) if expected_ids else None,
            "absent_ids": absent, "duplicate_count": len(duplicates),
            "duplicate_ids": duplicates,
        })
    pair_denominator = len(expected_ids) * len(providers)
    return {
        "schema": "brain-research-diagnostic.v1", "kind": "provider_roster_coverage",
        "input_sha256": digest,
        "summary": {
            "expected_id_count": len(expected_ids), "provider_count": len(providers),
            "observation_count": len(observations), "provider_identity_denominator": pair_denominator,
            "present_provider_identity_count": sum(len(ids) for ids in present.values()),
            "absent_provider_identity_count": sum(len(ids) for ids in absent_by_provider.values()),
            "shared_absent_count": len(shared_absent), "shared_absent_denominator": len(expected_ids),
            "shared_absent_rate": len(shared_absent) / len(expected_ids) if expected_ids else None,
            "provider_specific_absent_count": len(provider_specific_absent),
            "provider_specific_absent_denominator": len(expected_ids),
            "provider_specific_absent_rate": len(provider_specific_absent) / len(expected_ids) if expected_ids else None,
            "duplicate_count": sum(max(0, n - 1) for n in rows.values()),
            "has_expected_ids": bool(expected_ids), "has_providers": bool(providers),
        },
        "details": {"providers": details, "shared_absent_ids": shared_absent,
                    "provider_specific_absent_ids": provider_specific_absent},
        "limitations": [
            "Expected identities, provider labels, observation identities and row provenance are caller claims.",
            "Absence is measured only against the supplied roster; no identities or providers are inferred.",
            "Shared absence means absent from every declared provider; provider-specific absence means absent from some but not all.",
            "Repeated provider/identity rows are counted once as present and separately reported as duplicates.",
        ],
    }


def _reject_constant(_value: str) -> None:
    raise ValueError("provider_roster_coverage_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("provider_roster_coverage_duplicate_key")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc) or "provider_roster_coverage_invalid_input", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
