import json

import pytest

from memecoin_collector.wss_reference import PUMP_BONDING_CURVE_PROGRAM
from tools.brain_senses_wss_reference_benchmark import _mention_program, _notification


def tx(*, keys=None, loaded=None):
    meta = {"loadedAddresses": loaded or {}}
    return {
        "transaction": {"message": {"accountKeys": keys or []}, "signatures": ["sig"]},
        "meta": meta,
    }


def test_program_mention_resolves_static_and_loaded_addresses():
    assert _mention_program(tx(keys=[PUMP_BONDING_CURVE_PROGRAM])) is True
    assert (
        _mention_program(tx(loaded={"writable": [], "readonly": [PUMP_BONDING_CURVE_PROGRAM]}))
        is True
    )
    assert _mention_program(tx(keys=["other"])) is False


def test_unsupported_message_shape_fails_closed():
    with pytest.raises(ValueError, match="benchmark_account_key_encoding_unsupported"):
        _mention_program(tx(keys=[{"pubkey": PUMP_BONDING_CURVE_PROGRAM}]))


def test_notification_bytes_are_utf8_application_json():
    raw = _notification(42, "sig-A", {"InstructionError": [0, "Custom"]}, ["π"])
    message = json.loads(raw)
    assert message["method"] == "logsNotification"
    assert message["params"]["result"]["context"]["slot"] == 42
    assert message["params"]["result"]["value"]["logs"] == ["π"]
    assert len(raw) == len(raw.decode("utf-8").encode("utf-8"))
