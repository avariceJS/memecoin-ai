import importlib.util
import io
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "worker_preflight", Path(__file__).resolve().parents[2] / "tools/worker_preflight.py"
)
assert SPEC and SPEC.loader
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def test_metadata_probe_is_loopback_only_and_drops_unrelated_fields(monkeypatch):
    requested = []

    class Opener:
        def open(self, url, timeout):
            requested.append(url)
            assert timeout == 2
            return io.BytesIO(
                json.dumps(
                    {"models": [{"name": "fixture", "secret": "exclude", "details": {}}]}
                ).encode()
            )

    monkeypatch.setattr(worker.urllib.request, "build_opener", lambda *args: Opener())
    assert worker.ollama_metadata("tags")["models"][0]["name"] == "fixture"
    assert "secret" not in worker.ollama_metadata("ps")["models"][0]
    assert requested == ["http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/api/ps"]
    with pytest.raises(ValueError, match="metadata_only"):
        worker.ollama_metadata("generate")
    with pytest.raises(ValueError, match="redirect_rejected"):
        worker.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


@pytest.mark.parametrize("payload", [b"x" * 1_048_577, b'{"models":{}}', b"not JSON"])
def test_bad_metadata_is_unavailable_without_echoing_payload(monkeypatch, payload):
    class Opener:
        def open(self, url, timeout):
            return io.BytesIO(payload)

    monkeypatch.setattr(worker.urllib.request, "build_opener", lambda *args: Opener())
    result = worker.ollama_metadata("tags")
    assert result["available"] is False
    assert result["models"] == []
    assert len(json.dumps(result)) < 150


def test_receipt_never_overwrites_existing_evidence(monkeypatch, tmp_path):
    report = tmp_path / "receipt.json"
    report.write_text("preserve")
    monkeypatch.setattr(worker, "inventory", lambda *args: {"fixture": True})
    monkeypatch.setattr("sys.argv", ["worker_preflight.py", "--output", str(report)])
    with pytest.raises(FileExistsError):
        worker.main()
    assert report.read_text() == "preserve"


def test_unavailable_checkout_is_not_reported_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "command", lambda *args: None)
    monkeypatch.setattr(worker, "ollama_metadata", lambda *args: {"available": False})
    result = worker.inventory(tmp_path / "missing-repo", "mac2")
    assert result["repo"] == {"head": None, "clean": None}
    assert result["ready_for_live_or_training"] is False
    assert result["inference_requests"] == result["provider_requests"] == 0
