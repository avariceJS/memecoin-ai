"""Frozen public acquisition to role-separated, portable qualified experience sources."""

import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from memecoin_experience.continuous import export, qualify_capture
from memecoin_experience.storage import (
    atomic_json,
    canonical,
    digest,
    file_hash,
    lock,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)
from memecoin_registry.contracts import digest as legacy_digest

from .acquisition import validate
from .catalog import MAX_TIME, publish


def roles(plan):
    validate(plan)
    assets = {}
    cases = {c["pool"]: c for c in plan["cases"]}
    if {j["pool"] for j in plan["jobs"]} != set(cases):
        raise ValueError("population_campaign_unplanned_enrollment")
    for c in plan["cases"]:
        if c.get("role") not in {"train", "research_evaluation"}:
            raise ValueError("population_campaign_role")
        if (
            type(c.get("event_s")) is not int
            or not 0 <= c["event_s"] <= plan["created_ns"] // 10**9
            or c["event_s"] * 10**9 + 7 * 86400 * 10**9 >= MAX_TIME
            or not isinstance(c.get("month"), str)
            or not 1 <= len(c["month"]) <= 256
        ):
            raise ValueError("population_campaign_birth_metadata")
        previous = assets.setdefault(c["base_mint"], c["role"])
        if previous != c["role"]:
            raise ValueError("population_campaign_mint_overlap")
    if any(j["start_s"] < cases[j["pool"]]["event_s"] for j in plan["jobs"]):
        raise ValueError("population_campaign_prebirth_window")


def population(store, plan, role):
    roles(plan)
    if role not in {"train", "research_evaluation"}:
        raise ValueError("population_campaign_role")
    pin = digest(plan)

    def rows():
        for case in plan["cases"]:
            if case["role"] != role:
                continue
            born = case["event_s"] * 10**9
            edges = [born, born + 3 * 86400 * 10**9, born + 7 * 86400 * 10**9, MAX_TIME]
            for i, (start, end) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
                yield dict(
                    entity="solana/pool/" + case["pool"],
                    start_ns=start,
                    end_ns=end,
                    known_ns=plan["created_ns"],
                    evidence=[pin],
                    attributes=dict(
                        venue="pumpswap",
                        birth_cohort=case["month"],
                        lifecycle=f"age_band:{i}",
                        base_asset=case["base_mint"],
                        quote_asset=case["quote_mint"],
                        enrollment="authority_birth_page_prefix",
                    ),
                )

    return publish(
        store,
        "population",
        rows(),
        cutoff_ns=plan["created_ns"],
        definition=dict(
            role=role,
            producer="frozen_birth_campaign_v1",
            selection=plan["protocol"]["population_bias"],
            acquisition=pin,
        ),
    )


def source(campaign, output, role):
    """Require every planned request in this role resolved. Never open the other role's payload."""
    campaign, output = safe_path(campaign), safe_path(output)
    plan = json.loads((campaign / "plan.json").read_text())
    roles(plan)
    if plan != json.loads((campaign / "capture/plan.json").read_text()):
        raise ValueError("population_campaign_plan_binding")
    if role not in {"train", "research_evaluation"}:
        raise ValueError("population_campaign_role")
    cases = {c["pool"]: c for c in plan["cases"] if c["role"] == role}
    if not cases:
        raise ValueError("population_campaign_empty_role")
    jobs = [(i, j) for i, j in enumerate(plan["jobs"]) if j["pool"] in cases]
    output.parent.mkdir(parents=True, exist_ok=True)
    with lock(output.parent / ("." + output.name + ".campaign.lock")):
        if output.exists():
            verify_files(output / "accounting")
            report = json.loads((output / "accounting/report.json").read_text())
            if report["acquisition"] != digest(plan) or report["role"] != role:
                raise ValueError("population_campaign_source_binding")
            verify_files(output / "source/meta", report["source_pin"])
            with (output / "source/meta/captures.jsonl").open() as stream:
                for line in stream:
                    pin = json.loads(line)["pin"]
                    verify_files(output / "source/captures" / pin, pin)
            return report
        db = sqlite3.connect((campaign / "capture/requests.sqlite").as_uri() + "?mode=ro", uri=True)
        try:
            db.execute("BEGIN")
            # Metadata-only completeness gate precedes opening any response.
            for ordinal, job in jobs:
                found = db.execute(
                    "select ordinal,job,status from requests where id=?", (digest(job),)
                ).fetchone()
                if not found or found[2] == "reserved":
                    raise ValueError("population_campaign_incomplete_role")
                if found[0] != ordinal or json.loads(found[1]) != job:
                    raise ValueError("population_campaign_request_binding")
            stage = Path(tempfile.mkdtemp(prefix=".campaign-source-", dir=output.parent))
            try:
                accounting = stage / "accounting"
                accounting.mkdir()
                index = sqlite3.connect(stage / "index.sqlite")
                try:
                    index.executescript("""
                    create table captures(pin text primary key,path text,available integer,
                      status text,n integer,record text,checksum text);
                    create table versions(pin text,open_s integer,payload text,checksum text,
                      primary key(pin,open_s));
                    """)
                    counts, cutoff = Counter(), 0
                    intervals = {pool: [] for pool in cases}
                    with (accounting / "requests.jsonl").open("wb") as stream:
                        for ordinal, job in jobs:
                            key = digest(job)
                            _ordinal, raw, status, started, path, result = db.execute(
                                "select ordinal,job,status,started,capture,result "
                                "from requests where id=?",
                                (key,),
                            ).fetchone()
                            cap = safe_path(path)
                            if cap != campaign / "capture/requests" / f"{ordinal:07d}":
                                raise ValueError("population_campaign_capture_path")
                            verify_files(cap)
                            outcome = json.loads((cap / "outcome.json").read_text())
                            request = json.loads((cap / "request.json").read_text())
                            receipt = json.loads((cap / "receipt.json").read_text())
                            if (
                                outcome != json.loads(result)
                                or outcome["request"] != key
                                or outcome["ordinal"] != ordinal
                                or outcome["status"] != status
                                or request["started_ns"] != started
                                or outcome["bytes"] != (cap / "response.bin").stat().st_size
                                or outcome["status"] != receipt["status"]
                                or outcome["http_status"] != receipt["http_status"]
                            ):
                                raise ValueError("population_campaign_outcome_binding")
                            from urllib.parse import parse_qs, urlparse

                            if parse_qs(urlparse(request["url"]).query).get("before_timestamp") != [
                                str(job["end_s"])
                            ]:
                                raise ValueError("population_campaign_request_window")
                            pin = file_hash(cap / "manifest.json")
                            rows, reason = qualify_capture(cap, cases[job["pool"]])
                            counts["requests/" + status] += 1
                            counts["qualification/" + reason] += 1
                            counts["qualified_empty"] += int(reason == "qualified" and not rows)
                            record = dict(
                                pin=pin,
                                path=str(cap),
                                pool=job["pool"],
                                available_ns=receipt["available_ns"],
                                status=reason,
                                n=len(rows),
                            )
                            index.execute(
                                "insert into captures values(?,?,?,?,?,?,?)",
                                (
                                    pin,
                                    str(cap),
                                    record["available_ns"],
                                    reason,
                                    len(rows),
                                    canonical(record).decode(),
                                    legacy_digest(record),
                                ),
                            )
                            index.executemany(
                                "insert into versions values(?,?,?,?)",
                                (
                                    (pin, r["open_s"], canonical(r).decode(), legacy_digest(r))
                                    for r in rows
                                ),
                            )
                            stream.write(
                                canonical(
                                    dict(
                                        ordinal=ordinal,
                                        job=job,
                                        capture=pin,
                                        acquisition_status=status,
                                        qualification=reason,
                                        bars=len(rows),
                                    )
                                )
                                + b"\n"
                            )
                            cutoff = max(cutoff, receipt["available_ns"])
                            # Grid-aligned opens in the frozen [start, end) request window.
                            start = ((job["start_s"] + 299) // 300) * 300
                            end = ((job["end_s"] + 299) // 300) * 300
                            intervals[job["pool"]].append([start, end])
                            index.commit()
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    index.close()
                for pool, spans in intervals.items():
                    merged = []
                    for start, end in sorted(spans):
                        if start >= end:
                            continue
                        if merged and start <= merged[-1][1]:
                            merged[-1][1] = max(merged[-1][1], end)
                        else:
                            merged.append([start, end])
                    intervals[pool] = merged
                export_plan = dict(
                    role=role,
                    cutoff_ns=cutoff,
                    start_s=min(j["start_s"] for _, j in jobs) // 300 * 300,
                    end_s=(max(j["end_s"] for _, j in jobs) + 299) // 300 * 300,
                    selection="frozen_birth_campaign:" + digest(plan),
                    pools=[
                        {k: c[k] for k in ("pool", "base_mint", "quote_mint")}
                        for c in cases.values()
                    ],
                    intervals=intervals,
                )
                exported = export(stage / "index.sqlite", stage / "source", export_plan)
                report = dict(
                    version="population-campaign-source-v1",
                    acquisition=digest(plan),
                    role=role,
                    source_pin=exported["pin"],
                    cutoff_ns=cutoff,
                    enrollment=len(cases),
                    planned_requests=len(jobs),
                    counts=dict(counts),
                    bars=exported["bars"],
                    population_retained=True,
                    historical_online_availability_known=False,
                )
                atomic_json(accounting / "report.json", report)
                atomic_json(accounting / "plan.json", plan)
                seal_files(accounting)
                (stage / "index.sqlite").unlink()
                sync_dir(stage)
                os.rename(stage, output)
                sync_dir(output.parent)
                return report
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        finally:
            db.close()
