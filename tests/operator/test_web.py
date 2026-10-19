"""Localhost write API: host, origin, CSRF, replay, and no GET side effects."""

import hashlib
import importlib.util
import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from memecoin_ml.data import write
from memecoin_operator import watch_market
from memecoin_operator.control import status
from memecoin_operator.web import make_server
from memecoin_registry.contracts import seal

_spec = importlib.util.spec_from_file_location(
    "operator_test_control_for_web", Path(__file__).with_name("test_control.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
make_root = _helpers.make_root

PAGE_MARKERS = (
    "Операторский центр",
    "Обзор",
    "Данные",
    "Обучение",
    "Модели",
    "Paper",
    "Watch Market",
    "Система",
    "Стоп всё",
    "allowlist",
    "8790",
    "8791",
    "фиксированный",
    "operator-token",
    "aria-modal",
)


def serve(root):
    server = make_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def request(port, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    payload = response.read()
    status_code = response.status
    conn.close()
    return status_code, payload


def test_projection_and_write_guards(adapter, tmp_path, monkeypatch):
    root = make_root(
        tmp_path,
        inspect={
            "source": {"matured_samples": 4, "provider": "geckoterminal"},
            "limits": {"eligible": True},
        },
    )
    server, thread = serve(root)
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    try:
        code, page = request(port, "GET", "/")
        text = page.decode()
        assert code == 200
        for marker in PAGE_MARKERS:
            assert marker in text
        assert "cdn" not in text and "unpkg" not in text and "fonts.googleapis" not in text
        assert 'id="watch-pool"' in text and 'id="watch-portfolio"' in text
        code, raw = request(port, "GET", "/api/bootstrap")
        token = json.loads(raw)["token"]
        assert token and token in text
        code, raw = request(port, "GET", "/api/status")
        body = json.loads(raw)
        assert code == 200 and body["schema"] == "operator-status-v1"
        assert "/Users" not in raw.decode() and "operator.sqlite" not in raw.decode()
        code, raw = request(port, "GET", "/api/watch/status")
        watch = json.loads(raw)
        assert code == 200 and watch["schema"] == "watch-market-v1"
        assert watch["status"] == "idle" and watch["auto_untouched"] is True
        assert "/Users" not in raw.decode()
        code, raw = request(port, "GET", "/", headers={"Host": "evil.example"})
        assert code == 403 and json.loads(raw)["reason"] == "bad_host"
        code, raw = request(port, "GET", "/", headers={"Origin": "https://evil.example"})
        assert code == 403 and json.loads(raw)["reason"] == "bad_origin"
        code, raw = request(port, "GET", "/api/command")
        assert code == 405
        assert status(root)["jobs"] == []
        headers = {
            "Host": f"127.0.0.1:{port}",
            "Origin": origin,
            "Content-Type": "application/json",
            "X-Operator-Token": token,
        }
        invalid_watch = json.dumps(
            {
                "request_id": "watch1",
                "action": "resolve",
                "params": {"address_kind": "mint", "address": "bad"},
            }
        ).encode()
        code, raw = request(port, "POST", "/api/watch/resolve", invalid_watch, headers)
        assert code == 400 and json.loads(raw)["reason"] == "invalid_address"
        invalid_watch_headers = dict(headers)
        invalid_watch_headers["X-Operator-Token"] = "nope"
        code, raw = request(
            port, "POST", "/api/watch/resolve", invalid_watch, invalid_watch_headers
        )
        assert code == 403 and json.loads(raw)["reason"] == "bad_token"
        mint = "So11111111111111111111111111111111111111112"
        pool = "7XawhbbxtsRcQA8KTkHT9f9nc6d69UwqCDh6U5EEbEmX"

        def fake_fetch(provider, path, params, *, lane):
            capture = provider / "requests" / "000000"
            capture.mkdir()
            write(
                capture / "response.bin",
                {
                    "data": [
                        {
                            "id": "solana_" + pool,
                            "type": "pool",
                            "attributes": {"address": pool, "name": "TOKEN / SOL"},
                            "relationships": {
                                "base_token": {"data": {"id": "solana_" + mint}},
                                "quote_token": {"data": {"id": "solana_" + mint}},
                            },
                        }
                    ]
                },
            )
            seal(capture)
            return capture

        monkeypatch.setattr(watch_market.source, "fetch", fake_fetch)
        resolve = json.dumps(
            {
                "request_id": "watch2",
                "action": "resolve",
                "params": {"address_kind": "mint", "address": mint},
            }
        ).encode()
        code, raw = request(port, "POST", "/api/watch/resolve", resolve, headers)
        resolved = json.loads(raw)
        assert code == 200 and resolved["ok"] is True
        assert resolved["pools"][0]["address"] == pool
        assert status(root)["jobs"] == []
        payload = json.dumps({"request_id": "web1", "action": "snapshot", "params": {}}).encode()
        bad = dict(headers)
        bad["X-Operator-Token"] = "nope"
        code, raw = request(port, "POST", "/api/command", payload, bad)
        assert code == 403 and json.loads(raw)["reason"] == "bad_token"
        missing_origin = dict(headers)
        missing_origin.pop("Origin")
        code, raw = request(port, "POST", "/api/command", payload, missing_origin)
        assert code == 403 and json.loads(raw)["reason"] == "bad_origin"
        wrong_type = dict(headers)
        wrong_type["Content-Type"] = "text/plain"
        code, raw = request(port, "POST", "/api/command", payload, wrong_type)
        assert code == 400
        code, raw = request(port, "POST", "/api/command", payload, headers)
        first = json.loads(raw)
        assert code == 200 and first["ok"] is True and first["duplicate"] is False
        code, raw = request(port, "POST", "/api/command", payload, headers)
        second = json.loads(raw)
        assert second["duplicate"] is True and second["job_id"] == first["job_id"]
        other = json.dumps({"request_id": "web1", "action": "pause", "params": {}}).encode()
        code, raw = request(port, "POST", "/api/command", other, headers)
        assert code == 409 and json.loads(raw)["reason"] == "conflict"
        assert len(status(root)["jobs"]) == 1
        huge = b'{"request_id":"big","action":"pause","params":{}}' + b" " * 70000
        code, raw = request(
            port,
            "POST",
            "/api/command",
            huge,
            {**headers, "Content-Length": str(len(huge))},
        )
        assert code == 400 and json.loads(raw)["reason"] == "body_limit"
        code, raw = request(port, "GET", "/api/events?limit=5")
        assert code == 200 and "events" in json.loads(raw)
        code, raw = request(port, "PUT", "/api/command", payload, headers)
        assert code == 405
    finally:
        server.shutdown_background()
        thread.join(timeout=3)


def test_developer_read_only_preview_has_no_workers_and_rejects_posts(
    adapter, tmp_path, monkeypatch
):
    root = make_root(tmp_path)
    source = root / "source"
    source.mkdir()
    (source / "index.sqlite").write_bytes(b"unused fixture database")
    config = json.loads((root / "config.json").read_text())
    config.update(source_root=str(source), champion_model_path=str(root / "models/not-copied"))
    (root / "config.json").write_text(json.dumps(config))
    (root / "dev-preview.json").write_text(
        json.dumps(
            {
                "schema": "brain-dev-operator-preview-v1",
                "source_snapshot_at": "2026-10-19T12:00:00Z",
                "operator_database_sha256": hashlib.sha256(
                    (root / "operator.sqlite").read_bytes()
                ).hexdigest(),
                "source_index_sha256": hashlib.sha256(
                    (source / "index.sqlite").read_bytes()
                ).hexdigest(),
            }
        )
    )
    monkeypatch.setattr(
        "memecoin_operator.web.initialize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("initialize called")),
    )
    monkeypatch.setattr(
        watch_market,
        "status",
        lambda *_: (_ for _ in ()).throw(AssertionError("foreign Watch state read")),
    )
    server = make_server(root, 0, read_only=True)
    assert server._ticker is None and server._watch_ticker is None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Origin": f"http://127.0.0.1:{port}",
        "Content-Type": "application/json",
        "X-Operator-Token": server.token,
    }
    try:
        code, page = request(port, "GET", "/")
        assert code == 200
        assert b"Developer read-only preview" in page
        assert b"2026-10-19T12:00:00Z" in page
        code, body = request(port, "GET", "/api/watch/status")
        assert code == 200 and json.loads(body)["status"] == "unavailable"
        assert json.loads(body)["reason"] == "developer_preview_watch_not_copied"
        code, body = request(port, "POST", "/api/watch/start", b"{}", headers)
        assert code == 405 and json.loads(body)["reason"] == "developer_read_only"
        code, body = request(port, "POST", "/api/command", b"{}", headers)
        assert code == 405 and json.loads(body)["reason"] == "developer_read_only"
        assert status(root)["jobs"] == []
    finally:
        server.shutdown_background()
        thread.join(timeout=3)
