import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from memecoin_experience import alchemy_capture, observations, signature_capture


def _sig(value):
    return "1" * 63 + value


def _server(monkeypatch, results):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(length))
            received.append(request)
            result = results[min(len(received) - 1, len(results) - 1)]
            if isinstance(result, int):
                body = b'{"error":"rate limited"}'
                self.send_response(result)
            else:
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "result": result}
                ).encode()
                self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(alchemy_capture, "_endpoint_ok", lambda _: True)
    return server, thread, received, f"http://127.0.0.1:{server.server_port}/rpc"


def _row(signature, slot, timestamp):
    return dict(
        signature=signature,
        slot=slot,
        blockTime=timestamp,
        confirmationStatus="finalized",
        err=None,
    )


def test_bounded_raw_capture_binds_cursor_and_imports_wire_provenance(tmp_path, monkeypatch):
    results = [
        [_row(_sig("2"), 30, 15), _row(_sig("3"), 20, 14)],
        [_row(_sig("4"), 10, 13), _row(_sig("5"), 5, 12)],
    ]
    server, thread, received, endpoint = _server(monkeypatch, results)
    try:
        root = tmp_path / "private-capture"
        receipt = alchemy_capture.capture_signatures(
            endpoint,
            root,
            "11111111111111111111111111111111",
            limit=2,
            max_pages=2,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert receipt["attempts"] == 2
    assert receipt["retries"] == 0
    assert receipt["unique_signatures"] == 4
    assert (
        receipt["pages"][1]["before_signature_sha256"]
        == hashlib.sha256(_sig("3").encode()).hexdigest()
    )
    assert len(receipt["requests"][0]["request_sha256"]) == 64
    assert (
        receipt["requests"][0]["address_sha256"]
        == hashlib.sha256(b"11111111111111111111111111111111").hexdigest()
    )
    assert received[1]["params"][1]["before"] == _sig("3")
    assert receipt["pages"][0]["wire_sha256"]
    assert not any(str(endpoint) in path.read_text(errors="ignore") for path in root.rglob("*.*"))
    assert (root / "receipt.json").stat().st_mode & 0o777 == 0o600
    assert (root / "wire" / "response-0000.bin").stat().st_mode & 0o777 == 0o600

    result = signature_capture.publish(root / "receipt.json", tmp_path / "observations")
    source = observations.Source(tmp_path / "observations", result["observation_pin"], role="train")
    rows = list(source.rows())
    assert len(rows) == 4
    assert all(len(row["evidence"]) == 3 for row in rows)
    assert result["accounting"]["cursor_chain_verified"] is True
    assert result["accounting"]["support_status"] == "unknown"
    assert not any(row["kind"] in {"enrollment", "support"} for row in rows)

    receipt_path = root / "receipt.json"
    changed = json.loads(receipt_path.read_text())
    changed["requests"][0]["request_sha256"] = "a" * 64
    receipt_path.write_text(json.dumps(changed, sort_keys=True))
    with pytest.raises(ValueError, match="signature_capture_request_binding"):
        signature_capture.publish(receipt_path, tmp_path / "tampered-observations")


def test_http_429_is_sealed_without_retry_or_provider_body_logging(tmp_path, monkeypatch):
    server, thread, received, endpoint = _server(monkeypatch, [429])
    try:
        root = tmp_path / "private-capture"
        receipt = alchemy_capture.capture_signatures(
            endpoint,
            root,
            "11111111111111111111111111111111",
            limit=2,
            max_pages=2,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert len(received) == 1
    assert receipt["attempts"] == 1
    assert receipt["retries"] == 0
    assert receipt["aborted"] is True
    assert receipt["terminal_reason"] == "http_error"
    assert receipt["pages"] == []
    assert (root / "wire" / "response-0000.bin").read_bytes() == b'{"error":"rate limited"}'
    assert json.loads((root / "receipt.json").read_text())["support_status"] == "unknown"


def test_raw_capture_rejects_bad_host_before_dispatch(tmp_path):
    with pytest.raises(ValueError, match="capture_endpoint_network"):
        alchemy_capture.capture_signatures(
            "https://example.com/v2/not-a-key",
            tmp_path / "capture",
            "11111111111111111111111111111111",
        )
