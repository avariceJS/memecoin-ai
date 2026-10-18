"""Read-only source lineage. Fixtures are synthetic; snapshot must not mutate them."""

import json
import time
from pathlib import Path

from memecoin_ml.data import sha
from memecoin_operator import source_history
from memecoin_operator.acquisition import ensure_successor, stage_successor_bundle
from memecoin_operator.source_history import snapshot
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import publish
from tests.operator.test_acquisition import make_operator

NS = 10**9
DAY = 24 * 3600 * NS
SECRET = "super-secret-token"
CAPTURE_SECRET = "capture-body-secret"


def _config(source, **extra):
    body = {"expires_ns": 10**18, "max_requests": 25000, "paid_budget_usd": 0}
    body.update(extra)
    publish(source / "config.json", body)


def _source(path, *, seed=None, provider=None, config=None):
    path.mkdir()
    _config(path, **(config or {}))
    publish(path / "universe.json", {"pools": []})
    publish(path / "provider-seed.json", provider or {"attempts": 0, "failures": 0})
    publish(path / "seed.json", seed or {"quality": {}})
    return path


def _journal(source, specs):
    binding = {"config_sha": sha(source / "config.json"), "scope": "B08_source"}
    journal = source / "journal"
    journal.mkdir()
    publish(journal / "binding.json", binding)
    previous = digest(binding)
    head = previous
    for index, spec in enumerate(specs):
        row = {
            "sequence": index,
            "previous": previous,
            "kind": spec["kind"],
            "time_ns": spec["time_ns"],
            "payload": spec["payload"],
        }
        row["sha256"] = digest(row)
        previous = row["sha256"]
        head = previous
        publish(journal / f"event-{index:06d}.json", row)
    return head


def _outcome(stamp, attempts, failures, status, **extra):
    payload = {
        "status": status,
        "state": {"attempts": attempts, "failures": failures},
        "error": SECRET,
        "capture": extra.pop("capture", "/tmp/not-a-capture"),
    }
    payload.update(extra)
    return {"kind": "outcome", "time_ns": stamp, "payload": payload}


def _operator(tmp, source, now):
    root = tmp / "operator"
    root.mkdir(parents=True)
    init = now - 2 * DAY
    publish(
        root / "config.json",
        {
            "product": "operator",
            "mode": "operator",
            "provider": "geckoterminal",
            "profile": "balanced",
            "init_ns": init,
            "created_ns": init,
            "expires_ns": init + 6 * DAY,
            "paid_budget_usd": 0,
            "additional_requests": 30000,
            "max_background_requests": 6000,
            "source_root": str(source.resolve()),
        },
    )
    return root


def _pin(source, bundle):
    stage_successor_bundle(source, bundle)


def _files(root):
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        found[str(path)] = (path.stat().st_mtime_ns, sha(path))
    return found


def _text(report):
    return json.dumps(report, sort_keys=True)


def test_snapshot_module_does_not_take_a_ledger_lock():
    text = Path(source_history.__file__).read_text(encoding="utf-8")
    assert "Ledger(" not in text
    assert ".lock" not in text


def test_cutoff_keeps_journal_failures_and_does_not_mutate(tmp_path):
    now = time.time_ns()
    source = tmp_path / "source"
    _source(source, provider={"attempts": 4, "failures": 9})
    first = 1_000 * NS
    second = first + 1_000 * NS
    third = second + 10 * NS
    _journal(
        source,
        [
            _outcome(first, 1, 0, "valid"),
            _outcome(second, 2, 1, "transport"),
            _outcome(third, 3, 2, "transport"),
        ],
    )
    _pin(source, tmp_path / "runtime")
    publish(source / "STOP", {"reason": "provider_auth", "at_ns": first})
    publish(
        source / "health.json",
        {"at_ns": first, "failures": 9, "attempts": 99, "secret": "health-secret"},
    )
    publish(
        source / f"incident-{first}.json",
        {"category": "source_stop_or_deadline", "exit_code": -9, "body": SECRET},
    )
    root = _operator(tmp_path, source, now)
    stop_sha = sha(source / "STOP")
    before = _files(tmp_path)
    report = snapshot(root, second)
    assert _files(tmp_path) == before
    assert not (source / "journal" / ".lock").exists()
    assert report["ok"] is True
    assert report["writes"] is False
    assert report["network"] is False
    assert report["successor"]["phase"] == "borrowed"
    assert report["successor"]["active"] is False
    assert report["original_stop"]["same_as_current"] is True
    assert report["original_stop"]["distinct"] is False
    assert report["current"]["stop"]["sha256"] == stop_sha
    assert report["current"]["stop"]["applies_at_cutoff"] is True
    assert report["current"]["stop"]["reason"] == "provider_auth"
    assert report["current"]["incidents"]["items"] == [
        {
            "id": str(first),
            "at_ns": first,
            "sha256": sha(source / f"incident-{first}.json"),
            "category": "source_stop_or_deadline",
            "exit_code": -9,
        }
    ]
    journal = report["current"]["journal"]
    assert journal["chain_ok"] is True
    assert journal["outcome_counts"] == {"valid": 1, "transport": 1}
    assert journal["transport_outcomes"] == 1
    assert journal["attempts"] == 2
    assert journal["consecutive_failures"] == 1
    assert report["current"]["health_consecutive_failures"] == 9
    assert report["budget"]["current_journal_attempts"] == 2
    assert report["budget"]["current_consecutive_failures"] == 1
    assert report["budget"]["current_transport_outcomes"] == 1
    assert report["budget"]["summed"] is False
    gaps = report["current"]["gaps"]
    assert gaps["continuous_uptime"] is False
    assert gaps["uptime_known"] is False
    assert gaps["evidence"] == "journal_event_times"
    assert gaps["silent"] == [{"after_ns": first, "before_ns": second, "silent_ns": second - first}]
    rendered = _text(report)
    assert SECRET not in rendered
    assert "health-secret" not in rendered
    assert "99" not in json.dumps(report["budget"])


def test_broken_chain_and_pin_are_not_trusted(tmp_path):
    now = time.time_ns()
    source = tmp_path / "source"
    _source(source)
    stamp = 50 * NS
    _journal(
        source,
        [
            _outcome(stamp, 1, 0, "valid"),
            _outcome(stamp + NS, 2, 1, "transport"),
        ],
    )
    _pin(source, tmp_path / "runtime")
    event = source / "journal" / "event-000001.json"
    broken = json.loads(event.read_text())
    broken["time_ns"] = 0
    event.write_text(json.dumps(broken))
    root = _operator(tmp_path, source, now)
    before = _files(tmp_path)
    report = snapshot(root, stamp + NS)
    assert _files(tmp_path) == before
    assert report["ok"] is False
    assert report["reason"] == "journal_chain"
    assert report["current"]["journal"]["partial"] is True
    assert report["current"]["journal"]["outcome_counts"] == {"valid": 1}
    assert report["budget"]["current_transport_outcomes"] == 0
    assert SECRET not in _text(report)

    pinned = tmp_path / "pinned"
    _source(pinned, provider={"attempts": 7})
    _journal(pinned, [_outcome(stamp, 7, 3, "transport")])
    _pin(pinned, tmp_path / "pinned-runtime")
    config = pinned / "config.json"
    config.write_text(config.read_text() + "\n")
    other = _operator(tmp_path / "other", pinned, now)
    refused = snapshot(other, stamp)
    assert refused["ok"] is False
    assert refused["current"]["binding_ok"] is False
    assert refused["current"]["journal"]["unknown"] is True
    assert refused["current"]["journal"]["outcome_counts"] == {}
    assert refused["budget"]["current_journal_attempts"] is None
    assert refused["budget"]["current_seed_attempts"] is None


def test_unauthenticated_parent_and_protected_roots_are_not_opened(tmp_path):
    now = time.time_ns()
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "SECRET_MARKER").write_text(CAPTURE_SECRET)
    source = tmp_path / "source"
    _source(source, seed={"quality": {}, "parent": str((tmp_path / "real").resolve())})
    publish(
        source / "migration.json",
        {
            "kind": "source_audit_deadline_repair",
            "lineage": "technical_repair_genesis",
            "old_chain_rewritten": False,
            "old_attempts": 1,
            "new_attempts": 1,
            "parent_root": str(evil.resolve()),
            "old_ledger_head": "ab" * 32,
        },
    )
    config = json.loads((source / "config.json").read_text())
    config["migration_sha"] = sha(source / "migration.json")
    source.joinpath("config.json").unlink()
    publish(source / "config.json", config)
    _journal(source, [_outcome(10 * NS, 1, 0, "valid", capture=str(evil / "SECRET_MARKER"))])
    _pin(source, tmp_path / "runtime")
    root = _operator(tmp_path, source, now)
    before = sha(evil / "SECRET_MARKER")
    report = snapshot(root, 10 * NS)
    assert sha(evil / "SECRET_MARKER") == before
    assert report["repair_predecessor"] is None
    assert "migration_unauthenticated" in report["notices"]
    rendered = _text(report)
    assert "SECRET_MARKER" not in rendered
    assert CAPTURE_SECRET not in rendered

    hidden = tmp_path / "holdout-source"
    hidden.mkdir()
    (hidden / "SECRET_MARKER").write_text(CAPTURE_SECRET)
    protected = _operator(tmp_path / "protected-operator", hidden, now)
    refused = snapshot(protected, 10 * NS)
    assert refused["ok"] is False
    assert refused["reason"] == "protected_source"
    assert CAPTURE_SECRET not in _text(refused)
    assert snapshot(tmp_path / "holdout", 10 * NS)["reason"] == "protected_root"
    link = tmp_path / "link"
    link.symlink_to(root)
    assert snapshot(link, 10 * NS)["reason"] == "symlink_root"
    assert snapshot(root, -1)["reason"] == "bad_cutoff"
    limited = snapshot(root, 10 * NS, max_files=1)
    assert limited["ok"] is False
    assert limited["reason"] == "truncated"
    assert limited["limits"]["truncated"] is True
    assert limited["limits"]["unknown"] is True


def test_repair_carry_is_not_added_twice(tmp_path):
    now = time.time_ns()
    parent = tmp_path / "parent"
    _source(parent, provider={"attempts": 1417, "failures": 0})
    head = _journal(parent, [_outcome(20 * NS, 1417, 0, "valid")])
    _pin(parent, tmp_path / "parent-runtime")
    child = tmp_path / "child"
    parent_path = str(parent.resolve())
    _source(
        child,
        provider={"attempts": 1417, "failures": 0},
        seed={
            "quality": {},
            "lineage": "technical_repair_genesis",
            "parent": parent_path,
            "inherited_journal_head": head,
        },
    )
    publish(
        child / "migration.json",
        {
            "kind": "source_audit_deadline_repair",
            "lineage": "technical_repair_genesis",
            "old_chain_rewritten": False,
            "old_attempts": 1417,
            "new_attempts": 1417,
            "parent_root": parent_path,
            "old_ledger_head": head,
            "old_stop_sha": "cd" * 32,
        },
    )
    config = json.loads((child / "config.json").read_text())
    config["migration_sha"] = sha(child / "migration.json")
    child.joinpath("config.json").unlink()
    publish(child / "config.json", config)
    _journal(
        child,
        [
            {
                "kind": "repair_genesis",
                "time_ns": 21 * NS,
                "payload": {
                    "state": {"attempts": 1417, "failures": 0},
                    "lineage": "technical_repair_genesis",
                    "parent_root": parent_path,
                    "note": SECRET,
                },
            }
        ],
    )
    _pin(child, tmp_path / "child-runtime")
    root = _operator(tmp_path, child, now)
    before = _files(tmp_path)
    report = snapshot(root, 21 * NS)
    assert _files(tmp_path) == before
    assert report["ok"] is True
    assert report["configured_original"] == {"same_as_current": True}
    assert report["repair_predecessor"]["role"] == "repair_predecessor"
    assert report["repair_predecessor"]["binding_ok"] is True
    assert report["repair_predecessor"]["journal"]["attempts"] == 1417
    budget = report["budget"]
    assert budget["current_journal_attempts"] == 1417
    assert budget["current_seed_attempts"] == 1417
    assert budget["carried_attempts"] == 1417
    assert budget["repair_journal_attempts"] == 1417
    assert budget["original_journal_attempts"] is None
    assert budget["summed"] is False
    assert 2834 not in budget.values()
    assert SECRET not in _text(report)
    assert report["predecessor_hops"] == 1


def test_successor_status_stays_distinct_from_expired_stop(tmp_path):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    stage_successor_bundle(old, tmp_path / "old-runtime")
    name = f"incident-{now}.json"
    publish(
        old / name,
        {
            "exit_code": -9,
            "category": "source_stop_or_deadline",
            "elapsed_seconds": 1,
            "body": SECRET,
        },
    )
    publish(
        old / "STOP",
        {"reason": "source_worker_failure", "incident": name, "at_ns": now},
    )
    stop_sha = sha(old / "STOP")
    ensure_successor(root, now_ns=now)
    before = _files(root)
    old_files = _files(old)
    report = snapshot(root, time.time_ns())
    assert _files(root) == before
    assert _files(old) == old_files
    assert report["ok"] is True
    assert report["successor"]["active"] is True
    assert report["successor"]["phase"] == "successor"
    assert report["successor"]["stopped"] is False
    assert report["successor"]["stop_sha256"] is None
    assert report["original_stop"]["distinct"] is True
    assert report["original_stop"]["stopped"] is True
    assert report["original_stop"]["stop_sha256"] == stop_sha
    assert report["original_stop"]["stop_reason"] == "source_worker_failure"
    assert report["configured_original"]["stop"]["sha256"] == stop_sha
    assert report["configured_original"]["incidents"]["items"][0]["category"] == (
        "source_stop_or_deadline"
    )
    budget = report["budget"]
    assert budget["current_seed_attempts"] == 0
    assert budget["current_journal_attempts"] is None
    assert budget["prior_provider_attempts"] == 900
    assert budget["original_journal_attempts"] == 900
    assert budget["max_requests"] == 30000
    assert budget["carried_attempts"] is None
    assert budget["summed"] is False
    assert report["current"]["journal"]["present"] is False
    assert SECRET not in _text(report)
    assert report["repair_predecessor"] is None
