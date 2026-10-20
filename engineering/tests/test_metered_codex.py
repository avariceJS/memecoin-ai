import pytest

from engineering.metered_codex import Controller, MeteredCodex
from engineering.usage_ledger import UsageLedger


def quota(percent=68):
    return {
        "ordinaryUsageAllowed": True,
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {"usedPercent": percent, "resetsAt": 9999},
                "secondary": None,
                "rateLimitReachedType": None,
                "spendControlReached": False,
                "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
            }
        },
    }


class FakeRPC:
    def __init__(self, ledger):
        self.ledger = ledger
        self.calls = 0

    def start(self, model, instructions, *, permit):
        permit.consume("new-thread", model)
        return "thread"

    def turn(self, thread, prompt, **kwargs):
        self.calls += 1
        assert self.ledger.summary()["reservations"]
        kwargs["permit"].consume(thread, kwargs["model"])
        kwargs["started"]("provider-turn")
        return {
            "text": "{}",
            "turn_id": "provider-turn",
            "usage": {"total": {"totalTokens": 10}},
            "usage_start": None,
            "usage_end": None,
            "usage_delta": None,
            "usage_terminal": False,
        }


def make(tmp_path, percent=68):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    rpc = FakeRPC(ledger)
    controller = Controller(
        ledger,
        lambda: quota(percent),
        state=tmp_path,
        clock=lambda: 1000,
        preflight=lambda: {"resources": {"policy_id": "CEO-20261020-02"}},
    )
    context = {
        "role": "worker",
        "team": "Experience",
        "task": "T",
        "authority": "CEO-20261020-02",
    }
    return ledger, rpc, controller, MeteredCodex(rpc, controller, context)


def test_pre_send_reservation_and_replay_and_stop(tmp_path):
    ledger, rpc, controller, client = make(tmp_path)
    thread = client.start("gpt-6-luna", "bounded")
    kwargs = {
        "dispatch_id": "d1",
        "model": "gpt-6-luna",
        "estimate": {"tokens": 100, "wpu": 1},
        "tool": lambda _: None,
        "deadline_seconds": 30,
    }
    result = client.turn(thread, "bounded", **kwargs)
    assert result["dispatch_id"] == "d1"
    assert ledger.summary()["reservations"]
    calls = rpc.calls
    with pytest.raises(RuntimeError):
        client.turn(thread, "again", **kwargs)
    assert rpc.calls == calls
    (tmp_path / "STOP").touch()
    with pytest.raises(RuntimeError):
        client.turn(thread, "blocked", **{**kwargs, "dispatch_id": "d2"})
    assert rpc.calls == calls
    controller.close()
    ledger.close()


def test_quota_99_blocks_before_reservation(tmp_path):
    ledger, _, controller, client = make(tmp_path, 99)
    with pytest.raises(RuntimeError):
        client.turn(
            "thread",
            "prompt",
            dispatch_id="d",
            model="gpt-6-luna",
            estimate={"tokens": 1, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=3,
        )
    assert not ledger.summary()["reservations"]
    controller.close()
    ledger.close()


def test_lost_response_and_unknown_end_keep_dispatch_obligation(tmp_path):
    ledger, rpc, controller, client = make(tmp_path)
    thread = client.start("gpt-6-luna", "bounded")
    rpc.turn = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("lost response"))
    with pytest.raises(TimeoutError):
        client.turn(
            thread,
            "prompt",
            dispatch_id="lost",
            model="gpt-6-luna",
            estimate={"tokens": 100, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=20,
        )
    rows = ledger.summary()["reservations"]
    assert any(row.get("dispatch_id") == "lost" and row["status"] == "unknown" for row in rows)
    with pytest.raises((RuntimeError, ValueError)):
        client.turn(
            thread,
            "retry",
            dispatch_id="lost",
            model="gpt-6-luna",
            estimate={"tokens": 100, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=20,
        )
    assert rpc.calls == 0
    controller.close()
    ledger.close()


def test_unknown_required_or_paid_quota_evidence_stops_before_send(tmp_path):
    for name, change in (
        ("missing reset", lambda q: q["rateLimitsByLimitId"]["codex"]["primary"].pop("resetsAt")),
        (
            "paid credits",
            lambda q: q["rateLimitsByLimitId"]["codex"]["credits"].update(hasCredits=True),
        ),
        ("unknown spend", lambda q: q["rateLimitsByLimitId"]["codex"].pop("spendControlReached")),
    ):
        q = quota()
        change(q)
        ledger = UsageLedger(tmp_path / (name.replace(" ", "-") + ".sqlite"))
        rpc = FakeRPC(ledger)
        controller = Controller(ledger, lambda q=q: q, state=tmp_path, clock=lambda: 1000)
        client = MeteredCodex(rpc, controller, {"role": "worker", "team": "X", "task": "T"})
        with pytest.raises(RuntimeError):
            client.turn(
                "thread",
                "prompt",
                dispatch_id=name,
                model="gpt-6-luna",
                estimate={"tokens": 100, "wpu": 1},
                tool=lambda _: None,
                deadline_seconds=10,
            )
        assert rpc.calls == 0
        assert not ledger.summary()["reservations"]
        controller.close()
        ledger.close()
