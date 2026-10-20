"""Adapter contracts tested without inference or executing candidate commands."""

import io
import json
import queue
import threading
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
    assert client.start("gpt-6-luna", "bounded", permit=TrustedPermit()) == "thread"
    params = client.call.call_args.args[1]
    tool = params["dynamicTools"][0]
    timeout = tool["inputSchema"]["properties"]["timeout"]
    assert "seconds" in timeout["description"]
    assert (timeout["minimum"], timeout["maximum"]) == (1, 90)
    assert params["allowProviderModelFallback"] is False


def test_raw_turn_and_start_require_permit_before_provider_call():
    client = Codex.__new__(Codex)
    client.call = Mock()
    client.sink = Mock()
    client.messages = queue.Queue()
    with pytest.raises(Exception, match="permit_required"):
        client.turn("thread", "prompt", model="gpt-6-luna", tool=Mock())
    with pytest.raises(Exception, match="permit_required"):
        client.start("gpt-6-luna", "bounded")
    client.call.assert_not_called()


def test_reader_queues_raw_evidence_for_owner_thread_delivery():
    client = Codex.__new__(Codex)
    client.proc = type(
        "Process", (), {"stdout": io.BytesIO(b'not-json\r\n{"id":1,"result":{"ok":true}}\r\n')}
    )()
    client.messages = queue.Queue()
    client.notifications = []
    client.sequence = 0
    client.send = Mock()
    delivered = []
    ingressed = []
    client.raw_ingress_sink = lambda raw: ingressed.append((threading.get_ident(), raw))
    client.sink = lambda event: delivered.append((threading.get_ident(), event))
    reader = threading.Thread(target=client._read)
    reader.start()
    result = client.call("synthetic", timeout=1)
    reader.join(timeout=1)
    assert not reader.is_alive()
    assert result == {"ok": True}
    assert len(delivered) == 2
    assert all(owner == threading.get_ident() for owner, _event in delivered)
    assert [raw for _reader, raw in ingressed] == [
        b"not-json\r\n",
        b'{"id":1,"result":{"ok":true}}\r\n',
    ]
    assert all(reader != threading.get_ident() for reader, _raw in ingressed)
    assert [event["kind"] for _owner, event in delivered] == ["rpc_malformed", "rpc_raw"]
    assert delivered[0][1]["raw_bytes_hex"] == b"not-json\r\n".hex()
    assert json.loads(bytes.fromhex(delivered[1][1]["raw_bytes_hex"]))["id"] == 1
