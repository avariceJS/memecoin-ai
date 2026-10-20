"""Adapter contracts tested without inference or executing candidate commands."""

import queue
from pathlib import Path
from unittest.mock import Mock

import pytest

from engineering.codex_rpc import Codex
from engineering.supervisor import tool_function


class TrustedPermit:
    def consume(self, thread_id, model):
        assert model in ("gpt-6-luna", "gpt-6.1-sol")


def test_turn_returns_final_message_without_commentary():
    client = Codex.__new__(Codex)
    client.call = Mock(return_value={"turn": {"id": "turn"}})
    client.sink = Mock()
    client.messages = queue.Queue()
    client.notifications = [
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "item": {"type": "agentMessage", "phase": "commentary", "text": "Working"},
            },
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread",
                "turnId": "other",
                "item": {"type": "agentMessage", "phase": "final_answer", "text": "stale"},
            },
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "item": {"type": "agentMessage", "phase": "final_answer", "text": '{"ok":true}'},
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread",
                "turn": {"id": "turn", "status": "completed"},
            },
        },
    ]
    result = client.turn(
        "thread", "bounded", model="gpt-6-luna", tool=Mock(), permit=TrustedPermit()
    )
    assert result["text"] == '{"ok":true}'
    assert client.call.call_args.args == ("turn/start", client.call.call_args.args[1])
    assert client.call.call_args.args[1]["effort"] == "high"


def test_terminal_usage_requires_trusted_qualification_and_matching_turn():
    class QualifiedPermit(TrustedPermit):
        terminal_usage_qualified = True

    def counters(n):
        return {
            "inputTokens": n,
            "cachedInputTokens": 0,
            "outputTokens": 0,
            "reasoningOutputTokens": 0,
            "totalTokens": n,
        }

    client = Codex.__new__(Codex)
    client.call = Mock(return_value={"turn": {"id": "turn"}})
    client.sink = Mock()
    client.messages = queue.Queue()
    client.notifications = [
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread",
                "turnId": "stale",
                "tokenUsage": {"total": counters(99)},
            },
        },
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "tokenUsage": {"total": counters(10)},
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": "wrong-thread",
                "turn": {"id": "turn", "status": "completed"},
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread",
                "turn": {"id": "turn", "status": "completed"},
            },
        },
    ]
    result = client.turn(
        "thread",
        "bounded",
        model="gpt-6-luna",
        tool=Mock(),
        start_usage=counters(0),
        permit=QualifiedPermit(),
    )
    assert result["usage_end"] == counters(10)
    assert result["usage_delta"] == counters(10)
    assert result["usage_terminal"] is True


@pytest.mark.parametrize("timeout", [1000, 10000, 0, 91, True, 1.5, "30", None])
def test_broker_rejects_wrong_timeout_units_before_execution(tmp_path, timeout):
    box = Mock()
    execute = tool_function(box, tmp_path, Mock())
    with pytest.raises(ValueError, match="integer seconds in 1..90"):
        execute({"argv": ["/bin/pwd"], "timeout": timeout})
    box.run.assert_not_called()


def test_start_requires_and_consumes_permit_before_provider_call():
    client = Codex.__new__(Codex)
    client.cwd = Path("/tmp")
    client.call = Mock(return_value={"thread": {"id": "thread"}})
    with pytest.raises(Exception, match="permit_required"):
        client.start("gpt-6-luna", "bounded")
    client.call.assert_not_called()
    permit = Mock()
    assert client.start("gpt-6-luna", "bounded", permit=permit) == "thread"
    permit.consume.assert_called_once_with("new-thread", "gpt-6-luna")
    assert client.call.call_args.args[0] == "thread/start"


def test_registered_tool_explains_timeout_units(tmp_path):
    client = Codex.__new__(Codex)
    client.cwd = tmp_path
    client.call = Mock(return_value={"thread": {"id": "thread"}})
    assert client.start("gpt-6-luna", "bounded", permit=TrustedPermit()) == "thread"
    params = client.call.call_args.args[1]
    tool = params["dynamicTools"][0]
    timeout = tool["inputSchema"]["properties"]["timeout"]
    assert "seconds" in timeout["description"]
    assert (timeout["minimum"], timeout["maximum"]) == (1, 90)
    assert params["allowProviderModelFallback"] is False
