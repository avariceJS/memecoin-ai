import copy
import json
from dataclasses import replace

import pytest
from test_collection import fixture_client, setup_store, transaction

from memecoin_ai.collection import PilotCollector, block_signatures, captured_transaction
from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.reconciliation import audit_pilot
from memecoin_ai.sources import ReadOnlyRPC


def client_for_blocks(info, corruption=None):
    client, calls = fixture_client(info)
    old_transport = client.transport

    def transport(endpoint, body, timeout, limit):
        req = json.loads(body)
        if req["method"] != "getBlock":
            assert req["method"] != "getTransaction"
            return old_transport(endpoint, body, timeout, limit)
        calls.append(req)
        transactions = [
            transaction(info, "z-first", (10000, 10000), (10100, 9950)),
            transaction(info, "a-second", (10100, 9950), (10090, 9960)),
        ]
        if corruption == "missing":
            transactions.pop()
        if corruption == "duplicate":
            transactions.append(copy.deepcopy(transactions[0]))
        for tx in transactions:
            del tx["slot"]
        return canonical(
            dict(jsonrpc="2.0", id=1, result=dict(transactions=transactions, blockTime=100))
        ).encode()

    return ReadOnlyRPC("https://example.invalid", "fixture", transport=transport), calls


def test_full_block_recovery_equals_individual_and_preserves_raw_provenance(tmp_path):
    store, info = setup_store(tmp_path / "full.sqlite")
    client, calls = client_for_blocks(info)
    try:
        PilotCollector(store, client, delay=0, full_blocks=True).recover()
        full = audit_pilot(store)
        assert full["all_intervals_consistent"]
        assert not any(store.pending().values())
        assert sum(c["method"] == "getBlock" for c in calls) == 1
        ids = {r[0] for r in store.db.execute("select capture_id from transactions")}
        assert len(ids) == 1
        cap = store.capture(ids.pop())
        assert cap.method == "getBlock"
        assert block_signatures(cap, 110) == ["z-first", "a-second"]
        assert captured_transaction(cap, "a-second", 110)["slot"] == 110
        assert captured_transaction(cap, "a-second", 110)["blockTime"] == 100
        with pytest.raises(ValidationError):
            captured_transaction(cap, "a-second", 111)
        bad = replace(cap, request_json=cap.request_json.replace("finalized", "confirmed"))
        with pytest.raises(ValidationError):
            captured_transaction(bad, "a-second", 110)
        baseline, info = setup_store(tmp_path / "individual.sqlite")
        try:
            old_client, _ = fixture_client(info)
            PilotCollector(baseline, old_client, delay=0).recover()
            assert full["intervals"] == audit_pilot(baseline)["intervals"]
        finally:
            baseline.close()
    finally:
        store.close()


@pytest.mark.parametrize("corruption", ["missing", "duplicate"])
def test_bad_block_is_retained_without_marking_work_complete(tmp_path, corruption):
    store, info = setup_store(tmp_path / "pilot.sqlite")
    try:
        client, _ = client_for_blocks(info, corruption)
        PilotCollector(store, client, delay=0, full_blocks=True).recover()
        assert store.pending()["transactions"] == 2
        assert store.db.execute("select count(*) from blocks").fetchone()[0] == 0
        assert store.db.execute("select count(*) from issues").fetchone()[0] == 1
        assert not audit_pilot(store)["all_intervals_consistent"]
    finally:
        store.close()


def test_mutating_extracted_transaction_cannot_corrupt_cached_raw_evidence(tmp_path):
    store, info = setup_store(tmp_path / "pilot.sqlite")
    try:
        client, _ = client_for_blocks(info)
        PilotCollector(store, client, delay=0, full_blocks=True).recover()
        cap = store.capture(store.db.execute("select capture_id from blocks").fetchone()[0])
        tx = captured_transaction(cap, "a-second", 110)
        tx["meta"]["err"] = {"fixture": "corrupt"}
        assert captured_transaction(cap, "a-second", 110)["meta"]["err"] is None
        assert audit_pilot(store)["all_intervals_consistent"]
    finally:
        store.close()
