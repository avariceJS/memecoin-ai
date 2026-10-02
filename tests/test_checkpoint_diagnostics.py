import json
from pathlib import Path

import pytest

from memecoin_ai.causal import CausalBook
from memecoin_ai.checkpoint_diagnostics import diagnose_checkpoint
from memecoin_ai.domain import ValidationError
from memecoin_ai.sources import Capture
from memecoin_ai.tokens import decode_supported_checkpoint

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["token2022", "classic"])
def test_real_config_extension_is_not_misclassified_as_coherence_error(name):
    cap = Capture.load(FIXTURES / "config-reserved" / name)
    pool = json.loads(cap.request_json)["params"][0][0]
    result = diagnose_checkpoint(cap, pool)
    assert result["bank_clock_agreement"] and not result["economic_decoder_supported"]
    assert result["reason"] == "unsupported_config_reserved_bytes"
    assert result["reserved_config_words"] == [dict(offset=116, value=50000)]
    with pytest.raises(ValidationError, match="AmmConfig padding"):
        decode_supported_checkpoint(cap, pool)
    b = CausalBook([pool], max_receipt_age_ns=1, max_chain_age_ns=1, max_gap_ns=1)
    with pytest.raises(ValidationError, match="only validated"):
        b.ingest(result)


def test_real_clock_mismatch_still_fails_separately_from_protocol_support():
    cap = Capture.load(FIXTURES / "clock-mismatch/first")
    result = diagnose_checkpoint(cap, json.loads(cap.request_json)["params"][0][0])
    assert result["reason"] == "clock_context_mismatch"
    assert not result["bank_clock_agreement"]
    assert not result["economic_decoder_supported"]


def test_supported_capture_remains_supported_without_creating_an_observation():
    cap = Capture.load(FIXTURES / "token2022/checkpoint-2")
    result = diagnose_checkpoint(cap, json.loads(cap.request_json)["params"][0][0])
    assert result["bank_clock_agreement"] and result["economic_decoder_supported"]
    assert result["historical_available_ns"] is None
    assert result["kind"] == "checkpoint_diagnostic_only"


def test_versioned_diagnosis_recognizes_only_confirmed_collection_terms():
    cap = Capture.load(FIXTURES / "config-reserved/token2022")
    pool = json.loads(cap.request_json)["params"][0][0]
    report = diagnose_checkpoint(cap, pool, config_layout_version=2)
    assert report["economic_decoder_supported"] and report["reason"] is None
    assert report["reserved_config_words"] == []
    assert report["config_collection_terms"]["creator_fee_share_default_ppm"] == 50000
    assert report["config_collection_terms"]["effective_creator_fee_share_ppm"] is None
    with pytest.raises(ValidationError, match="layout version"):
        diagnose_checkpoint(cap, pool, config_layout_version=3)
