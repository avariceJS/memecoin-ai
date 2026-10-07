"""Bounded, development-only cold-mint collection. Never touches production state."""

import argparse
import datetime
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from memecoin_brain.data import build
from memecoin_brain.source import BASE_EXCLUDED, EXCLUDED, SourceFailure, bars, ohlcv
from memecoin_curriculum.admission import disjoint
from memecoin_ml.data import read, sha
from memecoin_operational.contract import GateError
from memecoin_operational.supervisor import Watch, child
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish

NS = 10**9
SALT = "brain-development-v1-20261007"


def select(inventory):
    """Conservatively exclude whole prior planned cohorts, not only admitted rows."""
    blocked_pools, blocked_mints = set(EXCLUDED), set(BASE_EXCLUDED)
    for entry in inventory["metadata"].values():
        value = entry["value"]
        for b in value.get("cases", []) + value.get("pools", []):
            blocked_pools.add(b["pool"])
            blocked_mints.add(b["base_mint"])
        # Public D40 account metadata, not protected prices/labels. Excluding every
        # account also covers both mint sides without decoding a protected capture.
        for b in value.get("cohort", []):
            blocked_pools.add(b["pool_id"])
            blocked_mints.update(b["params"][0])
    for b in inventory["births"]:
        if b["pool"] in blocked_pools:
            blocked_mints.add(b["base_mint"])
    for membership in inventory.get("dataset_membership", {}).values():
        if not set(membership["pools"]) <= blocked_pools:
            raise ValueError("unmapped_model_dataset_pool")
    canonical = {}
    for b in inventory["births"]:
        if b["pool"] in blocked_pools or b["base_mint"] in blocked_mints:
            continue
        key = b["base_mint"]
        if key not in canonical or (b["event_s"], b["pool"]) < (
            canonical[key]["event_s"],
            canonical[key]["pool"],
        ):
            canonical[key] = b
    months = {f"2026-{m:02d}": [] for m in range(5, 10)}
    for b in canonical.values():
        month = datetime.datetime.fromtimestamp(b["event_s"], datetime.UTC).strftime("%Y-%m")
        if month in months:
            months[month].append(dict(b, month=month))
    for values in months.values():
        values.sort(key=lambda b: digest([SALT, b["base_mint"], b["pool"]]))
    cases = [v[i] for i in range(4) for v in months.values() if len(v) > i]
    return dict(
        cases=cases,
        eligible_metadata_per_month={k: len(v) for k, v in months.items()},
        blocked_pools=sorted(blocked_pools),
        blocked_mints=sorted(blocked_mints),
        age_semantics="pool_birth_proxy_not_verified_mint_genesis",
    )


def qualify_window(capture, birth, days):
    if days not in (3, 10) or birth["pool"] in EXCLUDED:
        raise ValueError("development_scope")
    verify(capture)
    request, receipt = read(capture / "request.json"), read(capture / "receipt.json")
    url = urlparse(request["url"])
    query = parse_qs(url.query)
    expected = dict(aggregate="5", currency="usd", token="base", include_empty_intervals="false")
    upper = birth["event_s"] + days * 86400
    if (
        url.scheme != "https"
        or url.netloc != "api.geckoterminal.com"
        or url.path != f"/api/v2/networks/solana/pools/{birth['pool']}/ohlcv/minute"
        or any(query.get(k) != [v] for k, v in expected.items())
        or query.get("before_timestamp") != [str(upper)]
        or query.get("limit") != ["1000"]
        or request["lane"] != "historical"
    ):
        raise ValueError("development_namespace")
    if receipt["response_sha256"] != sha(capture / "response.bin") or not (
        request["started_ns"] <= receipt["received_ns"] <= receipt["available_ns"]
    ):
        raise ValueError("development_receipt")
    metadata = read(capture / "response.bin").get("meta", {})
    if any(metadata.get(s, {}).get("address") != birth[s + "_mint"] for s in ("base", "quote")):
        raise ValueError("development_mint_mismatch")
    rows = bars(capture, birth["pool"])
    if any(not birth["event_s"] // 300 * 300 <= r["open_s"] < upper for r in rows):
        raise ValueError("development_birth_range")
    bundle = build(rows, dict(as_of_ns=receipt["available_ns"]))
    opens = {r["open_s"] for r in rows}
    lower = birth["event_s"] + (7 * 86400 if days == 10 else 0)
    eligible = [
        s
        for s in bundle["samples"]
        if lower <= s["event_close_s"] < upper
        and s["maturity_s"] <= upper
        and all(s["event_close_s"] - k * 300 in opens for k in range(1, 16))
        and all(t["log_return"] is not None for t in s["targets"])
        and all(
            v is not None and math.isfinite(v)
            for k in s["history"]
            for v in bundle["frames"][k]["values"]
        )
    ]
    cohorts = {}
    for label, start, end in (("lt1d", 0, 1), ("1to3d", 1, 3), ("7to10d", 7, 10)):
        subset = [
            s
            for s in eligible
            if birth["event_s"] + start * 86400
            <= s["event_close_s"]
            < birth["event_s"] + end * 86400
        ]
        cohorts[label] = dict(
            origins=len(subset),
            disjoint=disjoint(subset),
            utc_days=sorted({s["event_close_s"] // 86400 for s in subset}),
        )
    return rows, dict(
        pool=birth["pool"],
        base_mint=birth["base_mint"],
        month=birth["month"],
        days=days,
        bars=len(rows),
        eligible=len(eligible),
        disjoint=disjoint(eligible),
        cohorts=cohorts,
        capture_sha=sha(capture / "manifest.json"),
        status="valid" if eligible else "no_support",
        role="development_historical_transfer",
        training_allowed=False,
        historical_online_availability_known=False,
    )


def consumer_guard(plan):
    """Reject an unexpected project process capable of making provider requests."""
    lines = subprocess.check_output(["ps", "-axo", "pid,command"], text=True).splitlines()
    observed = []
    for line in lines:
        if "memecoin" not in line or not any(s in line for s in ("Python", "python")):
            continue
        pid, command = line.strip().split(maxsplit=1)
        if int(pid) == os.getpid() or command.startswith(("ssh ", "scp ", "/bin/zsh ")):
            continue
        if (
            command.endswith(" ui")
            or "memecoin_operator.runtime" in command
            and "--port 8792" in command
            or plan["source"] in command
            and "memecoin_continuous.runtime" in command
            or "frozen/code/worker.py" in command
            or "memecoin-development-collect.py" in command
        ):
            observed.append(command)
        else:
            # Do not copy possible credentials from an unknown process's argv.
            raise GateError(f"unexpected_project_consumer:pid={pid}:sha={digest(command)}")
    return observed


def shared_guard(plan):
    source = Path(plan["source"])
    if (source / "STOP").exists() or sha(source / "config.json") != plan["source_config_sha"]:
        raise GateError("shared_source_changed_or_stopped")
    config = read(source / "config.json")
    if time.time_ns() >= config["expires_ns"]:
        raise GateError("shared_source_expired")
    event_path = max((source / "journal").glob("event-*.json"))
    event = read(event_path)
    state = event["payload"]["state"]
    if state["cooldown_ns"] > time.time_ns() or state["failures"]:
        raise GateError("shared_source_cooldown_or_failure")
    if (
        state["attempts"] + plan["reserved_requests"] + plan.get("prior_research_requests", 0)
        >= config["max_requests"]
    ):
        raise GateError("shared_budget_headroom")
    if not 0 <= time.time_ns() - state["last_ns"] <= 180 * NS:
        raise GateError("shared_source_stale")
    return event["kind"] == "outcome" and event["payload"].get("status") == "valid"


def prepare(root, inventory_path, source):
    inv = read(inventory_path)
    selection = select(inv)
    if len(inv.get("dataset_membership", {})) != 3:
        raise ValueError("model_membership_inventory_incomplete")
    prior_debit = sum(v["requests"] for v in inv["previous_campaign_debits"].values())
    depth_limit_receipt = None
    for name, debit in inv["previous_campaign_debits"].items():
        f = debit["last_failure"] or {}
        depth_limited = False
        if name == "admission-probe01" and f.get("http_status") == 401:
            capture = source.parents[2] / (
                "work/brain-expansion-20261006/admission-probe01/requests/000000"
            )
            verify(capture)
            message = read(capture / "error-response.bin")["errors"][0]["title"]
            depth_limited = message.startswith(
                "You can only access data from the past 180 days with Public API."
            ) and all(b["event_s"] > time.time() - 179 * 86400 for b in selection["cases"])
            depth_limit_receipt = dict(path=str(capture), sha=sha(capture / "manifest.json"))
        if (f.get("http_status") in (401, 403) and not depth_limited) or (
            f.get("http_status") == 429
            and f["received_ns"] + max(900, f.get("retry_after_seconds", 0)) * NS > time.time_ns()
        ):
            raise ValueError("previous_campaign_provider_block")
    if len(selection["cases"]) != 20:
        raise ValueError("metadata_cohort_shortfall")
    now = time.time_ns()
    expiry = min(now + 45 * 60 * NS, inv["source_config"]["expires_ns"] - 60 * NS)
    if expiry - now < 25 * 60 * NS:
        raise ValueError("insufficient_source_lease")
    root.mkdir(parents=True, exist_ok=False)
    for folder in ("requests", "results", "processes", "frozen/code"):
        (root / folder).mkdir(parents=True)
    shutil.copyfile(inventory_path, root / "frozen/inventory.json")
    shutil.copyfile(__file__, root / "frozen/code/worker.py")
    import memecoin_brain.source as module

    for p in Path(module.__file__).parent.parent.glob("memecoin_*/*.py"):
        dest = root / "frozen/code/runtime" / p.relative_to(Path(module.__file__).parent.parent)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    config = dict(
        created_ns=now,
        expires_ns=expiry,
        max_requests=40,
        min_request_interval_seconds=30,
        timeout_seconds=30,
        max_response_bytes=2 * 1024**2,
        free_floor_bytes=4 * 1024**3,
        storage_limit_bytes=192 * 1024**2,
        paid_budget_usd=0,
    )
    plan = dict(
        selection,
        source=str(source),
        source_config_sha=inv["source_config_sha"],
        reserved_requests=40,
        created_ns=now,
        expires_ns=expiry,
        catalogue_sha=inv["catalogue_sha"],
        prior_research_requests=prior_debit,
        prior_depth_limit_receipt=depth_limit_receipt,
        seed=None,
        salt=SALT,
        data_role="development_historical_transfer",
        training_allowed=False,
        model_inference_allowed=False,
        automatic_retry=False,
        jobs=[dict(birth=b, days=d) for b in selection["cases"] for d in (3, 10)],
    )
    shared_guard(plan)
    publish(root / "frozen/consumers.json", consumer_guard(plan))
    publish(root / "config.json", config)
    publish(root / "frozen/config.json", config)
    publish(root / "frozen/plan.json", plan)
    seal(root / "frozen")
    publish(
        root / "reservation.json",
        dict(frozen_sha=sha(root / "frozen/manifest.json"), requests=40, created_ns=now),
    )
    return plan


def continuation_plan(previous):
    """One reviewed continuation of an entirely completed prefix; never retry a GET."""
    verify(previous)
    old = read(previous / "frozen/plan.json")
    result = read(previous / "result.json")
    if old.get("continuation") or read(previous / "STOP")["category"] != (
        "shared_source_cooldown_or_failure"
    ):
        raise ValueError("continuation_scope")
    attempts = sorted((previous / "requests").iterdir())
    completed = sorted((previous / "results").glob("*.json"))
    reserved = sorted((previous / "processes").glob("*-reservation.json"))
    n = len(attempts)
    if not 0 < n == len(completed) == len(reserved) == result["requests"] < len(old["jobs"]):
        raise ValueError("continuation_incomplete_prefix")
    for index, capture in enumerate(attempts):
        verify(capture)
        if (capture / "failure.json").exists() or read(capture / "receipt.json")[
            "http_status"
        ] != 200:
            raise ValueError("continuation_failed_request")
        job = old["jobs"][index]
        _, expected = qualify_window(capture, job["birth"], job["days"])
        if expected != read(completed[index]):
            raise ValueError("continuation_prefix_mismatch")
    return dict(
        old,
        jobs=old["jobs"][n:],
        continuation=dict(
            path=str(previous),
            sha=sha(previous / "manifest.json"),
            charged_requests=n,
            manual_review="source_timeout_recovered_no_research_retry",
        ),
    )


def continue_prefix(root, previous):
    plan = continuation_plan(previous)
    if not shared_guard(plan):
        raise ValueError("continuation_source_not_recovered")
    if plan["expires_ns"] - time.time_ns() < (len(plan["jobs"]) * 30 + 180) * NS:
        raise ValueError("continuation_original_lease_insufficient")
    root.mkdir(parents=True, exist_ok=False)
    for folder in ("requests", "results", "processes", "frozen/code"):
        (root / folder).mkdir(parents=True)
    shutil.copyfile(previous / "frozen/inventory.json", root / "frozen/inventory.json")
    shutil.copytree(previous / "frozen/code/runtime", root / "frozen/code/runtime")
    shutil.copyfile(__file__, root / "frozen/code/worker.py")
    config = read(previous / "config.json")
    debit = dict(requests=plan["continuation"]["charged_requests"])
    publish(root / "external-request-debit.json", debit)
    publish(root / "frozen/external-request-debit.json", debit)
    publish(root / "frozen/consumers.json", consumer_guard(plan))
    publish(root / "config.json", config)
    publish(root / "frozen/config.json", config)
    publish(root / "frozen/plan.json", plan)
    seal(root / "frozen")
    publish(
        root / "reservation.json",
        dict(
            frozen_sha=sha(root / "frozen/manifest.json"),
            requests=len(plan["jobs"]),
            prior_requests=debit["requests"],
            created_ns=time.time_ns(),
        ),
    )
    return plan


def worker(root, index):
    from memecoin_operational.worker import lease_watchdog

    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    plan = read(root / "frozen/plan.json")
    if not shared_guard(plan):
        raise GateError("source_not_valid_before_get")
    job = plan["jobs"][index]
    try:
        capture = ohlcv(
            root,
            job["birth"]["pool"],
            lane="historical",
            before=job["birth"]["event_s"] + job["days"] * 86400,
            limit=1000,
        )
    except SourceFailure:
        attempts = sorted((root / "requests").iterdir())
        if not attempts or not (attempts[-1] / "failure.json").exists():
            raise
        failure = read(attempts[-1] / "failure.json")
        if failure.get("http_status") != 404:
            raise
        result = dict(pool=job["birth"]["pool"], days=job["days"], status="not_found", eligible=0)
    else:
        _, result = qualify_window(capture, job["birth"], job["days"])
    publish(root / "results" / f"{index:06d}.json", result)


def run(root):
    verify(root / "frozen")
    if sha(root / "frozen/manifest.json") != read(root / "reservation.json")["frozen_sha"]:
        raise ValueError("frozen_pin")
    if read(root / "config.json") != read(root / "frozen/config.json"):
        raise ValueError("config_pin")
    if (root / "frozen/external-request-debit.json").exists() and (
        read(root / "external-request-debit.json")
        != read(root / "frozen/external-request-debit.json")
    ):
        raise ValueError("debit_pin")
    if list((root / "requests").iterdir()) or list((root / "processes").iterdir()):
        raise ValueError("resume_forbidden")
    plan = read(root / "frozen/plan.json")
    publish(root / "execution-reservation.json", dict(started_ns=time.time_ns()))
    watch = Watch((plan["expires_ns"] - time.time_ns()) / NS)

    def guard():
        if (
            (root / "STOP").exists()
            or watch.remaining() <= 0
            or time.time_ns() >= plan["expires_ns"]
        ):
            raise GateError("campaign_stop_or_deadline")
        shared_guard(plan)

    error = None
    try:
        for index, _ in enumerate(plan["jobs"]):
            # Sleep outside fetch so the fresh guard runs after pacing, not before it.
            prior = sorted((root / "requests").iterdir())
            due = read(prior[-1] / "request.json")["started_ns"] + 30 * NS if prior else 0
            while time.time_ns() < due or not shared_guard(plan):
                guard()
                time.sleep(1)
            guard()
            consumer_guard(plan)
            if watch.remaining() < 40:
                raise GateError("insufficient_request_lease")
            publish(
                root / "processes" / f"{index:06d}-reservation.json",
                dict(index=index, reserved_ns=time.time_ns(), requests=1),
            )
            command = [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
                "runpy.run_path(sys.argv.pop(1),run_name='__main__')",
                str(root / "frozen/code/runtime"),
                str(root / "frozen/code/worker.py"),
                str(root),
                "--worker",
                str(index),
            ]
            result = child(command, root / "processes" / f"{index:06d}.log", 35, guard)
            publish(root / "processes" / f"{index:06d}-exit.json", result)
            if result["exit_code"] or result["category"] != "completed":
                raise GateError("request_child_failed")
            print(
                json.dumps(dict(index=index, **read(root / "results" / f"{index:06d}.json"))),
                flush=True,
            )
    except Exception as exc:
        error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        if not (root / "STOP").exists():
            publish(
                root / "STOP", dict(category=error, at_ns=time.time_ns(), automatic_retry=False)
            )
    results = [read(p) for p in sorted((root / "results").glob("*.json"))]
    publish(
        root / "result.json",
        dict(
            status="stopped" if error else "completed",
            error=error,
            requests=len(list((root / "requests").iterdir())),
            results=results,
            statuses=dict(Counter(r["status"] for r in results)),
            finished_ns=time.time_ns(),
            training_runs=0,
            predictions=0,
        ),
    )
    seal(root)


def report_campaigns(campaigns, output):
    """Recheck raw receipts independently; incomplete membership stays in denominator."""
    combined, records, refs, revisions = {}, [], [], []
    first = None
    attempted_jobs = set()
    for root in campaigns:
        verify(root)
        verify(root / "frozen")
        plan = read(root / "frozen/plan.json")
        if first is None:
            first = plan
        elif plan["cases"] != first["cases"] or plan["expires_ns"] != first["expires_ns"]:
            raise ValueError("report_campaign_membership")
        elif plan.get("continuation", {}).get("sha") != refs[-1]["manifest_sha"]:
            raise ValueError("report_continuation_pin")
        refs.append(
            dict(
                path=str(root),
                manifest_sha=sha(root / "manifest.json"),
                result=read(root / "result.json"),
            )
        )
        for index, capture in enumerate(sorted((root / "requests").iterdir())):
            job = plan["jobs"][index]
            key = (job["birth"]["pool"], job["days"])
            if job not in first["jobs"]:
                raise ValueError("report_changed_job")
            if key in attempted_jobs:
                raise ValueError("report_repeated_request")
            attempted_jobs.add(key)
            verify(capture)
            if (capture / "failure.json").exists():
                records.append(
                    dict(
                        pool=key[0],
                        days=key[1],
                        status="request_failed",
                        failure=read(capture / "failure.json"),
                    )
                )
                continue
            rows, report = qualify_window(capture, job["birth"], job["days"])
            saved = root / "results" / f"{index:06d}.json"
            if not saved.exists() or report != read(saved):
                raise ValueError("report_independent_reproduction")
            records.append(report)
            for row in rows:
                identity = row["pool"], row["open_s"]
                old = combined.get(identity)
                if old and old["ohlcv"] != row["ohlcv"]:
                    revisions.append(
                        dict(
                            pool=identity[0],
                            open_s=identity[1],
                            first=old["capture_sha"],
                            later=row["capture_sha"],
                        )
                    )
                if old is None or row["available_ns"] < old["available_ns"]:
                    combined[identity] = row
    if first is None:
        raise ValueError("report_empty_campaigns")
    cohorts = {}
    for name in ("lt1d", "1to3d", "7to10d"):
        valid = [r for r in records if r.get("cohorts", {}).get(name, {}).get("origins", 0)]
        cohorts[name] = dict(
            mints=len({r["base_mint"] for r in valid}),
            origins=sum(r["cohorts"][name]["origins"] for r in valid),
            disjoint=sum(r["cohorts"][name]["disjoint"] for r in valid),
            utc_days=len({d for r in valid for d in r["cohorts"][name]["utc_days"]}),
        )
    planned = {(b["pool"], d) for b in first["cases"] for d in (3, 10)}
    if not attempted_jobs <= planned:
        raise ValueError("report_unplanned_request")
    summary = dict(
        role="development_historical_transfer",
        training_allowed=False,
        prediction_count=0,
        planned_mints=len(first["cases"]),
        planned_windows=len(planned),
        attempted_windows=len(attempted_jobs),
        unattempted_windows=len(planned - attempted_jobs),
        supported_mints=len({r["base_mint"] for r in records if r.get("eligible", 0)}),
        eligible_origins=sum(r.get("eligible", 0) for r in records),
        cohorts=cohorts,
        unique_bars=len(combined),
        duplicate_bar_revisions=revisions,
        statuses=dict(Counter(r["status"] for r in records)),
        records=records,
        raw_verification=True,
        receipt_reports_reproduced=True,
        campaigns=refs,
        historical_online_availability_known=False,
        prospective=False,
        complete_benchmark_ready=False,
        age_semantics=first["age_semantics"],
        score_selection_performed=False,
    )
    output.mkdir(parents=True, exist_ok=False)
    with (output / "rows.jsonl").open("x") as stream:
        for key in sorted(combined):
            stream.write(json.dumps(combined[key], sort_keys=True, allow_nan=False) + "\n")
    publish(output / "selection.json", first)
    publish(output / "support.json", summary)
    seal(output)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--prepare", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--worker", type=int)
    parser.add_argument("--continue-prefix", type=Path)
    parser.add_argument("--report-campaigns", type=Path, nargs="+")
    args = parser.parse_args()
    if args.report_campaigns:
        result = report_campaigns(args.report_campaigns, args.root)
        print(json.dumps({k: v for k, v in result.items() if k not in ("records", "campaigns")}))
    elif args.prepare:
        plan = prepare(args.root.resolve(), args.prepare, args.source.resolve())
        print(json.dumps(dict(cases=len(plan["cases"]), expires_ns=plan["expires_ns"])))
    elif args.continue_prefix:
        plan = continue_prefix(args.root.resolve(), args.continue_prefix.resolve())
        print(json.dumps(dict(remaining=len(plan["jobs"]), expires_ns=plan["expires_ns"])))
    elif args.worker is not None:
        worker(args.root.resolve(), args.worker)
    else:
        run(args.root.resolve())


if __name__ == "__main__":
    main()
