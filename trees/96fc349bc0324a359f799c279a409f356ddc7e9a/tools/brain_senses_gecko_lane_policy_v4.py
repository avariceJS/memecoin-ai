"""Frozen Gecko train-only policy v4 with an independently versioned schedule."""

from __future__ import annotations

import copy
from typing import Any

from brain_senses_gecko_lane_policy_v3 import (
    BAR_SECONDS,
    BUCKET_SECONDS,
    NS,
    POOL_ROWS,
    TRAIN_ENROLLMENT_SHA256,
    TRAIN_MANIFEST_SHA256,
    TRAIN_PLAN_SHA256,
    TRAIN_SELECTION_SHA256,
    TRAIN_SOURCE_PIN,
    canonical,
    capacity_contract,
    sha256,
)
from brain_senses_gecko_lane_policy_v3 import (
    SOURCE_PATHS as V3_SOURCE_PATHS,
)
from brain_senses_gecko_lane_policy_v3 import (
    compile_pinned_policy as compile_pinned_policy_v3,
)
from brain_senses_gecko_lane_policy_v3 import (
    protocol_constants as v3_protocol_constants,
)
from brain_senses_gecko_lane_policy_v3 import (
    verify_policy as verify_policy_v3,
)

SCHEMA = "brain-senses-gecko-prospective-lane-v4"
SCHEMA_VERSION = 4
POLICY_ID = "gecko-train-four-15m-v4"
SOURCE_PATHS = dict(V3_SOURCE_PATHS)


def _v4_protocol() -> dict[str, Any]:
    protocol = copy.deepcopy(v3_protocol_constants())
    # Preserve provider/query/cap semantics. Only the reviewed schedule changes.
    protocol["dispatch_earliest_seconds_after_close"] = 60
    protocol["dispatch_deadline_seconds_after_close_exclusive"] = 240
    protocol["latest_request_start_seconds_after_close_exclusive"] = 215
    protocol["durable_availability_deadline_seconds_after_close_exclusive"] = 240
    protocol["late_durable_result"] = "persist_as_unknown_no_retry"
    return protocol


def compile_policy_v4(policy_v3: dict[str, Any]) -> dict[str, Any]:
    """Derive v4 from the exact frozen v3 population/provider/cap contract."""
    verify_policy_v3(policy_v3)
    result = copy.deepcopy(policy_v3)
    result["schema"] = SCHEMA
    result["schema_version"] = SCHEMA_VERSION
    result["policy_id"] = POLICY_ID
    result["protocol"] = _v4_protocol()
    result["schedule_contract"] = {
        "dispatch_window": "[bucket_close+60s,bucket_close+240s)",
        "request_started": "[bucket_close+60s,bucket_close+215s)",
        "durable_availability": "strictly_before_bucket_close+240s",
        "late_result": "persist_raw_and_terminal_as_unknown;never_retry",
    }
    result.pop("policy_sha256", None)
    result["policy_sha256"] = sha256(canonical(result))
    verify_policy_v4(result)
    return result


def verify_policy_v4(policy: dict[str, Any]) -> None:
    expected_keys = {
        "schema",
        "schema_version",
        "policy_id",
        "source",
        "capacity",
        "pools",
        "protocol",
        "activation_authorized",
        "provider_io",
        "economic_admission",
        "schedule_contract",
        "policy_sha256",
    }
    if (
        not isinstance(policy, dict)
        or set(policy) != expected_keys
        or policy.get("schema") != SCHEMA
        or policy.get("schema_version") != SCHEMA_VERSION
        or policy.get("policy_id") != POLICY_ID
        or policy.get("source")
        != {
            "role": "train",
            "source_pin": "3d3ff36feb8a73b3130611d9a9c22d3121fdc4ed2bfc7e6df2d2503be49fd181",
            "enrollment_sha256": "21b56ae5727f6ee6166b595631ddab3b3bf60cbdb3ca599c15e6fba7e4dc6054",
            "role_manifest_sha256": (
                "9cd169f958e3bc4ea8aec4822dd5e2efcd68257f62a40119a88d19dc39eeb0db"
            ),
            "plan_sha256": "d872e0d8d8266b2dd22b1c2a8ef7e505a12b6f93ccdf82df9b7b6f206d7e7de8",
            "population_selection_sha256": (
                "4aa13b951e11569bb893672c595783482ee7a987f57b26c93fd601150de39172"
            ),
            "enrolled_train_count": 170,
            "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
            "protected_screen_fields": ["pool_id", "base_mint"],
            "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
            "quote_token_features": False,
        }
        or policy.get("capacity") != capacity_contract()
        or policy.get("pools") != list(POOL_ROWS)
        or policy.get("protocol") != _v4_protocol()
        or policy.get("schedule_contract")
        != {
            "dispatch_window": "[bucket_close+60s,bucket_close+240s)",
            "request_started": "[bucket_close+60s,bucket_close+215s)",
            "durable_availability": "strictly_before_bucket_close+240s",
            "late_result": "persist_raw_and_terminal_as_unknown;never_retry",
        }
        or policy.get("activation_authorized") is not False
        or policy.get("provider_io") is not False
        or policy.get("economic_admission") is not False
    ):
        raise ValueError("gecko_v4_policy_contract")
    copy = dict(policy)
    claimed = copy.pop("policy_sha256", None)
    if claimed != sha256(canonical(copy)):
        raise ValueError("gecko_v4_policy_hash")


def compile_pinned_policy(
    *,
    enrollment_bytes: bytes,
    role_manifest_bytes: bytes,
    plan_bytes: bytes,
    selection_bytes: bytes,
) -> dict[str, Any]:
    """Validate the frozen metadata bundle using v3, then compile v4 timing."""
    return compile_policy_v4(
        compile_pinned_policy_v3(
            enrollment_bytes=enrollment_bytes,
            role_manifest_bytes=role_manifest_bytes,
            plan_bytes=plan_bytes,
            selection_bytes=selection_bytes,
        )
    )


verify_policy = verify_policy_v4
protocol_constants = _v4_protocol


__all__ = [
    "BAR_SECONDS",
    "BUCKET_SECONDS",
    "POLICY_ID",
    "NS",
    "POOL_ROWS",
    "TRAIN_ENROLLMENT_SHA256",
    "TRAIN_MANIFEST_SHA256",
    "TRAIN_PLAN_SHA256",
    "TRAIN_SELECTION_SHA256",
    "TRAIN_SOURCE_PIN",
    "SCHEMA",
    "SCHEMA_VERSION",
    "compile_policy_v4",
    "compile_pinned_policy",
    "SOURCE_PATHS",
    "verify_policy",
    "verify_policy_v4",
    "canonical",
    "capacity_contract",
    "protocol_constants",
    "sha256",
]
