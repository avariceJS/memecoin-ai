import json
import time

import pytest

from memecoin_ai.sources import ReadOnlyRPC, TransportSourceError
from memecoin_brain_sources.events import capacity, health_action, ingest, view
from memecoin_brain_sources.probe import run
from memecoin_ml.data import read, write
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
                transaction=dict(signatures=["s2"]),
            )
        return json.dumps(dict(jsonrpc="2.0", id=1, result=value)).encode()

    client = ReadOnlyRPC("https://fixture.invalid/private", "fixture", transport=transport)
    result = run(tmp_path, client, pacing=0)
    assert result["attempts"] == 4 and result["nominal_cu"] == 140
    assert result["pages"]["pool"]["history_complete"] is False
    assert not result["training_eligible"] and not result["decoder_qualified"]
    assert result["event_view"]["events"][0]["semantic_kind"] == "account_reference"
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
