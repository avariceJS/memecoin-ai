"""Offline tests for research-only retirement and suffix admission."""

import importlib.util
import sys
from pathlib import Path

import pytest

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

TOOLS = Path(__file__).resolve().parents[2] / "tools"
spec = importlib.util.spec_from_file_location(
    "brain_development_collect", TOOLS / "brain_development_collect.py"
)
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)
sys.modules[spec.name] = development
spec = importlib.util.spec_from_file_location(
    "research_collect", TOOLS / "brain_research_collect.py"
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.mark.parametrize("seed", [0, 4449])
@pytest.mark.parametrize("partial", [0, 1])
def test_inherited_and_unjournaled_reservations_are_charged(tmp_path, seed, partial):
    publish(tmp_path / "provider-seed.json", dict(attempts=seed))
    (tmp_path / "requests").mkdir()
    for i in range(seed, seed + 3):
        (tmp_path / "requests" / f"{i:06d}").mkdir()
    assert tool.source_debit_from_reservations(tmp_path, seed + 3 - partial) == (seed + 3, 3)
    (tmp_path / "requests" / f"{seed + 1:06d}").rmdir()
    with pytest.raises(ValueError, match="request_sequence"):
        tool.source_debit_from_reservations(tmp_path, seed + 3)


def test_retired_guard_is_explicit_and_legacy_stop_still_blocks(tmp_path, monkeypatch):
    from memecoin_curriculum import research_guard

    monkeypatch.setattr(research_guard, "owner_guard", lambda p: "research-only")
    assert development.shared_guard(dict(guard_kind="retired_owner_research_v1")) == "research-only"
    publish(tmp_path / "STOP", {})
    with pytest.raises(ValueError, match="shared_source_changed_or_stopped"):
        development.shared_guard(dict(source=str(tmp_path)))


def test_retired_run_requires_global_lease_before_execution(tmp_path, monkeypatch):
    from memecoin_curriculum.research_guard import research_lease

    (tmp_path / "frozen").mkdir()
    lock = tmp_path / "research.lock"
    publish(
        tmp_path / "frozen/plan.json",
        dict(guard_kind="retired_owner_research_v1", research_lease=str(lock)),
    )
    monkeypatch.setattr(development, "run_locked", lambda r: pytest.fail("overlap executed"))
    with research_lease(lock), pytest.raises(BlockingIOError):
        development.run(tmp_path)


@pytest.mark.parametrize("change", [None, "cases", "expiry", "failure", "incomplete"])
def test_suffix_admission_never_retries_or_changes_membership(tmp_path, change):
    cases = [dict(pool=str(i), base_mint=str(i)) for i in range(20)]
    jobs = [dict(birth=b, days=d) for b in cases for d in (3, 10)]
    roots = [tmp_path / "one", tmp_path / "two"]
    for i, root in enumerate(roots):
        for directory in ("frozen", "requests/000000", "results", "processes"):
            (root / directory).mkdir(parents=True)
        plan = dict(cases=cases, jobs=jobs[i:], expires_ns=12345)
        if i:
            plan["continuation"] = dict(sha=sha(roots[0] / "manifest.json"))
            if change == "cases":
                plan["cases"] = []
            if change == "expiry":
                plan["expires_ns"] += 1
            if change == "failure":
                publish(root / "requests/000000/failure.json", dict(http_status=429))
            if change == "incomplete":
                publish(root / "processes/000001-reservation.json", {})
        publish(root / "frozen/plan.json", plan)
        publish(root / "requests/000000/receipt.json", dict(http_status=200))
        publish(root / "results/000000.json", {})
        publish(root / "processes/000000-reservation.json", {})
        publish(root / "result.json", dict(requests=1))
        publish(
            root / "STOP",
            dict(
                category="unexpected_project_consumer" if i else "shared_source_cooldown_or_failure"
            ),
        )
        seal(root / "requests/000000")
        seal(root / "frozen")
        seal(root)
    if change:
        with pytest.raises(ValueError, match="research_"):
            tool.remaining_jobs(roots)
    else:
        old, refs, count = tool.remaining_jobs(roots)
        assert old["jobs"][count:] == jobs[2:]
        assert count == sum(r["requests"] for r in refs) == 2
        assert old["expires_ns"] == read(roots[1] / "frozen/plan.json")["expires_ns"]


def test_other_directory_cannot_replay_prepared_roster(tmp_path):
    (tmp_path / "prepared/frozen").mkdir(parents=True)
    publish(tmp_path / "prepared/frozen/plan.json", dict(original_roster_sha="fixture"))
    with pytest.raises(ValueError, match="research_roster_already_reserved"):
        tool.reject_reserved_roster(tmp_path, "fixture")
    tool.reject_reserved_roster(tmp_path, "different")


def test_preparation_also_requires_shared_lease(tmp_path):
    from memecoin_curriculum.research_guard import research_lease

    with research_lease(tmp_path / ".research-acquisition.lock"), pytest.raises(BlockingIOError):
        tool.prepare(tmp_path / "other", [], tmp_path / "unused")
