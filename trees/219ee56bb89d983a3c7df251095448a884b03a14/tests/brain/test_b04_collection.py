import time

import pytest
from test_quality import POOLS, capture, root

from memecoin_brain.source import NS, SourceFailure
from memecoin_brain_b04 import service
from memecoin_brain_b04.collection import progress
from memecoin_brain_b04.runtime import inherited_seed
from memecoin_brain_quality.policy import HOUR, due, event, states
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal


def test_stagnation_counts_valid_stale_and_stops_after_six_without_inactivity_claim(tmp_path):
    r = root(tmp_path)
    cap = capture(r, POOLS[0])
    close = time.time_ns() // (300 * NS) * 300
    write(r / "progress-seed.json", dict(last_closes={POOLS[0]: close}, stagnant_cycles=0))
    statuses = [dict(pool=p, status="quarantine_skipped") for p in POOLS]
    statuses[0].update(status="valid_stale_or_empty", capture=str(cap))
    report = dict(denominator=12, statuses=statuses, fresh=0)
    for i in range(6):
        out = r / "cycles" / f"{i:04d}"
        out.mkdir()
        if i < 5:
            result = progress(r, out, report)
            assert result["valid_captures"] == 1 and not result["inactivity_proven"]
            assert result["stagnant_cycles"] == i + 1
        else:
            with pytest.raises(SourceFailure, match="six_cycles_no_event_progress"):
                progress(r, out, report)
    assert read(r / "STOP")["reason"] == "six_cycles_no_event_progress"


def test_advance_resets_stagnation_but_failed_cycle_does_not(tmp_path):
    r = root(tmp_path)
    cap = capture(r, POOLS[0])
    write(r / "progress-seed.json", dict(last_closes={}, stagnant_cycles=5))
    failed = r / "cycles/0000"
    failed.mkdir()
    write(failed / "result.json", dict(status="failed"))
    out = r / "cycles/0001"
    out.mkdir()
    statuses = [dict(pool=p, status="quarantine_skipped") for p in POOLS]
    statuses[0].update(status="valid_fresh", capture=str(cap))
    result = progress(r, out, dict(denominator=12, statuses=statuses, fresh=1))
    assert result["stagnant_cycles"] == 0 and result["advanced_pools"] == [POOLS[0]]
    missing = dict(denominator=11, statuses=statuses[1:], fresh=0)
    with pytest.raises(ValueError, match="availability_denominator"):
        progress(r, out, missing)


def test_new_root_inherits_probe_debt_streak_pending_and_due(tmp_path):
    r = root(tmp_path, seeded=True)
    now = read(r / "quality-seed.json")["created_ns"]
    for i in [1, 2, 3]:
        event(r, POOLS[0], "reserve_probe", now_ns=now + i * HOUR)
    seed = inherited_seed(r, now + 4 * HOUR)
    assert seed["pools"] == states(r)[0]
    assert seed["pools"][POOLS[0]]["probes"] == 3
    assert not due(seed["pools"][POOLS[0]], now + 100 * HOUR)
    assert not seed["probe_budgets_reset"]


def test_completed_collector_cycle_never_calls_training(tmp_path, monkeypatch):
    r = root(tmp_path)
    monkeypatch.setattr(service, "verify_runtime", lambda *a: None)
    monkeypatch.setattr(service, "guard", lambda *a: None)
    phases = []

    def phase(root, output, phase, seconds):
        phases.append(phase)
        write(output / "collection.json", dict(attempted=1))
        return dict(category="completed", exit_code=0)

    monkeypatch.setattr(service, "run_phase", phase)
    assert service.tick(r, "fixture")["training"] == "disabled_collector_only"
    assert phases == ["collect"]


def test_activation_binds_two_seals_and_rejects_wrong_runtime(tmp_path, monkeypatch):
    r = root(tmp_path)
    monkeypatch.setattr(service, "verify_runtime", lambda *a: None)
    write(r / "config.json", dict(expires_ns=1000))
    write(r / "deployment.json", dict(service_plist_sha="plist"))
    refs = []
    for n in ["0000", "0001"]:
        p = r / "cycles" / n
        p.mkdir()
        write(p / "result.json", dict(status="completed"))
        write(p / "availability.json", dict(valid_captures=1, denominator=12, fresh=0))
        seal(p)
        refs.append(dict(name=n, sha=sha(p / "manifest.json")))
    receipt = dict(
        decision="approved_collector_only",
        runtime_pin="pin",
        root=str(r.resolve()),
        training_enabled=False,
        expiry_ns=1000,
        plist_sha="plist",
        cycles=refs,
    )
    write(r / "service-activation.json", receipt)
    service.check_activation(r, "pin")
    with pytest.raises(ValueError, match="collector_activation_binding"):
        service.check_activation(r, "different")
    with (r / "cycles/0000/availability.json").open("a") as f:
        f.write(" ")
    with pytest.raises(ValueError):
        service.check_activation(r, "pin")


def test_shadow_imported_model_roots_require_exact_declared_binding(tmp_path):
    from memecoin_brain_scale.audit import shadow_snapshot

    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "shadow/predictions").mkdir(parents=True)
    model = tmp_path / "parent/models/000000"
    model.mkdir(parents=True)
    write(model / "model-card.json", dict(training_as_of_ns=1, finished_ns=2))
    seal(model)
    write(
        primary / "shadow/predictions/p.json",
        dict(
            model_sha=sha(model / "manifest.json"),
            model_path=str(model),
            predicted_ns=3,
            input_available_ns=2,
            event_close_s=1,
            horizon=1,
            pool="p",
        ),
    )
    roots = (model.parent,)
    write(
        primary / "import.json",
        dict(primary=str(tmp_path / "legacy"), model_roots=[str(model.parent)]),
    )
    write(primary / "activation.json", dict(import_sha=sha(primary / "import.json")))
    result = shadow_snapshot(primary, tmp_path / "ok", [], 4, imported_model_roots=roots)
    assert result["predictions"] == 1 and result["pending"] == 1
    with pytest.raises(ValueError, match="undeclared_imported_models"):
        shadow_snapshot(
            primary, tmp_path / "bad", [], 4, imported_model_roots=(tmp_path / "unapproved",)
        )
