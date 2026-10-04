import json
import time

import pytest

from memecoin_brain.source import NS, SourceFailure
from memecoin_brain_quality.collection import collect
from memecoin_brain_quality.policy import HOUR, blocked, due, event, inspect_capture, states
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal

POOLS = [letter * 32 for letter in "ABCDEFGHJKLM"]


def root(tmp_path, *, seeded=False):
    for name in ("requests", "cycles", "quality", "models", "training", "shadow"):
        (tmp_path / name).mkdir()
    now = time.time_ns()
    seed = dict(
        created_ns=now,
        pools={
            p: dict(status="active", probes=0, streak=0, pending=None, next_probe_ns=0)
            for p in POOLS
        },
    )
    if seeded:
        seed["pools"][POOLS[0]].update(status="quarantined", next_probe_ns=now + HOUR)
    write(tmp_path / "quality-seed.json", seed)
    write(
        tmp_path / "activation.json",
        dict(
            quality_seed_sha=sha(tmp_path / "quality-seed.json"),
            prior_consecutive_acquisition_failures=0,
        ),
    )
    write(tmp_path / "universe.json", dict(admitted_ns=now, pools=[dict(pool=p) for p in POOLS]))
    return tmp_path


def capture(r, pool, *, conflict=False):
    path = r / "requests" / f"{len(list((r / 'requests').iterdir())):06d}"
    path.mkdir()
    now = time.time_ns()
    bar = now // (300 * NS) * 300 - 300
    rows = [[bar, 10, 12, 9, 11, 1]]
    if conflict:
        rows.append([bar, 11, 12, 9, 11, 1])
    write(
        path / "request.json",
        dict(
            started_ns=now - 2,
            lane="prospective",
            url=f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute",
        ),
    )
    write(path / "receipt.json", dict(received_ns=now - 1, available_ns=now))
    (path / "response.bin").write_text(
        json.dumps(dict(data=dict(attributes=dict(ohlcv_list=rows))))
    )
    seal(path)
    return path


def test_mixed_cycle_continues_healthy_keeps_denominator_and_blocks_predictions(tmp_path):
    r = root(tmp_path)
    output = r / "cycles/fixture"
    output.mkdir()
    allowed = []

    def shadow(*args, prediction_pools):
        allowed.extend(prediction_pools)
        return dict(new_predictions=0)

    report = collect(
        r, output, fetch=lambda r, p, **kw: capture(r, p, conflict=p == POOLS[0]), shadow=shadow
    )
    assert report["denominator"] == 12 and report["attempted"] == 12 and report["fresh"] == 11
    assert len(read(output / "denominator.json")["pools"]) == 12
    assert POOLS[0] not in allowed and len(allowed) == 11
    assert report["statuses"][0]["status"] == "schema_quarantine"
    assert len(report["rejected_captures"]) == 1
    # Restart reconstructs durable quarantine, sends no immediate probe and retains denominator.
    second = r / "cycles/next"
    second.mkdir()
    report = collect(r, second, fetch=lambda r, p, **kw: capture(r, p), shadow=shadow)
    assert report["attempted"] == 11 and report["denominator"] == 12
    assert report["statuses"][0]["status"] == "quarantine_skipped"


def test_seed_and_two_hourly_probes_required_for_release(tmp_path):
    r = root(tmp_path, seeded=True)
    seed_ns = read(r / "quality-seed.json")["created_ns"]
    state = states(r)[0][POOLS[0]]
    assert not due(state, seed_ns + HOUR - 1)
    first = event(r, POOLS[0], "reserve_probe", now_ns=seed_ns + HOUR)
    event(r, POOLS[0], "probe_valid", probe=first, now_ns=seed_ns + HOUR + 1)
    assert POOLS[0] in blocked(r)
    assert not due(states(r)[0][POOLS[0]], seed_ns + 2 * HOUR - 1)
    second = event(r, POOLS[0], "reserve_probe", now_ns=seed_ns + 2 * HOUR)
    event(r, POOLS[0], "probe_valid", probe=second, now_ns=seed_ns + 2 * HOUR + 1)
    assert POOLS[0] not in blocked(r)


def test_crashed_probe_is_charged_and_cannot_fake_consecutive_success(tmp_path):
    r = root(tmp_path, seeded=True)
    now = read(r / "quality-seed.json")["created_ns"]
    first = event(r, POOLS[0], "reserve_probe", now_ns=now + HOUR)
    event(r, POOLS[0], "probe_valid", probe=first, now_ns=now + HOUR + 1)
    event(r, POOLS[0], "reserve_probe", now_ns=now + 2 * HOUR)  # Interrupted, no outcome.
    third = event(r, POOLS[0], "reserve_probe", now_ns=now + 3 * HOUR)
    event(r, POOLS[0], "probe_valid", probe=third, now_ns=now + 3 * HOUR + 1)
    state = states(r)[0][POOLS[0]]
    assert state["probes"] == 3 and state["status"] == "quarantined"
    assert not due(state, now + 10 * HOUR)


def test_hash_corruption_is_global_and_quality_seed_is_pinned(tmp_path):
    r = root(tmp_path)
    cap = capture(r, POOLS[0], conflict=True)
    assert inspect_capture(cap, POOLS[0])["status"] == "schema_quarantine"
    with (cap / "response.bin").open("a") as f:
        f.write("corruption")
    with pytest.raises(ValueError):
        inspect_capture(cap, POOLS[0])
    with (r / "quality-seed.json").open("a") as f:
        f.write(" ")
    with pytest.raises(ValueError, match="quality_seed_binding"):
        states(r)


def test_provider_failure_never_quarantines_a_pool_or_retries(tmp_path):
    r = root(tmp_path)
    output = r / "cycles/global"
    output.mkdir()
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise SourceFailure("public_http_failure")

    with pytest.raises(SourceFailure):
        collect(r, output, fetch=fail)
    assert calls == [1] and not blocked(r)
    assert len(read(output / "denominator.json")["pools"]) == 12


def test_four_quarantined_pools_stop_the_whole_source(tmp_path):
    r = root(tmp_path)
    output = r / "cycles/broad"
    output.mkdir()
    with pytest.raises(SourceFailure, match="quality_universe_stop"):
        collect(r, output, fetch=lambda r, p, **kw: capture(r, p, conflict=True))
    assert len(blocked(r)) == 4
    assert len(list((r / "requests").iterdir())) == 4
    assert (r / "STOP").exists()


def test_controller_crash_claim_is_not_replayed(tmp_path, monkeypatch):
    from memecoin_brain_quality import service

    r = root(tmp_path)
    due_s = time.time_ns() // (300 * NS) * 300
    (r / "cycles" / str(due_s)).mkdir()
    monkeypatch.setattr(service, "verify_runtime", lambda *a: None)
    monkeypatch.setattr(service, "guard", lambda *a: None)
    monkeypatch.setattr(service, "run_phase", lambda *a: pytest.fail("no replay permitted"))
    assert service.tick(r, "fixture")["status"] == "already_reserved"
    assert read(r / "cycles" / str(due_s) / "result.json")["status"] == "failed"


@pytest.mark.parametrize("code", [401, 403, 429])
def test_controller_auth_or_rate_error_stops_globally(tmp_path, monkeypatch, code):
    from memecoin_brain_quality import service

    r = root(tmp_path)
    monkeypatch.setattr(service, "verify_runtime", lambda *a: None)
    monkeypatch.setattr(service, "guard", lambda *a: None)

    def phase(*args):
        request = r / "requests/000000"
        request.mkdir()
        write(request / "failure.json", dict(http_status=code, received_ns=time.time_ns()))
        return dict(category="worker_failed", exit_code=1)

    monkeypatch.setattr(service, "run_phase", phase)
    assert service.tick(r, "fixture")["status"] == "failed"
    assert read(r / "STOP")["reason"] == "global_provider_auth_or_rate"


def test_history_isolates_schema_failure_then_keeps_healthy_prefix(tmp_path):
    from memecoin_brain_quality.history import run

    r = root(tmp_path)
    output = r / "history"
    output.mkdir()
    bar = time.time_ns() // (300 * NS) * 300 - 300
    write(
        output / "binding.json",
        dict(tasks=[dict(pool=p, cursor=bar + 300, floor=bar, needed=True) for p in POOLS[:2]]),
    )
    calls = []

    def fetch(r, p, **kw):
        calls.append(kw["before"])
        return capture(r, p, conflict=p == POOLS[0])

    result = run(r, output, fetch=fetch)
    assert calls == [bar + 299, bar + 299]
    assert [t["status"] for t in result["completed"]] == ["schema_quarantine", "floor_reached"]
    assert blocked(r) == {POOLS[0]} and not (r / "STOP").exists()


def test_history_inclusive_boundary_without_progress_terminates(tmp_path):
    from memecoin_brain_quality.history import run

    r = root(tmp_path)
    output = r / "history"
    output.mkdir()
    bar = time.time_ns() // (300 * NS) * 300 - 300
    write(
        output / "binding.json",
        dict(tasks=[dict(pool=POOLS[0], cursor=bar, floor=bar - 300, needed=True)]),
    )
    result = run(r, output, fetch=lambda r, p, **kw: capture(r, p))
    assert len(result["captures"]) == 1
    assert result["completed"][0]["status"] == "provider_no_older_rows"
    assert not result["pending"] and not (r / "STOP").exists()


def test_history_future_page_stops_globally(tmp_path):
    from memecoin_brain_quality.history import run

    r = root(tmp_path)
    output = r / "history"
    output.mkdir()
    bar = time.time_ns() // (300 * NS) * 300 - 300
    write(
        output / "binding.json",
        dict(tasks=[dict(pool=POOLS[0], cursor=bar - 300, floor=bar - 600, needed=True)]),
    )
    with pytest.raises(SourceFailure, match="historical_page_after_cursor"):
        run(r, output, fetch=lambda r, p, **kw: capture(r, p))
    assert read(r / "STOP")["reason"] == "historical_page_after_cursor"
    assert len(list((r / "requests").iterdir())) == 1
