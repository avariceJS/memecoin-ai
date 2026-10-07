"""Synthetic data contracts only; no provider requests."""

import copy
import datetime
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from memecoin_ml.data import sha
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

spec = importlib.util.spec_from_file_location(
    "development_collect",
    Path(__file__).resolve().parents[2] / "tools/brain_development_collect.py",
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
qualify_window, select, shared_guard = tool.qualify_window, tool.select, tool.shared_guard


def fixture(tmp_path, *, days=3, gap=False):
    c = tmp_path / "capture"
    c.mkdir()
    birth = dict(
        pool="A" * 32, base_mint="B" * 32, quote_mint="C" * 32, event_s=300000, month="synthetic"
    )
    start = birth["event_s"] + (7 * 86400 - 15 * 300 if days == 10 else 0)
    rows = [[start + i * 300, 1, 1.1, 0.9, 1, 10] for i in range(60) if not gap or i % 6]
    publish(
        c / "response.bin",
        dict(
            data=dict(attributes=dict(ohlcv_list=rows)),
            meta=dict(
                base=dict(address=birth["base_mint"]), quote=dict(address=birth["quote_mint"])
            ),
        ),
    )
    publish(
        c / "request.json",
        dict(
            started_ns=10**18 - 2,
            lane="historical",
            url=(
                f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{birth['pool']}"
                f"/ohlcv/minute?aggregate=5&currency=usd&token=base&include_empty_intervals=false"
                f"&limit=1000&before_timestamp={birth['event_s'] + days * 86400}"
            ),
        ),
    )
    publish(
        c / "receipt.json",
        dict(received_ns=10**18 - 1, available_ns=10**18, response_sha256=sha(c / "response.bin")),
    )
    seal(c)
    return c, birth


def test_full_feature_dependency_and_no_gap_fill(tmp_path):
    c, b = fixture(tmp_path)
    rows, report = qualify_window(c, b, 3)
    assert len(rows) == 60
    assert report["eligible"] == 43  # 15 input bars + 3 target bars.
    assert report["training_allowed"] is False
    assert report["historical_online_availability_known"] is False


def test_later_window_keeps_warmup_but_excludes_its_targets(tmp_path):
    c, b = fixture(tmp_path, days=10)
    rows, report = qualify_window(c, b, 10)
    assert len(rows) == 60
    assert report["eligible"] == 43
    assert report["cohorts"]["7to10d"]["origins"] == 43
    assert report["cohorts"]["lt1d"]["origins"] == 0


def test_sparse_capture_is_negative_evidence(tmp_path):
    c, b = fixture(tmp_path, gap=True)
    _, report = qualify_window(c, b, 3)
    assert report["eligible"] == 0
    assert report["status"] == "no_support"


@pytest.mark.parametrize("change,reason", [("http:", "namespace"), ("wrong_mint", "mint_mismatch")])
def test_namespace_and_alias_integrity(tmp_path, change, reason):
    c, b = fixture(tmp_path)
    if change == "http:":
        p = c / "request.json"
        value = json.loads(p.read_text())
        value["url"] = value["url"].replace("https:", change)
        p.write_text(json.dumps(value))
    else:
        b["base_mint"] = change
    (c / "manifest.json").unlink()
    seal(c)
    with pytest.raises(ValueError, match=reason):
        qualify_window(c, b, 3)


def test_selection_excludes_mint_aliases_and_is_order_invariant():
    births = [
        dict(
            pool=f"p{i}",
            base_mint=f"m{i}",
            event_s=int(datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC).timestamp()),
        )
        for i in range(8)
    ]
    births.append(dict(births[0], pool="alias"))
    inv = dict(births=births, metadata={"prior": dict(value=dict(cases=[births[0]]))})
    result = select(inv)
    reverse = copy.deepcopy(inv)
    reverse["births"].reverse()
    assert result == select(reverse)
    assert all(c["base_mint"] != "m0" for c in result["cases"])
    assert len({c["base_mint"] for c in result["cases"]}) == 4


def test_guard_cannot_bypass_source_stop(tmp_path):
    publish(tmp_path / "STOP", {})
    with pytest.raises(ValueError, match="stopped"):
        shared_guard(dict(source=str(tmp_path)))


def test_unknown_consumer_fingerprint_is_recordable_without_argv_secret(monkeypatch):
    monkeypatch.setattr(
        tool.subprocess,
        "check_output",
        lambda *a, **k: "999999 python /memecoin/unknown.py --token=synthetic-secret",
    )
    with pytest.raises(ValueError, match="unexpected_project_consumer:pid=999999:sha=") as exc:
        tool.consumer_guard(dict(source="/known-source"))
    assert "synthetic-secret" not in str(exc.value)


@pytest.mark.parametrize("condition", ["expiry", "cooldown", "failure", "budget", "stale", "pin"])
def test_shared_guard_rejects_each_unsafe_state(tmp_path, monkeypatch, condition):
    now = 10**18
    monkeypatch.setattr(tool.time, "time_ns", lambda: now)
    config = dict(expires_ns=now + 1000, max_requests=25000)
    state = dict(cooldown_ns=0, failures=0, attempts=100, last_ns=now - 1)
    if condition == "expiry":
        config["expires_ns"] = now
    if condition == "cooldown":
        state["cooldown_ns"] = now + 1
    if condition == "failure":
        state["failures"] = 1
    if condition == "budget":
        state["attempts"] = 24960
    if condition == "stale":
        state["last_ns"] = now - 181 * 10**9
    publish(tmp_path / "config.json", config)
    (tmp_path / "journal").mkdir()
    publish(
        tmp_path / "journal/event-000000.json",
        dict(kind="outcome", payload=dict(status="valid", state=state)),
    )
    plan = dict(
        source=str(tmp_path), source_config_sha=sha(tmp_path / "config.json"), reserved_requests=40
    )
    if condition == "pin":
        plan["source_config_sha"] = "0" * 64
    with pytest.raises(ValueError, match="shared_"):
        shared_guard(plan)


def test_continuation_preserves_roster_expiry_and_charges_prefix(tmp_path):
    c, b = fixture(tmp_path)
    receipt = json.loads((c / "receipt.json").read_text())
    receipt["http_status"] = 200
    (c / "receipt.json").write_text(json.dumps(receipt))
    (c / "manifest.json").unlink()
    seal(c)
    _, result = qualify_window(c, b, 3)
    root = tmp_path / "previous"
    for folder in ("requests", "results", "processes", "frozen"):
        (root / folder).mkdir(parents=True)
    shutil.copytree(c, root / "requests/000000")
    publish(root / "results/000000.json", result)
    publish(root / "processes/000000-reservation.json", {})
    plan = dict(
        cases=[b],
        expires_ns=12345,
        jobs=[dict(birth=b, days=3), dict(birth=b, days=10)],
        age_semantics="pool_birth_proxy_not_verified_mint_genesis",
    )
    publish(root / "frozen/plan.json", plan)
    publish(root / "STOP", dict(category="shared_source_cooldown_or_failure"))
    publish(root / "result.json", dict(requests=1))
    seal(root / "frozen")
    seal(root)
    continued = tool.continuation_plan(root)
    assert continued["jobs"] == plan["jobs"][1:]
    assert continued["cases"] == plan["cases"]
    assert continued["expires_ns"] == 12345
    assert continued["continuation"]["charged_requests"] == 1
    report = tool.report_campaigns([root], tmp_path / "support")
    assert report["attempted_windows"] == report["unattempted_windows"] == 1
    assert report["eligible_origins"] == 43
    assert report["complete_benchmark_ready"] is False
    assert report["training_allowed"] is False
    publish(root / "processes/000001-reservation.json", {})
    (root / "manifest.json").unlink()
    seal(root)
    with pytest.raises(ValueError, match="incomplete_prefix"):
        tool.continuation_plan(root)


def test_new_envelope_reporting_requires_exact_sealed_roster_suffix(tmp_path):
    c, b = fixture(tmp_path)
    _, result = qualify_window(c, b, 3)
    first = tmp_path / "first"
    for directory in ("frozen", "requests", "results"):
        (first / directory).mkdir(parents=True)
    shutil.copytree(c, first / "requests/000000")
    publish(first / "results/000000.json", result)
    old = dict(
        cases=[b],
        expires_ns=12345,
        jobs=[dict(birth=b, days=d) for d in (3, 10)],
        age_semantics="synthetic",
    )
    publish(first / "frozen/plan.json", old)
    publish(first / "result.json", dict(requests=1))
    seal(first / "frozen")
    seal(first)
    new = tmp_path / "new"
    for directory in ("frozen", "requests", "results"):
        (new / directory).mkdir(parents=True)
    plan = dict(
        old,
        guard_kind="retired_owner_research_v1",
        jobs=old["jobs"][1:],
        created_ns=12346,
        expires_ns=99999,
        original_expiry_ns=12345,
        original_roster_sha=sha(first / "frozen/plan.json"),
        prior_envelopes=[
            dict(path="/other-host/first", sha=sha(first / "manifest.json"), requests=1)
        ],
    )
    publish(new / "frozen/plan.json", plan)
    publish(new / "result.json", dict(requests=0))
    seal(new / "frozen")
    seal(new)
    report = tool.report_campaigns([first, new], tmp_path / "report")
    assert report["attempted_windows"] == 1
    assert not report["complete_benchmark_ready"]
    plan["jobs"] = old["jobs"]
    (new / "frozen/plan.json").write_text(json.dumps(plan))
    (new / "frozen/manifest.json").unlink()
    (new / "manifest.json").unlink()
    seal(new / "frozen")
    seal(new)
    with pytest.raises(ValueError, match="new_envelope_binding"):
        tool.report_campaigns([first, new], tmp_path / "wrong")
