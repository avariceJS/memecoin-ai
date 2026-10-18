#!/usr/bin/env python3
"""Offline policy math for the prospective Alchemy Free corroboration sidecar.

This module does not open a provider connection, inspect a mounted volume, or
authorize a service. Runtime admission must supply fresh, independently checked
storage facts and use descriptor-bound writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SECOND_NS = 1_000_000_000
MINUTE_NS = 60 * SECOND_NS
HOUR_NS = 60 * MINUTE_NS
DAY_NS = 24 * HOUR_NS
THIRTY_DAYS_NS = 30 * DAY_NS

SEAL_DEADLINE_OFFSET_NS = 10 * MINUTE_NS
WINDOW_END_OFFSET_NS = 20 * MINUTE_NS
MAX_REQUESTS_PER_FRAME = 17
MAX_HELIUS_SELECTED_SLOTS = 16
MAX_REQUESTS_PER_SECOND = 2
MIN_REQUEST_SPACING_NS = SECOND_NS // MAX_REQUESTS_PER_SECOND
MAX_REQUESTS_PER_DAY = 408
MAX_REQUESTS_PER_30_DAYS = 12_240
MAX_INVENTORY_RESPONSE_BYTES = 16_000_000
MAX_SIGNATURE_RESPONSE_BYTES = 4_000_000
MAX_RESPONSE_BYTES = MAX_INVENTORY_RESPONSE_BYTES
MAX_FRAME_RAW_BYTES = 80_000_000
MAX_EPOCH_PHYSICAL_BYTES = 100_000_000
MAX_LIFETIME_ALLOCATION_BYTES = 256_000_000
MIN_ACTIVATION_ALLOCATION_BYTES = 256_000_000
ALCHEMY_PEAK_RESERVE_BYTES = 128_000_000
ALCHEMY_METADATA_RESERVE_BYTES = 32_000_000
PER_REQUEST_METADATA_RESERVE_BYTES = 64_000
MODELED_CU_PER_FRAME = 650
MODELED_CU_PER_30_DAYS = MODELED_CU_PER_FRAME * 24 * 30
_ROOT_ID = re.compile(r"[0-9a-f]{32}\Z")
_VOLUME_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")

HELIUS_DISK_FLOOR_BYTES = 50 * 2**30
OPERATING_RESERVE_BYTES = 8 * 2**30
HELIUS_NEXT_EPOCH_BYTES = 4_000_000_000
HELIUS_PEAK_METADATA_BYTES = 322_000_000

POLICY_ID = "alchemy-free-same-frame-corroboration-v1"


class PolicyError(ValueError):
    """Invalid sidecar policy input."""


def response_reservation_bytes(method: str) -> int:
    if method == "getBlocks":
        return MAX_INVENTORY_RESPONSE_BYTES
    if method == "getBlock":
        return MAX_SIGNATURE_RESPONSE_BYTES
    raise PolicyError("request_method_invalid")


@dataclass(frozen=True)
class Window:
    """The fixed UTC window derived from a Helius frame bucket."""

    bucket_start_ns: int
    seal_deadline_ns: int
    start_ns: int
    end_ns: int


def frame_window(bucket_start_ns: int) -> Window:
    if type(bucket_start_ns) is not int or bucket_start_ns <= 0 or bucket_start_ns % HOUR_NS:
        raise PolicyError("bucket_start_invalid")
    return Window(
        bucket_start_ns=bucket_start_ns,
        seal_deadline_ns=bucket_start_ns + SEAL_DEADLINE_OFFSET_NS,
        start_ns=bucket_start_ns + SEAL_DEADLINE_OFFSET_NS,
        end_ns=bucket_start_ns + WINDOW_END_OFFSET_NS,
    )


def disposition(*, bucket_start_ns: int, now_ns: int, helius_verified_ns: int | None) -> str:
    """Classify a new bucket without ever turning an expired bucket into work."""
    window = frame_window(bucket_start_ns)
    if type(now_ns) is not int or now_ns <= 0:
        raise PolicyError("clock_invalid")
    if helius_verified_ns is None or type(helius_verified_ns) is not int:
        return "awaiting_seal"
    if helius_verified_ns > window.seal_deadline_ns:
        return "unobserved_late_seal"
    if now_ns < window.start_ns:
        return "not_due"
    if now_ns >= window.end_ns:
        return "unobserved_expired_window"
    return "eligible"


def lifetime_allocation_cap(
    *,
    fresh_free_bytes: int,
    helius_remaining_lifetime_bytes: int,
    disk_floor_bytes: int = HELIUS_DISK_FLOOR_BYTES,
    operating_reserve_bytes: int = OPERATING_RESERVE_BYTES,
    helius_next_epoch_bytes: int = HELIUS_NEXT_EPOCH_BYTES,
    helius_peak_metadata_bytes: int = HELIUS_PEAK_METADATA_BYTES,
    alchemy_peak_bytes: int = ALCHEMY_PEAK_RESERVE_BYTES,
    alchemy_metadata_bytes: int = ALCHEMY_METADATA_RESERVE_BYTES,
) -> int:
    """Return fail-closed sidecar cap from fresh free space and pinned reserves.

    The 256 MB value is only a ceiling. Every Helius allocation still remaining
    under its pinned lifetime cap is reserved before assigning Alchemy space.
    """
    values = (
        fresh_free_bytes,
        helius_remaining_lifetime_bytes,
        disk_floor_bytes,
        operating_reserve_bytes,
        helius_next_epoch_bytes,
        helius_peak_metadata_bytes,
        alchemy_peak_bytes,
        alchemy_metadata_bytes,
    )
    if any(type(value) is not int or value < 0 for value in values):
        raise PolicyError("capacity_input_invalid")
    headroom = (
        fresh_free_bytes
        - helius_remaining_lifetime_bytes
        - disk_floor_bytes
        - operating_reserve_bytes
        - helius_next_epoch_bytes
        - helius_peak_metadata_bytes
        - alchemy_peak_bytes
        - alchemy_metadata_bytes
    )
    if headroom <= 0:
        return 0
    return min(MAX_LIFETIME_ALLOCATION_BYTES, headroom)


def activation_allocation_cap(**kwargs: int) -> int:
    """Return the usable cap, or zero when activation's 256 MB minimum fails."""
    cap = lifetime_allocation_cap(**kwargs)
    return cap if cap >= MIN_ACTIVATION_ALLOCATION_BYTES else 0


def request_storage_admissible(
    *,
    fresh_free_bytes: int,
    expected_root_id: str,
    observed_root_id: str,
    expected_volume_uuid: str,
    observed_volume_uuid: str,
    helius_remaining_lifetime_bytes: int,
    sidecar_lifetime_used_bytes: int,
    sidecar_lifetime_cap_bytes: int,
    sidecar_epoch_physical_bytes: int,
    frame_raw_bytes: int,
    request_method: str,
) -> bool:
    """Check descriptor-root identity and reserve a full response before intent."""
    values = (
        fresh_free_bytes,
        helius_remaining_lifetime_bytes,
        sidecar_lifetime_used_bytes,
        sidecar_lifetime_cap_bytes,
        sidecar_epoch_physical_bytes,
        frame_raw_bytes,
    )
    if any(type(value) is not int or value < 0 for value in values):
        raise PolicyError("request_storage_input_invalid")
    if (
        not isinstance(expected_root_id, str)
        or not _ROOT_ID.fullmatch(expected_root_id)
        or not isinstance(expected_volume_uuid, str)
        or not isinstance(observed_root_id, str)
        or not isinstance(observed_volume_uuid, str)
        or not _VOLUME_UUID.fullmatch(expected_volume_uuid.lower())
        or not _VOLUME_UUID.fullmatch(observed_volume_uuid.lower())
        or observed_root_id != expected_root_id
        or observed_volume_uuid.lower() != expected_volume_uuid.lower()
    ):
        return False
    request_reserve = (
        response_reservation_bytes(request_method) + PER_REQUEST_METADATA_RESERVE_BYTES
    )
    fixed_reserves = (
        HELIUS_DISK_FLOOR_BYTES
        + OPERATING_RESERVE_BYTES
        + helius_remaining_lifetime_bytes
        + HELIUS_NEXT_EPOCH_BYTES
        + HELIUS_PEAK_METADATA_BYTES
        + ALCHEMY_PEAK_RESERVE_BYTES
        + ALCHEMY_METADATA_RESERVE_BYTES
        + max(0, sidecar_lifetime_cap_bytes - sidecar_lifetime_used_bytes)
    )
    return (
        sidecar_lifetime_used_bytes + request_reserve <= sidecar_lifetime_cap_bytes
        and sidecar_epoch_physical_bytes + request_reserve <= MAX_EPOCH_PHYSICAL_BYTES
        and frame_raw_bytes + response_reservation_bytes(request_method) <= MAX_FRAME_RAW_BYTES
        and fresh_free_bytes >= fixed_reserves + request_reserve
    )


def policy_document() -> dict[str, Any]:
    return {
        "schema": "brain-senses-alchemy-corroboration-policy-v1",
        "policy_id": POLICY_ID,
        "provider": "alchemy-free",
        "helius_service_id": "external-rolling-v4",
        "frame_rule": "newly_independently_sealed_helius_frame_only",
        "seal_deadline_offset_ns": SEAL_DEADLINE_OFFSET_NS,
        "request_window_offsets_ns": [SEAL_DEADLINE_OFFSET_NS, WINDOW_END_OFFSET_NS],
        "window_end_exclusive": True,
        "max_requests_per_frame": MAX_REQUESTS_PER_FRAME,
        "max_helius_selected_slots": MAX_HELIUS_SELECTED_SLOTS,
        "corroborated_slots_per_frame": "all_Helius_selected_slots_up_to_16",
        "max_requests_per_second": MAX_REQUESTS_PER_SECOND,
        "max_requests_per_day": MAX_REQUESTS_PER_DAY,
        "max_requests_per_30_days": MAX_REQUESTS_PER_30_DAYS,
        "max_inventory_response_bytes": MAX_INVENTORY_RESPONSE_BYTES,
        "max_signature_response_bytes": MAX_SIGNATURE_RESPONSE_BYTES,
        "per_request_metadata_reserve_bytes": PER_REQUEST_METADATA_RESERVE_BYTES,
        "max_frame_raw_bytes": MAX_FRAME_RAW_BYTES,
        "max_epoch_physical_bytes": MAX_EPOCH_PHYSICAL_BYTES,
        "max_lifetime_allocation_bytes": MAX_LIFETIME_ALLOCATION_BYTES,
        "minimum_activation_allocation_bytes": MIN_ACTIVATION_ALLOCATION_BYTES,
        "alchemy_peak_reserve_bytes": ALCHEMY_PEAK_RESERVE_BYTES,
        "alchemy_metadata_reserve_bytes": ALCHEMY_METADATA_RESERVE_BYTES,
        "helius_reserves": {
            "disk_floor_bytes": HELIUS_DISK_FLOOR_BYTES,
            "operating_reserve_bytes": OPERATING_RESERVE_BYTES,
            "next_epoch_bytes": HELIUS_NEXT_EPOCH_BYTES,
            "peak_metadata_bytes": HELIUS_PEAK_METADATA_BYTES,
            "remaining_lifetime_bytes": "fresh_pinned_input",
        },
        "storage": "independent_Kingston_root_descriptor_bound_no_fallback",
        "capture_codec": "raw",
        "modeled_cu_per_frame": MODELED_CU_PER_FRAME,
        "modeled_cu_per_30_days": MODELED_CU_PER_30_DAYS,
        "ambiguous_intent": "possibly_spent_no_replay",
        "missed_window": "unobserved_permanent_skip",
        "calendar_expiry": None,
        "diagnostic_differences": "never_correct_Helius_or_claim_global_absence",
    }
