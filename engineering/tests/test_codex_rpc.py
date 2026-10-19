"""Adapter contracts tested without inference or executing candidate commands."""

import queue
from unittest.mock import Mock

import pytest

from engineering.codex_rpc import Codex
from engineering.supervisor import tool_function


def test_turn_returns_final_message_without_commentary():
    client = Codex.__new__(Codex)
    client.call = Mock(return_value={"turn": {"id": "turn"}})
    client.sink = Mock()
    client.messages = queue.Queue()
    client.notifications = [
        {"method": "item/completed", "params": {
            "threadId": "thread", "turnId": "turn",
            "item": {"type": "agentMessage", "phase": "commentary", "text": "Working"},
        }},
        {"method": "item/completed", "params": {
            "threadId": "thread", "turnId": "other",
            "item": {"type": "agentMessage", "phase": "final_answer", "text": "stale"},
        }},
        {"method": "item/completed", "params": {
            "threadId": "thread", "turnId": "turn",
            "item": {"type": "agentMessage", "phase": "final_answer", "text": '{"ok":true}'},
        }},
        {"method": "turn/completed", "params": {
            "threadId": "thread", "turn": {"id": "turn", "status": "completed"},
        }},
    ]
    result = client.turn("thread", "bounded", model="gpt-6-luna", tool=Mock())
    assert result["text"] == '{"ok":true}'
    assert client.call.call_args.args == ("turn/start", client.call.call_args.args[1])
    assert client.call.call_args.args[1]["effort"] == "high"


@pytest.mark.parametrize("timeout", [1000, 10000, 0, 91, True, 1.5, "30", None])
def test_broker_rejects_wrong_timeout_units_before_execution(tmp_path, timeout):
    box = Mock()
    execute = tool_function(box, tmp_path, Mock())
    with pytest.raises(ValueError, match="integer seconds in 1..90"):
        execute({"argv": ["/bin/pwd"], "timeout": timeout})
    box.run.assert_not_called()


def test_registered_tool_explains_timeout_units(tmp_path):
    client = Codex.__new__(Codex)
    client.cwd = tmp_path
    client.call = Mock(return_value={"thread": {"id": "thread"}})
    assert client.start("gpt-6-luna", "bounded") == "thread"
    params = client.call.call_args.args[1]
    tool = params["dynamicTools"][0]
    timeout = tool["inputSchema"]["properties"]["timeout"]
    assert "seconds" in timeout["description"]
    assert (timeout["minimum"], timeout["maximum"]) == (1, 90)
    assert params["allowProviderModelFallback"] is False
