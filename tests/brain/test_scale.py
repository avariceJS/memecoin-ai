import time

import pytest

from memecoin_brain.source import BAR, NS, SourceFailure
from memecoin_brain_scale.history import budget_bound, idle_slot
from memecoin_ml.data import write


def test_shared_lifetime_reserve_is_conservative(tmp_path):
    (tmp_path / "requests").mkdir()
    write(tmp_path / "external-request-debit.json", dict(requests=1))
    now = 123 * NS
    write(tmp_path / "config.json", dict(expires_ns=now + BAR * NS, max_requests=49))
    assert budget_bound(tmp_path, now)["future_max"] == 24
    (tmp_path / "requests/000000").mkdir()
    with pytest.raises(SourceFailure, match="shared_lifetime_budget"):
        budget_bound(tmp_path, now)


def test_history_requires_completed_current_cycle_and_boundary_headroom(tmp_path):
    due = 3000
    path = tmp_path / "cycles" / str(due)
    path.mkdir(parents=True)
    assert not idle_slot(tmp_path, (due + 100) * NS)
    write(path / "result.json", dict(status="completed"))
    assert idle_slot(tmp_path, (due + 100) * NS)
    assert not idle_slot(tmp_path, (due + 275) * NS)
    assert not idle_slot(tmp_path, (due + 400) * NS)


def test_history_failed_page_is_not_retried(tmp_path, monkeypatch):
    from memecoin_brain_scale import history

    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "requests").mkdir()
    output = tmp_path / "new"

    def prepare(*args):
        output.mkdir()
        (output / "requests").mkdir()
        return dict(tasks=[dict(pool="fixture", cursor=1000, floor=0, needed=True)])

    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise SourceFailure("fixture_HTTP429")

    monkeypatch.setattr(history, "prepare", prepare)
    monkeypatch.setattr(history, "host_guard", lambda p: None)
    monkeypatch.setattr(history, "cooldown", lambda p: 0)
    monkeypatch.setattr(history, "idle_slot", lambda *a: True)
    monkeypatch.setattr(history, "budget_bound", lambda *a: {})
    monkeypatch.setattr(history, "ohlcv", fail)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    result = history.acquire(primary, output)
    assert attempts == [1]
    assert result["status"] == "failed_no_retry"
    assert result["pending"][0]["pool"] == "fixture"
    assert (output / "failure.json").exists()


def fixture_capture(path, rows, available):
    import json

    from memecoin_registry.contracts import seal

    path.mkdir(parents=True)
    write(
        path / "request.json",
        dict(
            started_ns=available - 2,
            lane="historical",
            url="https://api.geckoterminal.com/api/v2/networks/solana/pools/"
            + "A" * 32
            + "/ohlcv/minute",
        ),
    )
    write(path / "receipt.json", dict(received_ns=available - 1, available_ns=available))
    (path / "response.bin").write_text(
        json.dumps(dict(data=dict(attributes=dict(ohlcv_list=rows))))
    )
    seal(path)
    return path


def test_conflicting_capture_quarantined_whole_not_arbitrarily_deduplicated(tmp_path):
    from memecoin_brain_scale.audit import safe_corpus

    write(tmp_path / "universe.json", dict(admitted_ns=0, pools=[dict(pool="A" * 32)]))
    fixture_capture(tmp_path / "requests/000000", [[0, 10, 12, 9, 10, 1]], 500 * NS)
    fixture_capture(
        tmp_path / "requests/000001", [[0, 10, 12, 9, 11, 1], [0, 11, 12, 9, 11, 1]], 600 * NS
    )
    rows, provenance = safe_corpus(tmp_path, 700 * NS)
    assert len(rows) == 1 and rows[0]["ohlcv"][3] == 10
    assert len(provenance["quarantined"]) == 1
    assert not provenance["revisions"]
    # Hash corruption is an integrity failure, never merely another excluded market capture.
    with (tmp_path / "requests/000001/response.bin").open("a") as f:
        f.write("tamper")
    with pytest.raises(ValueError):
        safe_corpus(tmp_path, 700 * NS)


def test_earliest_actual_receipt_wins_across_roots_and_future_is_rejected(tmp_path):
    from memecoin_brain_scale.audit import merge

    late = fixture_capture(tmp_path / "a", [[0, 10, 12, 9, 11, 1]], 600 * NS)
    early = fixture_capture(tmp_path / "z", [[0, 10, 12, 9, 10, 1]], 500 * NS)
    rows, prov = merge([(late, "A" * 32), (early, "A" * 32)], 700 * NS)
    assert rows[0]["ohlcv"][3] == 10 and len(prov["revisions"]) == 1
    with pytest.raises(ValueError, match="capture_after_snapshot"):
        merge([(late, "A" * 32)], 550 * NS)


def test_bootstrap_groups_time_across_pools_and_requires_support():
    from memecoin_brain_scale.audit import paired_summary

    rs = [
        dict(pool=p, block=b, losses=dict(empirical=2, linear=1, gru=3))
        for b in range(8)
        for p in ("a", "b")
    ]
    s = paired_summary(rs)
    assert s["ci"]["linear"] == [0.5, 0.5]
    assert s["ci"]["gru"] == [-0.5, -0.5]
    assert paired_summary(rs[:4])["ci"] is None
    rs[-1]["losses"].pop("gru")
    with pytest.raises(ValueError, match="unpaired_candidates"):
        paired_summary(rs)


def test_curve_folds_have_nested_train_fixed_evaluation_and_maturity_purge():
    from memecoin_brain_scale.curves import folds

    samples = [
        dict(
            id=str(i),
            pool="fixture",
            event_close_s=i * 300,
            maturity_s=(i + 3) * 300,
            targets=[dict(log_return=0.1)],
        )
        for i in range(200)
    ]
    designs = folds(dict(samples=samples))
    for start in (0, 3):
        trio = designs[start : start + 3]
        assert len({e["membership"]["evaluation"] for e in trio}) == 1
        assert len({e["membership"]["validation"] for e in trio}) == 1
        ids = [{s["id"] for s in e["parts"]["train"]} for e in trio]
        assert ids[0] < ids[1] < ids[2]
        for e in trio:
            for role, boundary in zip(
                ("train", "validation", "evaluation"), e["cutoffs"][1:], strict=True
            ):
                assert all(s["maturity_s"] < boundary for s in e["parts"][role])


def test_shadow_pending_is_not_zero_loss_and_offline_recovery_waits_for_maturity(tmp_path):
    from memecoin_brain_scale.audit import shadow_snapshot
    from memecoin_ml.data import sha
    from memecoin_registry.contracts import seal

    model = tmp_path / "models/000000"
    model.mkdir(parents=True)
    write(model / "model-card.json", dict(training_as_of_ns=100 * NS, finished_ns=200 * NS))
    seal(model)
    predictions = tmp_path / "shadow/predictions"
    predictions.mkdir(parents=True)
    p = dict(
        pool="fixture",
        model_path=str(model),
        model_sha=sha(model / "manifest.json"),
        predicted_ns=350 * NS,
        input_available_ns=310 * NS,
        event_close_s=300,
        horizon=1,
        origin_close=10,
        q=[-0.1, 0, 0.1],
        q_by_candidate={n: [-0.1, 0, 0.1] for n in ("empirical", "linear", "gru")},
    )
    write(predictions / "prediction.json", p)
    rows = [
        dict(
            pool="fixture",
            open_s=300,
            available_ns=620 * NS,
            ohlcv=[10, 12, 9, 11, 1],
            capture_sha="fixture",
        )
    ]
    first = shadow_snapshot(tmp_path, tmp_path / "before", rows, 610 * NS)
    assert first["overall"]["variants"] == 0 and first["pending"] == 1
    later = shadow_snapshot(tmp_path, tmp_path / "later", rows, 700 * NS)
    assert later["recovered_mature_outcomes"] == 1
    assert later["published_only"]["variants"] == 0
    assert later["overall"]["variants"] == 1
    assert not (tmp_path / "shadow/outcomes").exists()
