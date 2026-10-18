import copy
import json

import pytest

from memecoin_ai.sources import TransportSourceError
from memecoin_ml.data import read, write
from memecoin_pumpswap.indexed import IndexedRPC, run
from memecoin_pumpswap.ranges import append_page, progress
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import Ledger


def tx(sig, index=1, slot=100):
    return dict(
        slot=slot,
        transactionIndex=index,
        version=0,
        blockTime=1,
        meta=dict(err=None),
        transaction=dict(signatures=[sig], message=dict(accountKeys=["p", "q"])),
    )


def test_full_budget_fixture_and_standard_overlap(tmp_path):
    write(tmp_path / "binding.json", dict(cohort=["p", "q"], anchors={"p": "p1", "q": "q1"}))
    calls = []

    def transport(url, body, timeout, limit):
        req = json.loads(body)
        calls.append(req)
        if req["method"] == "getTransaction":
            value = tx("p1", 2)
        else:
            pool, cfg = req["params"]
            assert cfg["maxSupportedTransactionVersion"] == 0
            second = "paginationToken" in cfg
            value = dict(
                transactions=[tx(pool + ("2" if second else "1"), 1 if second else 2)],
                paginationToken="next2" if second else "next1",
            )
        return json.dumps(dict(jsonrpc="2.0", id=1, result=value)).encode()

    c = IndexedRPC("https://fixture.invalid/secret", "fixture", transport=transport)
    r = run(tmp_path, c, pacing=0)
    assert len(calls) == 5 and r["nominal_cu"] == 440
    assert r["standard"]["core_equal"]
    assert all(p["progress"]["anchor_reached"] for p in r["pages"])
    assert not r["history_complete"] and not r["training_eligible"]
    with pytest.raises(FileExistsError):
        run(tmp_path, c, pacing=0)
    with pytest.raises(ValueError, match="read_only"):
        c.fetch("sendTransaction", [])


def test_failure_is_charged_and_no_retry(tmp_path):
    write(tmp_path / "binding.json", dict(cohort=["p"], anchors={"p": "s"}))
    calls = []

    def transport(*args):
        calls.append(1)
        raise TransportSourceError("timeout")

    with pytest.raises(RuntimeError, match="transport_timeout"):
        run(
            tmp_path,
            IndexedRPC("https://fixture.invalid/secret", "fixture", transport=transport),
            pacing=0,
        )
    assert calls == [1]
    assert read(tmp_path / "attempts/000000/reservation.json")["cu"] == 100
    assert "secret" not in (tmp_path / "STOP").read_text()


def test_restart_budget_and_anchor_do_not_imply_completeness(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write(raw / "fixture.json", {})
    seal(raw)
    b = dict(pool="p", max_pages=2, anchor_signature="anchor")
    with Ledger(tmp_path / "ledger", b) as ledger:
        append_page(ledger, raw, [tx("new", 3)], None, "a")
        assert not progress(ledger)["anchor_reached"]
    with Ledger(tmp_path / "ledger", b) as ledger:
        append_page(ledger, raw, [tx("anchor", 2)], "a", "b")
        assert progress(ledger)["anchor_reached"] and not progress(ledger)["history_complete"]
        with pytest.raises(ValueError, match="budget"):
            append_page(ledger, raw, [tx("old", 1)], "b", "c")
        assert len(ledger.events) == 2


@pytest.mark.parametrize(
    "case", ["revision", "order", "same_slot_collision", "cursor", "repeat_page"]
)
def test_bad_page_cannot_advance_cursor(tmp_path, case):
    raw = tmp_path / "raw"
    raw.mkdir()
    write(raw / "fixture.json", {})
    seal(raw)
    with Ledger(tmp_path / "ledger", dict(pool="p", max_pages=2, anchor_signature="s")) as ledger:
        first = tx("s", 2)
        append_page(ledger, raw, [first], None, "a")
        row = tx("next", 1)
        cursor = "a"
        nxt = "b"
        if case == "revision":
            row = copy.deepcopy(first)
            row["meta"]["err"] = "changed"
        if case == "order":
            row = tx("next", 3)
        if case == "same_slot_collision":
            row = tx("next", 2)
        if case == "cursor":
            nxt = "a"
        if case == "repeat_page":
            row = copy.deepcopy(first)
        with pytest.raises(ValueError):
            append_page(ledger, raw, [row], cursor, nxt)
        assert progress(ledger)["cursor"] == "a" and len(ledger.events) == 1
