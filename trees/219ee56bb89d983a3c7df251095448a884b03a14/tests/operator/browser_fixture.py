"""Temporary synthetic operator server for browser acceptance.

The process binds 127.0.0.1 only, uses a new temp root, and loads a stub adapter
through MEMECOIN_OPERATOR_ADAPTER. It does not read live roots, credentials, or
protected datasets. Numbers here are scripted labels, not market results.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from memecoin_operator.control import initialize
from memecoin_operator.web import make_server

ADAPTER_SOURCE = r'''
"""Synthetic adapter. Scripted labels only; not a market and not a forecast."""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path

PHASES = (
    (0, "WAIT", "median_below_cost_buffer"),
    (9_000_000_000, "ENTER", "enter_next_observed_close"),
    (18_000_000_000, "HOLD", "already_exposed"),
    (27_000_000_000, "EXIT", "fixed_15m_exit"),
)
GROWTH = 160
POOL = "SYNPOOL01"


def _path(root: Path) -> Path:
    return Path(root) / "fixture-state.json"


def _read(root: Path) -> dict:
    path = _path(root)
    with path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        return json.load(handle)


def _update(root: Path, mutate):
    path = _path(root)
    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        state = json.load(handle)
        result = mutate(state)
        handle.seek(0)
        handle.write(json.dumps(state, indent=2, sort_keys=True))
        handle.truncate()
        return result


def _paper(state: dict, now: int) -> dict:
    cash = "1000.00000000"
    started = state.get("paper_started_ns")
    if not isinstance(started, int):
        return {
            "cash": cash,
            "equity": cash,
            "positions": [],
            "trades": [],
            "decisions": [],
        }
    elapsed = now - started
    visible = [row for row in PHASES if elapsed >= row[0]]
    if not visible:
        visible = [PHASES[0]]
    candidate = state.get("paper_candidate_id")
    decisions = [
        {
            "ns": started + offset,
            "action": action,
            "reason": reason,
            "pool": POOL,
            "candidate_id": candidate,
        }
        for offset, action, reason in visible
    ]
    latest = visible[-1][1]
    latest_ns = started + visible[-1][0]
    positions: list[dict] = []
    trades: list[dict] = []
    equity = cash
    if latest == "ENTER":
        positions = [
            {"pool": POOL, "qty": "100.00000000", "status": "pending", "entry_ns": latest_ns}
        ]
    elif latest == "HOLD":
        positions = [
            {
                "pool": POOL,
                "qty": "100.00000000",
                "status": "open",
                "entry_ns": latest_ns,
                "exit_due_ns": latest_ns + 900_000_000_000,
            }
        ]
    elif latest == "EXIT":
        equity = "998.75000000"
        entry_ns = started + PHASES[1][0]
        trades = [
            {
                "pool": POOL,
                "net_pnl": "-1.25000000",
                "entry_ns": entry_ns,
                "exit_ns": latest_ns,
            }
        ]
    return {
        "cash": cash,
        "equity": equity,
        "positions": positions,
        "trades": trades,
        "decisions": decisions,
    }


def _scene_view(root: Path, state: dict) -> dict:
    payload = json.loads((Path(root) / "rich-view.json").read_text(encoding="utf-8"))
    now = time.time_ns()
    scene = state.get("scene") or "rich"
    source = dict(payload.get("source") or {})
    limits = dict(payload.get("limits") or {})
    if scene == "rich":
        source["heartbeat_ns"] = now
        source["freshness_s"] = 20
        source["healthy"] = True
        source["cooldown_until_ns"] = 0
        source["stopped"] = False
        source["stop_reason"] = None
        limits["domain_stop"] = False
        limits["eligible"] = True
    elif scene == "stale":
        source["freshness_s"] = 1200
        source["healthy"] = False
        source["heartbeat_age_s"] = 400
        source["heartbeat_ns"] = now - 400 * 1_000_000_000
    elif scene == "cooldown":
        source["cooldown_until_ns"] = now + 3600 * 1_000_000_000
        source["healthy"] = False
        source["heartbeat_ns"] = now
    elif scene == "domain_stop":
        source["stopped"] = True
        source["healthy"] = False
        source["stop_reason"] = "source_stop_or_deadline"
        limits["domain_stop"] = True
        limits["eligible"] = False
    elif scene == "expired":
        source["expires_ns"] = now - 5_000_000_000
        limits["expires_ns"] = source["expires_ns"]
    payload = dict(payload)
    payload["source"] = source
    payload["limits"] = limits
    return payload


def inspect(root: Path) -> dict:
    root = Path(root)
    state = _read(root)
    if state.get("scene") not in (None, "", "workflow"):
        return _scene_view(root, state)
    now = time.time_ns()
    history = list(state.get("history") or [])
    return {
        "source": {
            "provider": "geckoterminal",
            "bar_seconds": 300,
            "heartbeat_ns": now,
            "freshness_s": 15,
            "budget_used": 4 + int(state.get("collects") or 0),
            "budget_max": 100,
            "cooldown_until_ns": None,
            "matured_samples": int(state["matured_samples"]),
            "healthy": True,
            "stopped": False,
            "stop_reason": None,
            "history": history,
        },
        "coverage": {"bars": int(state["bars"]), "pools": 3, "history": history},
        "datasets": list(state.get("datasets") or []),
        "candidates": list(state.get("candidates") or []),
        "exams": list(state.get("exams") or []),
        "paper": _paper(state, now),
        "legacy": [
            {"id": "b10", "port": 8790, "healthy": None, "stopped": None},
            {"id": "b12", "port": 8791, "healthy": None, "stopped": None},
        ],
        "limits": {
            "eligible": True,
            "reasons": [],
            "domain_stop": False,
            "max_open_exams": 4,
        },
        "policy": {"slippage_bps": 50, "fee_bps": 25},
    }


def run(root: Path, action: str, params: dict, output: Path) -> dict:
    root = Path(root)
    del output
    params = params if isinstance(params, dict) else {}
    if action == "train":
        _update(
            root,
            lambda state: state.__setitem__(
                "training", {"step": 2, "steps": 4, "checkpoint_id": "synthck1"}
            ),
        )
        time.sleep(8)
    return _update(root, lambda state: _apply(state, action, params))


def _apply(state: dict, action: str, params: dict) -> dict:
    if action == "collect":
        remaining = int(state.get("fail_collect_remaining") or 0)
        if remaining > 0:
            state["fail_collect_remaining"] = remaining - 1
            reason = "synthetic_failure"
            if state.get("scene") not in (None, "", "workflow"):
                reason = "interrupted"
            return {"ok": False, "reason": reason}
        state["matured_samples"] = int(state["matured_samples"]) + GROWTH
        state["bars"] = int(state["bars"]) + GROWTH
        state["collects"] = int(state.get("collects") or 0) + 1
        history = list(state.get("history") or [])
        history.append(
            {
                "ns": time.time_ns(),
                "samples": int(state["matured_samples"]),
                "bars": int(state["bars"]),
            }
        )
        state["history"] = history
        profile = params.get("profile")
        if profile not in {"balanced", "history"}:
            profile = "balanced"
        return {"ok": True, "profile": profile}
    if action == "snapshot":
        state["snapshot_seq"] = int(state.get("snapshot_seq") or 0) + 1
        number = int(state["snapshot_seq"])
        dataset_id = f"synthds{number}"
        digest = f"{number:02x}" + ("ab" * 31)
        matured = int(state["matured_samples"])
        state.setdefault("datasets", []).append(
            {
                "dataset_id": dataset_id,
                "snapshot_hash": digest,
                "matured_samples": matured,
                "created_ns": time.time_ns(),
            }
        )
        return {
            "ok": True,
            "dataset_id": dataset_id,
            "snapshot_hash": digest,
            "matured_samples": matured,
        }
    if action == "train":
        state["training"] = None
        state["train_seq"] = int(state.get("train_seq") or 0) + 1
        number = int(state["train_seq"])
        candidate_id = f"synthcand{number}"
        snapshot_id = params.get("snapshot_id")
        digest = None
        for item in state.get("datasets") or []:
            if item.get("dataset_id") == snapshot_id:
                digest = item.get("snapshot_hash")
        state.setdefault("candidates", []).append(
            {
                "candidate_id": candidate_id,
                "snapshot_id": snapshot_id,
                "snapshot_hash": digest,
                "status": "completed",
                "validation": {"synthetic": True},
            }
        )
        result = {
            "ok": True,
            "candidate_id": candidate_id,
            "snapshot_id": snapshot_id,
        }
        if isinstance(digest, str):
            result["snapshot_hash"] = digest
        return result
    if action == "paper":
        state["paper_seq"] = int(state.get("paper_seq") or 0) + 1
        number = int(state["paper_seq"])
        exam_id = f"synthexam{number}"
        candidate_id = params.get("candidate_id")
        started = time.time_ns()
        state["paper_started_ns"] = started
        state["paper_candidate_id"] = candidate_id
        state.setdefault("exams", []).append(
            {
                "exam_id": exam_id,
                "candidate_id": candidate_id,
                "started_ns": started,
                "status": "running",
            }
        )
        return {"ok": True, "exam_id": exam_id, "candidate_id": candidate_id}
    return {"ok": False, "reason": "invalid_result"}


def maintain(root: Path, state: dict) -> dict:
    del state
    current = _read(Path(root))
    progress = current.get("training")
    body = {"ok": True}
    if isinstance(progress, dict):
        body["training_progress"] = progress
    return body


def stop(root: Path, full: bool = False) -> dict:
    def mark(state: dict) -> dict:
        state["stop_calls"] = int(state.get("stop_calls") or 0) + 1
        state["stop_full"] = bool(full)
        return {"ok": True, "full": bool(full)}

    return _update(Path(root), mark)
'''


def initial_state(now: int) -> dict[str, object]:
    return {
        "bars": 400,
        "candidates": [],
        "collects": 0,
        "data_kind": "synthetic",
        "datasets": [],
        "exams": [],
        "fail_collect_remaining": 1,
        "history": [{"bars": 400, "ns": now - 60_000_000_000, "samples": 80}],
        "matured_samples": 80,
        "paper_candidate_id": None,
        "paper_seq": 0,
        "paper_started_ns": None,
        "snapshot_seq": 0,
        "stop_calls": 0,
        "stop_full": False,
        "train_seq": 0,
        "training": None,
    }


def fixture_config(now: int) -> dict[str, object]:
    """Interval 0 lets one session show a later auto cycle. It is not the 24h gate."""
    return {
        "champion_model_sha": "cd" * 32,
        "expires_ns": now + 7 * 24 * 3600 * 1_000_000_000,
        "max_training_runs": 6,
        "min_refit_interval_s": 0,
        "minimum_new_matured": 100,
        "paid_budget_usd": 0,
        "real_execution_enabled": False,
        "run_envelope_s": 604800,
    }


RICH_SCENES = ("rich", "stale", "cooldown", "domain_stop", "expired")
SUMMED_EQUITY = "1999.15000000"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _pool_name(index: int) -> str:
    return f"SYN{index:02d}"


def rich_config(now: int) -> dict[str, object]:
    """Production interval stays 86400. This scene does not wait it out."""
    config = fixture_config(now)
    config["min_refit_interval_s"] = 86400
    return config


def rich_view(now: int) -> dict[str, object]:
    """Static synthetic inspect payload. Cash/equity are the adapter's book sum."""
    now_s = now // 1_000_000_000
    day = 86400
    first_s = now_s - 91 * day
    last_s = now_s
    pools: list[dict[str, object]] = []
    quarantine: list[dict[str, str]] = []
    for index in range(1, 25):
        if index <= 6:
            role, days = "hot", 91
        elif index >= 22:
            role, days = "quarantine", 3
        else:
            role, days = "cold", 40
        pool = _pool_name(index)
        pools.append(
            {
                "bars": days * 24,
                "days": days,
                "first_s": last_s - days * day,
                "last_s": last_s,
                "lane": role,
                "pool": pool,
                "role": role,
            }
        )
        if role == "quarantine":
            pools[-1]["reason"] = "duplicate_bar_in_response"
            quarantine.append({"pool": pool, "reason": "duplicate_bar_in_response"})
    dataset = "d4" * 32
    model_a = "a1" * 32
    model_b = "b2" * 32
    retained = {
        "test": {"days": 16, "decisions": 700, "pools": 10},
        "train": {"days": 40, "decisions": 4800, "pools": 18},
        "validation": {"days": 20, "decisions": 900, "pools": 12},
    }
    history = [
        {"bars": 24000, "ns": now - 120_000_000_000, "samples": 2400},
        {"bars": 24000, "ns": now - 60_000_000_000, "samples": 2400},
    ]
    support = {
        "test": {"days": 16, "decisions": 700, "pools": 10},
        "train": {"days": 40, "decisions": 4800, "pools": 18},
        "validation": {"days": 20, "decisions": 900, "pools": 12},
    }
    return {
        "candidates": [
            {
                "candidate_id": "synthcandA",
                "dataset_sha": dataset,
                "model_sha": model_a,
                "promoted": False,
                "selected": {"backbone": "gru"},
                "snapshot_hash": dataset,
                "snapshot_id": "synthdsR",
                "status": "completed",
                "support": support,
                "validation": {"empirical": 0.125, "gru": 0.25, "linear": 0.5},
            },
            {
                "candidate_id": "synthcandB",
                "dataset_sha": dataset,
                "model_sha": model_b,
                "promoted": False,
                "snapshot_hash": dataset,
                "snapshot_id": "synthdsR",
                "status": "completed",
                "support": support,
                "validation": {"gru": 0.25},
            },
            {
                "candidate_id": "synthcandF",
                "model_sha": None,
                "promoted": False,
                "snapshot_id": "synthdsR",
                "status": "failed",
                "support": {},
                "validation": {},
            },
            {
                "candidate_id": "synthcandI",
                "model_sha": None,
                "promoted": False,
                "snapshot_id": "synthdsR",
                "status": "incomplete",
                "support": {},
                "validation": {},
            },
        ],
        "coverage": {
            "bars": 24000,
            "event_days": 91,
            "first_s": first_s,
            "history": history,
            "last_s": last_s,
            "matured_samples": 2400,
            "observed_pools": 24,
            "per_pool": pools,
            "pools": 24,
            "receipt_days": 91,
        },
        "datasets": [
            {
                "created_ns": now,
                "dataset_id": "synthdsR",
                "dataset_sha": dataset,
                "historical_test_independent": False,
                "matured_samples": 2400,
                "retained": retained,
                "snapshot_hash": dataset,
                "unseen_test_pools": ["SYN20", "SYN21"],
            }
        ],
        "exams": [
            {
                "candidate_id": "synthcandA",
                "exam_id": "synthexamA",
                "expires_ns": now + 86_400 * 1_000_000_000,
                "status": "running",
            },
            {
                "candidate_id": "synthcandB",
                "exam_id": "synthexamB",
                "expires_ns": now + 86_400 * 1_000_000_000,
                "status": "running",
            },
        ],
        "legacy": [
            {
                "healthy": True,
                "id": "synthactive",
                "label": "synthactive",
                "port": 18880,
                "stopped": False,
            },
            {
                "healthy": False,
                "id": "synthhist",
                "label": "synthhist",
                "port": 18881,
                "stopped": True,
            },
        ],
        "limits": {
            "domain_stop": False,
            "eligible": True,
            "expires_ns": now + 7 * 24 * 3600 * 1_000_000_000,
            "max_open_exams": 8,
            "max_training_runs": 6,
            "min_refit_interval_s": 86400,
            "paid_budget_usd": 0,
            "reasons": [],
        },
        "paper": {
            "books": [
                {
                    "candidate_id": "synthcandA",
                    "capital": "1000.00000000",
                    "cash": "998.75000000",
                    "equity": "998.75000000",
                    "exam_id": "synthexamA",
                    "fees": "1.25000000",
                    "model_sha": model_a,
                    "realized": "-1.25000000",
                    "unrealized": "0.00000000",
                },
                {
                    "candidate_id": "synthcandB",
                    "capital": "1000.00000000",
                    "cash": "1000.40000000",
                    "equity": "1000.40000000",
                    "exam_id": "synthexamB",
                    "fees": "0.40000000",
                    "model_sha": model_b,
                    "realized": "0.40000000",
                    "unrealized": "0.00000000",
                },
            ],
            "capital_per_book": "1000.00000000",
            "cash": SUMMED_EQUITY,
            "decisions": [
                {
                    "action": "ENTER",
                    "candidate_id": "synthcandA",
                    "exam_id": "synthexamA",
                    "filled": False,
                    "model_sha": model_a,
                    "ns": now - 20_000_000_000,
                    "pool": "SYN01",
                    "q": {"q10": -0.125, "q50": 0.25, "q90": 0.5},
                    "reason": "enter_next_observed_close",
                    "stage": "intent",
                },
                {
                    "action": "ENTER",
                    "candidate_id": "synthcandA",
                    "exam_id": "synthexamA",
                    "filled": True,
                    "model_sha": model_a,
                    "ns": now - 10_000_000_000,
                    "pool": "SYN01",
                    "reason": "observed_future_fill",
                    "stage": "fill",
                },
                {
                    "action": "HOLD",
                    "candidate_id": "synthcandB",
                    "exam_id": "synthexamB",
                    "filled": False,
                    "model_sha": model_b,
                    "ns": now - 5_000_000_000,
                    "pool": "SYN02",
                    "reason": "already_exposed",
                    "stage": "existing_or_pending_exposure",
                },
            ],
            "equity": SUMMED_EQUITY,
            "fees": "1.65000000",
            "independent_books": True,
            "positions": [
                {
                    "candidate_id": "synthcandA",
                    "entry_ns": now - 10_000_000_000,
                    "exam_id": "synthexamA",
                    "model_sha": model_a,
                    "pool": "SYN01",
                    "qty": "100.00000000",
                    "status": "pending",
                },
                {
                    "candidate_id": "synthcandB",
                    "entry_ns": now - 5_000_000_000,
                    "exam_id": "synthexamB",
                    "exit_due_ns": now + 900_000_000_000,
                    "model_sha": model_b,
                    "pool": "SYN02",
                    "qty": "50.00000000",
                    "status": "open",
                },
            ],
            "realized": "-0.85000000",
            "trades": [
                {
                    "candidate_id": "synthcandA",
                    "entry_ns": now - 30_000_000_000,
                    "exam_id": "synthexamA",
                    "exit_ns": now - 15_000_000_000,
                    "model_sha": model_a,
                    "net_pnl": "-1.25000000",
                    "pool": "SYN03",
                }
            ],
            "unrealized": "0.00000000",
        },
        "policy": {
            "automatic_promotion": False,
            "capital_usd": 1000,
            "champion": "empirical",
            "champion_model_sha": "cd" * 32,
            "fee_bps": 25,
            "holding_minutes": 15,
            "learned_exit": False,
            "name": "B07 fixed-time price proxy",
            "slippage_bps": 50,
        },
        "source": {
            "attempts": 12,
            "bar_seconds": 300,
            "borrowed": False,
            "budget_max": 100,
            "budget_used": 12,
            "cooldown_until_ns": 0,
            "event_days": 91,
            "failures": 0,
            "first_s": first_s,
            "freshness_s": 20,
            "healthy": True,
            "heartbeat_ns": now,
            "history": history,
            "last_s": last_s,
            "matured_samples": 2400,
            "max_requests": 100,
            "hot_pools": [_pool_name(index) for index in range(1, 7)],
            "observed_pools": 24,
            "per_pool": pools,
            "profile_effective": "balanced",
            "provider": "geckoterminal",
            "quarantine": quarantine,
            "receipt_days": 91,
            "single_provider": True,
            "stop_reason": None,
            "stopped": False,
        },
    }


def rich_state(now: int, scene: str, expires_ns: int) -> dict[str, object]:
    state = initial_state(now)
    state["bars"] = 24000
    state["fail_collect_remaining"] = 1
    state["matured_samples"] = 2400
    state["saved_expires_ns"] = expires_ns
    state["scene"] = scene
    return state


def label_fixture_page() -> None:
    """Mark the in-memory page. The production template file stays untouched."""
    import memecoin_operator.web as web

    marker = (
        '<p id="fixture-kind" data-kind="synthetic" role="note">'
        "SYNTHETIC FIXTURE. Не рынок, не прогноз и не результат экзамена.</p>"
    )
    if 'id="fixture-kind"' in web.PAGE:
        return
    if "<body>" not in web.PAGE:
        raise SystemExit("fixture cannot mark PAGE: no body tag")
    web.PAGE = web.PAGE.replace("<body>", "<body>" + marker, 1)


def set_scene(root: Path, scene: str) -> None:
    assert_safe_root(root)
    if scene not in {"workflow", *RICH_SCENES}:
        raise SystemExit(f"unknown scene {scene}")
    state_path = root / "fixture-state.json"
    config_path = root / "config.json"
    if not state_path.exists() or not config_path.exists():
        raise SystemExit("fixture root is not prepared")
    with state_path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        state = json.load(handle)
        if "saved_expires_ns" not in state:
            current = json.loads(config_path.read_text(encoding="utf-8"))
            state["saved_expires_ns"] = current.get("expires_ns")
        state["scene"] = scene
        saved = state.get("saved_expires_ns")
        handle.seek(0)
        handle.write(json.dumps(state, indent=2, sort_keys=True))
        handle.truncate()
        handle.flush()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if scene == "expired":
        config["expires_ns"] = time.time_ns() - 5_000_000_000
    elif isinstance(saved, int):
        config["expires_ns"] = saved
    _write_json(config_path, config)
    sys.stdout.write(json.dumps({"ok": True, "scene": scene}, sort_keys=True) + "\n")
    sys.stdout.flush()


def assert_safe_root(root: Path) -> None:
    resolved = root.resolve()
    lowered = str(resolved).lower()
    banned = (
        "brain-b0",
        "brain-b1",
        "calendar-execution",
        "corpus-registry",
        "holdout",
        "/f01",
        "d43",
    )
    for item in banned:
        if item in lowered:
            raise SystemExit(f"refusing fixture root near protected data: {item}")
    work = Path(__file__).resolve().parents[2] / "work"
    if resolved == work or work in resolved.parents:
        raise SystemExit("refusing fixture root inside work/")


def prepare(root: Path, scene: str = "workflow") -> None:
    assert_safe_root(root)
    if scene not in {"workflow", *RICH_SCENES}:
        raise SystemExit(f"unknown scene {scene}")
    root.mkdir(parents=True, exist_ok=True)
    adapter = root / "synthetic_adapter.py"
    adapter.write_text(ADAPTER_SOURCE, encoding="utf-8")
    os.environ["MEMECOIN_OPERATOR_ADAPTER"] = str(adapter)
    state_path = root / "fixture-state.json"
    if not state_path.exists():
        now = time.time_ns()
        if scene == "workflow":
            state: dict[str, object] = initial_state(now)
            config: dict[str, object] = fixture_config(now)
        else:
            config = rich_config(now)
            state = rich_state(now, scene, int(config["expires_ns"]))
            _write_json(root / "rich-view.json", rich_view(now))
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        initialize(root, config)
    else:
        initialize(root)


def bind(root: Path, port: int):
    last: OSError | None = None
    for candidate in range(port, port + 20):
        try:
            return make_server(root, candidate), candidate
        except OSError as exc:
            last = exc
    raise SystemExit(f"no free loopback port from {port}: {last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic loopback operator fixture")
    parser.add_argument("--port", type=int, default=18821)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--scene", default="workflow")
    parser.add_argument("--set-scene", default=None)
    args = parser.parse_args(argv)
    if args.set_scene:
        if args.root is None:
            print("--set-scene needs --root", file=sys.stderr)
            return 2
        set_scene(args.root, args.set_scene)
        return 0
    if args.port < 18800 or args.port > 65535:
        print("fixture port must be >= 18800", file=sys.stderr)
        return 2
    root = args.root
    if root is None:
        root = Path(tempfile.mkdtemp(prefix="operator-browser-fixture-"))
    prepare(root, args.scene)
    label_fixture_page()
    server, port = bind(root, args.port)
    interval = 0 if args.scene == "workflow" else 86400
    payload = {
        "ok": True,
        "data_kind": "synthetic",
        "scene": args.scene,
        "url": f"http://127.0.0.1:{port}",
        "root": str(root),
        "port": port,
        "mutation_scope": "synthetic_fixture_only",
        "proves_market_performance": False,
        "proves_production_refit_interval": False,
        "fixture_min_refit_interval_s": interval,
        "production_min_refit_interval_s": 86400,
        "note": (
            "Scripted SYNPOOL01 lifecycle. Interval 0 is fixture-only."
            if args.scene == "workflow"
            else "Rich synthetic view. Interval stays 86400; this process does not wait it out."
        ),
    }
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    finally:
        server.shutdown_background()
    return 0


if __name__ == "__main__":
    sys.exit(main())
