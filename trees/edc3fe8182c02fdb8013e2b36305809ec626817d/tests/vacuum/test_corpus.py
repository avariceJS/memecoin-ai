import copy
import sqlite3

import pytest

from memecoin_pumpswap.corpus import merge_events, schema
from memecoin_registry.contracts import digest


def event():
    return dict(
        id=digest(dict(signature="synthetic", path=[0])),
        signature="synthetic",
        path=[0],
        slot=1,
        event_s=1,
        pool="p",
        base_mint="b",
        quote_mint="q",
        side="buy",
        movement=dict(base_amount=1),
        received_ns=2,
        available_ns=3,
        raw_sha="a" * 64,
        profiles=["synthetic"],
        training_eligible=False,
        execution_supported=False,
    )


def test_cross_capture_dedup_preserves_provenance_and_rejects_revision():
    with sqlite3.connect(":memory:") as db:
        schema(db)
        e = event()
        assert merge_events(db, [e], "first") == 0
        second = dict(e, received_ns=4, available_ns=5, raw_sha="b" * 64)
        assert merge_events(db, [second], "second") == 1
        assert db.execute("select count(*) from events").fetchone()[0] == 1
        assert db.execute("select count(*) from provenance").fetchone()[0] == 2
        bad = copy.deepcopy(e)
        bad["movement"]["base_amount"] = 2
        with pytest.raises(ValueError, match="revision"):
            merge_events(db, [bad], "third")
        assert db.execute("select count(*) from provenance").fetchone()[0] == 2
