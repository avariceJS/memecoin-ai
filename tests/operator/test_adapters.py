"""Filesystem publication, scope pins, exam bounds. Synthetic fixtures only."""

import json
import sys
import time
import types
from pathlib import Path

import pytest

from memecoin_continuous.store import connect, ingest
from memecoin_ml.data import read, sha
from memecoin_operator.adapters import inspect, paper_projection, public_registry, registered, run
from memecoin_operator.learning import prepare
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish

NS = 10**9


@pytest.fixture
def acquisition(monkeypatch):
    try:
        from memecoin_operator import acquisition as existing

        if hasattr(existing, "current_source"):
            return existing
    except ImportError:
        pass
    module = types.ModuleType("memecoin_operator.acquisition")

    def current_source(root):
        return Path(read(Path(root) / "config.json")["source_root"])

    module.current_source = current_source
    monkeypatch.setitem(sys.modules, "memecoin_operator.acquisition", module)
    return module


def operator_config(root, source, expires_ns, **extra):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "config.json"
    if path.exists():
        path.unlink()
    init_ns = min(time.time_ns(), expires_ns - 1)
    payload = dict(
        product="operator",
        mode="operator",
        provider="geckoterminal",
        profile="balanced",
        init_ns=init_ns,
        real_execution_enabled=False,
        paid_budget_usd=0,
        additional_requests=0,
        max_background_requests=6000,
        expires_ns=expires_ns,
        source_root=str(Path(source).resolve()),
        champion_model_path=str((Path(source) / "champion").resolve()),
        champion_model_sha="ab" * 32,
        max_training_runs=6,
        min_refit_interval_s=86400,
    )
    payload.update(extra)
    if payload["expires_ns"] > payload["init_ns"] + 7 * 86400 * NS:
        payload["init_ns"] = payload["expires_ns"] - 6 * 86400 * NS
    publish(path, payload)


def bind_source(source, expires_ns):
    source = Path(source).resolve()
    source.mkdir(parents=True, exist_ok=True)
    publish(
        source / "config.json",
        dict(expires_ns=expires_ns, max_requests=30000, hot_pools=["oldpool"], paid_budget_usd=0),
    )
    bundle = source / "runtime-bundle"
    bundle.mkdir()
    publish(
        bundle / "binding.json",
        dict(
            root=str(source),
            python_sha=sha(Path(sys.executable).resolve()),
            files={"config.json": sha(source / "config.json")},
        ),
    )
    publish(bundle / "marker.json", dict(synthetic=True))
    seal(bundle)
    publish(
        source / "deployment.json",
        dict(bundle=str(bundle.resolve()), pin=sha(bundle / "manifest.json")),
    )
    db = connect(source / "index.sqlite")
    db.close()
    return source


def sealed_box(path):
    path.mkdir(parents=True)
    publish(path / "note.json", dict(synthetic=True))
    seal(path)
    return path


def point(root, kind, item, artifact, manifest_sha=None):
    folder = root / kind
    folder.mkdir(parents=True, exist_ok=True)
    artifact = Path(artifact)
    payload = dict(
        id=item,
        artifact=str(artifact),
        manifest_sha=manifest_sha if manifest_sha is not None else sha(artifact / "manifest.json"),
        status="completed",
        dataset_sha="cd" * 32,
        finished_ns=1,
        model_sha="ef" * 32,
    )
    (folder / f"{item}.json").write_text(json.dumps(payload))
    return payload


def ohlcv(i):
    close = 1.0 + (i % 23) * 0.001
    open_ = close * 0.999
    high = close * 1.002
    low = min(open_, close) * 0.998
    return [open_, high, low, close, 10.0 + (i % 7)]


def capture(path, opens):
    path.mkdir(parents=True)
    raw = [[t, *ohlcv(t)] for t in opens]
    received = (max(opens) + 300) * NS
    publish(
        path / "request.json",
        dict(started_ns=received - 2, lane="historical", url="synthetic"),
    )
    (path / "response.bin").write_text(json.dumps(dict(data=dict(attributes=dict(ohlcv_list=raw)))))
    publish(path / "receipt.json", dict(received_ns=received, available_ns=received + 2))
    seal(path)
    return path


def index_source(source, end):
    old = list(range(end - 60 * 3600, end, 300))
    young = list(range(end - 12000, end, 300))
    db = connect(source / "index.sqlite")
    ingest(db, capture(source / "captures" / "old", old), "oldpool")
    ingest(db, capture(source / "captures" / "young", young), "youngpool")
    db.close()


def small_rows():
    rows = []
    for pool, start in [("old", 0), ("young", 100)]:
        for i in range(start, 300):
            close = 1.0 + (i % 17) * 0.002
            open_ = close * 0.998
            rows.append(
                dict(
                    pool=pool,
                    open_s=i * 300,
                    available_ns=(i + 1) * 300 * NS + 1,
                    ohlcv=[open_, close * 1.01, open_ * 0.99, close, 100.0],
                    capture_sha=f"synthetic-{pool}-{i}",
                )
            )
    return rows, dict(as_of_ns=300 * 300 * NS + 1, synthetic=True)


def register_dataset(root):
    rows, prov = small_rows()
    folder = root / "manual" / "snap1"
    folder.mkdir(parents=True)
    report = prepare(rows, prov, folder)
    seal(folder)
    from memecoin_operator.adapters import register

    return register(root, "datasets", folder, dict(status="completed", **report))


def model_candidate(root, name, *, finished_ns, card_finished=None, claimed_sha=None):
    from memecoin_operator.adapters import register

    art = root / "manual" / name
    model = art / "model"
    model.mkdir(parents=True)
    card_finished = finished_ns if card_finished is None else card_finished
    publish(
        model / "model-card.json",
        dict(
            training_as_of_ns=card_finished - NS,
            finished_ns=card_finished,
            selected="empirical",
            confirmatory_F01=False,
            production_trade_authorized=False,
            prospective_shadow_required=True,
        ),
    )
    publish(model / "synthetic.json", dict(kind="synthetic-fixture"))
    seal(model)
    actual = sha(model / "manifest.json")
    result = dict(
        status="completed",
        finished_ns=finished_ns,
        model_sha=actual if claimed_sha is None else claimed_sha,
        dataset_id="synthetic-snap",
        dataset_sha="cd" * 32,
        selected="empirical",
        promoted=False,
        new_fits=4,
    )
    publish(art / "result.json", result)
    seal(art)
    return register(root, "candidates", art, result), model


def test_collect_publishes_sealed_profile_and_rejects_a_second_copy(tmp_path, acquisition):
    root = tmp_path / "op"
    source = tmp_path / "source"
    source.mkdir()
    operator_config(root, source, time.time_ns() + 3 * 86400 * NS)
    job = root / "jobs" / "collect1"
    result = run(root, "collect", dict(profile="history"), job)
    artifact = job / "artifact"
    assert result["status"] == "completed"
    assert result["profile"] == "history"
    assert result["source_owned"] is False
    verify(artifact)
    assert read(root / "collection-profile.json")["profile"] == "history"
    # The seal lives on the artifact directory. The existence guard must see it.
    assert (artifact / "manifest.json").exists()
    assert not (job / "manifest.json").exists()
    pinned = sha(artifact / "manifest.json")
    with pytest.raises(ValueError, match="operator_output_exists"):
        run(root, "collect", dict(profile="balanced"), job)
    assert sha(artifact / "manifest.json") == pinned


def test_virtual_expiry_scope_and_profile_guards(tmp_path, acquisition):
    root = tmp_path / "op"
    source = tmp_path / "source"
    source.mkdir()
    operator_config(root, source, time.time_ns() + 86400 * NS, real_execution_enabled=True)
    with pytest.raises(ValueError, match="operator_virtual_only"):
        run(root, "collect", {}, root / "jobs" / "a")
    operator_config(root, source, time.time_ns() + 86400 * NS, paid_budget_usd=1)
    with pytest.raises(ValueError, match="operator_virtual_only"):
        run(root, "collect", {}, root / "jobs" / "b")
    operator_config(root, source, time.time_ns() - 1)
    with pytest.raises(ValueError, match="operator_expired"):
        run(root, "collect", {}, root / "jobs" / "c")
    operator_config(root, source, time.time_ns() + 86400 * NS)
    with pytest.raises(ValueError, match="operator_collect_profile"):
        run(root, "collect", dict(profile="live"), root / "jobs" / "d")
    with pytest.raises(ValueError, match="operator_unknown_action"):
        run(root, "promote", {}, root / "jobs" / "e")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="operator_output_scope"):
        run(root, "collect", {}, link / "job")
    assert list(outside.iterdir()) == []
    with pytest.raises(ValueError, match="operator_output_scope"):
        run(root, "collect", {}, root / "jobs" / ".." / ".." / "escaped-job")


def test_registry_rejects_escape_symlink_and_tampered_pin(tmp_path):
    root = (tmp_path / "op").resolve()
    root.mkdir()
    outside = sealed_box(tmp_path / "outside" / "hidden")
    point(root, "datasets", "outside", outside)
    with pytest.raises(ValueError, match="operator_artifact_scope"):
        registered(root, "datasets", "outside")
    (root / "nest").mkdir()
    lexical = root / "nest" / ".." / ".." / "outside" / "hidden"
    assert lexical.resolve() == outside.resolve()
    point(root, "datasets", "dotdot", lexical, manifest_sha=sha(outside / "manifest.json"))
    with pytest.raises(ValueError, match="operator_artifact_scope"):
        registered(root, "datasets", "dotdot")
    alias = root / "alias"
    alias.symlink_to(tmp_path / "outside", target_is_directory=True)
    via = root / "alias" / "hidden"
    assert via.is_symlink() is False
    point(root, "datasets", "via", via)
    with pytest.raises(ValueError, match="operator_artifact_scope"):
        registered(root, "datasets", "via")
    inside = sealed_box(root / "inside")
    leaf = root / "leaf"
    leaf.symlink_to(inside, target_is_directory=True)
    point(root, "datasets", "leaf", leaf)
    with pytest.raises(ValueError, match="operator_artifact_scope"):
        registered(root, "datasets", "leaf")
    point(root, "datasets", "pin", inside, manifest_sha="0" * 64)
    with pytest.raises(ValueError, match="operator_artifact_pin"):
        registered(root, "datasets", "pin")
    (inside / "note.json").write_text('{"synthetic": false}\n')
    point(root, "datasets", "mutated", inside)
    with pytest.raises(ValueError, match="inventory|checksum|artifact"):
        registered(root, "datasets", "mutated")


def test_snapshot_publishes_immutable_registry_and_unseen_recent_pool(tmp_path, acquisition):
    end = time.time_ns() // NS // 300 * 300
    source = bind_source(tmp_path / "source", time.time_ns() + 5 * 86400 * NS)
    index_source(source, end)
    root = tmp_path / "op"
    operator_config(root, source, time.time_ns() + 5 * 86400 * NS)
    publish(
        source / "health.json",
        dict(at_ns=time.time_ns(), attempts=3, cooldown_ns=0, failures=0),
    )
    result = run(root, "snapshot", {}, root / "jobs" / "snap1")
    artifact = root / "jobs" / "snap1" / "artifact"
    verify(artifact)
    assert result["snapshot_id"] == "snap1"
    assert result["dataset_id"] == "snap1"
    assert result["status"] == "completed"
    assert result["new_fits"] == 0
    assert result["historical_test_independent"] is False
    assert result["training_pnl_used"] is False
    assert "youngpool" in result["unseen_test_pools"]
    assert "oldpool" not in result["unseen_test_pools"]
    assert result["raw_audit"]["exact"] is True
    record = read(root / "datasets" / "snap1.json")
    assert record["manifest_sha"] == sha(artifact / "manifest.json")
    assert record["artifact"] == str(artifact.resolve())
    public = public_registry(root, "datasets")
    assert "artifact" not in public[0]
    assert public[0]["snapshot_hash"] == record["dataset_sha"]
    coverage = read(artifact / "coverage.json")
    assert coverage["retained"]["train"]["decisions"] >= 16
    assert coverage["retained"]["validation"]["decisions"] >= 16
    assert coverage["retained"]["test"]["decisions"] >= 16
    original = (artifact / "coverage.json").read_bytes()
    (artifact / "coverage.json").write_bytes(original + b" ")
    with pytest.raises(ValueError):
        registered(root, "datasets", "snap1")
    view = inspect(root)
    assert view["source"]["provider"] == "geckoterminal"
    assert view["source"]["single_provider"] is True
    assert view["policy"]["learned_exit"] is False
    assert view["policy"]["automatic_promotion"] is False
    assert view["limits"]["paid_budget_usd"] == 0
    assert view["coverage"]["bars"] > 0


def test_json_stop_is_visible_and_touch_stop_does_not_blank_inspect(tmp_path, acquisition):
    source = bind_source(tmp_path / "source", time.time_ns() + 86400 * NS)
    root = tmp_path / "op"
    operator_config(root, source, time.time_ns() + 86400 * NS)
    publish(source / "health.json", dict(at_ns=time.time_ns(), attempts=1, cooldown_ns=0))
    publish(source / "STOP", dict(reason="synthetic_halt"))
    view = inspect(root)
    assert view["source"]["stopped"] is True
    assert view["source"]["healthy"] is False
    assert view["source"]["stop_reason"] == "synthetic_halt"


def test_touch_stop_still_reports_a_stopped_source(tmp_path, acquisition):
    source = bind_source(tmp_path / "source", time.time_ns() + 86400 * NS)
    root = tmp_path / "op"
    operator_config(root, source, time.time_ns() + 86400 * NS)
    publish(source / "health.json", dict(at_ns=time.time_ns(), attempts=1, cooldown_ns=0))
    # Existing ops stop is `touch .../STOP`: an empty file, not a JSON object.
    (source / "STOP").write_bytes(b"")
    view = inspect(root)
    assert view["source"]["stopped"] is True
    assert view["source"]["healthy"] is False
    assert view["source"]["stop_reason"] == "unknown_STOP"


def test_same_dataset_cannot_train_twice(tmp_path, acquisition):
    root = tmp_path / "op"
    source = tmp_path / "source"
    source.mkdir()
    operator_config(root, source, time.time_ns() + 86400 * NS)
    entry = register_dataset(root)
    (root / "candidates").mkdir()
    (root / "candidates" / "prior.json").write_text(
        json.dumps(dict(id="prior", dataset_sha=entry["dataset_sha"]))
    )
    with pytest.raises(ValueError, match="operator_duplicate_training_snapshot"):
        run(root, "train", dict(snapshot_id=entry["id"]), root / "jobs" / "train1")
    assert not (root / "jobs" / "train1" / "artifact" / "model").exists()


def test_signal_projection_uses_the_baseline_lifecycle():
    projected = paper_projection(
        [
            dict(
                id="exam-synthetic",
                books=[
                    dict(
                        candidate="gru",
                        model_sha="m" * 64,
                        cash="1000",
                        equity="1000",
                        realized="0",
                        fees="0",
                        positions=[],
                        closed=[],
                        signals=[
                            dict(
                                decision_ns=3,
                                reason="enter_next_observed_close",
                                pool="youngpool",
                                q=[-0.01, 0.05, 0.2],
                            ),
                            dict(
                                decision_ns=2,
                                reason="already_exposed",
                                pool="oldpool",
                                q=[-0.01, 0.05, 0.2],
                            ),
                            dict(
                                decision_ns=1,
                                reason="median_below_cost_buffer",
                                pool="oldpool",
                                q=[-0.02, 0.0, 0.01],
                            ),
                        ],
                    )
                ],
            )
        ]
    )
    by_reason = {row["reason"]: row["action"] for row in projected["decisions"]}
    assert by_reason == {
        "enter_next_observed_close": "ENTER",
        "already_exposed": "HOLD",
        "median_below_cost_buffer": "WAIT",
    }


def test_incomplete_and_unfinished_models_cannot_open_an_exam(tmp_path, acquisition):
    now = time.time_ns()
    source = bind_source(tmp_path / "source", now + 3 * 86400 * NS)
    root = tmp_path / "op"
    operator_config(root, source, now + 3 * 86400 * NS)
    future, _model = model_candidate(root, "future", finished_ns=now + 365 * 86400 * NS)
    with pytest.raises(ValueError, match="operator_model_not_ready"):
        run(root, "paper", dict(candidate_id=future["id"]), root / "jobs" / "future-exam")
    assert not (root / "paper").exists()
    lying, _model = model_candidate(root, "lying", finished_ns=now - 60 * NS, claimed_sha="12" * 32)
    with pytest.raises(ValueError, match="operator_model_not_ready"):
        run(root, "paper", dict(candidate_id=lying["id"]), root / "jobs" / "lying-exam")
    late_card, _model = model_candidate(
        root,
        "latecard",
        finished_ns=now - 60 * NS,
        card_finished=now + 365 * 86400 * NS,
    )
    with pytest.raises(ValueError, match="frozen_exam_training_boundary"):
        run(root, "paper", dict(candidate_id=late_card["id"]), root / "jobs" / "late-exam")
    assert list((root / "exams").glob("*.json")) == []
    bare = root / "manual" / "bare"
    bare.mkdir(parents=True)
    publish(
        bare / "result.json",
        dict(status="completed", finished_ns=now - NS, model_sha="0" * 64),
    )
    seal(bare)
    from memecoin_operator.adapters import register

    bare_row = register(root, "candidates", bare, read(bare / "result.json"))
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        run(root, "paper", dict(candidate_id=bare_row["id"]), root / "jobs" / "bare-exam")
    assert list((root / "exams").glob("*.json")) == []


def test_exam_expiry_is_one_day_and_never_past_source_or_operator(tmp_path, acquisition):
    now = time.time_ns()
    horizon = now + 6 * 86400 * NS
    source = bind_source(tmp_path / "source", now + 10 * 86400 * NS)
    root = tmp_path / "op"
    operator_config(root, source, horizon)
    ready, model = model_candidate(root, "ready", finished_ns=now - 60 * NS)
    opened = run(root, "paper", dict(candidate_id=ready["id"]), root / "jobs" / "examday")
    assert opened["expires_ns"] - opened["activation_ns"] == 86400 * NS
    exam = root / "paper" / "examday"
    config = read(exam / "config.json")
    assert config["training_max_runs"] == 0
    assert config["real_execution_enabled"] is False
    frozen = config["frozen_exam_models"]
    assert len(frozen) == 1
    assert Path(frozen[0]["path"]).resolve() == model.resolve()
    assert frozen[0]["sha"] == ready["model_sha"]
    assert frozen[0]["label"] == "examday"
    assert read(exam / "activation.json")["no_backfill"] is True
    assert read(exam / "activation.json")["promotion"] is False
    verify(root / "jobs" / "examday" / "artifact")
    verify(Path(read(exam / "deployment.json")["bundle"]))

    source_short = bind_source(tmp_path / "source-short", now + 7200 * NS)
    root_short = tmp_path / "op-short"
    operator_config(root_short, source_short, horizon)
    ready_short, _model = model_candidate(root_short, "ready", finished_ns=now - 60 * NS)
    opened_short = run(
        root_short, "paper", dict(candidate_id=ready_short["id"]), root_short / "jobs" / "exam2h"
    )
    assert opened_short["expires_ns"] == now + 7200 * NS

    source_long = bind_source(tmp_path / "source-long", horizon)
    root_op = tmp_path / "op-bound"
    operator_expires = now + 5000 * NS
    operator_config(root_op, source_long, operator_expires)
    ready_op, _model = model_candidate(root_op, "ready", finished_ns=now - 60 * NS)
    opened_op = run(
        root_op, "paper", dict(candidate_id=ready_op["id"]), root_op / "jobs" / "exambound"
    )
    assert opened_op["expires_ns"] == operator_expires

    source_wait = bind_source(tmp_path / "source-wait", now + 1200 * NS)
    root_wait = tmp_path / "op-wait"
    operator_config(root_wait, source_wait, horizon)
    ready_wait, _model = model_candidate(root_wait, "ready", finished_ns=now - 60 * NS)
    with pytest.raises(ValueError, match="operator_source_expiry_wait_successor"):
        run(
            root_wait,
            "paper",
            dict(candidate_id=ready_wait["id"]),
            root_wait / "jobs" / "examwait",
        )
    assert list((root_wait / "exams").glob("*.json")) == []
    assert not (root_wait / "paper" / "examwait" / "config.json").exists()

    publish(source / "STOP", dict(reason="synthetic_halt"))
    ready2, _model = model_candidate(root, "ready2", finished_ns=now - 60 * NS)
    with pytest.raises(ValueError, match="operator_source_stopped_or_expired"):
        run(root, "paper", dict(candidate_id=ready2["id"]), root / "jobs" / "stopped-exam")
