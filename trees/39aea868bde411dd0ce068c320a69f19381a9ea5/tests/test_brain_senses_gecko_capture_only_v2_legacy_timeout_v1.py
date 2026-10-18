from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools import brain_senses_gecko_capture_only_v2_legacy_timeout_v1 as adapter  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_verify as verifier  # noqa: E402
from tools import brain_senses_gecko_capture_only_v4_host as host_v4  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2  # noqa: E402


def _sha(value: dict[str, Any]) -> str:
    return hashlib.sha256(shared_usage.canonical_json(value)).hexdigest()


def _case() -> dict[str, Any]:
    pin = copy.deepcopy(adapter.LEGACY_PIN)
    rid = adapter.REQUEST_ID
    binding = {
        "root": adapter.ROOT,
        "release_sha256": pin["release_sha256"],
        "binding_sha256": pin["binding_sha256"],
        "storage": pin["storage"],
    }
    policy = {"policy_sha256": pin["policy_sha256"]}
    chain_row = {
        "record_sha256": pin["plan_record_sha256"],
        "sequence": 2,
        "bucket_close_ns": 1791865800000000000,
    }
    intent = {
        "request_id": rid,
        "intent_sha256": "2" * 64,
        "decision_ns": 1791865948837668000,
        "request": {
            "plan_record_sha256": pin["plan_record_sha256"],
            "timeout_seconds": adapter.BASE_TIMEOUT_SECONDS,
        },
    }
    dispatch = {
        "request_id": rid,
        "sequence": 9,
        "dispatch_ns": pin["dispatch_ns"],
        "record_sha256": "3" * 64,
        "intent_sha256": intent["intent_sha256"],
    }
    terminal = {
        "request_id": rid,
        "intent_sha256": intent["intent_sha256"],
        "status": "transport_uncertain_possibly_spent",
        "transport_error_class": "URLError",
        "http_status": None,
        "provider_io": True,
        "request_started_ns": pin["request_started_ns"],
        "receipt_ns": pin["receipt_ns"],
        "dispatch_ns": pin["dispatch_ns"],
        "cooldown_until_ns": pin["cooldown_until_ns"],
        "raw_body_b64": None,
        "response_bytes": None,
        "response_sha256": None,
    }
    confirmation = {
        "request_id": rid,
        "confirmation_sha256": "4" * 64,
        "outcome_sha256": _sha(terminal),
        "availability_ns": pin["availability_ns"],
        "coordinator_event_sha256": "6" * 64,
    }
    reserved = {
        "event_sha256": "5" * 64,
        "kind": "reserved",
        "fields": {
            "request_id": rid,
            "service": "gecko",
            "intent_bytes_sha256": _sha(intent),
        },
    }
    settled = {
        "event_sha256": "6" * 64,
        "kind": "ambiguous_terminal",
        "fields": {
            "reservation_id": rid,
            "outcome": "transport_uncertain_possibly_spent",
            "outcome_sha256": _sha(terminal),
        },
    }
    pin.update(
        {
            "binding_file_sha256": _sha(binding),
            "policy_file_sha256": _sha(policy),
            "intent_file_sha256": _sha(intent),
            "intent_sha256": intent["intent_sha256"],
            "dispatch_record_sha256": dispatch["record_sha256"],
            "terminal_file_sha256": _sha(terminal),
            "confirmation_file_sha256": _sha(confirmation),
            "confirmation_sha256": confirmation["confirmation_sha256"],
            "coordinator_reserved_event_sha256": reserved["event_sha256"],
            "coordinator_terminal_event_sha256": settled["event_sha256"],
        }
    )
    return {
        "root": adapter.ROOT,
        "binding": binding,
        "policy": policy,
        "chain_records": [chain_row],
        "intents": [intent],
        "dispatch_records": [dispatch],
        "terminals": [terminal],
        "raw_responses": {},
        "confirmations": [confirmation],
        "coordinator_events": [reserved, settled],
        "skips": [],
        "pin": pin,
    }


def _install_stub(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    observed: list[int] = []

    def fake_verify(**_kwargs: Any) -> dict[str, Any]:
        observed.append(scout.HTTP_TIMEOUT_SECONDS)
        if scout.HTTP_TIMEOUT_SECONDS == adapter.BASE_TIMEOUT_SECONDS:
            raise verifier.VerifyError("v2_terminal_times")
        return {"status": "verified_prefix", "attempts": 1}

    monkeypatch.setattr(verifier, "verify_capture_prefix_v2", fake_verify)
    return observed


def test_exact_legacy_timeout_is_call_local_and_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    monkeypatch.setattr(adapter, "LEGACY_PIN", case.pop("pin"))
    observed = _install_stub(monkeypatch)
    result = adapter.verify_capture_prefix_v2(**case)
    assert observed == [20, 21]
    assert scout.HTTP_TIMEOUT_SECONDS == 20
    assert result["legacy_wall_timeout_exception"] == {
        "schema": adapter.SCHEMA,
        "request_id": adapter.REQUEST_ID,
        "request_started_ns": adapter.LEGACY_PIN["request_started_ns"],
        "receipt_ns": adapter.LEGACY_PIN["receipt_ns"],
        "interval_ns": adapter.LEGACY_PIN["receipt_ns"] - adapter.LEGACY_PIN["request_started_ns"],
        "exception_bound_seconds": 21,
        "provider_outcome": "transport_uncertain_possibly_spent",
        "retry_authorized": False,
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("release", "legacy_timeout_binding_mismatch"),
        ("intent", "legacy_timeout_intent_mismatch"),
        ("dispatch", "legacy_timeout_dispatch_mismatch"),
        ("terminal", "legacy_timeout_terminal_mismatch"),
        ("confirmation", "legacy_timeout_confirmation_mismatch"),
        ("coordinator", "legacy_timeout_coordinator_mismatch"),
    ],
)
def test_changed_legacy_evidence_is_rejected(
    monkeypatch: pytest.MonkeyPatch, mutation: str, reason: str
) -> None:
    case = _case()
    pin = case.pop("pin")
    monkeypatch.setattr(adapter, "LEGACY_PIN", pin)
    observed = _install_stub(monkeypatch)
    if mutation == "release":
        case["binding"]["release_sha256"] = "f" * 64
    elif mutation == "intent":
        case["intents"][0]["decision_ns"] += 1
    elif mutation == "dispatch":
        case["dispatch_records"][0]["dispatch_ns"] += 1
    elif mutation == "terminal":
        case["terminals"][0]["status"] = "received"
    elif mutation == "confirmation":
        case["confirmations"][0]["availability_ns"] += 1
    else:
        case["coordinator_events"][1]["fields"]["outcome"] = "released"
    with pytest.raises(adapter.LegacyTimeoutError, match=reason):
        adapter.verify_capture_prefix_v2(**case)
    assert observed == [20]
    assert scout.HTTP_TIMEOUT_SECONDS == 20


def test_second_timeout_overrun_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case()
    monkeypatch.setattr(adapter, "LEGACY_PIN", case.pop("pin"))
    observed = _install_stub(monkeypatch)
    case["dispatch_records"].append(
        {
            "request_id": "other-request",
            "dispatch_ns": 1_800_000_000_000_000_000,
        }
    )
    case["terminals"].append(
        {
            "request_id": "other-request",
            "request_started_ns": 1_800_000_000_000_000_000,
            "receipt_ns": 1_800_000_021_000_000_000,
        }
    )
    with pytest.raises(adapter.LegacyTimeoutError, match="legacy_timeout_second_overrun"):
        adapter.verify_capture_prefix_v2(**case)
    assert observed == [20]


def test_other_terminal_within_twenty_seconds_remains_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    monkeypatch.setattr(adapter, "LEGACY_PIN", case.pop("pin"))
    observed = _install_stub(monkeypatch)
    other_id = "other-request"
    other_dispatch = 1_800_000_000_000_000_000
    case["dispatch_records"].append({"request_id": other_id, "dispatch_ns": other_dispatch})
    case["terminals"].append(
        {
            "request_id": other_id,
            "request_started_ns": other_dispatch + 1_000_000,
            "receipt_ns": other_dispatch + 19_999_999_999,
        }
    )
    result = adapter.verify_capture_prefix_v2(**case)
    assert result["status"] == "verified_prefix"
    assert observed == [20, 21]


def test_other_roots_and_other_verifier_failures_keep_original_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    observed = _install_stub(monkeypatch)
    other_root = dict(case, root="services/gecko-market-v1/capture-only-v2/" + "f" * 32)
    with pytest.raises(verifier.VerifyError, match="v2_terminal_times"):
        adapter.verify_capture_prefix_v2(**other_root)
    assert observed == [20]

    def unrelated_failure(**_kwargs: Any) -> dict[str, Any]:
        raise verifier.VerifyError("v2_clock_invalid")

    monkeypatch.setattr(verifier, "verify_capture_prefix_v2", unrelated_failure)
    with pytest.raises(verifier.VerifyError, match="v2_clock_invalid"):
        adapter.verify_capture_prefix_v2(**case)


def test_inventory_uses_and_v4_source_binds_the_versioned_adapter() -> None:
    assert inventory_v2.legacy_timeout_v2.verify_capture_prefix_v2 is (
        adapter.verify_capture_prefix_v2
    )
    assert "tools/brain_senses_gecko_capture_only_v2_legacy_timeout_v1.py" in (
        host_v4.RELEASE_CODE_PATHS
    )
