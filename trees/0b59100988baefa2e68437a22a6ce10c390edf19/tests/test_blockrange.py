import json
from dataclasses import replace

import pytest
from test_collection import setup_store
from test_full_blocks import client_for_blocks

from memecoin_ai.blockrange import listed_slots, recover_ranges
from memecoin_ai.collection import BudgetExhausted, PilotCollector
from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.reconciliation import audit_pilot, export_pilot, verify_pilot
from memecoin_ai.sources import ReadOnlyRPC


def range_client(info):
    client, calls = client_for_blocks(info)
    underlying = client.transport

    def transport(endpoint, body, timeout, limit):
        req = json.loads(body)
        if req["method"] == "getBlocks":
            calls.append(req)
            assert req["params"] == [101, 110, dict(commitment="finalized")]
            return canonical(dict(jsonrpc="2.0", id=1, result=[110])).encode()
        assert req["method"] == "getBlock"
        return underlying(endpoint, body, timeout, limit)

    return ReadOnlyRPC("https://example.invalid", "fixture", transport=transport), calls


def test_range_recovery_can_replace_incomplete_address_scans_without_faking_them(tmp_path):
    store, info = setup_store(tmp_path / "pilot.sqlite")
    try:
        client, calls = range_client(info)
        with pytest.raises(BudgetExhausted):
            recover_ranges(PilotCollector(store, client, budget=1, delay=0))
        assert not audit_pilot(store)["all_intervals_consistent"]
        assert store.pending()["scans"] == 3
        recover_ranges(PilotCollector(store, client, budget=1, delay=0))
        assert [c["method"] for c in calls] == ["getBlocks", "getBlock"]
        assert not any(store.pending().values())
        report = audit_pilot(store)
        assert report["all_intervals_consistent"]
        assert not report["intervals"][0]["scans_complete"]
        assert report["intervals"][0]["signatures"] == 2
        assert "block_range_coverage_capture" in report["intervals"][0]
        assert "full block range" in report["coverage_scope"]
        assert store.db.execute("select count(*) from pages").fetchone()[0] == 0
        recover_ranges(PilotCollector(store, client, budget=1, delay=0))
        assert len(calls) == 2
        export_pilot(store, tmp_path / "frozen")
        assert verify_pilot(tmp_path / "frozen")["consistent"]
        with store.db:
            store.db.execute("delete from transactions where signature=?", ("a-second",))
        with pytest.raises(ValidationError, match="range transaction missing"):
            audit_pilot(store)
    finally:
        store.close()


@pytest.mark.parametrize("slots", [[], [109], [110, 110], [111], [100, 110]])
def test_invalid_block_enumeration_never_closes_an_interval(tmp_path, slots):
    store, info = setup_store(tmp_path / "pilot.sqlite")
    try:
        client, _ = range_client(info)
        cap = client.fetch("getBlocks", [101, 110, dict(commitment="finalized")])
        bad = replace(cap, response=canonical(dict(jsonrpc="2.0", id=1, result=slots)).encode())
        with pytest.raises(ValidationError):
            listed_slots(bad, 100, 110)
        with pytest.raises(ValidationError):
            listed_slots(cap, 0, 1100)
    finally:
        store.close()
