"""Deterministic evidence reduction with optional, resource-gated local annotations.

Input is an explicitly curated JSONL packet, never a scan of work/ or production.
LLM text is a derivative index and cannot change source events or research decisions.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from worker_artifacts import plain, read_json, safe_name, sha
from worker_preflight import NoRedirect, command, ollama_metadata

FIELDS = {"at_ns", "severity", "component", "reason", "source_ref"}
TOKEN = re.compile(r"[A-Za-z0-9_./:#-]{1,200}\Z")


def publish(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def summarize(packet, scope):
    packet = plain(packet)
    safe_name(packet.name)
    if scope not in {"synthetic", "exploratory"} or packet.stat().st_size > 16 * 1024**2:
        raise ValueError("packet_bound_or_scope")
    raw_pin = sha(packet)
    groups = collections.defaultdict(list)
    total = 0
    for line in packet.read_text().splitlines():
        row = json.loads(line)
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError("curated_packet_schema")
        if type(row["at_ns"]) is not int or row["at_ns"] < 0:
            raise ValueError("packet_time")
        if row["severity"] not in {"INFO", "WARN", "ERROR", "STOP"}:
            raise ValueError("packet_severity")
        for key in ("component", "reason", "source_ref"):
            if not isinstance(row[key], str) or not TOKEN.fullmatch(row[key]):
                raise ValueError("packet_text_not_machine_token")
            safe_name(row[key])
        groups[(row["severity"], row["component"], row["reason"])].append(row)
        total += 1
        if total > 50000 or len(groups) > 1000:
            raise ValueError("packet_cardinality_bound")
    if not total:
        raise ValueError("empty_packet")
    rows = [
        dict(
            severity=k[0],
            component=k[1],
            reason=k[2],
            count=len(v),
            first_ns=min(x["at_ns"] for x in v),
            last_ns=max(x["at_ns"] for x in v),
            example_refs=[x["source_ref"] for x in v[:3]],
        )
        for k, v in sorted(groups.items())
    ]
    return dict(
        schema=1,
        scope=scope,
        source_sha256=raw_pin,
        events=total,
        groups=rows,
        source_bytes=packet.stat().st_size,
        inference_used=False,
        protected_holdout_allowed=False,
        research_feature_admitted=False,
    )


def enqueue(packet, queue, scope):
    packet, queue = plain(packet), plain(queue)
    report = summarize(packet, scope)
    raw_pin = report["source_sha256"]
    queue.mkdir(parents=True, exist_ok=True)
    job = queue / raw_pin
    if job.exists():
        if sha(job / "source.jsonl") != raw_pin or read_json(job / "summary.json") != report:
            raise ValueError("queue_conflict")
        return job
    job.mkdir()
    with (job / "source.jsonl").open("xb") as stream:
        stream.write(packet.read_bytes())
    if sha(job / "source.jsonl") != raw_pin:
        raise ValueError("packet_changed")
    publish(job / "summary.json", report)
    return job


@contextlib.contextmanager
def resource_lease(queue):
    queue = plain(queue)
    queue.mkdir(parents=True, exist_ok=True)
    with plain(queue / ".compute.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("compute_busy") from None
        yield handle.fileno()


def gate(model):
    memory = command(["sysctl", "-n", "hw.memsize"])
    pressure = command(["memory_pressure", "-Q"])
    match = re.search(r"free percentage: (\d+)%", pressure or "")
    models = ollama_metadata("tags")
    installed = [m for m in models["models"] if m["name"] == model]
    loaded = ollama_metadata("ps")
    if not memory or not match or len(installed) != 1 or not loaded["available"]:
        return "inventory_unavailable"
    if loaded["models"]:
        return "ollama_already_loaded"
    if installed[0]["size"] > int(memory) * 0.60:
        return "model_exceeds_60_percent_ram"
    if int(match[1]) < 70:
        return "memory_pressure"
    return None


def annotate(job, model):
    job = plain(job)
    summary = read_json(job / "summary.json")
    if sha(job / "source.jsonl") != summary["source_sha256"]:
        raise ValueError("queue_source_changed")
    if summarize(job / "source.jsonl", summary["scope"]) != summary:
        raise ValueError("queue_summary_changed")
    if (job / "annotation.json").exists():
        return {"status": "already_annotated"}
    if (job / "attempt.json").exists():
        return {"status": "deferred", "reason": "prior_attempt_requires_review"}
    with resource_lease(job.parent):
        blocked = gate(model)
        if blocked:
            return dict(status="deferred", reason=blocked, source_sha256=summary["source_sha256"])
        text = json.dumps(summary, sort_keys=True)
        if len(text.encode()) > 6000:
            return dict(status="deferred", reason="summary_context_bound")
        prompt = (
            "Summarize these machine-grouped events in at most 120 words. "
            "Cite the supplied component/reason and counts. Keep ERROR and STOP visible. "
            "Do not diagnose causes, suggest actions, infer market facts or claim readiness. "
            "This is an untrusted derivative index only.\n" + text
        )
        request = dict(
            model=model,
            prompt=prompt,
            stream=False,
            think=False,
            keep_alive=0,
            options=dict(num_ctx=4096, num_predict=256, temperature=0, seed=7),
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps(request).encode(),
            headers={"Content-Type": "application/json"},
        )
        base = command(["git", "rev-parse", "HEAD"])
        attempt = dict(
            at_ns=time.time_ns(),
            model=model,
            base_commit=base,
            task="bounded evidence annotation",
            node="local",
            ownership=[],
            request_sha256=hashlib.sha256(json.dumps(request).encode()).hexdigest(),
            source_sha256=summary["source_sha256"],
            summary_sha256=sha(job / "summary.json"),
        )
        publish(job / "attempt.json", attempt)
        try:
            with opener.open(req, timeout=180) as response:
                raw = response.read(65537)
        except (OSError, ValueError) as exc:
            publish(job / "failure.json", dict(error=type(exc).__name__, at_ns=time.time_ns()))
            raise
        if len(raw) > 65536:
            raise ValueError("annotation_bound")
        result = json.loads(raw)
        accepted = result.get("done") is True and result.get("done_reason") == "stop"
        receipt = dict(
            model=result.get("model"),
            requested_model=model,
            source_sha256=summary["source_sha256"],
            summary_sha256=sha(job / "summary.json"),
            annotation=result.get("response", ""),
            complete=accepted,
            authority="none",
            review="unreviewed",
            usage={
                k: result.get(k)
                for k in ("prompt_eval_count", "eval_count", "eval_duration", "total_duration")
            },
            billed_tokens=None,
            at_ns=time.time_ns(),
            final_commit=command(["git", "rev-parse", "HEAD"]),
            output_sha256=hashlib.sha256(result.get("response", "").encode()).hexdigest(),
        )
        publish(job / "annotation.json", receipt)
        return dict(status="annotated_unreviewed" if accepted else "incomplete", **receipt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    enq = sub.add_parser("enqueue")
    enq.add_argument("packet", type=Path)
    enq.add_argument("--queue", type=Path, required=True)
    enq.add_argument("--scope", choices=("synthetic", "exploratory"), required=True)
    drain = sub.add_parser("drain")
    drain.add_argument("--queue", type=Path, required=True)
    drain.add_argument("--model", required=True)
    training = sub.add_parser("compute")
    training.add_argument("--queue", type=Path, required=True)
    training.add_argument("argv", nargs=argparse.REMAINDER)
    a = p.parse_args()
    if a.command == "enqueue":
        print(enqueue(a.packet, a.queue, a.scope))
    elif a.command == "drain":
        pending = sorted(plain(a.queue).glob("*/summary.json"))
        for path in pending:
            if not (path.parent / "annotation.json").exists():
                print(json.dumps(annotate(path.parent, a.model), sort_keys=True))
                break
        else:
            print(json.dumps(dict(status="empty")))
    else:
        args = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
        if not args:
            p.error("compute requires a command")
        with resource_lease(a.queue) as fd:
            loaded = ollama_metadata("ps")
            if not loaded["available"] or loaded["models"]:
                raise ValueError("compute_ollama_not_idle")
            return subprocess.run(args, pass_fds=(fd,), check=False).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
