"""Prepare a separate finite research envelope after owner retirement qualification."""

import argparse
import json
import shutil
import time
from pathlib import Path

from brain_development_collect import consumer_guard, report_campaigns, shared_guard

from memecoin_curriculum.research_guard import research_lease
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish

NS = 10**9


def source_debit_from_reservations(source, journal_debit):
    seed_path = source / "provider-seed.json"
    inherited = read(seed_path)["attempts"] if seed_path.exists() else 0
    names = sorted(p.name for p in (source / "requests").iterdir())
    end = inherited + len(names)
    if names != [f"{i:06d}" for i in range(inherited, end)] or not 0 <= end - journal_debit <= 1:
        raise ValueError("research_source_request_sequence")
    return end, len(names)


def remaining_jobs(previous):
    """Validate the old sealed prefix; neither retry nor alter its original expiry."""
    if len(previous) != 2 or any(p.parent != previous[0].parent for p in previous):
        raise ValueError("research_prior_scope")
    plans, refs, count = [], [], 0
    for root in previous:
        verify(root)
        verify(root / "frozen")
        p = read(root / "frozen/plan.json")
        attempts = sorted((root / "requests").iterdir())
        results = sorted((root / "results").glob("*.json"))
        reservations = sorted((root / "processes").glob("*-reservation.json"))
        n = len(attempts)
        if (
            not 0 < n == len(results) == len(reservations) == read(root / "result.json")["requests"]
            or any((r / "failure.json").exists() for r in attempts)
            or any(read(r / "receipt.json")["http_status"] != 200 for r in attempts)
        ):
            raise ValueError("research_prior_incomplete_or_failed")
        if plans and (
            p["continuation"]["sha"] != refs[-1]["sha"]
            or p["jobs"] != plans[0]["jobs"][count:]
            or p["cases"] != plans[0]["cases"]
            or p["expires_ns"] != plans[0]["expires_ns"]
        ):
            raise ValueError("research_prior_chain")
        plans.append(p)
        refs.append(dict(path=str(root), sha=sha(root / "manifest.json"), requests=n))
        count += n
    if (
        read(previous[0] / "STOP")["category"] != "shared_source_cooldown_or_failure"
        or not read(previous[1] / "STOP")["category"].startswith("unexpected_project_consumer")
        or not 0 < count < len(plans[0]["jobs"]) == 40
    ):
        raise ValueError("research_reassessment_scope")
    return plans[0], refs, count


def prepare(root, previous, qualification):
    with research_lease(root.parent / ".research-acquisition.lock"):
        return prepare_locked(root, previous, qualification)


def reject_reserved_roster(parent, roster_sha):
    # A prepared, interrupted or completed envelope consumes this suffix. No silent
    # retry through a different directory after releasing the process lock.
    for p in parent.glob("*/frozen/plan.json"):
        plan = read(p)
        if plan.get("original_roster_sha") == roster_sha:
            raise ValueError("research_roster_already_reserved")


def prepare_locked(root, previous, qualification):
    verify(qualification)
    q = read(qualification / "qualification.json")
    now = time.time_ns()
    if not q["ready"] or not 0 <= now - q["finished_ns"] <= 1800 * NS:
        raise ValueError("research_fresh_qualification_required")
    old, refs, count = remaining_jobs(previous)
    reject_reserved_roster(root.parent, sha(previous[0] / "frozen/plan.json"))
    if root.parent != previous[0].parent or now <= old["expires_ns"]:
        raise ValueError("research_new_envelope_scope")
    owner = dict(q["retired_owner"])
    source = Path(old["source"])
    owner["source_journal"] = str(source / "journal")
    owner["source_last_event"] = str(max((source / "journal").glob("event-*.json")))
    if owner["source_last_event"] not in owner["pins"]:
        raise ValueError("research_source_progressed_since_qualification")
    source_config = read(source / "config.json")
    # A deadline-killed request can have a reservation but no terminal journal event.
    source_debit, source_count = source_debit_from_reservations(source, q["source_debit"])
    if (source / "provider-seed.json").exists():
        owner["pins"][str(source / "provider-seed.json")] = sha(source / "provider-seed.json")
    total_reserved = (
        source_debit
        + source_config.get("predecessor_total_get", 0)
        + old["prior_research_requests"]
        + 40
    )
    if total_reserved >= source_config["max_requests"]:
        raise ValueError("research_shared_budget")
    plan = dict(
        old,
        guard_kind="retired_owner_research_v1",
        retired_owner=owner,
        consumer_rules=q["consumer_rules"],
        research_lease=str(root.parent / ".research-acquisition.lock"),
        created_ns=now,
        expires_ns=now + 30 * 60 * NS,
        jobs=old["jobs"][count:],
        prior_envelopes=refs,
        original_expiry_ns=old["expires_ns"],
        original_roster_sha=sha(previous[0] / "frozen/plan.json"),
        qualification_sha=sha(qualification / "manifest.json"),
        decision="new_research_envelope_after_expiry_not_resume",
        source_debit=source_debit,
        source_request_directories=source_count,
        predecessor_debit=source_config.get("predecessor_total_get", 0),
        shared_total_reserved=total_reserved,
    )
    shared_guard(plan)
    consumers = consumer_guard(plan)
    root.mkdir(parents=True, exist_ok=False)
    for folder in ("requests", "results", "processes", "frozen/code"):
        (root / folder).mkdir(parents=True)
    # Reproduce every old capture before any new GET. This touches only the research roots.
    report_campaigns(previous, root / "prior-verification")
    shutil.copyfile(previous[0] / "frozen/inventory.json", root / "frozen/inventory.json")
    shutil.copyfile(
        Path(__file__).with_name("brain_development_collect.py"), root / "frozen/code/worker.py"
    )
    shutil.copyfile(__file__, root / "frozen/code/prepare.py")
    import memecoin_brain.source as module

    runtime = Path(module.__file__).parent.parent
    for p in runtime.glob("memecoin_*/*.py"):
        target = root / "frozen/code/runtime" / p.relative_to(runtime)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
    config = dict(read(previous[0] / "config.json"), created_ns=now, expires_ns=plan["expires_ns"])
    debit = dict(requests=count)
    for prefix in (root, root / "frozen"):
        publish(prefix / "config.json", config)
        publish(prefix / "external-request-debit.json", debit)
    publish(root / "frozen/plan.json", plan)
    publish(root / "frozen/consumers.json", consumers)
    seal(root / "frozen")
    publish(
        root / "reservation.json",
        dict(
            frozen_sha=sha(root / "frozen/manifest.json"),
            requests=len(plan["jobs"]),
            prior_requests=count,
            created_ns=now,
        ),
    )
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--previous", type=Path, nargs=2, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    a = parser.parse_args()
    plan = prepare(a.root.resolve(), [p.resolve() for p in a.previous], a.qualification.resolve())
    print(json.dumps(dict(remaining=len(plan["jobs"]), expires_ns=plan["expires_ns"])))


if __name__ == "__main__":
    main()
