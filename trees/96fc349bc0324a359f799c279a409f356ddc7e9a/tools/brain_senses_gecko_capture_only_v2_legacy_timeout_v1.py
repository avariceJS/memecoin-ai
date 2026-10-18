"""Narrow replay adapter for one durably confirmed legacy Gecko timeout.

The base v2 verifier remains unchanged and keeps its 20-second limit. This
adapter recognizes only the exact source-bound terminal named below, verifies
all its durable links, and invokes a private function copy with a 21-second
chronology bound. Every other request remains subject to the original limit.
"""

from __future__ import annotations

import hashlib
import types
from collections.abc import Mapping, Sequence
from typing import Any, cast

from tools import brain_senses_gecko_capture_only_v2_verify as verifier
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage

SCHEMA = "brain-senses-gecko-capture-only-v2-legacy-wall-timeout-exception-v1"
ROOT = "services/gecko-market-v1/capture-only-v2/3569c27ff9916382c209ea71ef23e345"
RELEASE_ID = "3569c27ff9916382c209ea71ef23e345"
REQUEST_ID = "6b32a574f771989245f2f4ca37bd959e3812cf1f33314cccb0640eb9f55e2750"
BASE_TIMEOUT_SECONDS = 20
LOCAL_EXCEPTION_TIMEOUT_SECONDS = 21

# These pins were read from the existing descriptor-bound root and the shared
# coordinator ledger. They identify one completed ambiguous attempt, not a
# reusable timeout allowance.
LEGACY_PIN: dict[str, Any] = {
    "binding_file_sha256": "7c955a4ce016c23f31be8c35431c732ec8fc0eefff4c6ccdf544f9ae3cb1a6a8",
    "binding_sha256": "16e1d0095aa1f0227beb3986964536530dd5d5e9becd11e173f90bbeb60c4df0",
    "release_sha256": "3569c27ff9916382c209ea71ef23e3452908af3f306370a75af47ac196f16399",
    "storage": {
        "root_id": "9428695af6d4f46c08e1a444cdc66de8",
        "volume_uuid": "8f8f1081-9be4-4914-9111-75f1b90255bf",
        "device": 16777242,
        "inode": 400,
    },
    "policy_file_sha256": "5cc5d6ec6b02031f2ae183ba8f1ab4944f93ac2c13dc1aa1b4b8541a8c8e60a7",
    "policy_sha256": "a81d5827a8c4d236188b3c13f4af7a3e97e3bf4a949f0c441848ff27401f054a",
    "plan_record_sha256": "7611f5a2d7d994c6cae7e28203b08d6570f5889f05c6ca0cb717d8de75987d34",
    "intent_file_sha256": "ffc9edeb606ecc2c1074e206b5c18a447eeb57ff54de421f3b915d70a55a0f9d",
    "intent_sha256": "77ca326a3a03a26ca5feeaf0d8c8c9b958749ebde02bd5cdc4707f4071c3c155",
    "dispatch_record_sha256": "9db76237918f806cc2606c0f16874d3271c712451d68f5b6750bd0d3976be5ad",
    "dispatch_sequence": 9,
    "dispatch_ns": 1791865952284206000,
    "request_started_ns": 1791865952289347000,
    "receipt_ns": 1791865972505506000,
    "cooldown_until_ns": 1791866872505506000,
    "terminal_file_sha256": "e7a77c3a3876400e943d826817ea5d1502591e0d5403e1d9f3bef340dd951ad1",
    "confirmation_file_sha256": "2c09fa05faa24987ffdb58e78c2a1a9a6c289f72b4267d350f5e66febbc5f4b1",
    "confirmation_sha256": "afa2974d4810a89ea13117c425e210e3d160213a9c21898dab5af3b086a5e20d",
    "availability_ns": 1791865975000190000,
    "coordinator_reserved_event_sha256": (
        "e05043fb22ddd4530483a9ea0947437084821035be692494fbf1e3f82339959a"
    ),
    "coordinator_terminal_event_sha256": (
        "ff1057f514675a20f7cd314437636cca73144798a3eb231bf9d7422d7783865b"
    ),
}


class LegacyTimeoutError(ValueError):
    """The exact legacy terminal or its durable links no longer match."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise LegacyTimeoutError(reason)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha(value: Mapping[str, Any]) -> str:
    return _sha(shared_usage.canonical_json(dict(value)))


def _pin_exception_input(
    *,
    root: str,
    binding: Mapping[str, Any],
    policy: Mapping[str, Any],
    chain_records: Sequence[Mapping[str, Any]],
    intents: Sequence[Mapping[str, Any]],
    dispatch_records: Sequence[Mapping[str, Any]],
    terminals: Sequence[Mapping[str, Any]],
    raw_responses: Mapping[str, bytes],
    confirmations: Sequence[Mapping[str, Any]],
    coordinator_events: Sequence[Mapping[str, Any]],
) -> None:
    pin = LEGACY_PIN
    _require(root == ROOT, "legacy_timeout_root_mismatch")
    _require(
        binding.get("root") == ROOT
        and binding.get("release_sha256") == pin["release_sha256"]
        and binding.get("binding_sha256") == pin["binding_sha256"]
        and binding.get("storage") == pin["storage"]
        and _canonical_sha(binding) == pin["binding_file_sha256"],
        "legacy_timeout_binding_mismatch",
    )
    _require(
        policy.get("policy_sha256") == pin["policy_sha256"]
        and _canonical_sha(policy) == pin["policy_file_sha256"],
        "legacy_timeout_policy_mismatch",
    )
    target_plans = [
        row for row in chain_records if row.get("record_sha256") == pin["plan_record_sha256"]
    ]
    _require(
        len(target_plans) == 1
        and target_plans[0].get("sequence") == 2
        and target_plans[0].get("bucket_close_ns") == 1791865800000000000,
        "legacy_timeout_plan_record_mismatch",
    )
    target_intents = [row for row in intents if row.get("request_id") == REQUEST_ID]
    _require(len(target_intents) == 1, "legacy_timeout_intent_count")
    intent = target_intents[0]
    _require(
        _canonical_sha(intent) == pin["intent_file_sha256"]
        and intent.get("intent_sha256") == pin["intent_sha256"]
        and intent.get("request", {}).get("plan_record_sha256") == pin["plan_record_sha256"]
        and intent.get("request", {}).get("timeout_seconds") == BASE_TIMEOUT_SECONDS
        and intent.get("decision_ns") == 1791865948837668000,
        "legacy_timeout_intent_mismatch",
    )
    target_dispatches = [row for row in dispatch_records if row.get("request_id") == REQUEST_ID]
    _require(
        len(target_dispatches) == 1
        and target_dispatches[0].get("record_sha256") == pin["dispatch_record_sha256"]
        and target_dispatches[0].get("sequence") == pin["dispatch_sequence"]
        and target_dispatches[0].get("dispatch_ns") == pin["dispatch_ns"]
        and target_dispatches[0].get("intent_sha256") == pin["intent_sha256"],
        "legacy_timeout_dispatch_mismatch",
    )
    target_terminals = [row for row in terminals if row.get("request_id") == REQUEST_ID]
    _require(len(target_terminals) == 1, "legacy_timeout_terminal_count")
    terminal = target_terminals[0]
    _require(
        _canonical_sha(terminal) == pin["terminal_file_sha256"]
        and terminal.get("status") == "transport_uncertain_possibly_spent"
        and terminal.get("transport_error_class") == "URLError"
        and terminal.get("http_status") is None
        and terminal.get("provider_io") is True
        and terminal.get("request_started_ns") == pin["request_started_ns"]
        and terminal.get("receipt_ns") == pin["receipt_ns"]
        and terminal.get("dispatch_ns") == pin["dispatch_ns"]
        and terminal.get("cooldown_until_ns") == pin["cooldown_until_ns"]
        and terminal.get("intent_sha256") == pin["intent_sha256"]
        and terminal.get("raw_body_b64") is None
        and terminal.get("response_bytes") is None
        and terminal.get("response_sha256") is None,
        "legacy_timeout_terminal_mismatch",
    )
    _require(REQUEST_ID not in raw_responses, "legacy_timeout_unexpected_raw_response")
    target_confirmations = [row for row in confirmations if row.get("request_id") == REQUEST_ID]
    _require(len(target_confirmations) == 1, "legacy_timeout_confirmation_count")
    confirmation = target_confirmations[0]
    _require(
        _canonical_sha(confirmation) == pin["confirmation_file_sha256"]
        and confirmation.get("confirmation_sha256") == pin["confirmation_sha256"]
        and confirmation.get("outcome_sha256") == pin["terminal_file_sha256"]
        and confirmation.get("availability_ns") == pin["availability_ns"]
        and confirmation.get("coordinator_event_sha256")
        == pin["coordinator_terminal_event_sha256"],
        "legacy_timeout_confirmation_mismatch",
    )
    reserve_events = [
        row
        for row in coordinator_events
        if row.get("event_sha256") == pin["coordinator_reserved_event_sha256"]
    ]
    outcome_events = [
        row
        for row in coordinator_events
        if row.get("event_sha256") == pin["coordinator_terminal_event_sha256"]
    ]
    _require(
        len(reserve_events) == len(outcome_events) == 1
        and reserve_events[0].get("kind") == "reserved"
        and reserve_events[0].get("fields", {}).get("request_id") == REQUEST_ID
        and reserve_events[0].get("fields", {}).get("service") == "gecko"
        and reserve_events[0].get("fields", {}).get("intent_bytes_sha256")
        == pin["intent_file_sha256"]
        and outcome_events[0].get("kind") == "ambiguous_terminal"
        and outcome_events[0].get("fields", {}).get("reservation_id") == REQUEST_ID
        and outcome_events[0].get("fields", {}).get("outcome")
        == "transport_uncertain_possibly_spent"
        and outcome_events[0].get("fields", {}).get("outcome_sha256")
        == pin["terminal_file_sha256"],
        "legacy_timeout_coordinator_mismatch",
    )

    # The exact target is the only allowed overrun. Every other result must
    # still meet both original 20-second intervals before the local replay copy
    # receives its narrowly raised bound.
    dispatch_by_id = {row.get("request_id"): row for row in dispatch_records}
    for row in terminals:
        rid = row.get("request_id")
        if rid == REQUEST_ID:
            continue
        dispatch = dispatch_by_id.get(rid)
        _require(type(dispatch) is dict, "legacy_timeout_other_dispatch_missing")
        started, received = row.get("request_started_ns"), row.get("receipt_ns")
        dispatch_ns = dispatch.get("dispatch_ns")
        _require(
            type(started) is int
            and type(received) is int
            and type(dispatch_ns) is int
            and dispatch_ns <= started <= dispatch_ns + BASE_TIMEOUT_SECONDS * 1_000_000_000
            and started <= received <= started + BASE_TIMEOUT_SECONDS * 1_000_000_000,
            "legacy_timeout_second_overrun",
        )


def _verify_with_local_timeout(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run the unchanged verifier function with an isolated module-global proxy."""
    original = verifier.verify_capture_prefix_v2
    globals_copy = dict(original.__globals__)
    scout_module = cast(Any, globals_copy["scout"])
    proxy_values = dict(vars(scout_module))
    proxy_values["HTTP_TIMEOUT_SECONDS"] = LOCAL_EXCEPTION_TIMEOUT_SECONDS
    globals_copy["scout"] = types.SimpleNamespace(**proxy_values)
    local_verify = types.FunctionType(
        original.__code__,
        globals_copy,
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    local_verify.__kwdefaults__ = original.__kwdefaults__
    return local_verify(*args, **kwargs)


def verify_capture_prefix_v2(**kwargs: Any) -> dict[str, Any]:
    """Replay normally, with one exact legacy timeout exception if necessary."""
    try:
        return verifier.verify_capture_prefix_v2(**kwargs)
    except verifier.VerifyError as exc:
        if exc.args != ("v2_terminal_times",) or kwargs.get("root") != ROOT:
            raise
    _pin_exception_input(
        root=kwargs["root"],
        binding=kwargs["binding"],
        policy=kwargs["policy"],
        chain_records=kwargs["chain_records"],
        intents=kwargs["intents"],
        dispatch_records=kwargs["dispatch_records"],
        terminals=kwargs["terminals"],
        raw_responses=kwargs["raw_responses"],
        confirmations=kwargs["confirmations"],
        coordinator_events=kwargs["coordinator_events"],
    )
    summary = _verify_with_local_timeout(**kwargs)
    summary["legacy_wall_timeout_exception"] = {
        "schema": SCHEMA,
        "request_id": REQUEST_ID,
        "request_started_ns": LEGACY_PIN["request_started_ns"],
        "receipt_ns": LEGACY_PIN["receipt_ns"],
        "interval_ns": LEGACY_PIN["receipt_ns"] - LEGACY_PIN["request_started_ns"],
        "exception_bound_seconds": LOCAL_EXCEPTION_TIMEOUT_SECONDS,
        "provider_outcome": "transport_uncertain_possibly_spent",
        "retry_authorized": False,
    }
    return summary


__all__ = ["LegacyTimeoutError", "SCHEMA", "verify_capture_prefix_v2"]
