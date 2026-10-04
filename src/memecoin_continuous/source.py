"""One paced public source, durable reservations, local pool failures and bounded recovery."""

import fcntl
import http.client
import json
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from memecoin_brain.source import HOST, bars
from memecoin_ml.data import read, sha
from memecoin_paper.runtime import atomic
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import Ledger, publish

from .store import connect, ingest, stats

NS = 10**9


def seed_state(root):
    return dict(
        last_ns=0,
        cooldown_ns=0,
        failures=0,
        rate_events=[],
        bytes=0,
        attempts=0,
        pools={
            p["pool"]: dict(
                next_ns=0,
                status=p.get("initial_quality", "active"),
                streak=0,
                probe_day=-1,
                probes=0,
                cursor=p.get("history_before"),
                history_pages=0,
                history_done=False,
            )
            for p in read(root / "universe.json")["pools"]
        },
    )


def state(root):
    s = seed_state(root)
    binding = dict(config_sha=sha(root / "config.json"), scope="B08_source")
    with Ledger(root / "journal", binding) as ledger:
        for e in ledger.events:
            s = e["payload"]["state"]
    return s


def choose(s, universe, now):
    day = now // (86400 * NS)
    live = []
    for p, q in s["pools"].items():
        probes = q["probes"] if q["probe_day"] == day else 0
        if q["next_ns"] <= now and (q["status"] == "active" or probes < 2):
            live.append((q["next_ns"], p))
    if live:
        p = min(live)[1]
        return dict(pool=p, lane="prospective", before=None, limit=100)
    history = []
    for u in universe:
        q = s["pools"][u["pool"]]
        if q["status"] == "active" and not q["history_done"] and q["history_pages"] < 30:
            history.append((q["history_pages"], u["pool"]))
    if history:
        p = min(history)[1]
        return dict(pool=p, lane="historical", before=s["pools"][p]["cursor"], limit=1000)
    return None


def reserve(s, job, now):
    # Reservation advances due/counters before IO: crash cannot issue the same reserved job again.
    s = json.loads(json.dumps(s))
    s["last_ns"] = now
    s["attempts"] += 1
    s["bytes"] += 2 * 1024**2 + 65536
    q = s["pools"].get(job.get("pool"))
    if q:
        if job["lane"] == "prospective":
            q["next_ns"] = now + (300 if q["status"] == "active" else 43200) * NS
            if q["status"] != "active":
                day = now // (86400 * NS)
                q["probes"] = (q["probes"] if q["probe_day"] == day else 0) + 1
                q["probe_day"] = day
                q["streak"] = 0 if q.get("probe_pending") else q["streak"]
                q["probe_pending"] = True
        else:
            q["history_pages"] += 1
            q["history_done"] = True  # failure/crash never retries this history page
    return s


def outcome(s, job, receipt, rows, status, now, floor):
    s = json.loads(json.dumps(s))
    code = receipt.get("http_status")
    stop = None
    if status == "transport":
        s["failures"] += 1
        if code in (401, 403):
            stop = "provider_auth"
        elif code == 429:
            s["rate_events"] = [t for t in s["rate_events"] if now - t < 86400 * NS] + [now]
            s["cooldown_ns"] = now + max(900, receipt.get("retry_after_seconds", 0)) * NS
            if len(s["rate_events"]) >= 3:
                stop = "three_429_in_day"
        else:
            s["cooldown_ns"] = now + min(3600, 30 * 2 ** min(7, s["failures"] - 1)) * NS
        if s["failures"] >= 6:
            stop = "six_consecutive_transport_failures"
    else:
        s["failures"] = 0
        if job.get("pool"):
            q = s["pools"][job["pool"]]
            if status != "valid":
                q.update(
                    status="quarantined", streak=0, next_ns=now + 43200 * NS, probe_pending=False
                )
            else:
                if q["status"] == "quarantined" and job["lane"] == "prospective":
                    fresh = (
                        bool(rows)
                        and 0 <= now - max(r["open_s"] + 300 for r in rows) * NS <= 600 * NS
                    )
                    q["streak"] = q["streak"] + 1 if fresh else 0
                    q["probe_pending"] = False
                    if q["streak"] >= 2:
                        q.update(status="active", next_ns=now + 300 * NS)
                if job["lane"] == "historical":
                    earliest = min((r["open_s"] for r in rows), default=0)
                    q["history_done"] = (
                        len(rows) < 999
                        or earliest <= floor
                        or (job["before"] is not None and earliest >= job["before"])
                    )
                    q["cursor"] = earliest - 1
    return s, stop


def fetch(root, job, s, ledger):
    c = read(root / "config.json")
    delay = s["last_ns"] / NS + 4 - time.time()
    if delay > 6:
        raise ValueError("source_clock_regression")
    if delay > 0:
        time.sleep(delay)
    now = time.time_ns()
    if (root / "STOP").exists() or now >= c["expires_ns"]:
        raise ValueError("source_stop_or_expiry")
    if s["attempts"] >= c["max_requests"]:
        raise ValueError("request_budget")
    if shutil.disk_usage(root).free < 4 * 1024**3 or s["bytes"] + 2 * 1024**2 > 8 * 1024**3:
        raise ValueError("storage_budget")
    target = root / "requests" / f"{s['attempts']:06d}"
    if job["lane"] == "discovery":
        path = "/networks/solana/pools"
        params = dict(page=job["page"])
    else:
        path = "/networks/solana/pools/" + job["pool"] + "/ohlcv/minute"
        params = dict(
            aggregate=5,
            limit=job["limit"],
            currency="usd",
            token="base",
            include_empty_intervals="false",
        )
        if job["before"] is not None:
            params["before_timestamp"] = job["before"]
    url = HOST + path + "?" + urllib.parse.urlencode(params)
    s = reserve(s, job, now)
    ledger.append("reserved", job=job, state=s, capture=str(target.resolve()))
    target.mkdir()
    publish(target / "request.json", dict(url=url, started_ns=now, lane=job["lane"]))
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json;version=20230203",
                "User-Agent": "memecoin-ai-research/0.1",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = response.read(2 * 1024**2 + 1)
            code = response.status
        (target / "response.bin").write_bytes(payload)
        if len(payload) > 2 * 1024**2:
            raise ValueError("response_budget")
        json.loads(payload)
        receipt = dict(
            status="completed",
            http_status=code,
            received_ns=time.time_ns(),
            available_ns=time.time_ns(),
            response_sha256=sha(target / "response.bin"),
        )
        publish(target / "receipt.json", receipt)
        seal(target)
        if job["lane"] == "discovery":
            rows = []
            status = "valid"
        else:
            with connect(root / "index.sqlite") as db:
                r = ingest(db, target, job["pool"])
            status = r["status"]
            rows = bars(target, job["pool"]) if status == "valid" else []
    except (
        urllib.error.URLError,
        TimeoutError,
        ConnectionError,
        ssl.SSLError,
        http.client.HTTPException,
    ) as exc:
        receipt = dict(
            http_status=getattr(exc, "code", None),
            error_type=type(exc).__name__,
            received_ns=time.time_ns(),
            category="public_http_failure",
        )
        if isinstance(exc, urllib.error.HTTPError):
            retry = exc.headers.get("Retry-After", "0")
            receipt["retry_after_seconds"] = int(retry) if retry.isdigit() else 0
        publish(target / "failure.json", receipt)
        seal(target)
        rows = []
        status = "transport"
        print(json.dumps(receipt), flush=True)
    s["bytes"] -= 2 * 1024**2 + 65536
    s["bytes"] += sum(p.stat().st_size for p in target.iterdir() if p.is_file())
    u = next(
        (p for p in read(root / "universe.json")["pools"] if p["pool"] == job.get("pool")), None
    )
    floor = max(c["history_floor_s"], u["pool_created_ns"] // NS if u else 0)
    s, stop = outcome(s, job, receipt, rows, status, time.time_ns(), floor)
    ledger.append(
        "outcome",
        job=job,
        state=s,
        status=status,
        capture=str(target.resolve()),
        capture_sha=sha(target / "manifest.json"),
    )
    if stop:
        publish(root / "STOP", dict(reason=stop, at_ns=time.time_ns()))
    return s, target


def tick(root):
    with (root / ".source.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = dict(config_sha=sha(root / "config.json"), scope="B08_source")
        with Ledger(root / "journal", binding) as ledger:
            s = ledger.events[-1]["payload"]["state"] if ledger.events else seed_state(root)
            # Sealed raw from a crash between receipt and index is recovered with original times.
            for e in ledger.events[-2:]:
                if e["kind"] == "reserved":
                    cap = Path(e["payload"]["capture"])
                    job = e["payload"]["job"]
                    if (
                        job.get("pool")
                        and (cap / "manifest.json").exists()
                        and (cap / "receipt.json").exists()
                    ):
                        with connect(root / "index.sqlite") as db:
                            ingest(db, cap, job["pool"])
            for _ in range(8):
                if (root / "STOP").exists() or time.time_ns() < s["cooldown_ns"]:
                    break
                job = choose(s, read(root / "universe.json")["pools"], time.time_ns())
                if not job:
                    break
                s, _ = fetch(root, job, s, ledger)
        atomic(
            root / "health.json",
            dict(
                at_ns=time.time_ns(),
                attempts=s["attempts"],
                cooldown_ns=s["cooldown_ns"],
                failures=s["failures"],
                pools=s["pools"],
                **stats(root),
            ),
        )
