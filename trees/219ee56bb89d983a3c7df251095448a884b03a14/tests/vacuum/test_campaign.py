import json
import time

from memecoin_ai.sources import Capture
from memecoin_ml.data import read
from memecoin_pumpswap.vacuum import run
from memecoin_runner.ledger import Ledger, publish


def binding(root):
    b = dict(
        cases=[dict(address="synthetic", start=0, end=100)],
        pages_per_case=2,
        max_rpc=2,
        max_cu=200,
        expires_ns=time.time_ns() + 10**10,
    )
    publish(root / "binding.json", b)
    return b


class Client:
    def __init__(self):
        self.calls = []

    def fetch(self, method, params):
        self.calls.append(params)
        n = len(self.calls)
        tx = dict(
            slot=n,
            transactionIndex=0,
            blockTime=1,
            transaction=dict(signatures=[str(n)], message=dict(accountKeys=["synthetic"])),
            meta=dict(err="synthetic_failure"),
        )
        return Capture(
            "synthetic",
            method,
            json.dumps(params),
            1,
            2,
            json.dumps(
                dict(jsonrpc="2.0", id=1, result=dict(data=[tx], paginationToken="next"))
            ).encode(),
        )

    def close(self):
        pass


def test_pages_durable_cursor_and_gap_debt(tmp_path):
    binding(tmp_path)
    c = Client()
    run(tmp_path, c, pacing=0)
    assert c.calls[1][1]["paginationToken"] == "next"
    # Provider repeating its token must stop; first committed page remains intact.
    assert read(tmp_path / "STOP")["category"] == "page_cursor_nonprogress"
    assert (tmp_path / "births-000000.json").exists()
    assert not (tmp_path / "births-000001.json").exists()


def test_crash_reservation_is_charged_and_never_retried(tmp_path):
    b = binding(tmp_path)
    with Ledger(tmp_path / "journal", b) as ledger:
        ledger.append("reserved", cu=100)
    c = Client()
    run(tmp_path, c, pacing=0)
    assert not c.calls
    assert read(tmp_path / "STOP")["charged_cu"] == 100
