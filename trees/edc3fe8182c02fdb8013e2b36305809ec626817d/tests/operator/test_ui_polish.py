"""Operator cabin: honest projection, gated commands, and viewport screenshots."""

import inspect
import json
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from memecoin_operator.web import PAGE, OperatorHandler, make_server

VISUAL_SCRIPT = Path(__file__).with_name("polish_visual.cjs")
NOW = int(datetime(2026, 10, 4, 20, 0, tzinfo=UTC).timestamp() * 1_000_000_000)
NS = 1_000_000_000
_SHA_CHAMPION = "9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920"
_SHA_CANDIDATE = "c895590ac4726af6fcc8e46b870b6f08c8fdd7caf1fe50dbca018b16a8e77a76"
_EXAM = "job-shared"


def _books() -> list[dict]:
    rows = []
    index = 0
    for sha in (_SHA_CHAMPION, _SHA_CANDIDATE):
        for backbone in ("empirical", "gru", "linear"):
            index += 1
            rows.append(
                {
                    "exam_id": _EXAM,
                    "key": f"{sha}:{backbone}",
                    "candidate": backbone,
                    "backbone": backbone,
                    "model_sha": sha,
                    "cash": f"{1000 + index}.00000000",
                    "equity": f"{1000 + index}.00000000",
                    "realized": f"{index}.00000000",
                    "fees": f"0.{index}0000000",
                }
            )
    return rows


def test_page_keeps_security_and_operator_language():
    source = inspect.getsource(OperatorHandler.do_POST)
    assert "compare_digest" in source
    assert "bad_token" in source
    assert "bad_origin" in source
    assert "BODY_LIMIT" in source
    for marker in (
        "Операторский центр",
        "Сбор",
        "Снимок",
        "Обучить",
        "Paper exam",
        "Watch Market",
        'id="watch-address"',
        'id="watch-forecasts"',
        'id="watch-portfolio"',
        "Авто",
        "Пауза",
        "Продолжить",
        "Стоп всё",
        "Обзор",
        "Данные",
        "allowlist",
        "8790",
        "8791",
        "фиксированный",
        "не MPC",
        "не доказывает edge",
        "aria-modal",
        "operator-token",
        "Пока пусто",
        "Одно реальное наблюдение",
        "В статусе нет per_pool",
        "уже можно",
        "existing_or_pending_exposure",
        "limit=200",
        "location.port",
        "Рост matured",
        "function bookId",
        "loss_sums",
        "status.recovery",
        "q10",
    ):
        assert marker in PAGE
    assert "cdn" not in PAGE
    assert "unpkg" not in PAGE
    assert "fonts.googleapis" not in PAGE
    assert 'id="act-train" disabled' not in PAGE
    assert "el.disabled = blocked" in PAGE
    assert 'aria-disabled", "true"' in PAGE
    assert "127.0.0.1:8794" not in PAGE
    assert "20:32" not in PAGE
    assert "prefers-reduced-motion" in PAGE


def _status() -> dict:
    second = NOW // NS
    return {
        "schema": "operator-status-v1",
        "ok": True,
        "revision": 7,
        "mode": "idle",
        "phase": "idle",
        "collect_profile": "balanced",
        "selected_candidate_id": None,
        "active_exam_id": None,
        "auto_reason": None,
        "resume_mode": None,
        "expired": False,
        "expires_ns": NOW + 2 * 86400 * NS,
        "now_ns": NOW,
        "domain_stop": False,
        "promotion": False,
        "synthetic": True,
        "engine": {
            "name": "fixed_time_15m",
            "exit": "fixed_15m",
            "learned_exit": False,
            "mpc": False,
            "price_proxy": "geckoterminal_5m",
            "slippage_bps": 50,
            "fee_bps_per_side": 25,
            "champion_policy": "empirical_unchanged",
        },
        "gates": {
            "auto": {"next_action": "collect", "eligible": False, "reasons": ["mode_idle"]},
            "manual": {
                "snapshot": {"eligible": True, "reasons": []},
                "train": {"eligible": True, "reasons": []},
                "paper": {"eligible": False, "reasons": ["exam_capacity"]},
                "collect": {"eligible": True, "reasons": []},
            },
            "checks": [
                {"id": "expiry", "pass": True, "reason": None},
                {"id": "initial_collect", "pass": False, "reason": "collect_first"},
            ],
            "minimum_new_matured": 100,
            "runs_used": 1,
            "runs_max": 6,
            "envelope_s": 604800,
            "min_refit_interval_s": 86400,
            "last_reservation_ns": NOW - 3600 * NS,
            "matured_now": 240,
            "matured_prior": 80,
            "matured_delta": 160,
            "initial_run": False,
            "initial_collect_done": False,
            "busy": False,
            "not_before_ns": NOW - 60 * NS,
        },
        "source": {
            "provider": "geckoterminal",
            "bar_seconds": 300,
            "heartbeat_ns": NOW - 40 * NS,
            "freshness_s": 42,
            "budget_used": 1200,
            "budget_max": 25000,
            "cooldown_until_ns": 0,
            "matured_samples": 240,
            "healthy": True,
            "stopped": False,
            "stop_reason": None,
            "borrowed": True,
            "profile_effective": "frozen_B09",
            "single_provider": True,
            "attempts": 1200,
            "expires_ns": NOW + 86400 * NS,
            "event_days": 6,
            "hot_pools": ["SYN-HOT"],
            "quarantine": [{"pool": "SYN-QUAR", "reason": "duplicate_bar_in_response"}],
            "per_pool": [
                {
                    "pool": "SYN-HOT",
                    "bars": 1200,
                    "days": 6,
                    "first_s": second - 5 * 86400,
                    "last_s": second - 300,
                    "freshness_s": 40,
                    "ranges": [{"start": 1}, {"start": 2}],
                    "gaps": [{"seconds": 900}],
                },
                {
                    "pool": "SYN-COLD",
                    "lane": "cold",
                    "bars": 80,
                    "event_days": 2,
                    "first_s": second - 2 * 86400,
                    "last_s": second - 7200,
                },
                {
                    "pool": "SYN-QUAR",
                    "quarantine": True,
                    "bars": 12,
                    "days": 1,
                    "first_s": second - 86400,
                    "last_s": second - 40000,
                },
            ],
        },
        "coverage": {
            "bars": 1292,
            "pools": 3,
            "event_days": 6,
            "history": [
                {"ns": NOW - 3 * 3600 * NS, "samples": 80, "bars": 400},
                {"ns": NOW - 2 * 3600 * NS, "samples": 140, "bars": 700},
                {"ns": NOW - 3600 * NS, "samples": 190, "bars": 900},
                {"ns": NOW - 600 * NS, "samples": 240, "bars": 1100},
            ],
        },
        "paper": {
            "cash": None,
            "equity": None,
            "realized": None,
            "fees": None,
            "positions": [],
            "trades": [],
            "decisions": [
                {
                    "ns": NOW - 900 * NS,
                    "action": "WAIT",
                    "reason": "median_below_cost_buffer",
                    "stage": "decision",
                    "filled": False,
                    "pool": "SYN-HOT",
                    "q": {"q10": -0.01, "q50": 0.002, "q90": 0.02},
                },
                {
                    "ns": NOW - 600 * NS,
                    "action": "ENTER",
                    "reason": "enter_next_observed_close",
                    "stage": "intent",
                    "filled": False,
                    "pool": "SYN-HOT",
                },
                {
                    "ns": NOW - 300 * NS,
                    "action": "HOLD",
                    "reason": "already_exposed",
                    "stage": "existing_or_pending_exposure",
                    "filled": False,
                    "pool": "SYN-HOT",
                },
                {
                    "ns": NOW - 100 * NS,
                    "action": "WAIT",
                    "reason": "median_below_cost_buffer",
                    "stage": "decision",
                    "filled": False,
                    "pool": "SYN-BAND",
                    "exam_id": _EXAM,
                    "model_sha": _SHA_CHAMPION,
                    "candidate": "empirical",
                    "q": [-0.0311, -0.0052, 0.0683],
                },
                {
                    "ns": NOW - 50 * NS,
                    "action": "WAIT",
                    "reason": "median_below_cost_buffer",
                    "stage": "decision",
                    "filled": False,
                    "pool": "SYN-OTHER",
                    "exam_id": _EXAM,
                    "model_sha": _SHA_CANDIDATE,
                    "candidate": "linear",
                    "q": [-0.12, 0.01, 0.2],
                },
            ],
            "books": _books(),
            "forecast_scores": [
                {
                    "UTC_day": 20730,
                    "exam_id": _EXAM,
                    "horizon": 1,
                    "model_sha": _SHA_CHAMPION,
                    "n": 4,
                    "loss_sums": {
                        "empirical": 0.0058149706073023465,
                        "gru": 0.01928567615073596,
                        "linear": 0.0697924590518733,
                    },
                },
                {
                    "UTC_day": 20730,
                    "exam_id": _EXAM,
                    "horizon": 3,
                    "model_sha": _SHA_CANDIDATE,
                    "n": 0,
                    "loss_sums": {"empirical": 1, "linear": 1, "gru": 1},
                },
                {
                    "UTC_day": 20731,
                    "exam_id": _EXAM,
                    "horizon": 1,
                    "model_sha": _SHA_CHAMPION,
                    "n": 2,
                    "loss_sums": {"empirical": 0.02, "linear": 0.04},
                },
            ],
        },
        "snapshots": [
            {
                "dataset_id": "snap-synth",
                "snapshot_hash": "ab" * 32,
                "matured_samples": 240,
                "created_ns": NOW - 1800 * NS,
                "origin": "inspect",
                "historical_test_independent": False,
                "retained": {
                    "train": {"decisions": 120, "pools": 4, "days": 5},
                    "validation": {"decisions": 30, "pools": 4, "days": 1},
                    "test": {"decisions": 28, "pools": 6, "days": 1},
                },
                "unseen_test_pools": ["SYN-NEW"],
                "recipe": {"version": "b13", "steps": 1000, "sha256": "cd" * 32},
            }
        ],
        "candidates": [
            {
                "candidate_id": "cand-synth",
                "snapshot_id": "snap-synth",
                "status": "completed",
                "promoted": False,
                "selected": {"backbone": "gru"},
                "model_sha": "ef" * 32,
                "support": {"train": {"decisions": 4800, "pools": 18, "days": 40}},
                "validation": {"gru": {"pool_macro_pinball": 0.021}},
                "historical_test": {"label": "reused_descriptive", "independent": False},
                "recipe": {"version": "b13", "steps": 1000},
            },
            {
                "candidate_id": "cand-failed",
                "snapshot_id": "snap-synth",
                "status": "failed",
                "model_sha": None,
                "support": {},
            },
            {
                "candidate_id": "cand-bare",
                "snapshot_id": "snap-synth",
                "status": "completed",
                "model_sha": None,
                "support": {},
            },
        ],
        "champion": {"sha": _SHA_CHAMPION, "promoted": False},
        "exams": [],
        "jobs": [
            {
                "job_id": "job_synth",
                "action": "collect",
                "status": "completed",
                "origin": "manual",
                "reason": "accepted",
                "created_ns": NOW - 100 * NS,
                "finished_ns": NOW - 90 * NS,
            }
        ],
        "commands": [],
        "incidents": [
            {
                "event_id": 3,
                "reason": "source_stop_or_deadline",
                "ns": NOW - 7200 * NS,
                "summary": {"category": "audit_600s"},
            }
        ],
        "legacy": [
            {
                "id": "B10-active",
                "port": 8796,
                "url": "http://127.0.0.1:8796",
                "healthy": True,
                "stopped": False,
            },
            {
                "id": "B12-active",
                "port": 8795,
                "url": "http://127.0.0.1:8795",
                "healthy": True,
                "stopped": False,
            },
            {
                "id": "B12-original-STOP",
                "port": 8791,
                "url": "http://127.0.0.1:8791",
                "healthy": False,
                "stopped": True,
            },
        ],
        "host": {
            "cache_age_s": 12.5,
            "reason": None,
            "disk": {"free_bytes": 50000000000, "total_bytes": 200000000000, "reason": None},
            "power": {"source": None, "percent": None, "reason": "power_unparsed"},
            "uptime": {
                "process_s": None,
                "process_reason": "operator_not_launchd",
                "boot_s": None,
                "boot_reason": "boot_not_measured",
            },
            "operator": {"loaded": None, "disposition": "dev", "label": "ai.memecoin.operator"},
            "services": [],
        },
        "stop_effects": {
            "phase": "pending",
            "kind": "own",
            "attempts": 1,
            "max_attempts": 3,
            "reason": None,
            "confirmed": False,
            "needs_operator": False,
        },
        "training": {
            "job_id": None,
            "status": None,
            "step": None,
            "steps": None,
            "checkpoint_id": None,
            "elapsed_s": None,
            "deadline_s": 900,
        },
        "timeline": [
            {
                "revision": 7,
                "mode": "idle",
                "phase": "idle",
                "reason": "accepted",
                "ns": NOW - 90 * NS,
            }
        ],
        "limits": {"eligible": True, "reasons": [], "max_open_exams": 8},
        "warnings": [],
        "recovery": {
            "paused": True,
            "resume_attempts": 2,
            "retry_after_ns": NOW + 3600 * NS,
            "services": ["source"],
        },
        "forecasts": {
            "prediction_count": 12,
            "pending": 4,
            "scored": 8,
            "loss": {"gru": {"n": 8, "pinball_sum": "0.168"}},
        },
    }


EVENTS = [
    {
        "event_id": 1,
        "kind": "audit",
        "reason": "accepted",
        "ns": NOW - 100 * NS,
        "summary": {"action": "collect"},
    },
    {
        "event_id": 2,
        "kind": "incident",
        "reason": "source_stop_or_deadline",
        "ns": NOW - 7200 * NS,
        "summary": {"category": "audit_600s"},
    },
]


class _SynthHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            page = PAGE.replace("__TOKEN__", "synthetic-token").encode()
            self._send(200, page, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            body = json.dumps(_status()).encode()
            self._send(200, body, "application/json")
            return
        if path == "/api/events":
            body = json.dumps(
                {"schema": "operator-events-v1", "ok": True, "events": EVENTS, "next_after": 2}
            ).encode()
            self._send(200, body, "application/json")
            return
        if path == "/api/bootstrap":
            self._send(
                200,
                b'{"schema":"operator-bootstrap-v1","ok":true,"token":"synthetic-token"}',
                "application/json",
            )
            return
        self._send(404, b'{"ok":false,"reason":"not_found"}', "application/json")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(length)
        self._send(200, b'{"ok":true,"reason":"accepted"}', "application/json")


class _SavedHandler(BaseHTTPRequestHandler):
    """Serve a frozen synthetic status without depending on a production capture."""

    payload = json.dumps(_status()).encode()

    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            page = PAGE.replace("__TOKEN__", "saved-token").encode()
            self._send(200, page, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._send(200, self.payload, "application/json")
            return
        if path == "/api/events":
            self._send(
                200,
                b'{"schema":"operator-events-v1","ok":true,"events":[],"next_after":0}',
                "application/json",
            )
            return
        if path == "/api/bootstrap":
            self._send(
                200,
                b'{"schema":"operator-bootstrap-v1","ok":true,"token":"saved-token"}',
                "application/json",
            )
            return
        self._send(404, b'{"ok":false,"reason":"not_found"}', "application/json")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(length)
        self._send(403, b'{"ok":false,"reason":"read_only"}', "application/json")


def _bind(server_type, handler, root, start: int):
    last = None
    for port in range(start, start + 20):
        try:
            if root is None:
                server = server_type(("127.0.0.1", port), handler)
            else:
                server = make_server(root, port)
            return server
        except OSError as exc:
            last = exc
    raise RuntimeError(last)


def _serve(server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def cabin_urls(tmp_path):
    real = _bind(None, None, tmp_path / "real", 18790)
    synth = _bind(ThreadingHTTPServer, _SynthHandler, None, 18990)
    saved = _bind(ThreadingHTTPServer, _SavedHandler, None, 19010)
    real_thread = _serve(real)
    synth_thread = _serve(synth)
    saved_thread = _serve(saved)
    try:
        yield {
            "real": f"http://127.0.0.1:{real.server_address[1]}/",
            "synth": f"http://127.0.0.1:{synth.server_address[1]}/",
            "saved": f"http://127.0.0.1:{saved.server_address[1]}/",
        }
    finally:
        real.shutdown_background()
        real_thread.join(timeout=3)
        synth.shutdown()
        synth.server_close()
        synth_thread.join(timeout=3)
        saved.shutdown()
        saved.server_close()
        saved_thread.join(timeout=3)


def test_real_and_synthetic_cabin(cabin_urls):
    import urllib.request

    with urllib.request.urlopen(cabin_urls["real"], timeout=5) as response:
        page = response.read().decode()
        assert response.status == 200
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "Операторский центр" in page
    for name, url in cabin_urls.items():
        with urllib.request.urlopen(url + "api/status", timeout=5) as response:
            body = json.loads(response.read().decode())
        assert body["schema"] == "operator-status-v1"
        assert body["promotion"] is False
        if name != "real":
            assert body["synthetic"] is True
            assert body == _status()


def test_cabin_viewports(tmp_path, request):
    node = shutil.which("node")
    if node is None:
        pytest.skip("optional browser acceptance requires Node.js and Playwright Chromium")
    probe = subprocess.run(
        [node, str(VISUAL_SCRIPT), "--probe"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode == 77:
        pytest.skip(probe.stderr.strip())
    assert probe.returncode == 0, probe.stderr
    cabin_urls = request.getfixturevalue("cabin_urls")
    args = [node, str(VISUAL_SCRIPT)]
    for name, url in cabin_urls.items():
        args.extend(["--" + name, url])
    args.extend(["--out", str(tmp_path)])
    completed = subprocess.run(args, check=False, capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr[-2000:] or completed.stdout[-2000:]
    report = json.loads((tmp_path / "polish-visual.json").read_text())
    assert report["ok"] is True
    assert report["contrast"]["value"] >= 4.5
