"""Offline contract and replay for prospective GeckoTerminal Solana pool discovery.

The source is GeckoTerminal's provider-indexed latest-pools feed. Its pages do
not form an atomic or complete Solana birth census. This module performs no
network or filesystem I/O and never reads price, volume, liquidity, or activity
attributes when enrolling a syntactically valid pool.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import pairwise
from typing import Any

SCHEMA = "brain-senses-gecko-new-pool-discovery-v1"
POLICY_SCHEMA = f"{SCHEMA}-policy"
PLAN_SCHEMA = f"{SCHEMA}-scan-plan"
PAGE_SCHEMA = f"{SCHEMA}-page-slot"
RECEIPT_SCHEMA = f"{SCHEMA}-scan-receipt"
NETWORK = "solana"
ENDPOINT = "/api/v2/networks/solana/new_pools"
API_ACCEPT = "application/json;version=20230203"
INCLUDE = "base_token,quote_token,dex"
NS = 1_000_000_000
MAX_PAGE = 10
MAX_ROWS_PER_PAGE = 4_096
MAX_INCLUDED_RESOURCES_PER_PAGE = 4_096
MAX_RAW_BYTES_PER_PAGE = 512 * 1024
MAX_SCAN_COUNT = 864
SCAN_EXPIRY_NS = 60 * NS
PHYSICAL_RESERVE_MULTIPLIER = 5
MIN_PAGE_SPACING_NS = 12 * 1_000_000_000
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58 = frozenset(BASE58_ALPHABET)
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
SCAN_ID_RE = re.compile(r"[a-zA-Z0-9._:-]{1,128}\Z")


class DiscoveryError(ValueError):
    """Invalid discovery policy, page, or replay binding."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_hash(value: Any) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def _base58_pubkey(value: Any) -> bool:
    if (
        type(value) is not str
        or not 32 <= len(value) <= 44
        or any(ch not in BASE58 for ch in value)
    ):
        return False
    number = 0
    for char in value:
        number = number * 58 + BASE58_ALPHABET.index(char)
    decoded = (number.bit_length() + 7) // 8
    decoded += len(value) - len(value.lstrip("1"))
    return decoded == 32


def compile_policy(
    *,
    first_scan_ns: int,
    scan_interval_seconds: int,
    pages: int,
    page_spacing_seconds: int,
    sample_numerator: int,
    sample_denominator: int,
    max_raw_bytes_per_page: int = MAX_RAW_BYTES_PER_PAGE,
    max_scan_count: int = MAX_SCAN_COUNT,
    source_spec_sha256: str,
) -> dict[str, Any]:
    """Freeze a fixed scan/page schedule and deterministic follow-up sampling rate."""
    if (
        type(first_scan_ns) is not int
        or first_scan_ns <= 0
        or first_scan_ns % NS
        or type(scan_interval_seconds) is not int
        or not 60 <= scan_interval_seconds <= 86_400
        or 86_400 % scan_interval_seconds
        or first_scan_ns % (scan_interval_seconds * NS)
        or type(max_scan_count) is not int
        or not 1 <= max_scan_count <= MAX_SCAN_COUNT
        or type(pages) is not int
        or not 1 <= pages <= MAX_PAGE
        or type(page_spacing_seconds) is not int
        or page_spacing_seconds * NS < MIN_PAGE_SPACING_NS
        or (pages - 1) * page_spacing_seconds * NS >= SCAN_EXPIRY_NS
        or min(pages, (60 // page_spacing_seconds) + 1) > 5
        or type(sample_numerator) is not int
        or type(sample_denominator) is not int
        or not 0 <= sample_numerator <= sample_denominator <= 1_000_000
        or type(max_raw_bytes_per_page) is not int
        or not 1_024 <= max_raw_bytes_per_page <= MAX_RAW_BYTES_PER_PAGE
        or not _is_hash(source_spec_sha256)
    ):
        raise DiscoveryError("discovery_policy_input")
    core = {
        "schema": POLICY_SCHEMA,
        "schema_version": 1,
        "source": "geckoterminal_provider_indexed_latest_pools",
        "network": NETWORK,
        "endpoint": ENDPOINT,
        "accept": API_ACCEPT,
        "include": INCLUDE,
        "page_range": list(range(1, pages + 1)),
        "first_scan_ns": first_scan_ns,
        "scan_interval_seconds": scan_interval_seconds,
        "max_scan_count": max_scan_count,
        "lease_duration_seconds": max_scan_count * scan_interval_seconds,
        "scan_expiry_seconds": SCAN_EXPIRY_NS // NS,
        "page_spacing_seconds": page_spacing_seconds,
        "request_policy": "one_attempt_per_scan_page_no_retry_or_backfill",
        "enrollment_policy": "all_syntactically_valid_pool_ids_before_content_filters",
        "pool_created_at_semantics": (
            "provider_assertion_not_verified_chain_birth_or_historical_availability"
        ),
        "missing_page_semantics": "unknown_discovery_exposure_no_zero_or_empty_inference",
        "disappearance_semantics": "no_death_inference",
        "sample": {
            "method": "sha256(policy_sha256 || NUL || network || NUL || pool_id) threshold",
            "numerator": sample_numerator,
            "denominator": sample_denominator,
            "population": "first_seen_valid_pool_ids_in_observed_scan_cohort",
            "replacement": False,
        },
        "caps": {
            "max_raw_bytes_per_page": max_raw_bytes_per_page,
            "max_requests_per_rolling_60_seconds": 5,
            "max_requests_per_utc_day": (86_400 // scan_interval_seconds) * pages,
            "physical_reserve_bytes_per_page": PHYSICAL_RESERVE_MULTIPLIER * max_raw_bytes_per_page,
            "max_physical_reserve_per_utc_day": (86_400 // scan_interval_seconds)
            * pages
            * PHYSICAL_RESERVE_MULTIPLIER
            * max_raw_bytes_per_page,
            "max_requests_per_lease": max_scan_count * pages,
            "max_raw_bytes_per_lease": max_scan_count * pages * max_raw_bytes_per_page,
            "max_physical_reserve_per_lease": max_scan_count
            * pages
            * PHYSICAL_RESERVE_MULTIPLIER
            * max_raw_bytes_per_page,
        },
        "source_spec_sha256": source_spec_sha256,
    }
    result = {**core, "policy_sha256": sha256(canonical(core))}
    verify_policy(result)
    return result


def verify_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping) or set(policy) != {
        "schema",
        "schema_version",
        "source",
        "network",
        "endpoint",
        "accept",
        "include",
        "page_range",
        "first_scan_ns",
        "scan_interval_seconds",
        "max_scan_count",
        "lease_duration_seconds",
        "scan_expiry_seconds",
        "page_spacing_seconds",
        "request_policy",
        "enrollment_policy",
        "pool_created_at_semantics",
        "missing_page_semantics",
        "disappearance_semantics",
        "sample",
        "caps",
        "source_spec_sha256",
        "policy_sha256",
    }:
        raise DiscoveryError("discovery_policy_shape")
    core = dict(policy)
    digest = core.pop("policy_sha256")
    sample = core.get("sample")
    caps = core.get("caps")
    pages = core.get("page_range")
    if (
        core.get("schema") != POLICY_SCHEMA
        or core.get("schema_version") != 1
        or core.get("source") != "geckoterminal_provider_indexed_latest_pools"
        or core.get("network") != NETWORK
        or core.get("endpoint") != ENDPOINT
        or core.get("accept") != API_ACCEPT
        or core.get("include") != INCLUDE
        or type(pages) is not list
        or any(type(page) is not int for page in pages)
        or pages != list(range(1, len(pages) + 1))
        or not 1 <= len(pages) <= MAX_PAGE
        or type(core.get("first_scan_ns")) is not int
        or core["first_scan_ns"] <= 0
        or core["first_scan_ns"] % NS
        or type(core.get("scan_interval_seconds")) is not int
        or not 60 <= core["scan_interval_seconds"] <= 86_400
        or 86_400 % core["scan_interval_seconds"]
        or core["first_scan_ns"] % (core["scan_interval_seconds"] * NS)
        or type(core.get("max_scan_count")) is not int
        or not 1 <= core["max_scan_count"] <= MAX_SCAN_COUNT
        or core.get("lease_duration_seconds")
        != core["max_scan_count"] * core["scan_interval_seconds"]
        or core.get("scan_expiry_seconds") != SCAN_EXPIRY_NS // NS
        or type(core.get("page_spacing_seconds")) is not int
        or core["page_spacing_seconds"] * NS < MIN_PAGE_SPACING_NS
        or (len(pages) - 1) * core["page_spacing_seconds"] * NS >= SCAN_EXPIRY_NS
        or min(len(pages), (60 // core["page_spacing_seconds"]) + 1) > 5
        or core.get("request_policy") != "one_attempt_per_scan_page_no_retry_or_backfill"
        or core.get("enrollment_policy")
        != "all_syntactically_valid_pool_ids_before_content_filters"
        or core.get("pool_created_at_semantics")
        != "provider_assertion_not_verified_chain_birth_or_historical_availability"
        or core.get("missing_page_semantics")
        != "unknown_discovery_exposure_no_zero_or_empty_inference"
        or core.get("disappearance_semantics") != "no_death_inference"
        or not isinstance(sample, Mapping)
        or set(sample) != {"method", "numerator", "denominator", "population", "replacement"}
        or sample.get("method")
        != "sha256(policy_sha256 || NUL || network || NUL || pool_id) threshold"
        or type(sample.get("numerator")) is not int
        or type(sample.get("denominator")) is not int
        or not 0 <= sample["numerator"] <= sample["denominator"] <= 1_000_000
        or sample.get("population") != "first_seen_valid_pool_ids_in_observed_scan_cohort"
        or sample.get("replacement") is not False
        or not isinstance(caps, Mapping)
        or set(caps)
        != {
            "max_raw_bytes_per_page",
            "max_requests_per_rolling_60_seconds",
            "max_requests_per_utc_day",
            "physical_reserve_bytes_per_page",
            "max_physical_reserve_per_utc_day",
            "max_requests_per_lease",
            "max_raw_bytes_per_lease",
            "max_physical_reserve_per_lease",
        }
        or type(caps.get("max_raw_bytes_per_page")) is not int
        or not 1_024 <= caps["max_raw_bytes_per_page"] <= MAX_RAW_BYTES_PER_PAGE
        or type(caps.get("max_requests_per_rolling_60_seconds")) is not int
        or caps.get("max_requests_per_rolling_60_seconds") != 5
        or type(caps.get("max_requests_per_utc_day")) is not int
        or caps.get("max_requests_per_utc_day")
        != (86_400 // core["scan_interval_seconds"]) * len(pages)
        or type(caps.get("physical_reserve_bytes_per_page")) is not int
        or caps.get("physical_reserve_bytes_per_page")
        != PHYSICAL_RESERVE_MULTIPLIER * caps["max_raw_bytes_per_page"]
        or type(caps.get("max_physical_reserve_per_utc_day")) is not int
        or caps.get("max_physical_reserve_per_utc_day")
        != (86_400 // core["scan_interval_seconds"])
        * len(pages)
        * caps["physical_reserve_bytes_per_page"]
        or type(caps.get("max_requests_per_lease")) is not int
        or caps.get("max_requests_per_lease") != core["max_scan_count"] * len(pages)
        or type(caps.get("max_raw_bytes_per_lease")) is not int
        or caps.get("max_raw_bytes_per_lease")
        != caps["max_requests_per_lease"] * caps["max_raw_bytes_per_page"]
        or type(caps.get("max_physical_reserve_per_lease")) is not int
        or caps.get("max_physical_reserve_per_lease")
        != caps["max_requests_per_lease"] * caps["physical_reserve_bytes_per_page"]
        or not _is_hash(core.get("source_spec_sha256"))
        or digest != sha256(canonical(core))
    ):
        raise DiscoveryError("discovery_policy_invalid")
    return dict(policy)


def compile_scan_plan(
    policy: Mapping[str, Any], *, scan_index: int, scheduled_ns: int
) -> dict[str, Any]:
    bound = verify_policy(policy)
    if (
        type(scan_index) is not int
        or scan_index < 0
        or type(scheduled_ns) is not int
        or scheduled_ns <= 0
        or scan_index >= bound["max_scan_count"]
    ):
        raise DiscoveryError("discovery_scan_identity")
    expected_ns = bound["first_scan_ns"] + scan_index * bound["scan_interval_seconds"] * NS
    if scheduled_ns != expected_ns:
        raise DiscoveryError("discovery_scan_not_on_schedule")
    scan_id = f"{bound['policy_sha256'][:16]}:{scan_index}:{scheduled_ns}"
    if not SCAN_ID_RE.fullmatch(scan_id):
        raise DiscoveryError("discovery_scan_id")
    slots = []
    for page in bound["page_range"]:
        request_id = sha256(
            canonical({"policy_sha256": bound["policy_sha256"], "scan_id": scan_id, "page": page})
        )
        slots.append(
            {
                "page": page,
                "request_id": request_id,
                "planned_dispatch_ns": scheduled_ns
                + (page - 1) * bound["page_spacing_seconds"] * NS,
                "method": "GET",
                "url": f"https://api.geckoterminal.com{ENDPOINT}?include={INCLUDE}&page={page}",
                "accept": API_ACCEPT,
            }
        )
    core = {
        "schema": PLAN_SCHEMA,
        "schema_version": 1,
        "policy_sha256": bound["policy_sha256"],
        "scan_id": scan_id,
        "scan_index": scan_index,
        "scheduled_ns": scheduled_ns,
        "expires_ns": scheduled_ns + SCAN_EXPIRY_NS,
        "page_slots": slots,
        "provider_io": False,
    }
    return {**core, "plan_sha256": sha256(canonical(core))}


def _strict_json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DiscoveryError("discovery_duplicate_json_key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=_reject_constant,
            parse_float=_finite_json_float,
        )
    except DiscoveryError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        OverflowError,
        ValueError,
    ) as exc:
        raise DiscoveryError("discovery_json_invalid") from exc


def _reject_constant(value: str) -> None:
    raise DiscoveryError(f"discovery_json_{value.lower()}")


def _finite_json_float(value: str) -> float:
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise DiscoveryError("discovery_json_nonfinite_float")
    return result


def _timestamp(value: Any) -> tuple[int | None, str]:
    if value is None:
        return None, "missing"
    if type(value) is not str or not value:
        return None, "malformed"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "malformed"
    if parsed.tzinfo is None:
        return None, "timezone_missing"
    return int(parsed.timestamp() * NS), "parsed"


def _relation_id(row: Mapping[str, Any], name: str, expected_type: str) -> tuple[str | None, str]:
    relationships = row.get("relationships")
    if relationships is None:
        return None, "missing"
    if not isinstance(relationships, Mapping):
        return None, "malformed_relationships"
    relation = relationships.get(name)
    if relation is None:
        return None, "missing"
    if not isinstance(relation, Mapping):
        return None, "malformed_relationship"
    data = relation.get("data")
    if data is None:
        return None, "missing"
    if (
        not isinstance(data, Mapping)
        or data.get("type") != expected_type
        or type(data.get("id")) is not str
    ):
        return None, "malformed"
    return data["id"], "parsed"


def _pool_row(row: Any, *, available_ns: int, row_index: int) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise DiscoveryError("discovery_pool_row_shape")
    pool_id = row.get("id")
    if type(pool_id) is not str or not pool_id.startswith("solana_"):
        raise DiscoveryError("discovery_pool_network_identity")
    address = pool_id.removeprefix("solana_")
    if not _base58_pubkey(address):
        raise DiscoveryError("discovery_pool_address_invalid")
    attrs = row.get("attributes")
    attrs_state = (
        "parsed" if isinstance(attrs, Mapping) else "missing" if attrs is None else "malformed"
    )
    attrs = attrs if isinstance(attrs, Mapping) else {}
    declared_address = attrs.get("address")
    address_state = (
        "missing"
        if declared_address is None
        else "matches_pool_id"
        if declared_address == address
        else "conflicts_with_pool_id"
    )
    base_token, base_state = _relation_id(row, "base_token", "token")
    quote_token, quote_state = _relation_id(row, "quote_token", "token")
    dex_id, dex_state = _relation_id(row, "dex", "dex")
    token_states = {}
    for name, token_id, state in (
        ("base_token", base_token, base_state),
        ("quote_token", quote_token, quote_state),
    ):
        if token_id is not None:
            prefix, _, token_address = token_id.partition("_")
            if prefix != NETWORK:
                state = "other_network"
            elif not _base58_pubkey(token_address):
                state = "invalid_address"
        token_states[name] = state
    created_ns, created_state = _timestamp(attrs.get("pool_created_at"))
    if attrs_state == "malformed":
        created_state = "attributes_malformed"
    if created_ns is not None:
        created_state = (
            "future_relative_to_first_receipt"
            if created_ns > available_ns
            else "not_future_relative_to_first_receipt"
        )
    return {
        "row_index": row_index,
        "row_sha256": sha256(canonical(row)),
        "provider_row_type": row.get("type"),
        "provider_row_type_state": "pool" if row.get("type") == "pool" else "unexpected",
        "pool_id": pool_id,
        "pool_address": address,
        "base_token_id": base_token,
        "quote_token_id": quote_token,
        "dex_id": dex_id,
        "base_token_state": token_states["base_token"],
        "quote_token_state": token_states["quote_token"],
        "dex_state": dex_state,
        "attributes_state": attrs_state,
        "provider_address_state": address_state,
        "provider_pool_created_at": attrs.get("pool_created_at"),
        "provider_pool_created_at_ns": created_ns,
        "provider_pool_created_at_state": created_state,
    }


def parse_page(raw: bytes, *, available_ns: int) -> dict[str, Any]:
    if (
        not isinstance(raw, bytes)
        or len(raw) > MAX_RAW_BYTES_PER_PAGE
        or type(available_ns) is not int
        or available_ns <= 0
    ):
        raise DiscoveryError("discovery_page_input")
    value = _strict_json(raw)
    if not isinstance(value, Mapping) or not isinstance(value.get("data"), list):
        raise DiscoveryError("discovery_page_shape")
    if len(value["data"]) > MAX_ROWS_PER_PAGE:
        raise DiscoveryError("discovery_page_row_cap")
    rows: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for index, raw_row in enumerate(value["data"]):
        try:
            rows.append(_pool_row(raw_row, available_ns=available_ns, row_index=index))
        except DiscoveryError as exc:
            quarantined.append(
                {"row_index": index, "reason": str(exc), "row_sha256": sha256(canonical(raw_row))}
            )
    included = value.get("included", [])
    included_state = (
        "missing"
        if "included" not in value
        else "parsed"
        if isinstance(included, list) and len(included) <= MAX_INCLUDED_RESOURCES_PER_PAGE
        else "oversize"
        if isinstance(included, list)
        else "malformed"
    )
    return {
        "raw_sha256": sha256(raw),
        "raw_bytes": len(raw),
        "row_count": len(value["data"]),
        "valid_pool_count": len(rows),
        "malformed_row_count": len(quarantined),
        "rows": rows,
        "quarantined_rows": quarantined,
        "included_resource_count": len(included) if isinstance(included, list) else None,
        "included_state": included_state,
    }


def _sampled(policy: Mapping[str, Any], pool_id: str) -> bool:
    sample = policy["sample"]
    digest = hashlib.sha256(f"{policy['policy_sha256']}\0{NETWORK}\0{pool_id}".encode()).digest()
    return int.from_bytes(digest, "big") * sample["denominator"] < sample["numerator"] * (1 << 256)


def replay_scan(
    *,
    policy: Mapping[str, Any],
    plan: Mapping[str, Any],
    page_slots: Sequence[Mapping[str, Any]],
    previously_seen_pool_ids: Sequence[str],
) -> dict[str, Any]:
    """Verify page denominators and derive first-seen enrollments from durable receipts."""
    bound = verify_policy(policy)
    if not isinstance(plan, Mapping):
        raise DiscoveryError("discovery_plan_binding")
    try:
        expected_plan = compile_scan_plan(
            bound,
            scan_index=plan.get("scan_index"),
            scheduled_ns=plan.get("scheduled_ns"),
        )
    except (DiscoveryError, TypeError) as exc:
        raise DiscoveryError("discovery_plan_binding") from exc
    if (
        dict(plan) != expected_plan
        or not isinstance(page_slots, Sequence)
        or isinstance(page_slots, (str, bytes))
        or not isinstance(previously_seen_pool_ids, Sequence)
        or isinstance(previously_seen_pool_ids, (str, bytes))
    ):
        raise DiscoveryError("discovery_plan_binding")
    expected = {row["page"]: row for row in expected_plan["page_slots"]}
    by_page: dict[int, Mapping[str, Any]] = {}
    for slot in page_slots:
        if not isinstance(slot, Mapping):
            raise DiscoveryError("discovery_page_slot_shape")
        page = slot.get("page")
        if type(page) is not int or page not in expected or page in by_page:
            raise DiscoveryError("discovery_page_slot_duplicate_or_unknown")
        by_page[page] = slot
    if set(by_page) != set(expected):
        raise DiscoveryError("discovery_page_denominator_incomplete")

    if any(
        type(pool) is not str
        or not pool.startswith("solana_")
        or not _base58_pubkey(pool.removeprefix("solana_"))
        for pool in previously_seen_pool_ids
    ):
        raise DiscoveryError("discovery_prior_seen_identity")
    seen = set(previously_seen_pool_ids)
    if len(seen) != len(previously_seen_pool_ids):
        raise DiscoveryError("discovery_prior_seen_identity")
    parsed_by_page: dict[int, dict[str, Any]] = {}
    page_states: Counter[str] = Counter()
    page_appearances: list[dict[str, Any]] = []
    page_order = sorted(by_page.items())
    last_dispatch_ns = 0
    for page, slot in page_order:
        req = expected[page]
        if (
            set(slot)
            != {
                "page",
                "request_id",
                "status",
                "http_status",
                "request_started_ns",
                "receipt_ns",
                "durable_available_ns",
                "raw_body",
                "raw_sha256",
                "raw_bytes",
                "body_truncated",
            }
            or slot.get("request_id") != req["request_id"]
        ):
            raise DiscoveryError("discovery_page_request_binding")
        status = slot.get("status")
        if type(status) is not str or status not in {
            "received",
            "http_error",
            "transport_ambiguous",
            "oversize_response",
            "missed",
        }:
            raise DiscoveryError("discovery_page_status")
        started = slot.get("request_started_ns")
        receipt = slot.get("receipt_ns")
        available = slot.get("durable_available_ns")
        raw = slot.get("raw_body")
        if status == "missed":
            if (
                started is not None
                or receipt is not None
                or available is not None
                or raw is not None
                or slot.get("raw_sha256") is not None
                or slot.get("raw_bytes") != 0
                or slot.get("http_status") is not None
                or slot.get("body_truncated") is not False
            ):
                raise DiscoveryError("discovery_missed_slot_has_attempt")
            page_states[status] += 1
            parsed_by_page[page] = {
                "slot_status": status,
                "rows": [],
                "raw_sha256": None,
                "raw_bytes": 0,
                "row_count": 0,
                "valid_pool_count": 0,
                "malformed_row_count": 0,
                "quarantined_rows": [],
                "ordered_pool_ids": [],
                "duplicate_pool_appearance_count": 0,
                "page_parse_error": None,
            }
            continue
        if type(started) is not int or started < plan["scheduled_ns"] or started < last_dispatch_ns:
            raise DiscoveryError("discovery_page_dispatch_order")
        if (
            started < req["planned_dispatch_ns"]
            or started >= plan["scheduled_ns"] + bound["scan_interval_seconds"] * NS
        ):
            raise DiscoveryError("discovery_page_dispatch_window")
        if last_dispatch_ns and started - last_dispatch_ns < bound["page_spacing_seconds"] * NS:
            raise DiscoveryError("discovery_page_spacing")
        if started >= plan["expires_ns"]:
            raise DiscoveryError("discovery_page_scan_expired")
        last_dispatch_ns = started
        if status == "transport_ambiguous":
            if (
                receipt is not None
                or available is not None
                or raw is not None
                or slot.get("http_status") is not None
                or slot.get("raw_sha256") is not None
                or slot.get("raw_bytes") != 0
                or slot.get("body_truncated") is not False
            ):
                raise DiscoveryError("discovery_ambiguous_has_response")
            page_states[status] += 1
            parsed_by_page[page] = {
                "slot_status": status,
                "rows": [],
                "raw_sha256": None,
                "raw_bytes": 0,
                "row_count": 0,
                "valid_pool_count": 0,
                "malformed_row_count": 0,
                "quarantined_rows": [],
                "ordered_pool_ids": [],
                "duplicate_pool_appearance_count": 0,
                "page_parse_error": None,
            }
            continue
        if (
            type(receipt) is not int
            or receipt < started
            or type(available) is not int
            or available < receipt
        ):
            raise DiscoveryError("discovery_page_time_order")
        http_status = slot.get("http_status")
        if (
            (status == "received" and (type(http_status) is not int or http_status != 200))
            or (
                status == "http_error"
                and (type(http_status) is not int or not 400 <= http_status <= 599)
            )
            or (
                status == "oversize_response"
                and (type(http_status) is not int or http_status != 200)
            )
        ):
            raise DiscoveryError("discovery_page_http_status")
        if raw is not None:
            if not isinstance(raw, bytes) or len(raw) > bound["caps"]["max_raw_bytes_per_page"]:
                raise DiscoveryError("discovery_page_raw_cap")
            if (
                slot.get("raw_sha256") != sha256(raw)
                or type(slot.get("raw_bytes")) is not int
                or slot.get("raw_bytes") != len(raw)
            ):
                raise DiscoveryError("discovery_page_raw_binding")
        if status == "received" and raw is None:
            raise DiscoveryError("discovery_success_raw_missing")
        if status == "oversize_response":
            if (
                not isinstance(raw, bytes)
                or len(raw) != bound["caps"]["max_raw_bytes_per_page"]
                or slot.get("body_truncated") is not True
            ):
                raise DiscoveryError("discovery_oversize_prefix_binding")
            page_states[status] += 1
            parsed_by_page[page] = {
                "slot_status": status,
                "rows": [],
                "raw_sha256": sha256(raw),
                "raw_bytes": len(raw),
                "row_count": None,
                "valid_pool_count": 0,
                "malformed_row_count": None,
                "quarantined_rows": [],
                "ordered_pool_ids": [],
                "duplicate_pool_appearance_count": 0,
                "page_parse_error": None,
                "body_truncated": True,
            }
            continue
        if slot.get("body_truncated") is True and not (
            status == "http_error"
            and isinstance(raw, bytes)
            and len(raw) == bound["caps"]["max_raw_bytes_per_page"]
        ):
            raise DiscoveryError("discovery_unexpected_body_truncation")
        if type(slot.get("body_truncated")) is not bool:
            raise DiscoveryError("discovery_unexpected_body_truncation")
        if status == "http_error":
            page_states[status] += 1
            if raw is None and (slot.get("raw_sha256") is not None or slot.get("raw_bytes") != 0):
                raise DiscoveryError("discovery_page_raw_binding")
            parsed_by_page[page] = {
                "slot_status": status,
                "rows": [],
                "raw_sha256": sha256(raw) if isinstance(raw, bytes) else None,
                "raw_bytes": len(raw) if isinstance(raw, bytes) else 0,
                "row_count": 0,
                "valid_pool_count": 0,
                "malformed_row_count": 0,
                "quarantined_rows": [],
                "ordered_pool_ids": [],
                "duplicate_pool_appearance_count": 0,
                "page_parse_error": None,
                "body_truncated": slot["body_truncated"],
            }
            continue
        if not isinstance(raw, bytes):
            raise DiscoveryError("discovery_success_raw_missing")
        if (
            slot.get("raw_sha256") != sha256(raw)
            or type(slot.get("raw_bytes")) is not int
            or slot.get("raw_bytes") != len(raw)
        ):
            raise DiscoveryError("discovery_page_raw_binding")
        try:
            parsed = parse_page(raw, available_ns=available)
        except DiscoveryError as exc:
            parsed = {
                "raw_sha256": sha256(raw),
                "raw_bytes": len(raw),
                "row_count": None,
                "valid_pool_count": 0,
                "malformed_row_count": None,
                "rows": [],
                "quarantined_rows": [],
                "page_parse_error": str(exc),
                "ordered_pool_ids": [],
                "duplicate_pool_appearance_count": 0,
            }
        else:
            ordered_ids = [row["pool_id"] for row in parsed["rows"]]
            parsed["ordered_pool_ids"] = ordered_ids
            parsed["duplicate_pool_appearance_count"] = len(ordered_ids) - len(set(ordered_ids))
        parsed["slot_status"] = status
        parsed_by_page[page] = parsed
        page_states[status] += 1

    # Enrollment order follows durable availability time, while plan and dispatch
    # verification above remains the preregistered page order.
    availability_order = sorted(
        by_page.items(),
        key=lambda item: (
            item[1]["durable_available_ns"]
            if type(item[1].get("durable_available_ns")) is int
            else 2**63,
            item[0],
        ),
    )
    for page, slot in availability_order:
        parsed = parsed_by_page[page]
        available = slot.get("durable_available_ns")
        parsed.update(
            {
                "request_id": slot["request_id"],
                "planned_dispatch_ns": expected[page]["planned_dispatch_ns"],
                "request_started_ns": slot.get("request_started_ns"),
                "receipt_ns": slot.get("receipt_ns"),
                "durable_available_ns": available,
            }
        )
        for row in parsed["rows"]:
            pool_id = row["pool_id"]
            first = pool_id not in seen
            if first:
                seen.add(pool_id)
            page_appearances.append(
                {
                    **row,
                    "page": page,
                    "scheduled_ns": plan["scheduled_ns"],
                    "request_id": slot["request_id"],
                    "request_started_ns": slot.get("request_started_ns"),
                    "receipt_ns": slot.get("receipt_ns"),
                    "durable_available_ns": available,
                    "decision_ns": None,
                    "first_seen_in_scan": first,
                    "selected_for_followup": first and _sampled(bound, pool_id),
                }
            )
    status_summary = {
        "planned_pages": len(expected),
        "received_pages": page_states["received"],
        "http_error_pages": page_states["http_error"],
        "ambiguous_pages": page_states["transport_ambiguous"],
        "missed_pages": page_states["missed"],
        "oversize_response_pages": page_states["oversize_response"],
        "truncated_http_error_pages": sum(
            parsed_by_page[page].get("body_truncated") is True
            and parsed_by_page[page].get("slot_status") == "http_error"
            for page in parsed_by_page
        ),
        "malformed_response_pages": sum(
            parsed_by_page[page].get("page_parse_error") is not None for page in parsed_by_page
        ),
        "malformed_included_pages": sum(
            parsed_by_page[page].get("included_state") == "malformed" for page in parsed_by_page
        ),
        "oversize_included_pages": sum(
            parsed_by_page[page].get("included_state") == "oversize" for page in parsed_by_page
        ),
        "unknown_page_exposure": page_states["http_error"]
        + page_states["transport_ambiguous"]
        + page_states["missed"]
        + page_states["oversize_response"]
        + sum(parsed_by_page[page].get("page_parse_error") is not None for page in parsed_by_page),
        "provider_index_completeness_claim": False,
        "solana_birth_census_claim": False,
        "lifecycle_death_claim": False,
    }
    new_first_seen = [row for row in page_appearances if row["first_seen_in_scan"]]
    pool_page_sets: dict[str, set[int]] = {}
    for row in page_appearances:
        pool_page_sets.setdefault(row["pool_id"], set()).add(row["page"])
    core_receipt = {
        "schema": RECEIPT_SCHEMA,
        "schema_version": 1,
        "policy_sha256": bound["policy_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "scan_id": plan["scan_id"],
        "scheduled_ns": plan["scheduled_ns"],
        "decision_time_semantics": "discovery recorder has no model decision timestamp",
        "page_summary": status_summary,
        "page_results": {str(page): parsed_by_page[page] for page in sorted(parsed_by_page)},
        "page_overlap_summary": {
            "duplicate_appearances_within_pages": sum(
                parsed_by_page[page]["duplicate_pool_appearance_count"] for page in parsed_by_page
            ),
            "cross_page_duplicate_appearances": sum(
                max(0, len(page_ids) - 1) for page_ids in pool_page_sets.values()
            ),
            "provider_page_shift_or_drift_interpreted_as_completeness": False,
        },
        "pool_appearances": page_appearances,
        "first_seen_pool_ids": [row["pool_id"] for row in new_first_seen],
        "followup_sample_pool_ids": [
            row["pool_id"] for row in new_first_seen if row["selected_for_followup"]
        ],
        "previously_seen_count": len(previously_seen_pool_ids),
        "provider_io": False,
    }
    return {**core_receipt, "receipt_sha256": sha256(canonical(core_receipt))}


def replay_campaign(
    *,
    policy: Mapping[str, Any],
    scans: Sequence[Mapping[str, Any]],
    first_scan_index: int = 0,
    previously_seen_pool_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Carry first-seen enrollment and request identity over a contiguous scan prefix.

    Callers represent a scheduled scan with missing pages explicitly as `missed`
    slots. An omitted scan is an integrity failure, never an empty scan.
    """
    bound = verify_policy(policy)
    if (
        not isinstance(scans, Sequence)
        or isinstance(scans, (str, bytes))
        or not scans
        or type(first_scan_index) is not int
        or first_scan_index < 0
        or first_scan_index + len(scans) > bound["max_scan_count"]
    ):
        raise DiscoveryError("discovery_campaign_input")
    seen = list(previously_seen_pool_ids)
    enrolled: list[dict[str, Any]] = []
    scan_receipts: list[dict[str, Any]] = []
    request_ids: set[str] = set()
    totals: Counter[str] = Counter()
    dispatch_times: list[int] = []
    captured_raw_bytes = 0
    page_transitions: list[dict[str, Any]] = []
    previous_page_results: Mapping[str, Any] | None = None
    for offset, scan in enumerate(scans):
        if not isinstance(scan, Mapping) or set(scan) != {"plan", "page_slots"}:
            raise DiscoveryError("discovery_campaign_scan_shape")
        plan = scan["plan"]
        expected_index = first_scan_index + offset
        if not isinstance(plan, Mapping) or plan.get("scan_index") != expected_index:
            raise DiscoveryError("discovery_campaign_scan_sequence")
        receipt = replay_scan(
            policy=bound,
            plan=plan,
            page_slots=scan["page_slots"],
            previously_seen_pool_ids=seen,
        )
        for row in plan["page_slots"]:
            request_id = row["request_id"]
            if request_id in request_ids:
                raise DiscoveryError("discovery_campaign_duplicate_request")
            request_ids.add(request_id)
        for slot in scan["page_slots"]:
            if slot.get("status") != "missed":
                dispatch_times.append(slot["request_started_ns"])
            if isinstance(slot.get("raw_body"), bytes):
                captured_raw_bytes += len(slot["raw_body"])
        for row in receipt["pool_appearances"]:
            if row["first_seen_in_scan"]:
                enrolled.append(
                    {
                        "pool_id": row["pool_id"],
                        "first_seen_scan_index": expected_index,
                        "first_seen_page": row["page"],
                        "first_seen_request_id": row["request_id"],
                        "first_seen_scheduled_ns": row["scheduled_ns"],
                        "first_seen_request_started_ns": row["request_started_ns"],
                        "first_seen_receipt_ns": row["receipt_ns"],
                        "first_seen_available_ns": row["durable_available_ns"],
                        "first_seen_decision_ns": row["decision_ns"],
                        "provider_pool_created_at": row["provider_pool_created_at"],
                        "provider_pool_created_at_ns": row["provider_pool_created_at_ns"],
                        "provider_pool_created_at_state": row["provider_pool_created_at_state"],
                        "selected_for_followup": row["selected_for_followup"],
                    }
                )
        if previous_page_results is not None:
            for page_text, current in receipt["page_results"].items():
                previous = previous_page_results[page_text]
                comparable = (
                    previous.get("slot_status") == "received"
                    and current.get("slot_status") == "received"
                    and previous.get("page_parse_error") is None
                    and current.get("page_parse_error") is None
                )
                old_ids = previous.get("ordered_pool_ids", [])
                new_ids = current.get("ordered_pool_ids", [])
                old_set = set(old_ids)
                new_set = set(new_ids)
                page_transitions.append(
                    {
                        "from_scan_index": expected_index - 1,
                        "to_scan_index": expected_index,
                        "page": int(page_text),
                        "comparable_successful_pages": comparable,
                        "previous_ordered_ids_sha256": sha256(canonical(old_ids))
                        if comparable
                        else None,
                        "current_ordered_ids_sha256": sha256(canonical(new_ids))
                        if comparable
                        else None,
                        "ordered_ids_changed": comparable and old_ids != new_ids,
                        "previous_count": len(old_ids) if comparable else None,
                        "current_count": len(new_ids) if comparable else None,
                        "intersection_count": len(old_set & new_set) if comparable else None,
                        "union_count": len(old_set | new_set) if comparable else None,
                        "provider_index_completeness_or_lifecycle_claim": False,
                    }
                )
        seen.extend(receipt["first_seen_pool_ids"])
        totals.update(receipt["page_summary"])
        scan_receipts.append(receipt)
        previous_page_results = receipt["page_results"]
    if len(dispatch_times) > bound["caps"]["max_requests_per_lease"]:
        raise DiscoveryError("discovery_campaign_lease_request_cap")
    if captured_raw_bytes > bound["caps"]["max_raw_bytes_per_lease"]:
        raise DiscoveryError("discovery_campaign_lease_raw_cap")
    requests_per_day: Counter[int] = Counter()
    for started in sorted(dispatch_times):
        requests_per_day[started // (86_400 * NS)] += 1
    if any(
        count > bound["caps"]["max_requests_per_utc_day"] for count in requests_per_day.values()
    ):
        raise DiscoveryError("discovery_campaign_day_request_cap")
    ordered_dispatches = sorted(dispatch_times)
    if any(
        right - left < bound["page_spacing_seconds"] * NS
        for left, right in pairwise(ordered_dispatches)
    ):
        raise DiscoveryError("discovery_campaign_global_page_spacing")
    if any(
        ordered_dispatches[index] - ordered_dispatches[index - 5] < 60 * NS
        for index in range(5, len(ordered_dispatches))
    ):
        raise DiscoveryError("discovery_campaign_rolling_request_cap")
    summary = {
        "scan_count": len(scan_receipts),
        "planned_page_slots": totals["planned_pages"],
        "attempted_request_count": len(dispatch_times),
        "captured_raw_bytes": captured_raw_bytes,
        "maximum_physical_reserve_bytes": len(dispatch_times)
        * bound["caps"]["physical_reserve_bytes_per_page"],
        "received_pages": totals["received_pages"],
        "http_error_pages": totals["http_error_pages"],
        "ambiguous_pages": totals["ambiguous_pages"],
        "missed_pages": totals["missed_pages"],
        "oversize_response_pages": totals["oversize_response_pages"],
        "truncated_http_error_pages": totals["truncated_http_error_pages"],
        "malformed_response_pages": totals["malformed_response_pages"],
        "malformed_included_pages": totals["malformed_included_pages"],
        "oversize_included_pages": totals["oversize_included_pages"],
        "unknown_page_exposure": totals["unknown_page_exposure"],
        "unique_first_seen_pool_count": len(enrolled),
        "adjacent_page_transitions": len(page_transitions),
        "successful_page_order_changes": sum(
            row["ordered_ids_changed"] for row in page_transitions
        ),
        "page_transition_unknowns": sum(
            not row["comparable_successful_pages"] for row in page_transitions
        ),
        "provider_index_completeness_claim": False,
        "solana_birth_census_claim": False,
        "lifecycle_death_claim": False,
        "provider_io": False,
    }
    core = {
        "schema": f"{SCHEMA}-campaign-replay",
        "schema_version": 1,
        "policy_sha256": bound["policy_sha256"],
        "first_scan_index": first_scan_index,
        "last_scan_index": first_scan_index + len(scan_receipts) - 1,
        "summary": summary,
        "enrollments": enrolled,
        "page_transitions": page_transitions,
        "scan_receipts": scan_receipts,
        "provider_io": False,
    }
    return {**core, "campaign_sha256": sha256(canonical(core))}
