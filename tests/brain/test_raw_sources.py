import json
import time

import pytest

from memecoin_ai.sources import Capture, ReadOnlyRPC, TransportSourceError
from memecoin_brain_sources.events import (
    capacity,
    health_action,
    ingest,
    transaction_evidence,
    view,
)
from memecoin_brain_sources.probe import run
from memecoin_brain_sources.replay import reproduce
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import Ledger


def evidence(**kw):
    return dict(
        provider="a",
        network="solana",
        identity="sig",
        semantic_sha="first",
        status="raw_reference",
        available_ns=1,
        event_time_s=0,
        semantic_kind="account_reference",
        **kw,
    )


def test_duplicate_restart_conflict_and_source_identity_preserve_asof(tmp_path):
    binding = dict(scope="fixture")
    with Ledger(tmp_path / "ledger", binding) as ledger:
        a = ingest(ledger, evidence())
        cut = a["time_ns"]
        assert ingest(ledger, evidence())["payload"]["classification"] == "duplicate"
        other = evidence()
        other["provider"] = "b"
        ingest(ledger, other)
    with Ledger(tmp_path / "ledger", binding) as ledger:
        changed = evidence()
        changed["semantic_sha"] = "second"
        ingest(ledger, changed)
        assert len(view(ledger, cut)["events"]) == 1
        assert not view(ledger, cut)["events"][0]["conflicted"]
        latest = view(ledger, time.time_ns())["events"]
        assert latest[0]["status"] == "revision_quarantine"
        assert latest[1]["status"] == "raw_reference"
        assert all(not e["training_eligible"] for e in latest)
        assert latest[0]["first_available_ns"] == cut


def test_null_then_hydration_does_not_backdate_availability(tmp_path):
    with Ledger(tmp_path / "ledger", dict(scope="fixture")) as ledger:
        e = evidence()
        e["status"] = "unavailable"
        ingest(ledger, e)
        assert view(ledger, time.time_ns())["events"][0]["first_available_ns"] is None
        hydrated = ingest(ledger, evidence())
        assert (
            view(ledger, time.time_ns())["events"][0]["first_available_ns"] == hydrated["time_ns"]
        )


def test_hash_tamper_fails_and_health_is_scoped(tmp_path):
    with Ledger(tmp_path / "ledger", dict(scope="fixture")) as ledger:
        ingest(ledger, evidence())
        p = next((tmp_path / "ledger").glob("event-*.json"))
        r = read(p)
        r["payload"]["evidence"]["semantic_sha"] = "bad"
        p.write_text(json.dumps(r))
        with pytest.raises(ValueError, match="ledger_chain_invalid"):
            view(ledger, time.time_ns())
    assert health_action("schema")["scope"] == "provider_pool"
    assert health_action("auth")["scope"] == "provider"
    assert health_action("hash_integrity")["scope"] == "all_lanes"
    with pytest.raises(ValueError):
        health_action("unknown")


def test_raw_probe_fully_bounded_and_not_trade_dataset(tmp_path):
    write(tmp_path / "binding.json", dict(universe=["pool"], cohort=["pool"]))
    calls = []

    def transport(endpoint, body, timeout, limit):
        q = json.loads(body)
        calls.append(q["method"])
        if q["method"] == "getMultipleAccounts":
            value = dict(context=dict(slot=100), value=[dict(owner="program")])
        elif q["method"] == "getSignaturesForAddress":
            value = [
                dict(
                    signature="s2" if len(calls) == 2 else "s1",
                    slot=90 if len(calls) == 2 else 89,
                    err=None,
                    blockTime=10,
                )
            ]
        else:
            value = dict(
                slot=90,
                blockTime=10,
                version=0,
                meta=dict(err=None),
                transaction=dict(signatures=["s2"], message=dict(accountKeys=["pool"])),
            )
        return json.dumps(dict(jsonrpc="2.0", id=1, result=value)).encode()

    client = ReadOnlyRPC("https://fixture.invalid/private", "fixture", transport=transport)
    result = run(tmp_path, client, pacing=0)
    assert result["attempts"] == 4 and result["nominal_cu"] == 140
    assert result["pages"]["pool"]["history_complete"] is False
    assert not result["training_eligible"] and not result["decoder_qualified"]
    assert result["event_view"]["events"][0]["semantic_kind"] == "account_reference"
    assert reproduce(tmp_path, sha(tmp_path / "manifest.json"))["exact"]
    with pytest.raises(ValueError, match="external_pin_mismatch"):
        reproduce(tmp_path, "wrong")
    with pytest.raises(FileExistsError):
        run(tmp_path, client, pacing=0)


def test_failed_transport_consumes_attempt_and_never_retries(tmp_path):
    write(tmp_path / "binding.json", dict(universe=["pool"], cohort=["pool"]))
    calls = []

    def transport(*a):
        calls.append(1)
        raise TransportSourceError("timeout")

    client = ReadOnlyRPC("https://fixture.invalid/private", "fixture", transport=transport)
    with pytest.raises(RuntimeError, match="transport_timeout"):
        run(tmp_path, client, pacing=0)
    assert calls == [1] and read(tmp_path / "attempts/000000/reservation.json")["cu"] == 20
    assert read(tmp_path / "STOP")["no_retry"]
    assert "private" not in (tmp_path / "STOP").read_text()


def test_capacity_exposes_polling_bottleneck():
    c = capacity(300)
    assert c["serial_gecko_cycle_seconds"] == 3600
    assert c["total_CU_per_30days"] == 622080000


@pytest.mark.parametrize("pools", [True, 1.5, "3", -1])
def test_capacity_rejects_invalid_pool_counts(pools):
    with pytest.raises(ValueError):
        capacity(pools)


@pytest.mark.parametrize("seconds", [True, "300", 0, -1, float("nan"), float("inf"), -float("inf")])
def test_capacity_rejects_invalid_poll_intervals(seconds):
    with pytest.raises(ValueError):
        capacity(3, seconds=seconds)


@pytest.mark.parametrize(
    "rate", [True, "1", -1, float("nan"), float("inf"), -float("inf")]
)
def test_capacity_rejects_invalid_transaction_rates(rate):
    with pytest.raises(ValueError):
        capacity(3, tx_per_pool_minute=rate)


def test_capacity_preserves_zero_pools_and_zero_rate():
    c = capacity(0, tx_per_pool_minute=0)
    assert c["gecko_GET_per_day"] == 0
    assert c["serial_gecko_cycle_seconds"] == 0
    assert c["signature_poll_CU_per_day"] == 0
    assert c["hydration_CU_per_day"] == 0
    assert c["total_CU_per_30days"] == 0
    assert c["actual_traffic_measured"] is False

    c = capacity(3, tx_per_pool_minute=0)
    assert c["hydration_CU_per_day"] == 0
    assert c["total_CU_per_30days"] == c["gecko_GET_per_day"] * 40 * 30
    assert c["actual_traffic_measured"] is False


@pytest.mark.parametrize(
    ("pools", "seconds", "rate"),
    [
        (1, 5e-324, 1),
        (10**10000, 300, 1),
        (1, 300, 10**10000),
    ],
    ids=["tiny-interval", "huge-pool-count", "huge-rate"],
)
def test_capacity_rejects_unrepresentable_derived_arithmetic(pools, seconds, rate):
    with pytest.raises(ValueError, match="capacity_arithmetic_unrepresentable"):
        capacity(pools, seconds=seconds, tx_per_pool_minute=rate)


def test_capacity_accepts_large_finite_rate_when_results_are_representable():
    c = capacity(1, tx_per_pool_minute=1e100)
    assert c["hydration_CU_per_day"] == 1e100 * 1440 * 40
    assert c["actual_traffic_measured"] is False


def test_capacity_preserves_valid_fractional_interval_calculations():
    c = capacity(3, seconds=2.5, tx_per_pool_minute=0.5)
    assert c["gecko_GET_per_day"] == 3 * 86400 / 2.5
    assert c["hydration_CU_per_day"] == 3 * 0.5 * 1440 * 40


def test_causal_admission_rejects_future_receipt_and_unknown_health(tmp_path):
    with Ledger(tmp_path / "ledger", dict(scope="fixture")) as ledger:
        e = evidence()
        e["available_ns"] = time.time_ns() + 10**12
        with pytest.raises(ValueError, match="evidence_scope_or_time"):
            ingest(ledger, e)
        assert not ledger.events


def test_nonprogressing_pagination_stops_without_hydration(tmp_path):
    write(tmp_path / "binding.json", dict(universe=["pool"], cohort=["pool"]))
    calls = []

    def transport(endpoint, body, timeout, limit):
        method = json.loads(body)["method"]
        calls.append(method)
        value = (
            dict(context=dict(slot=100), value=[dict(owner="program")])
            if method == "getMultipleAccounts"
            else [dict(signature="same", slot=90, err=None)]
        )
        return json.dumps(dict(jsonrpc="2.0", id=1, result=value)).encode()

    client = ReadOnlyRPC("https://fixture.invalid/private", "fixture", transport=transport)
    with pytest.raises(ValueError, match="cursor_no_progress"):
        run(tmp_path, client, pacing=0)
    assert len(calls) == 3 and "getTransaction" not in calls
    assert read(tmp_path / "STOP")["no_retry"]
    assert all((p / "manifest.json").exists() for p in (tmp_path / "attempts").iterdir())


def _transaction_fixture(path, *, version, slot, err=None):
    path.mkdir()
    value = (
        None
        if version is None and slot is None
        else dict(
            version=version,
            slot=slot,
            blockTime=10,
            meta=dict(err=err),
            transaction=dict(signatures=["sig"], message=dict(accountKeys=[])),
        )
    )
    request = json.dumps(
        dict(
            jsonrpc="2.0",
            id=1,
            method="getTransaction",
            params=["sig", dict(commitment="finalized")],
        )
    )
    capture = Capture(
        "fixture",
        "getTransaction",
        request,
        1,
        2,
        json.dumps(dict(jsonrpc="2.0", id=1, result=value)).encode(),
    )
    capture.save(path / "capture")
    write(path / "availability.json", dict(available_ns=3))
    seal(path)
    return path


@pytest.mark.parametrize(
    ("version", "slot"),
    [(False, 10), (0.0, 10), (0, True), (0, -1)],
)
def test_transaction_evidence_rejects_non_exact_version_and_slot_types(tmp_path, version, slot):
    evidence = transaction_evidence(
        _transaction_fixture(tmp_path / "capture", version=version, slot=slot)
    )
    assert evidence["status"] == "unsupported"
    assert evidence["available_ns"] == 3
    assert evidence["semantic_kind"] == "account_reference"
    assert evidence["decoded_trade"] is False
    assert evidence["execution_supported"] is False
    assert evidence["historical_online_available"] is False


@pytest.mark.parametrize(
    ("version", "slot", "err", "status"),
    [
        ("legacy", 0, None, "raw_reference"),
        (0, 0, None, "raw_reference"),
        (0, 1, {"InstructionError": [0, "Custom"]}, "failed_transaction"),
    ],
)
def test_transaction_evidence_accepts_exact_valid_boundaries(
    tmp_path, version, slot, err, status
):
    evidence = transaction_evidence(
        _transaction_fixture(tmp_path / "capture", version=version, slot=slot, err=err)
    )
    assert evidence["status"] == status
    assert evidence["slot"] == slot
    assert evidence["available_ns"] == 3
    assert evidence["semantic_kind"] == "account_reference"
    assert evidence["decoded_trade"] is False
    assert evidence["execution_supported"] is False
    assert evidence["historical_online_available"] is False


def test_transaction_evidence_preserves_unavailable_behavior(tmp_path):
    evidence = transaction_evidence(
        _transaction_fixture(tmp_path / "capture", version=None, slot=None)
    )
    assert evidence["status"] == "unavailable"
    assert evidence["event_time_s"] is None
    assert evidence["slot"] is None
    assert evidence["available_ns"] == 3
