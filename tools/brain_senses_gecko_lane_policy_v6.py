"""Source-pinned local-budget policy for the Gecko forward-only v6 epoch."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tools import brain_senses_gecko_lane_policy_v3 as frozen_v3
from tools import brain_senses_gecko_rotating_scout_v1 as scout

SCHEMA = "brain-senses-gecko-capture-policy-v6"
SCHEMA_VERSION = 6
POLICY_ID = "gecko-train-six-15m-rotating-scout-v6"
EPOCH_SCHEMA = "brain-senses-gecko-sampling-epoch-v6"
SOURCE_PIN = "3d3ff36feb8a73b3130611d9a9c22d3121fdc4ed2bfc7e6df2d2503be49fd181"
POPULATION_SHA256 = "938ee425361c27e03f18a123bcdf3d35edbbfafe719c12324c517b408c1cbc8d"

MAX_INTENTS_PER_UTC_DAY = 576
MAX_INTENTS_PER_ROLLING_30D = 17_280
MAX_RAW_BYTES_PER_UTC_DAY = 10 * 1024**2
MAX_RAW_BYTES_PER_ROLLING_30D = 300 * 1024**2
MAX_PHYSICAL_RESERVE_PER_ROLLING_30D = 2 * 1024**3
MAX_RESIDENT_BYTES = 16 * 1024**3
MIN_FREE_BYTES = 50 * 1024**3
MAX_RESPONSE_BYTES = 16 * 1024
PHYSICAL_RESERVE_PER_INTENT = 80 * 1024
REQUEST_TIMEOUT_SECONDS = 20
PERSISTENCE_MARGIN_SECONDS = 5
NS = 1_000_000_000
BUCKET_NS = 900 * NS


class PolicyError(ValueError):
    """Invalid v6 population binding or local safety policy."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sampling_epoch_contract() -> dict[str, Any]:
    return {
        "schema": EPOCH_SCHEMA,
        "sampling_policy_id": scout.POLICY_ID,
        "cursor_in": 0,
        "retention": [],
        "predecessor_cursor_disposition": "not_carried_new_epoch",
        "reset_reason": "explicit_new_v6_sampling_epoch",
        "old_gaps_disclosed_separately": True,
        "reset_once_only": True,
    }


def _protocol() -> dict[str, Any]:
    return {
        "provider": "geckoterminal",
        "base_url": "https://api.geckoterminal.com",
        "path_template": "/api/v2/networks/solana/pools/{pool_id}/ohlcv/minute",
        "query": {
            "aggregate": "5",
            "currency": "usd",
            "token": "base",
            "include_empty_intervals": "false",
            "limit": "6",
            "before_timestamp": "bucket_close_epoch_seconds",
        },
        "headers": {
            "Accept": "application/json;version=20230203",
            "User-Agent": "memecoin-ai-research/0.1",
        },
        "bucket_seconds": 900,
        "bar_seconds": 300,
        "dispatch_earliest_seconds_after_close": 60,
        "dispatch_end_exclusive_seconds_after_close": 300,
        "request_start_exclusive_seconds_after_close": 270,
        "availability_exclusive_seconds_after_close": 300,
        "minimum_dispatch_spacing_seconds": 12,
        "maximum_dispatches_per_rolling_60_seconds": 5,
        "maximum_intents_per_pool_bucket": 1,
        "new_open_window": "[bucket_close-900s,bucket_close)",
        "revision_window": "[bucket_close-1800s,bucket_close-900s)",
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "persistence_margin_seconds": PERSISTENCE_MARGIN_SECONDS,
        "maximum_response_bytes": MAX_RESPONSE_BYTES,
        "no_retry": True,
        "no_backfill": True,
    }


def _source(rows: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "role": "train",
        "source_pin": SOURCE_PIN,
        "source_sha256": {
            "train_enrollment": frozen_v3.TRAIN_ENROLLMENT_SHA256,
            "train_role_manifest": frozen_v3.TRAIN_MANIFEST_SHA256,
            "train_plan": frozen_v3.TRAIN_PLAN_SHA256,
            "population_selection": frozen_v3.TRAIN_SELECTION_SHA256,
        },
        "enrolled_train_count": 170,
        "population_sha256": scout.population_digest(rows),
        "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
        "protected_screen_fields": ["pool_id", "base_mint"],
        "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
        "quote_token_features": False,
    }


def compile_policy_v6(
    *,
    enrollment_bytes: bytes,
    role_manifest_bytes: bytes,
    plan_bytes: bytes,
    selection_bytes: bytes,
) -> dict[str, Any]:
    """Compile v6 from the exact prequalified 170-row train source bundle."""
    try:
        population = scout.compile_pinned_population(
            enrollment_bytes=enrollment_bytes,
            role_manifest_bytes=role_manifest_bytes,
            plan_bytes=plan_bytes,
            selection_bytes=selection_bytes,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyError("v6_population_source_invalid") from exc
    rows = population.get("population")
    if (
        population.get("policy_id") != scout.POLICY_ID
        or population.get("population_count") != 170
        or population.get("population_sha256") != POPULATION_SHA256
        or type(rows) is not list
        or len(rows) != 170
    ):
        raise PolicyError("v6_population_pin_mismatch")
    core = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "source": _source(rows),
        "population": rows,
        "sampling_epoch": sampling_epoch_contract(),
        "protocol": _protocol(),
        "capacity": {
            "scope": "v6_local_gecko_only;no_global_provider_census_claim",
            "maximum_intents_per_utc_day": MAX_INTENTS_PER_UTC_DAY,
            "maximum_intents_per_rolling_30d": MAX_INTENTS_PER_ROLLING_30D,
            "maximum_raw_bytes_per_utc_day": MAX_RAW_BYTES_PER_UTC_DAY,
            "maximum_raw_bytes_per_rolling_30d": MAX_RAW_BYTES_PER_ROLLING_30D,
            "maximum_physical_reserve_per_rolling_30d": MAX_PHYSICAL_RESERVE_PER_ROLLING_30D,
            "physical_reserve_per_intent": PHYSICAL_RESERVE_PER_INTENT,
            "resident_allocation_ceiling_bytes": MAX_RESIDENT_BYTES,
            "minimum_free_bytes": MIN_FREE_BYTES,
            "conservative_prior_gecko_usage_offset_required": True,
            "prior_usage_claim": "known_gecko_attempts_only;not_global_account_usage",
        },
        "activation_authorized": False,
        "provider_io": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return {**core, "policy_sha256": sha256(canonical(core))}


def verify_policy_v6(
    policy: dict[str, Any],
    *,
    enrollment_bytes: bytes,
    role_manifest_bytes: bytes,
    plan_bytes: bytes,
    selection_bytes: bytes,
) -> None:
    expected = compile_policy_v6(
        enrollment_bytes=enrollment_bytes,
        role_manifest_bytes=role_manifest_bytes,
        plan_bytes=plan_bytes,
        selection_bytes=selection_bytes,
    )
    if type(policy) is not dict or canonical(policy) != canonical(expected):
        raise PolicyError("v6_policy_source_or_hash_mismatch")


def verify_policy_hash(policy: dict[str, Any]) -> None:
    """Validate structure/hash before source bytes are available; grants no authority."""
    expected_keys = {
        "schema",
        "schema_version",
        "policy_id",
        "source",
        "population",
        "sampling_epoch",
        "protocol",
        "capacity",
        "activation_authorized",
        "provider_io",
        "market_source_qualified",
        "training_admission",
        "economic_admission",
        "policy_sha256",
    }
    source_keys = {
        "role",
        "source_pin",
        "source_sha256",
        "enrolled_train_count",
        "population_sha256",
        "ranking",
        "protected_screen_fields",
        "quote_mint_rule",
        "quote_token_features",
    }
    expected_source_hashes = {
        "train_enrollment": frozen_v3.TRAIN_ENROLLMENT_SHA256,
        "train_role_manifest": frozen_v3.TRAIN_MANIFEST_SHA256,
        "train_plan": frozen_v3.TRAIN_PLAN_SHA256,
        "population_selection": frozen_v3.TRAIN_SELECTION_SHA256,
    }
    if type(policy) is not dict or set(policy) != expected_keys:
        raise PolicyError("v6_policy_shape")
    source = policy.get("source")
    if (
        policy.get("schema") != SCHEMA
        or policy.get("schema_version") != SCHEMA_VERSION
        or policy.get("policy_id") != POLICY_ID
        or policy.get("activation_authorized") is not False
        or policy.get("provider_io") is not False
        or policy.get("market_source_qualified") is not False
        or policy.get("training_admission") is not False
        or policy.get("economic_admission") is not False
        or type(source) is not dict
        or set(source) != source_keys
        or source.get("role") != "train"
        or source.get("source_pin") != SOURCE_PIN
        or source.get("source_sha256") != expected_source_hashes
        or source.get("enrolled_train_count") != 170
        or source.get("population_sha256") != POPULATION_SHA256
        or source.get("ranking") != "sha256(utf8(source_pin + NUL + pool_id)),ascending"
        or source.get("protected_screen_fields") != ["pool_id", "base_mint"]
        or source.get("quote_mint_rule") != "preserve_and_match_pinned_pool_metadata_only"
        or source.get("quote_token_features") is not False
        or type(policy.get("population")) is not list
        or len(policy["population"]) != 170
        or scout.population_digest(policy["population"]) != POPULATION_SHA256
        or scout.pool_order(policy["population"], SOURCE_PIN) != policy["population"]
        or policy.get("sampling_epoch") != sampling_epoch_contract()
        or policy.get("protocol") != _protocol()
        or policy.get("capacity")
        != {
            "scope": "v6_local_gecko_only;no_global_provider_census_claim",
            "maximum_intents_per_utc_day": MAX_INTENTS_PER_UTC_DAY,
            "maximum_intents_per_rolling_30d": MAX_INTENTS_PER_ROLLING_30D,
            "maximum_raw_bytes_per_utc_day": MAX_RAW_BYTES_PER_UTC_DAY,
            "maximum_raw_bytes_per_rolling_30d": MAX_RAW_BYTES_PER_ROLLING_30D,
            "maximum_physical_reserve_per_rolling_30d": MAX_PHYSICAL_RESERVE_PER_ROLLING_30D,
            "physical_reserve_per_intent": PHYSICAL_RESERVE_PER_INTENT,
            "resident_allocation_ceiling_bytes": MAX_RESIDENT_BYTES,
            "minimum_free_bytes": MIN_FREE_BYTES,
            "conservative_prior_gecko_usage_offset_required": True,
            "prior_usage_claim": "known_gecko_attempts_only;not_global_account_usage",
        }
    ):
        raise PolicyError("v6_policy_shape")
    core = dict(policy)
    claimed = core.pop("policy_sha256", None)
    if claimed != sha256(canonical(core)):
        raise PolicyError("v6_policy_hash")


__all__ = [
    "BUCKET_NS",
    "EPOCH_SCHEMA",
    "MAX_INTENTS_PER_UTC_DAY",
    "MAX_INTENTS_PER_ROLLING_30D",
    "MAX_RAW_BYTES_PER_UTC_DAY",
    "MAX_RAW_BYTES_PER_ROLLING_30D",
    "MAX_PHYSICAL_RESERVE_PER_ROLLING_30D",
    "MAX_RESIDENT_BYTES",
    "MIN_FREE_BYTES",
    "MAX_RESPONSE_BYTES",
    "PHYSICAL_RESERVE_PER_INTENT",
    "NS",
    "POLICY_ID",
    "POPULATION_SHA256",
    "PolicyError",
    "SCHEMA",
    "SCHEMA_VERSION",
    "SOURCE_PIN",
    "canonical",
    "compile_policy_v6",
    "sampling_epoch_contract",
    "sha256",
    "verify_policy_hash",
    "verify_policy_v6",
]
