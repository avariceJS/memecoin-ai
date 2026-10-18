from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools.brain_senses_external_capture_verify_v4 import (
    CaptureVerifyError,
    verify_capture,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256


@pytest.fixture
def capture_tree(tmp_path: Path) -> tuple[DescriptorWriter, dict[str, object]]:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)
    response = canonical_json({"jsonrpc": "2.0", "id": 1, "result": 123})
    request = b'{"jsonrpc":"2.0","id":1,"method":"getSlot","params":[]}'
    expected: dict[str, object] = {
        "request_id": "20261012T110000Z-0123456789ab-00-00",
        "request_sha256": hashlib.sha256(request).hexdigest(),
        "wire_request_sha256": hashlib.sha256(request).hexdigest(),
        "decision_ns": 10,
    }
    writer.mkdir("epoch/captures/request")
    writer.create("epoch/captures/request/response.json", response)
    receipt = {
        "schema": "brain-senses-external-rolling-v4-request-receipt",
        "schema_version": 1,
        "provider": "helius-free",
        "request_id": expected["request_id"],
        "request_sha256": expected["request_sha256"],
        "wire_request_sha256": expected["wire_request_sha256"],
        "request_started_ns": 11,
        "received_ns": 12,
        "availability_ns": 12,
        "decision_ns": 10,
        "response_sha256": sha256(response),
        "response_bytes": len(response),
        "storage": {
            "codec": "raw",
            "path": "response.json",
            "sha256": sha256(response),
            "bytes": len(response),
            "codec_version": None,
        },
    }
    writer.create("epoch/captures/request/receipt.json", canonical_json(receipt))
    yield writer, expected
    writer.close()


def test_capture_receipt_and_response_are_bound_by_hash_and_request(capture_tree) -> None:
    writer, expected = capture_tree
    result = verify_capture(writer, relative_directory="epoch/captures/request", expected=expected)
    assert result["response"]["result"] == 123


def test_capture_rejects_provider_error_as_captured_market_observation(capture_tree) -> None:
    writer, expected = capture_tree
    response = canonical_json({"jsonrpc": "2.0", "id": 1, "error": {"code": -32005}})
    writer.atomic_write("epoch/captures/request/response.json", response)
    # Update the receipt too: provider errors remain a response envelope, but are
    # handled by the caller as a typed failed request rather than an observation.
    receipt = json.loads(writer.read_bytes("epoch/captures/request/receipt.json"))
    receipt["response_sha256"] = sha256(response)
    receipt["response_bytes"] = len(response)
    receipt["storage"]["sha256"] = sha256(response)
    receipt["storage"]["bytes"] = len(response)
    writer.atomic_write("epoch/captures/request/receipt.json", canonical_json(receipt))
    verified = verify_capture(
        writer, relative_directory="epoch/captures/request", expected=expected
    )
    assert "error" in verified["response"]


def test_capture_rejects_receipt_hash_mismatch(capture_tree) -> None:
    writer, expected = capture_tree
    receipt = json.loads(writer.read_bytes("epoch/captures/request/receipt.json"))
    receipt["response_sha256"] = "0" * 64
    writer.atomic_write("epoch/captures/request/receipt.json", canonical_json(receipt))
    with pytest.raises(CaptureVerifyError, match="capture_receipt_binding_invalid"):
        verify_capture(writer, relative_directory="epoch/captures/request", expected=expected)
