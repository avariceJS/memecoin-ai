"""Append-only evidence observations with source-scoped identities and causal admission."""

import json
import math
import time
from pathlib import Path

from memecoin_ai.sources import Capture
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify
from memecoin_runner.ledger import Ledger


def transaction_evidence(path: Path) -> dict:
    verify(path)
    cap = Capture.load(path / "capture")
    request = json.loads(cap.request_json)
    available = read(path / "availability.json")["available_ns"]
    if not cap.request_started_ns <= cap.received_ns <= available:
        raise ValueError("capture_clock_integrity")
    if cap.method != "getTransaction" or request["params"][1]["commitment"] != "finalized":
        raise ValueError("transaction_scope")
    value = cap.result()
    signature = request["params"][0]
    if value is None:
        status = "unavailable"
    elif (
        not (
            type(value.get("version")) is str and value.get("version") == "legacy"
            or (type(value.get("version")) is int and value.get("version") == 0)
        )
        or not isinstance(value.get("meta"), dict)
        or type(value.get("slot")) is not int
        or value["slot"] < 0
    ):
        status = "unsupported"
    else:
        tx = value["transaction"]
        if not tx["signatures"] or tx["signatures"][0] != signature:
            raise ValueError("transaction_signature_mismatch")
        status = "failed_transaction" if value["meta"].get("err") is not None else "raw_reference"
    return dict(
        provider=cap.source_alias,
        network="solana-mainnet",
        identity=signature,
        semantic_sha=digest(value),
        status=status,
        available_ns=available,
        event_time_s=value.get("blockTime") if value else None,
        slot=value.get("slot") if value else None,
        raw_path=str(path.resolve()),
        manifest_sha=sha(path / "manifest.json"),
        semantic_kind="account_reference",
        decoded_trade=False,
        execution_supported=False,
        historical_online_available=False,
    )


def ingest(ledger: Ledger, evidence: dict) -> dict:
    if (
        evidence["semantic_kind"] != "account_reference"
        or type(evidence["available_ns"]) is not int
        or not 0 <= evidence["available_ns"] <= time.time_ns()
        or evidence["status"]
        not in ("raw_reference", "failed_transaction", "unsupported", "unavailable")
    ):
        raise ValueError("evidence_scope_or_time")
    key = (evidence["provider"], evidence["network"], evidence["identity"])
    prior = [
        r["payload"]["evidence"]
        for r in ledger.events
        if r["kind"] == "observation" and tuple(r["payload"]["key"]) == key
    ]
    # Null/unsupported observations never prevent later successful hydration.
    valid = [r for r in prior if r["status"] in ("raw_reference", "failed_transaction")]
    classification = "first_observation"
    if evidence["status"] in ("raw_reference", "failed_transaction") and valid:
        classification = (
            "duplicate"
            if all(r["semantic_sha"] == evidence["semantic_sha"] for r in valid)
            else "revision_conflict"
        )
    return ledger.append(
        "observation", key=list(key), classification=classification, evidence=evidence
    )


def view(ledger: Ledger, as_of_ns: int) -> dict:
    states = {}
    for row in ledger.audit():
        e = row["payload"]["evidence"]
        actual = max(row["time_ns"], e["available_ns"])
        if actual > as_of_ns:
            continue
        key = tuple(row["payload"]["key"])
        s = states.setdefault(
            key,
            dict(
                first_observed_ns=actual,
                first_available_ns=None,
                observations=0,
                conflicted=False,
                status=e["status"],
            ),
        )
        s["observations"] += 1
        s["conflicted"] |= row["payload"]["classification"] == "revision_conflict"
        s["last_observation_status"] = e["status"]
        if e["status"] in ("raw_reference", "failed_transaction"):
            if s["first_available_ns"] is None:
                s["first_available_ns"] = actual
            s["status"] = e["status"]
        if s["conflicted"]:
            s["status"] = "revision_quarantine"
        s.update(semantic_kind=e["semantic_kind"], decoded_trade=False, execution_supported=False)
    return dict(
        events=[dict(key=list(k), **v, training_eligible=False) for k, v in sorted(states.items())],
        as_of_ns=as_of_ns,
        ledger_head=ledger.head,
    )


def health_action(category: str) -> dict:
    if category in ("hash_integrity", "clock_integrity", "budget_integrity"):
        return dict(scope="all_lanes", action="fatal_stop", retry=False)
    if category in ("auth", "rate_limit"):
        return dict(scope="provider", action="disable_until_review", retry=False)
    if category == "transport":
        return dict(scope="provider", action="bounded_cooldown", retry=False)
    if category in ("schema", "unsupported_protocol", "revision_conflict"):
        return dict(scope="provider_pool", action="quarantine", retry=False)
    raise ValueError("unknown_failure_category")


def capacity(pools: int, *, seconds=300, tx_per_pool_minute=1) -> dict:
    if type(pools) is not int:
        raise ValueError("pools_must_be_int")
    if pools < 0:
        raise ValueError("pools_must_be_nonnegative")
    if type(seconds) not in (int, float):
        raise ValueError("seconds_must_be_numeric")
    if (type(seconds) is float and not math.isfinite(seconds)) or seconds <= 0:
        raise ValueError("seconds_must_be_finite_positive")
    if type(tx_per_pool_minute) not in (int, float):
        raise ValueError("tx_per_pool_minute_must_be_numeric")
    if (
        type(tx_per_pool_minute) is float
        and not math.isfinite(tx_per_pool_minute)
    ) or tx_per_pool_minute < 0:
        raise ValueError("tx_per_pool_minute_must_be_finite_nonnegative")

    try:
        polls = pools * 86400 / seconds
        tx = pools * tx_per_pool_minute * 1440
        serial_seconds = pools * 12
        signature_cu = polls * 40
        hydration_cu = tx * 40
        total_cu = (polls * 40 + tx * 40) * 30
        for value in (polls, signature_cu, hydration_cu, total_cu):
            if type(value) is float and not math.isfinite(value):
                raise ValueError("capacity_arithmetic_unrepresentable")
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("capacity_arithmetic_unrepresentable") from exc

    return dict(
        pools=pools,
        poll_seconds=seconds,
        assumed_transactions_per_pool_minute=tx_per_pool_minute,
        gecko_GET_per_day=polls,
        serial_gecko_cycle_seconds=serial_seconds,
        signature_poll_CU_per_day=signature_cu,
        hydration_CU_per_day=hydration_cu,
        total_CU_per_30days=total_cu,
        actual_traffic_measured=False,
    )
