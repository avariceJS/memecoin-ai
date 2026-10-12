"""Offline compiler for a small, frozen GeckoTerminal train-only observation lane."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA = "brain-senses-gecko-prospective-lane-v3"
SCHEMA_VERSION = 3
NS = 1_000_000_000
BUCKET_SECONDS = 900
BAR_SECONDS = 300
POOL_LIMIT = 4

TRAIN_ENROLLMENT_SHA256 = "21b56ae5727f6ee6166b595631ddab3b3bf60cbdb3ca599c15e6fba7e4dc6054"
TRAIN_MANIFEST_SHA256 = "9cd169f958e3bc4ea8aec4822dd5e2efcd68257f62a40119a88d19dc39eeb0db"
TRAIN_PLAN_SHA256 = "d872e0d8d8266b2dd22b1c2a8ef7e505a12b6f93ccdf82df9b7b6f206d7e7de8"
TRAIN_SELECTION_SHA256 = "4aa13b951e11569bb893672c595783482ee7a987f57b26c93fd601150de39172"
TRAIN_SOURCE_PIN = "3d3ff36feb8a73b3130611d9a9c22d3121fdc4ed2bfc7e6df2d2503be49fd181"
TRAIN_ROLE = "train"
TRAIN_COUNT = 170
MAX_INTENTS_PER_ROLLING_30D = 11_520
MAX_RAW_BYTES_PER_ROLLING_30D = 960 * 1024**2
GECKO_RESIDENT_ALLOCATION_CEILING_BYTES = 16_000_000_000

POOL_ROWS = (
    {
        "pool_id": "DW24TdFi8cYSM85PCx25TDqkqwf94i9yfeChcBTjCSH2",
        "base_mint": "2ig5Dh1vTCqjTcvxSLC1ggLCMsx89cHiWQZAcN7xpump",
        "quote_mint": "So11111111111111111111111111111111111111112",
        "rank_sha256": "004e7143167499a1e2891a912c763bbecfca694ba201ca0dcb745d442c27cb35",
    },
    {
        "pool_id": "FBpUN77t8zT88aJ5L7hLQybEHq4zCz31Ph2yUWqmCRZ1",
        "base_mint": "Ed75fFB4HrJdiAehca97FvRadfWd1jhysqkxDMmnpump",
        "quote_mint": "So11111111111111111111111111111111111111112",
        "rank_sha256": "00d81cc6cd0329b61ae054a23929a878fbd7abb968d6ce6c941589d614596000",
    },
    {
        "pool_id": "6P6pepGxdf3AykMpMC3qkuogepf7uFEavAeCKpbEsStE",
        "base_mint": "DzNQ97zuE9YDDyMB1FArdjmL3kcL615x3EcKFQSbnUp",
        "quote_mint": "So11111111111111111111111111111111111111112",
        "rank_sha256": "015c2ca13fd216f4694247078a959dcdd7a94f56e0e43fe04010e757ec46c0df",
    },
    {
        "pool_id": "BkgieFGyJFDBfKcskkvrb2krAnLHTFgBQ6tsArxeXy4c",
        "base_mint": "8emTj7Ly5VcconbJX7swG4o1JCdUMb4xBVDgS4Cnpump",
        "quote_mint": "So11111111111111111111111111111111111111112",
        "rank_sha256": "019c4fbd813463c1261e9a3420b157c7b9e2c14e5361ac6af8d442ebaaa19188",
    },
)

SOURCE_PATHS = {
    "train_enrollment": (
        "/Volumes/Kingston/BrainData/brain/sparse-observation-reference-v1/"
        "independent-population-2026-10-08/train/enrollment.jsonl"
    ),
    "train_role_manifest": (
        "/Volumes/Kingston/BrainData/brain/sparse-observation-reference-v1/"
        "independent-population-2026-10-08/train/manifest.json"
    ),
    "train_plan": "work/brain-diverse-campaign-20261008/plan.json",
    "population_selection": "work/brain-diverse-campaign-20261008/population-selection.json",
}


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(raw: bytes, code: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{code}_duplicate_key")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{code}_json") from exc


def rank_pool(source_pin: str, pool_id: str) -> str:
    if not _is_sha(source_pin) or not isinstance(pool_id, str) or not pool_id:
        raise ValueError("gecko_rank_input")
    return sha256(source_pin.encode("utf-8") + b"\0" + pool_id.encode("utf-8"))


def rank_and_select(
    source_pin: str, rows: list[dict[str, Any]], *, limit: int = POOL_LIMIT
) -> list[dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= POOL_LIMIT:
        raise ValueError("gecko_pool_limit")
    seen: set[str] = set()
    ranked: list[dict[str, Any]] = []
    for row in rows:
        if set(row) != {"pool_id", "base_mint", "quote_mint"}:
            raise ValueError("gecko_population_row")
        pool = row["pool_id"]
        if not _safe_id(pool) or not _safe_id(row["base_mint"]) or not _safe_id(row["quote_mint"]):
            raise ValueError("gecko_population_identity")
        if pool in seen:
            raise ValueError("gecko_population_duplicate")
        seen.add(pool)
        ranked.append({**row, "rank_sha256": rank_pool(source_pin, pool)})
    ranked.sort(key=lambda item: (item["rank_sha256"], item["pool_id"]))
    return ranked[:limit]


def compile_pinned_policy(
    *,
    enrollment_bytes: bytes,
    role_manifest_bytes: bytes,
    plan_bytes: bytes,
    selection_bytes: bytes,
) -> dict[str, Any]:
    """Validate already-pinned metadata bytes and return the fixed, non-authorizing policy."""
    expected = (
        (enrollment_bytes, TRAIN_ENROLLMENT_SHA256, "enrollment"),
        (role_manifest_bytes, TRAIN_MANIFEST_SHA256, "role_manifest"),
        (plan_bytes, TRAIN_PLAN_SHA256, "plan"),
        (selection_bytes, TRAIN_SELECTION_SHA256, "selection"),
    )
    for raw, pin, name in expected:
        if sha256(raw) != pin:
            raise ValueError(f"gecko_{name}_pin")

    manifest = strict_json(role_manifest_bytes, "gecko_manifest")
    if (
        manifest.get("schema") != "brain-senses-sparse-observation-role-v1"
        or manifest.get("schema_version") != 1
        or manifest.get("role") != TRAIN_ROLE
        or manifest.get("files", {}).get("enrollment.jsonl") != TRAIN_ENROLLMENT_SHA256
        or manifest.get("source_pins", {}).get("source_pin") != TRAIN_SOURCE_PIN
    ):
        raise ValueError("gecko_role_manifest")

    enrollment = _jsonl(enrollment_bytes, "gecko_enrollment")
    if len(enrollment) != TRAIN_COUNT or any(row.get("role") != TRAIN_ROLE for row in enrollment):
        raise ValueError("gecko_train_enrollment")
    enrollment_ids = [row.get("entity_id") for row in enrollment]
    if (
        any(not _safe_id(entity_id) for entity_id in enrollment_ids)
        or len(set(enrollment_ids)) != TRAIN_COUNT
    ):
        raise ValueError("gecko_train_enrollment_ids")

    plan = strict_json(plan_bytes, "gecko_plan")
    cases = plan.get("cases")
    if not isinstance(cases, list) or len(cases) != 307:
        raise ValueError("gecko_population_plan")
    plan_train = [case for case in cases if case.get("role") == TRAIN_ROLE]
    if len(plan_train) != TRAIN_COUNT or {case.get("pool") for case in plan_train} != set(
        enrollment_ids
    ):
        raise ValueError("gecko_train_plan_join")
    if any(case.get("training_eligible") is not False for case in plan_train):
        raise ValueError("gecko_historical_eligibility_confusion")

    selection = strict_json(selection_bytes, "gecko_selection")
    if (
        selection.get("plan") != TRAIN_PLAN_SHA256
        or selection.get("enrolled") != len(cases)
        or not isinstance(selection.get("blocked_pools"), list)
        or not isinstance(selection.get("blocked_mints"), list)
    ):
        raise ValueError("gecko_population_selection")
    blocked_pools = set(selection["blocked_pools"])
    blocked_mints = set(selection["blocked_mints"])
    train_rows: list[dict[str, Any]] = []
    for case in plan_train:
        pool, base, quote = case.get("pool"), case.get("base_mint"), case.get("quote_mint")
        if not all(_safe_id(value) for value in (pool, base, quote)):
            raise ValueError("gecko_train_identity")
        if pool in blocked_pools or base in blocked_mints:
            raise ValueError("gecko_protected_or_blocked_identity")
        train_rows.append({"pool_id": pool, "base_mint": base, "quote_mint": quote})

    selected = rank_and_select(TRAIN_SOURCE_PIN, train_rows)
    if selected != list(POOL_ROWS):
        raise ValueError("gecko_frozen_selection_mismatch")
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "policy_id": "gecko-train-four-15m-v3",
        "source": {
            "role": TRAIN_ROLE,
            "source_pin": TRAIN_SOURCE_PIN,
            "enrollment_sha256": TRAIN_ENROLLMENT_SHA256,
            "role_manifest_sha256": TRAIN_MANIFEST_SHA256,
            "plan_sha256": TRAIN_PLAN_SHA256,
            "population_selection_sha256": TRAIN_SELECTION_SHA256,
            "enrolled_train_count": TRAIN_COUNT,
            "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
            "protected_screen_fields": ["pool_id", "base_mint"],
            "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
            "quote_token_features": False,
        },
        "capacity": {
            "coordinator_schema": "brain-senses-capacity-coordinator-policy-v2",
            "coordinator_version": 2,
            "service": "gecko",
            "resident_allocation_ceiling_bytes": GECKO_RESIDENT_ALLOCATION_CEILING_BYTES,
            "coordinator_source_binding": "runtime_release_manifest_required",
            "admission_required_before_every_intent": True,
            "calendar_expiry": "none",
        },
        "pools": selected,
        "protocol": protocol_constants(),
        "activation_authorized": False,
        "provider_io": False,
        "economic_admission": False,
    }
    payload["policy_sha256"] = sha256(canonical(payload))
    return payload


def verify_policy(policy: dict[str, Any]) -> None:
    if (
        not isinstance(policy, dict)
        or set(policy)
        != {
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
            "policy_sha256",
        }
        or policy.get("schema") != SCHEMA
        or policy.get("schema_version") != 3
    ):
        raise ValueError("gecko_policy_schema")
    expected_source = {
        "role": TRAIN_ROLE,
        "source_pin": TRAIN_SOURCE_PIN,
        "enrollment_sha256": TRAIN_ENROLLMENT_SHA256,
        "role_manifest_sha256": TRAIN_MANIFEST_SHA256,
        "plan_sha256": TRAIN_PLAN_SHA256,
        "population_selection_sha256": TRAIN_SELECTION_SHA256,
        "enrolled_train_count": TRAIN_COUNT,
        "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
        "protected_screen_fields": ["pool_id", "base_mint"],
        "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
        "quote_token_features": False,
    }
    if (
        policy.get("policy_id") != "gecko-train-four-15m-v3"
        or policy.get("source") != expected_source
        or policy.get("capacity") != capacity_contract()
        or policy.get("activation_authorized") is not False
        or policy.get("provider_io") is not False
        or policy.get("economic_admission") is not False
    ):
        raise ValueError("gecko_policy_authority")
    copy = dict(policy)
    claimed = copy.pop("policy_sha256", None)
    if claimed != sha256(canonical(copy)):
        raise ValueError("gecko_policy_hash")
    if policy.get("pools") != list(POOL_ROWS) or policy.get("protocol") != protocol_constants():
        raise ValueError("gecko_policy_contract")


def protocol_constants() -> dict[str, Any]:
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
        "bucket_seconds": BUCKET_SECONDS,
        "bar_seconds": BAR_SECONDS,
        "dispatch_earliest_seconds_after_close": 60,
        "dispatch_deadline_seconds_after_close_exclusive": 180,
        "minimum_request_spacing_seconds": 12,
        "maximum_dispatches_per_rolling_60_seconds": 5,
        "maximum_intents_per_pool_bucket": 1,
        "new_open_window": "[bucket_close-900s,bucket_close)",
        "revision_window": "[bucket_close-1800s,bucket_close-900s)",
        "quote_token_features": False,
        "price_namespace": "usd_base_only",
        "maximum_intents_per_utc_day": 384,
        "maximum_raw_bytes_per_utc_day": 32 * 1024**2,
        "rolling_accounting_window_days": 30,
        "maximum_intents_per_rolling_30d": MAX_INTENTS_PER_ROLLING_30D,
        "maximum_raw_bytes_per_rolling_30d": MAX_RAW_BYTES_PER_ROLLING_30D,
        "calendar_expiry": "none",
        "maximum_raw_response_bytes": 16 * 1024,
        "peak_storage_bytes": 32 * 1024**2,
        "metadata_storage_bytes": 16 * 1024**2,
        "physical_reserve_per_request_bytes": 80 * 1024,
        "physical_reserve_components": {"raw_bytes": 16 * 1024, "metadata_bytes": 64 * 1024},
        "timeout_seconds": 20,
        "persistence_margin_seconds": 5,
        "cooldowns": {
            "http_429_base_seconds": 3600,
            "http_429_retry_after_max_seconds": 86400,
            "malformed_response_seconds": 3600,
            "http_5xx_seconds": 900,
            "transport_uncertain_seconds": 900,
            "generic_4xx": "pause_until_contract_access_review",
        },
        "no_retry": True,
        "no_backfill": True,
    }


def capacity_contract() -> dict[str, Any]:
    """Pin the required v2 coordinator; live admission remains an injected gate."""
    return {
        "coordinator_schema": "brain-senses-capacity-coordinator-policy-v2",
        "coordinator_version": 2,
        "service": "gecko",
        "resident_allocation_ceiling_bytes": GECKO_RESIDENT_ALLOCATION_CEILING_BYTES,
        "coordinator_source_binding": "runtime_release_manifest_required",
        "admission_required_before_every_intent": True,
        "calendar_expiry": "none",
    }


def _jsonl(raw: bytes, code: str) -> list[dict[str, Any]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{code}_utf8") from exc
    if not lines or any(not line for line in lines):
        raise ValueError(f"{code}_lines")
    rows = [strict_json(line.encode(), code) for line in lines]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{code}_row")
    return rows


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9]{20,64}", value))


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))
