import queue

from engineering.codex_rpc import Codex


class TrustedPermit:
    def consume(self, thread_id, model):
        assert thread_id == "thread"


def counters(total):
    return {
        "inputTokens": total,
        "cachedInputTokens": 0,
        "outputTokens": 0,
        "reasoningOutputTokens": 0,
        "totalTokens": total,
    }


def make_client(messages):
    client = Codex.__new__(Codex)
    client.usage_counters = {}
    client.notifications = []
    client.messages = queue.Queue()
    for message in messages:
        client.messages.put(message)
    client.sink = lambda event: None
    client.send = lambda message: None
    client.call = lambda method, params, **kwargs: {"turn": {"id": "turn"}}
    return client


def completed():
    return {
        "method": "turn/completed",
        "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
    }


def test_missing_end_usage_invalidates_cached_baseline_for_next_turn():
    client = make_client([completed()])
    first = client.turn(
        "thread",
        "prompt",
        model="gpt-6.1-sol",
        tool=lambda _: None,
        start_usage=counters(100),
        permit=TrustedPermit(),
    )
    assert first["usage_delta"] is None
    assert "thread" not in client.usage_counters

    client.messages.put(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "tokenUsage": {"total": counters(150)},
            },
        }
    )
    client.messages.put(completed())
    second = client.turn(
        "thread", "prompt", model="gpt-6.1-sol", tool=lambda _: None, permit=TrustedPermit()
    )
    assert second["usage_start"] is None
    assert second["usage_delta"] is None
    assert "thread" not in client.usage_counters


def test_invalid_end_usage_does_not_seed_cached_baseline():
    bad = counters(150)
    bad["totalTokens"] = 151
    client = make_client(
        [
            {
                "method": "thread/tokenUsage/updated",
                "params": {"threadId": "thread", "turnId": "turn", "tokenUsage": {"total": bad}},
            },
            completed(),
        ]
    )
    client.usage_counters["thread"] = counters(100)
    result = client.turn(
        "thread", "prompt", model="gpt-6.1-sol", tool=lambda _: None, permit=TrustedPermit()
    )
    assert result["usage_delta"] is None
    assert "thread" not in client.usage_counters
