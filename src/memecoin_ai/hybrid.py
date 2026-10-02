"""D40 bounded snapshot-trigger experiment. Existing decoder/features/targets are shared."""

from __future__ import annotations

import fcntl
import json
import os
import queue
import resource
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memecoin_ai.causal import (
    CausalBook,
    observation_targets,
    observed_checkpoint,
    publish_features,
)
from memecoin_ai.domain import ValidationError, canonical, digest, strict_json
from memecoin_ai.prospective import hashes, read, write
from memecoin_ai.pubsub import ReadOnlyWebSocket, SnapshotTriggers, StreamError
from memecoin_ai.sources import Capture, HTTPSourceError, ReadOnlyRPC, TransportSourceError
from memecoin_ai.tokens import CLOCK

ARMS = ("http", "hybrid")


def safe_error(exc: Exception) -> dict[str, Any]:
    return dict(
        error_type=type(exc).__name__,
        category=(
            exc.category
            if isinstance(exc, StreamError)
            else exc.reason
            if isinstance(exc, TransportSourceError)
            else "http_error"
            if isinstance(exc, HTTPSourceError)
            else "checkpoint_validation"
            if isinstance(exc, ValidationError)
            else "local_error"
        ),
        code=exc.code
        if isinstance(exc, StreamError)
        else exc.status
        if isinstance(exc, HTTPSourceError)
        else None,
    )


def append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class StreamReader:
    """Persist exact message bytes before offering them to the bounded consumer queue."""

    def __init__(self, ws: ReadOnlyWebSocket, root: Path, plan: dict[str, Any]):
        self.ws, self.root, self.plan = ws, root, plan
        root.mkdir(exist_ok=False)
        self.pending: queue.Queue[dict[str, Any]] = queue.Queue(plan["max_pending_messages"])
        self.stop = threading.Event()
        self.failure: dict[str, Any] | None = None
        self.byte_count = self.count = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                if (
                    self.count >= self.plan["max_notifications_per_segment"]
                    or self.byte_count + self.plan["max_wss_message_bytes"]
                    > self.plan["max_wss_bytes_per_segment"]
                ):
                    raise StreamError("stream_budget")
                raw = self.ws.receive()
                receipt = time.time_ns()
                name = f"{self.count:06d}.bin"
                with (self.root / name).open("xb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                row = dict(file=name, received_ns=receipt, sha256=digest(raw), bytes=len(raw))
                append(self.root / "receipts.jsonl", row)
                self.count += 1
                self.byte_count += len(raw)
                try:
                    self.pending.put_nowait(row)
                except queue.Full:
                    raise StreamError("queue_overflow") from None
        except Exception as exc:
            if not self.stop.is_set():
                self.failure = dict(**safe_error(exc), available_ns=time.time_ns())
                write(self.root / "failure.json", self.failure)
        finally:
            self.ws.close()

    def close(self) -> None:
        self.stop.set()
        self.ws.close()
        if self.thread.ident is not None:
            self.thread.join(timeout=11)
        if self.thread.is_alive():
            raise StreamError("reader_shutdown")


class Reducer:
    """Same append-order decisions online and offline; no WSS value enters CausalBook."""

    def __init__(self, root: Path, plan: dict[str, Any]):
        self.root, self.plan = root, plan
        deps = {c["pool_id"]: [a for a in c["params"][0] if a != CLOCK] for c in plan["cohort"]}
        self.triggers = SnapshotTriggers(deps)
        self.books = {
            a: CausalBook(
                list(deps),
                max_receipt_age_ns=25 * 10**9,
                max_chain_age_ns=45 * 10**9,
                max_gap_ns=25 * 10**9,
            )
            for a in ARMS
        }
        self.rows: dict[str, list[dict[str, Any]]] = {a: [] for a in ARMS}
        self.subscriptions: dict[int, str | None] = {}
        self.requests: dict[int, str | None] = {}
        self.generation = -1
        self.stats: Counter[str] = Counter()
        self.last_event_ns = 0
        self.last_accounts: dict[str, str] = {}

    def apply(self, event: dict[str, Any]) -> Any:
        now = event["available_ns"]
        if now < self.last_event_ns:
            raise ValidationError("event availability regressed")
        self.last_event_ns = now
        kind = event["kind"]
        if kind == "connect":
            self.generation += 1
            if event["generation"] != self.generation:
                raise ValidationError("connection generation mismatch")
            self.subscriptions = {}
            self.requests = {int(k): v for k, v in event["requests"].items()}
            return None
        if kind == "stream":
            if event["generation"] != self.generation:
                raise ValidationError("stale connection generation")
            raw = (self.root / event["file"]).read_bytes()
            if digest(raw) != event["sha256"] or event["received_ns"] > now:
                raise ValidationError("raw integrity/availability")
            msg = strict_json(raw.decode())
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                raise StreamError("invalid_envelope")
            if "error" in msg:
                raise StreamError("rpc_error", msg["error"].get("code"))
            if "id" in msg:
                rid, sid = msg["id"], msg.get("result")
                if (
                    type(rid) is not int
                    or rid not in self.requests
                    or type(sid) is not int
                    or sid in self.subscriptions
                ):
                    raise StreamError("invalid_ack")
                self.subscriptions[sid] = self.requests.pop(rid)
                self.stats["ack"] += 1
                return "ack"
            params = msg.get("params", {})
            sid = params.get("subscription")
            if type(sid) is not int or sid not in self.subscriptions:
                raise StreamError("unknown_subscription")
            address = self.subscriptions[sid]
            if address is None:
                if msg.get("method") != "rootNotification" or type(params["result"]) is not int:
                    raise StreamError("invalid_root")
                self.stats["root"] += 1
                return "root"
            if msg.get("method") != "accountNotification":
                raise StreamError("invalid_notification")
            result = params["result"]
            status = self.triggers.notification(address, result["context"]["slot"], result["value"])
            self.stats[status] += 1
            return status
        if kind == "snapshot":
            arm, pool = event["arm"], event["pool"]
            if arm == "hybrid" and (self.requests or self.generation < 0):
                raise ValidationError("snapshot before subscription barrier")
            cap = Capture.load(self.root / event["capture"])
            item = next(c for c in self.plan["cohort"] if c["pool_id"] == pool)
            request = json.loads(cap.request_json)
            if request["params"] != item["params"] or cap.method != "getMultipleAccounts":
                raise ValidationError("snapshot differs from frozen cohort")
            obs = observed_checkpoint(cap, pool, now, config_layout_version=2)
            if arm == "hybrid":
                body = cap.result()
                assert isinstance(body, dict)
                fingerprint = digest(
                    canonical(
                        [
                            value
                            for addr, value in zip(item["params"][0], body["value"], strict=True)
                            if addr != CLOCK
                        ]
                    ).encode()
                )
                if (
                    pool in self.last_accounts
                    and self.last_accounts[pool] != fingerprint
                    and not self.triggers.needed(pool)
                ):
                    # Could be silent loss OR a delivery race. Neither proves no activity.
                    self.books[arm].gap(pool, now, "unwitnessed_account_change")
                    self.stats["unwitnessed_account_change"] += 1
                self.last_accounts[pool] = fingerprint
            status = self.books[arm].ingest(obs)
            if status in ("regressed_slot", "conflicting_same_bank"):
                raise ValidationError("invalid finalized snapshot progression")
            if arm == "hybrid":
                self.triggers.anchor(pool, obs["slot"])
            return dict(status=status, slot=obs["slot"], response_sha256=obs["response_sha256"])
        if kind == "gap":
            for pool in self.books[event["arm"]].pools:
                self.books[event["arm"]].gap(pool, now, event["reason"])
            return None
        if kind == "frame":
            arm = event["arm"]
            rows = publish_features(self.books[arm].features(event["as_of_ns"]), now)
            self.rows[arm].extend(rows)
            return rows
        if kind == "error":
            self.stats["errors"] += 1
            return None
        raise ValidationError("unknown pilot event")


def derived(reducer: Reducer, start: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for arm, rows in reducer.rows.items():
        targets = (
            observation_targets(
                rows,
                horizon_ns=20 * 10**9,
                tolerance_ns=15 * 10**9,
                calibration_end_ns=start + 60 * 10**9,
            )
            if rows
            else []
        )
        result[arm] = dict(features=rows, targets=targets)
    result["stream_stats"] = dict(reducer.stats)
    return result


def verify_segment(root: Path) -> dict[str, Any]:
    manifest = read(root / "manifest.json")
    actual = hashes(root)
    actual.pop("manifest.json", None)
    if actual != manifest:
        raise ValidationError("pilot manifest mismatch")
    plan = read(root / "plan.json")
    # Source pin prevents declaring reproduction with a different implementation.
    saved = read(root / "source.json")
    if saved != source_snapshot():
        raise ValidationError("pilot source mismatch; use pinned code")
    reducer = Reducer(root, plan)
    for line in (root / "events.jsonl").read_text().splitlines():
        row = json.loads(line)
        result = reducer.apply(row["event"])
        if result != row["result"]:
            raise ValidationError("online/offline event disagreement")
    output = derived(reducer, read(root / "run.json")["started_ns"])
    if output != read(root / "derived.json"):
        raise ValidationError("online/offline feature or target disagreement")
    return dict(
        verified=True,
        stream_stats=output["stream_stats"],
        rows={a: len(output[a]["features"]) for a in ARMS},
    )


def source_snapshot() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))}


def run_segment(
    plan_path: Path,
    root: Path,
    http_endpoint: str,
    wss_endpoint: str,
    should_stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """One invocation per segment. New directory reserves the complete segment budget."""
    plan = read(plan_path)
    client = ReadOnlyRPC(
        http_endpoint,
        "alchemy-http",
        keep_alive=True,
        timeout=plan["timeout_seconds"],
        max_bytes=plan["max_http_response_bytes"],
    )
    from urllib.parse import urlsplit

    url = urlsplit(wss_endpoint)
    if (
        client.endpoint_origin != plan["http_origin"]
        or f"{url.scheme}://{url.hostname}" != plan["wss_origin"]
    ):
        raise ValidationError("endpoint origin differs from frozen plan")
    root.parent.mkdir(parents=True, exist_ok=True)
    lock = (root.parent / "pilot.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    index = (
        int(root.name.removeprefix("segment-")) if root.name in ("segment-00", "segment-01") else -1
    )
    if index < 0 or index >= plan["segments"]:
        raise ValidationError("expected a bounded segment-00/01 destination")
    if index:
        previous = root.parent / "segment-00"
        verify_segment(previous)
        if read(previous / "plan.json") != plan or not read(previous / "report.json")["complete"]:
            raise ValidationError("cannot resume failed or different pilot")
    root.mkdir(exist_ok=False)
    write(root / "plan.json", plan)
    write(root / "source.json", source_snapshot())
    (root / "events.jsonl").touch(exist_ok=False)
    start = time.time_ns()
    mono, cpu = time.monotonic(), time.process_time()
    write(
        root / "run.json",
        dict(
            started_ns=start, pid=os.getpid(), seed=None, plan_sha256=digest(plan_path.read_bytes())
        ),
    )
    reducer = Reducer(root, plan)
    reader: StreamReader | None = None
    counts: Counter[str] = Counter()
    measurements: list[dict[str, Any]] = []
    failure = None
    stream_totals = dict(messages=0, bytes=0, wire_received_bytes=0, wire_sent_bytes=0)

    def record(kind: str, **data: Any) -> Any:
        event = dict(kind=kind, available_ns=time.time_ns(), **data)
        # Raw captures/messages are already durable before applying this event.
        result = reducer.apply(event)
        append(root / "events.jsonl", dict(event=event, result=result))
        return result

    def frame(arm: str) -> None:
        as_of = time.time_ns()
        reducer.books[arm].features(as_of)  # Complete computation BEFORE publication timestamp.
        record("frame", arm=arm, as_of_ns=as_of)

    def drain() -> None:
        if should_stop():
            raise StreamError("paired_control_failed")
        assert reader is not None
        if reader.failure:
            raise StreamError(reader.failure["category"], reader.failure["code"])
        # Do not let an always-full queue starve time/budget checks.
        for _ in range(plan["max_pending_messages"]):
            try:
                row = reader.pending.get_nowait()
            except queue.Empty:
                break
            record(
                "stream",
                generation=reducer.generation,
                file=str((reader.root / row["file"]).relative_to(root)),
                received_ns=row["received_ns"],
                sha256=row["sha256"],
            )

    def disconnect() -> None:
        nonlocal reader
        if reader is not None:
            reader.close()
            stream_totals["messages"] += reader.count
            stream_totals["bytes"] += reader.byte_count
            stream_totals["wire_received_bytes"] += reader.ws.received_bytes
            stream_totals["wire_sent_bytes"] += reader.ws.sent_bytes
            reader = None

    def connect() -> None:
        nonlocal reader
        generation = reducer.generation + 1
        if generation >= plan["max_connections_per_segment"]:
            raise StreamError("connection_budget")
        addresses = sorted({a for deps in reducer.triggers.dependencies.values() for a in deps})
        reqs = dict(enumerate([*addresses, None], 1))
        if len(reqs) > plan["max_subscriptions"]:
            raise StreamError("subscription_budget")
        record("connect", generation=generation, requests=reqs)
        ws = ReadOnlyWebSocket(
            wss_endpoint, timeout=plan["timeout_seconds"], max_bytes=plan["max_wss_message_bytes"]
        )
        # Remaining segment budget is shared across connection generations.
        remaining = {
            **plan,
            "max_wss_bytes_per_segment": plan["max_wss_bytes_per_segment"] - stream_totals["bytes"],
            "max_notifications_per_segment": plan["max_notifications_per_segment"]
            - stream_totals["messages"],
        }
        reader = StreamReader(ws, root / f"stream-{generation:02d}", remaining)
        for rid, address in reqs.items():
            ws.subscribe(rid, address)
        reader.thread.start()
        deadline = time.monotonic() + plan["timeout_seconds"]
        while reducer.requests:
            drain()
            if time.monotonic() > deadline:
                raise StreamError("ack_timeout")
            time.sleep(0.01)

    def snapshot(arm: str, item: dict[str, Any], reason: str) -> None:
        if counts[arm] >= plan["max_http_per_segment_per_arm"]:
            raise StreamError("http_budget")
        if time.monotonic() - mono + client.timeout > plan["max_wall_seconds_per_segment"]:
            raise StreamError("wall_budget")
        counts[arm] += 1
        name = f"captures/{arm}-{counts[arm]:04d}"
        began = time.monotonic()
        cap = client.fetch("getMultipleAccounts", item["params"])
        cap.save(root / name)
        observed_checkpoint(cap, item["pool_id"], cap.received_ns, config_layout_version=2)
        # Validation/decode completed before the recorded availability timestamp.
        result = record("snapshot", arm=arm, pool=item["pool_id"], capture=name, reason=reason)
        measurements.append(
            dict(
                arm=arm,
                reason=reason,
                capture=name,
                response_seconds=time.monotonic() - began,
                bytes=len(cap.response),
                transport=client.transport_measurement(),
                **result,
            )
        )

    last = {a: dict.fromkeys(reducer.triggers.dependencies, -float("inf")) for a in ARMS}
    try:
        connect()
        forced = False
        round_index = 0
        while time.monotonic() - mono < plan["segment_seconds"]:
            drain()
            elapsed = time.monotonic() - mono
            if not forced and elapsed >= plan["forced_disconnect_seconds"]:
                record("gap", arm="hybrid", reason="forced_disconnect_history_unrecoverable")
                frame("hybrid")
                disconnect()
                connect()
                # Recovery means a NEW observed state, never a backdated historical state.
                last["hybrid"] = dict.fromkeys(last["hybrid"], -float("inf"))
                forced = True
            if elapsed < round_index * plan["http_interval_seconds"]:
                time.sleep(0.02)
                continue
            # Alternate serial ordering to avoid always favouring one Alchemy arm.
            arms = ARMS if round_index % 2 == 0 else tuple(reversed(ARMS))
            for arm in arms:
                for item in plan["cohort"]:
                    drain()
                    pool = item["pool_id"]
                    age = time.monotonic() - last[arm][pool]
                    reason = "periodic" if arm == "http" else "heartbeat"
                    due = arm == "http" or age >= plan["hybrid_heartbeat_seconds"]
                    if (
                        arm == "hybrid"
                        and reducer.triggers.needed(pool)
                        and age >= plan["hybrid_min_interval_seconds"]
                    ):
                        due, reason = True, "notification"
                    if due:
                        snapshot(arm, item, reason)
                        last[arm][pool] = time.monotonic()
                frame(arm)
            round_index += 1
        drain()
    except Exception as exc:
        failure = safe_error(exc)
        print(canonical(dict(stage="D40", **failure)), flush=True)
        write(root / "failure.json", dict(**failure, time_ns=time.time_ns()))
        for arm in ARMS:
            record("gap", arm=arm, reason=failure["category"])
            frame(arm)
        record("error", **failure)
    finally:
        disconnect()
        client.close()
        lock.close()
    write(root / "derived.json", derived(reducer, start))
    report = dict(
        complete=failure is None,
        failure=failure,
        requests=dict(counts),
        wall_seconds=time.monotonic() - mono,
        cpu_seconds=time.process_time() - cpu,
        peak_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        stream=stream_totals,
        measurements=measurements,
        stream_history_complete=False,
        migration_approved=False,
        training_ready=False,
    )
    write(root / "report.json", report)
    write(root / "manifest.json", hashes(root))
    verify_segment(root)
    return report
