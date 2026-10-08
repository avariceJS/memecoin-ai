"""Isolated, paced public capture batches; durable reservation precedes each network request."""

import http.client
import json
import os
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from memecoin_brain.source import BASE_EXCLUDED, EXCLUDED
from memecoin_experience.storage import (
    atomic_json,
    digest,
    file_hash,
    lock,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)

NS = 10**9


def validate(plan):
    if (
        set(plan)
        != {
            "version",
            "created_ns",
            "expires_ns",
            "spacing_s",
            "max_requests",
            "max_bytes",
            "metadata",
            "cases",
            "jobs",
            "protocol",
        }
        or plan["version"] != "population-public-capture-v1"
    ):
        raise ValueError("acquisition_plan")
    if (
        any(
            type(plan[k]) is not int
            for k in ("created_ns", "expires_ns", "spacing_s", "max_requests", "max_bytes")
        )
        or not 0 < plan["created_ns"] < plan["expires_ns"]
        or not 3 <= plan["spacing_s"] <= 60
        or not 1 <= plan["max_requests"] <= 100000
        or not 1 <= plan["max_bytes"] <= 8 * 1024**3
    ):
        raise ValueError("acquisition_limits")
    cases = {c["pool"]: c for c in plan["cases"]}
    if len(cases) != len(plan["cases"]):
        raise ValueError("acquisition_duplicate_population")
    for case in cases.values():
        if case["pool"] in EXCLUDED or case["base_mint"] in BASE_EXCLUDED:
            raise ValueError("acquisition_protected")
        if any(
            not isinstance(case[k], str) or not case[k].isalnum()
            for k in ("pool", "base_mint", "quote_mint")
        ):
            raise ValueError("acquisition_identity")
    seen = set()
    urls = set()
    for job in plan["jobs"]:
        if (
            set(job) != {"pool", "start_s", "end_s"}
            or job["pool"] not in cases
            or any(type(job[k]) is not int for k in ("start_s", "end_s"))
            or not 0 <= job["start_s"] < job["end_s"] <= plan["created_ns"] // NS
            or job["end_s"] - job["start_s"] > 999 * 300
        ):
            raise ValueError("acquisition_job")
        key = digest(job)
        request_identity = (job["pool"], job["end_s"])
        if key in seen or request_identity in urls:
            raise ValueError("acquisition_duplicate_job")
        seen.add(key)
        urls.add(request_identity)
    if len(seen) > plan["max_requests"]:
        raise ValueError("acquisition_request_budget")
    return cases


def capture(
    root, plan, *, guard, opener=urllib.request.urlopen, clock=time.time_ns, sleep=time.sleep
):
    """guard owns shared-provider fencing. Ambiguous requests never retry, even after restart."""
    cases = validate(plan)
    root = safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    with lock(root / ".capture.lock"):
        atomic_json(root / "plan.json", plan)
        if (root / "complete.json").exists():
            return json.loads((root / "complete.json").read_text())
        if (root / "STOP.json").exists():
            raise ValueError("acquisition_stopped_requires_review")
        (root / "requests").mkdir(exist_ok=True)
        db = sqlite3.connect(root / "requests.sqlite")
        db.execute("pragma synchronous=FULL")
        db.execute(
            "create table if not exists requests(id text primary key,ordinal integer unique,"
            "job text,status text,started integer,capture text,result text)"
        )
        db.commit()
        sync_dir(root)
        try:
            # A crash after capture sealing can recover locally, never by a second GET.
            for key, _ordinal, _raw, _status, _started, path, _ in db.execute(
                "select * from requests where status='reserved'"
            ).fetchall():
                directory = safe_path(path)
                if not (directory / "manifest.json").exists():
                    atomic_json(
                        root / "STOP.json",
                        dict(
                            reason="unresolved_reserved_request", request=key, automatic_retry=False
                        ),
                    )
                    raise ValueError("acquisition_unresolved_reservation")
                verify_files(directory)
                result = json.loads((directory / "outcome.json").read_text())
                if result["request"] != key:
                    raise ValueError("acquisition_recovery_binding")
                db.execute(
                    "update requests set status=?,result=? where id=?",
                    (result["status"], json.dumps(result, sort_keys=True), key),
                )
                db.commit()
                if result["status"] in {"transport_failure", "provider_stop"}:
                    atomic_json(
                        root / "STOP.json",
                        dict(reason=result["status"], request=key, automatic_retry=False),
                    )
                    raise ValueError("acquisition_recovered_provider_stop")
            total_bytes = sum(
                p.stat().st_size for p in (root / "requests").glob("*/*") if p.is_file()
            )
            for ordinal, job in enumerate(plan["jobs"]):
                key = digest(job)
                if db.execute("select 1 from requests where id=?", (key,)).fetchone():
                    continue
                last = db.execute("select max(started) from requests").fetchone()[0]
                now = clock()
                if last:
                    if now < last:
                        raise ValueError("acquisition_clock_regression")
                    delay = (last + plan["spacing_s"] * NS - now) / NS
                    if delay > 0:
                        sleep(delay)
                guard()
                now = clock()
                if (root / "STOP.json").exists() or not plan["created_ns"] <= now < plan[
                    "expires_ns"
                ]:
                    raise ValueError("acquisition_stop_or_expiry")
                if (
                    total_bytes + 2 * 1024**2 + 65536 > plan["max_bytes"]
                    or shutil.disk_usage(root).free < 4 * 1024**3
                ):
                    raise ValueError("acquisition_storage_budget")
                target = root / "requests" / f"{ordinal:07d}"
                db.execute(
                    "insert into requests values(?,?,?,?,?,?,?)",
                    (
                        key,
                        ordinal,
                        json.dumps(job, sort_keys=True),
                        "reserved",
                        now,
                        str(target),
                        None,
                    ),
                )
                db.commit()
                target.mkdir()
                sync_dir(target.parent)
                params = dict(
                    aggregate=5,
                    currency="usd",
                    token="base",
                    include_empty_intervals="false",
                    limit=1000,
                    before_timestamp=job["end_s"],
                )
                url = (
                    "https://api.geckoterminal.com/api/v2/networks/solana/pools/"
                    + job["pool"]
                    + "/ohlcv/minute?"
                    + urllib.parse.urlencode(params)
                )
                atomic_json(
                    target / "request.json", dict(url=url, started_ns=now, lane="historical")
                )
                status, code, payload, error_type = "received", None, b"", None
                try:
                    request = urllib.request.Request(
                        url,
                        headers={
                            "Accept": "application/json;version=20230203",
                            "User-Agent": "memecoin-ai-research/0.1",
                        },
                    )
                    with opener(request, timeout=20) as response:
                        code = response.status
                        payload = response.read(2 * 1024**2 + 1)
                    if code != 200 or len(payload) > 2 * 1024**2:
                        status = "provider_stop"
                except urllib.error.HTTPError as exc:
                    code, payload, error_type = (
                        exc.code,
                        exc.read(2 * 1024**2 + 1),
                        type(exc).__name__,
                    )
                    status = "unsupported" if code in (404, 410, 422) else "provider_stop"
                except (OSError, TimeoutError, http.client.HTTPException) as exc:
                    status, error_type = "transport_failure", type(exc).__name__
                received = clock()
                with (target / "response.bin").open("wb") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_json(
                    target / "receipt.json",
                    dict(
                        status=status,
                        http_status=code,
                        received_ns=received,
                        available_ns=received,
                        response_sha256=file_hash(target / "response.bin"),
                        error_type=error_type,
                    ),
                )
                result = dict(
                    request=key,
                    status=status,
                    http_status=code,
                    bytes=len(payload),
                    ordinal=ordinal,
                )
                atomic_json(target / "outcome.json", result)
                pin = seal_files(target)
                db.execute(
                    "update requests set status=?,result=? where id=?",
                    (status, json.dumps(result, sort_keys=True), key),
                )
                db.commit()
                total_bytes += sum(p.stat().st_size for p in target.iterdir())
                print(
                    json.dumps(dict(ordinal=ordinal, status=status, http_status=code, capture=pin)),
                    flush=True,
                )
                if status in {"provider_stop", "transport_failure"}:
                    atomic_json(
                        root / "STOP.json", dict(reason=status, request=key, automatic_retry=False)
                    )
                    raise ValueError("acquisition_provider_stopped")
            counts = dict(db.execute("select status,count(*) from requests group by status"))
            result = dict(
                plan=digest(plan),
                requests=sum(counts.values()),
                statuses=counts,
                population=len(cases),
                completed=True,
                known_at_ns=clock(),
                trading=False,
            )
            atomic_json(root / "complete.json", result)
            return result
        finally:
            db.close()
