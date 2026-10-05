"""Domain bridge for the operator. No signing, execution network, or PnL training."""

import os
import re
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_paper.runtime import atomic
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish

NS = 10**9
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_MANAGERS = {}
_AWAKE = {}


def config(root):
    c = read(root / "config.json")
    if (root / "deployment.json").exists():
        from .deployment import guard

        guard(root)
    if c.get("real_execution_enabled", False) or c.get("paid_budget_usd", 0) != 0:
        raise ValueError("operator_virtual_only")
    return c


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("operator_invalid_id")
    return value


def source_path(root):
    from .acquisition import current_source

    return current_source(root)


def registry(root, kind):
    folder = root / kind
    return [read(p) for p in sorted(folder.glob("*.json"))]


def registered(root, kind, item):
    p = root / kind / (identifier(item) + ".json")
    r = read(p)
    target = Path(r["artifact"])
    resolved = target.resolve(strict=True)
    if any(p.is_symlink() for p in [target, *target.parents]) or not resolved.is_relative_to(
        root.resolve()
    ):
        raise ValueError("operator_artifact_scope")
    verify(target)
    if sha(target / "manifest.json") != r["manifest_sha"]:
        raise ValueError("operator_artifact_pin")
    return r, target


def artifact_id(output):
    return output.parent.name if output.name == "artifact" else output.name


def register(root, kind, output, value):
    folder = root / kind
    folder.mkdir(exist_ok=True)
    value = dict(
        value,
        id=identifier(artifact_id(output)),
        artifact=str(output.resolve()),
        manifest_sha=sha(output / "manifest.json"),
        created_ns=time.time_ns(),
    )
    publish(folder / (value["id"] + ".json"), value)
    return value


def snapshot(root, output):
    from memecoin_continuous.store import audit, view

    from .learning import prepare

    source = source_path(root)
    cutoff = time.time_ns()
    # SQLite backup defines a consistent physical prefix; receipt cutoff defines logical as-of.
    with sqlite3.connect(f"file:{source / 'index.sqlite'}?mode=ro", uri=True) as src:
        with sqlite3.connect(output / "index.sqlite") as dst:
            src.backup(dst)
    src.close()
    dst.close()
    rows, prov = view(output, cutoff)
    if len(rows) > 510000:
        raise ValueError("operator_snapshot_capacity")
    report = prepare(rows, prov, output)
    report["raw_audit"] = audit(output, cutoff)
    report["source_pin"] = read(source / "deployment.json")["pin"]
    report["source_config_sha"] = sha(source / "config.json")
    publish(output / "result.json", report)
    seal(output)
    entry = register(root, "datasets", output, dict(status="completed", **report))
    return dict(
        snapshot_id=entry["id"], dataset_id=entry["id"], snapshot_hash=entry["dataset_sha"], **entry
    )


def training(root, params, output):
    from .learning import train

    row, dataset = registered(root, "datasets", params.get("snapshot_id"))
    # Independent domain defense even if control-plane scheduling regresses.
    for old in registry(root, "candidates"):
        if old.get("dataset_sha") == row["dataset_sha"]:
            raise ValueError("operator_duplicate_training_snapshot")
    (root / "progress").mkdir(exist_ok=True)
    card = train(dataset, output / "model", root / "progress" / (artifact_id(output) + ".json"))
    result = dict(
        status="completed",
        dataset_id=row["id"],
        dataset_sha=row["dataset_sha"],
        model_sha=sha(output / "model/manifest.json"),
        selected=card["selected"],
        finished_ns=card["finished_ns"],
        recipe=card["recipe"],
        validation=card["validation"],
        test=card["test"],
        support=card["support"],
        promoted=False,
        new_fits=4,
    )
    publish(output / "result.json", result)
    seal(output)
    entry = register(root, "candidates", output, result)
    return dict(candidate_id=entry["id"], **entry)


def paper(root, params, output):
    c = config(root)
    row, artifact = registered(root, "candidates", params.get("candidate_id"))
    model = artifact / "model"
    now = time.time_ns()
    if row["finished_ns"] >= now or sha(model / "manifest.json") != row["model_sha"]:
        raise ValueError("operator_model_not_ready")
    source = source_path(root)
    from memecoin_continuous.runtime import guard_source
    from memecoin_paper.service import exam_models

    sc = guard_source(source)
    if (source / "STOP").exists() or now >= sc["expires_ns"]:
        raise ValueError("operator_source_stopped_or_expired")
    # Separate mutable exam root, immutable model refs + actual future clocks.
    exam_id = artifact_id(output)
    exam = root / "paper" / exam_id
    exam.mkdir(parents=True, exist_ok=False)
    (exam / "training").mkdir()
    expiry = min(now + 86400 * NS, c["expires_ns"], sc["expires_ns"])
    if expiry - now < 1800 * NS:
        raise ValueError("operator_source_expiry_wait_successor")
    ec = dict(
        activation_ns=now,
        exam_start_ns=now,
        expires_ns=expiry,
        champion_model_path=c["champion_model_path"],
        champion_model_sha=c["champion_model_sha"],
        source=str(source),
        source_backend="continuous",
        source_runtime_pin=read(source / "deployment.json")["pin"],
        frozen_exam_models=[dict(path=str(model), sha=row["model_sha"], label=exam_id)],
        training_max_runs=0,
        policy="B07-price-proxy-1",
        real_execution_enabled=False,
        service_prefix="ai.memecoin.brain.operator.exam.",
        port=0,
    )
    exam_models(ec)
    publish(exam / "config.json", ec)
    publish(
        exam / "activation.json",
        dict(
            at_ns=now,
            mode="frozen_future_exam",
            candidate_id=row["id"],
            model_sha=row["model_sha"],
            no_backfill=True,
            promotion=False,
        ),
    )
    stage_exam(exam, root / "runtimes" / exam_id)
    result = dict(
        exam_id=exam_id,
        candidate_id=row["id"],
        model_sha=row["model_sha"],
        activation_ns=now,
        expires_ns=expiry,
        status="active",
        exam_root=str(exam),
    )
    publish(output / "result.json", result)
    seal(output)
    entry = register(root, "exams", output, result)
    return dict(**entry)


def stage_exam(exam, bundle):
    """Stage same paper runtime binding without creating a LaunchAgent for each exam."""
    import shutil

    bundle.mkdir(parents=True, exist_ok=False)
    src = Path(__file__).parent.parent
    for path in src.glob("memecoin_*/*.py"):
        dest = bundle / "runtime" / path.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    publish(
        bundle / "binding.json",
        dict(
            root=str(exam.resolve()),
            config_sha=sha(exam / "config.json"),
            python_sha=sha(Path(sys.executable).resolve()),
        ),
    )
    seal(bundle)
    publish(
        exam / "deployment.json",
        dict(bundle=str(bundle.resolve()), pin=sha(bundle / "manifest.json")),
    )


def run(root, action, params, output):
    root, output = Path(root).resolve(), Path(output).absolute()
    if any(p.is_symlink() for p in [output, *output.parents]):
        raise ValueError("operator_output_scope")
    output = output.resolve()
    c = config(root)
    if time.time_ns() >= c["expires_ns"]:
        raise ValueError("operator_expired")
    if not output.is_relative_to(root) or output.is_symlink():
        raise ValueError("operator_output_scope")
    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists() or (output / "artifact").exists():
        raise ValueError("operator_output_exists")
    output = output / "artifact"
    output.mkdir(exist_ok=False)
    if action == "snapshot":
        return snapshot(root, output)
    if action == "train":
        return training(root, params, output)
    if action == "paper":
        return paper(root, params, output)
    if action == "collect":
        profile = params.get("profile", "balanced")
        if profile not in ("balanced", "history"):
            raise ValueError("operator_collect_profile")
        atomic(root / "collection-profile.json", dict(profile=profile, at_ns=time.time_ns()))
        result = dict(
            profile=profile,
            status="completed",
            source_owned=source_path(root) != Path(c["source_root"]),
        )
        publish(output / "result.json", result)
        seal(output)
        return result
    raise ValueError("operator_unknown_action")


def public_registry(root, kind):
    result = []
    for record in registry(root, kind):
        r = {k: v for k, v in record.items() if k not in ("artifact", "exam_root")}
        if kind == "datasets":
            r.update(dataset_id=r["id"], snapshot_id=r["id"], snapshot_hash=r["dataset_sha"])
        elif kind == "candidates":
            r.update(
                candidate_id=r["id"], snapshot_id=r["dataset_id"], snapshot_hash=r["dataset_sha"]
            )
            r["validation_scores"] = r["validation"]
            r["validation"] = {k: v["pool_macro_pinball"] for k, v in r["validation"].items()}
        elif kind == "exams":
            r.update(exam_id=r["id"], started_ns=r["activation_ns"])
            p = Path(record["exam_root"])
            if (p / "STOP").exists():
                r["status"] = "stopped"
            elif time.time_ns() >= r["expires_ns"]:
                r["status"] = "expired"
        result.append(r)
    return result


def _source_coverage(source, now):
    from .readmodel import coverage

    return coverage(source, now, ttl_s=15)


def stop_reason(root):
    path = root / "STOP"
    if not path.exists():
        return None
    try:
        value = read(path)
        return value.get("reason", "unknown_STOP") if isinstance(value, dict) else "unknown_STOP"
    except (OSError, ValueError):
        return "unknown_STOP"


def inspect(root):
    from .host import snapshot as host_snapshot

    root = Path(root)
    c = config(root)
    now = time.time_ns()
    source = source_path(root)
    health = read(source / "health.json") if (source / "health.json").exists() else {}
    sc = read(source / "config.json")
    coverage = _source_coverage(source, now)
    from .acquisition import successor_pending

    handoff = successor_pending(root, now_ns=now)
    source_view = dict(
        coverage,
        succession_pending=handoff,
        provider="geckoterminal",
        single_provider=True,
        healthy=not (source / "STOP").exists()
        and now < sc["expires_ns"]
        and 0 <= now - health.get("at_ns", 0) < 180 * NS
        and now >= health.get("cooldown_ns", 0),
        stopped=(source / "STOP").exists(),
        expires_ns=sc["expires_ns"],
        heartbeat_ns=health.get("at_ns"),
        heartbeat_age_s=(now - health["at_ns"]) / NS if health.get("at_ns") else None,
        attempts=health.get("attempts", 0),
        budget_used=health.get("attempts", 0),
        budget_max=sc["max_requests"],
        bar_seconds=300,
        cooldown_until_ns=health.get("cooldown_ns", 0),
        max_requests=sc["max_requests"],
        cooldown_ns=health.get("cooldown_ns", 0),
        failures=health.get("failures", 0),
        pools=health.get("pools", {}),
        hot_pools=sc.get("hot_pools", []),
        borrowed=source == Path(c["source_root"]),
        profile_effective="frozen_B09"
        if source == Path(c["source_root"])
        else read(root / "collection-profile.json").get("profile", "balanced")
        if (root / "collection-profile.json").exists()
        else "balanced",
    )
    source_view["freshness_s"] = (
        int(now / NS - coverage["last_s"] - 300) if coverage["last_s"] is not None else None
    )
    source_view["stop_reason"] = stop_reason(source)
    coverage["pools"] = coverage["observed_pools"]
    legacy = []
    for entry in c.get("legacy_papers", []):
        path = Path(entry["root"] if isinstance(entry, dict) else entry)
        from memecoin_paper.health import operational

        label = (
            entry.get("label", path.parent.name) if isinstance(entry, dict) else path.parent.name
        )
        try:
            status = operational(path)
            legacy.append(
                dict(
                    id=label,
                    label=label,
                    port=read(path / "config.json")["port"],
                    healthy=status["paper"]["healthy"],
                    stopped=status["paper"]["stopped"],
                    **status,
                )
            )
        except (OSError, ValueError, KeyError):
            legacy.append(dict(id=label, label=label, status="unavailable", healthy=False))
    exams = public_registry(root, "exams")
    dashboards = []
    for e in registry(root, "exams"):
        p = Path(e["exam_root"])
        d = read(p / "dashboard.json") if (p / "dashboard.json").exists() else {}
        from memecoin_paper.health import operational

        status = operational(p)
        dashboards.append(dict(id=e["id"], candidate_id=e["candidate_id"], **d, operations=status))
    progress = []
    for p in sorted((root / "progress").glob("*.json")):
        progress.append(dict(id=p.stem, **read(p)))
    incidents = []
    for p in sorted((root / "incidents").glob("*.json"))[-100:]:
        incidents.append(dict(id=p.stem, **read(p)))
    recovery = read(root / "legacy-paused.json") if (root / "legacy-paused.json").exists() else {}
    return dict(
        host=host_snapshot(root),
        recovery={
            key: recovery[key]
            for key in ("paused", "resume_attempts", "retry_after_ns", "services", "checked_ns")
            if key in recovery
        },
        source=source_view,
        coverage=coverage,
        datasets=public_registry(root, "datasets"),
        candidates=public_registry(root, "candidates"),
        exams=exams,
        paper=paper_projection(dashboards),
        paper_exams=dashboards,
        legacy=legacy,
        training_progress=progress,
        incidents=incidents,
        policy=dict(
            name="B07 fixed-time price proxy",
            holding_minutes=15,
            learned_exit=False,
            slippage_bps=50,
            fee_bps=25,
            capital_usd=1000,
            automatic_promotion=False,
            champion_model_sha=c["champion_model_sha"],
            champion="empirical",
        ),
        limits=dict(
            expires_ns=c["expires_ns"],
            max_training_runs=c.get("max_training_runs", 6),
            min_refit_interval_s=c.get("min_refit_interval_s", 86400),
            # A STOP belongs to its paper exam. It must remain visible and
            # immutable, but cannot freeze source collection or future exams.
            # A source STOP still fences the whole operator domain.
            domain_stop=source_view["stopped"] and not handoff,
            max_open_exams=8,
            eligible=not source_view["stopped"] and not handoff and now < sc["expires_ns"],
            paid_budget_usd=0,
        ),
    )


def paper_projection(dashboards):
    books = [dict(b, exam_id=d["id"]) for d in dashboards for b in d.get("books", [])]
    positions, trades, decisions = [], [], []
    for b in books:
        metadata = dict(
            candidate_id=b.get("candidate"), model_sha=b.get("model_sha"), exam_id=b["exam_id"]
        )
        begin_positions, begin_trades, begin_decisions = len(positions), len(trades), len(decisions)
        for p in b.get("pending", []):
            positions.append(dict(p, status="pending", **metadata))
        for p in b.get("positions", []):
            positions.append(dict(p, status="open", exit_due_ns=p["exit_due_s"] * NS, **metadata))
            decisions.append(
                dict(
                    ns=p["entry_ns"],
                    action="ENTER",
                    reason="observed_future_fill",
                    stage="fill",
                    filled=True,
                    pool=p["pool"],
                    **metadata,
                )
            )
        for p in b.get("closed", []):
            trades.append(dict(p, **metadata))
            decisions.append(
                dict(
                    ns=p["exit_ns"],
                    action="EXIT",
                    reason="fixed_time_15m",
                    stage="fill",
                    filled=True,
                    pool=p["pool"],
                    **metadata,
                )
            )
        for s in b.get("signals", []):
            decisions.append(
                dict(
                    ns=s["decision_ns"],
                    action="ENTER"
                    if s["reason"] == "enter_next_observed_close"
                    else "HOLD"
                    if s["reason"] == "already_exposed"
                    else "WAIT",
                    stage="intent"
                    if s["reason"] == "enter_next_observed_close"
                    else "existing_or_pending_exposure"
                    if s["reason"] == "already_exposed"
                    else "decision",
                    filled=False,
                    reason=s["reason"],
                    pool=s["pool"],
                    q=s.get("q"),
                    **metadata,
                )
            )
        b["positions"] = positions[begin_positions:]
        b["decisions"] = sorted(decisions[begin_decisions:], key=lambda r: r["ns"], reverse=True)
        b["closed"] = trades[begin_trades:]
        b["open_positions"] = sum(p["status"] == "open" for p in b["positions"])
        b["pending_count"] = sum(p["status"] == "pending" for p in b["positions"])
        b["closed_trades"] = len(b["closed"])
        b["unrealized"] = str(Decimal(b["equity"]) - Decimal("1000") - Decimal(b["realized"]))
    return dict(
        books=books,
        positions=positions,
        trades=trades,
        decisions=sorted(decisions, key=lambda r: r["ns"], reverse=True)[:100],
        cash=sum(float(b["cash"]) for b in books) if books else None,
        equity=sum(float(b["equity"]) for b in books) if books else None,
        realized=sum(float(b["realized"]) for b in books) if books else None,
        unrealized=sum(float(b["equity"]) - 1000 - float(b["realized"]) for b in books)
        if books
        else None,
        fees=sum(float(b["fees"]) for b in books) if books else None,
        independent_books=True,
        capital_per_book=1000,
        forecast_scores=[
            dict(s, exam_id=d["id"]) for d in dashboards for s in d.get("forecast_scores", [])
        ],
        generated_ns=max((d.get("generated_ns", 0) for d in dashboards), default=None),
    )


def manager(root):
    from .background import Manager

    key = str(root.resolve())
    if key not in _MANAGERS:
        _MANAGERS[key] = Manager(root)
    return _MANAGERS[key]


def awake(root, active):
    """Hold an AC-only sleep assertion while the explicitly enabled operator is active."""
    key = str(root.resolve())
    child = _AWAKE.get(key)
    if not active:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        _AWAKE.pop(key, None)
        return
    if sys.platform != "darwin" or not config(root).get("keep_awake_on_ac", False):
        return
    if child is None or child.poll() is not None:
        _AWAKE[key] = subprocess.Popen(
            ["/usr/bin/caffeinate", "-s", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def maintain(root, state):
    from .background import paper_command, source_command
    from .telemetry import record

    root = Path(root)
    now = time.time_ns()
    c = config(root)
    active = state.get("mode") not in ("idle", "paused", "stopped") and now < c["expires_ns"]
    awake(root, active)
    service = manager(root)
    processes = service.poll()
    observed = record(root, inspect(root), state, now_ns=now)
    if not active:
        stopped = service.stop()
        return dict(status="paused", background=processes, stopped=stopped, telemetry=observed)
    legacy_recovery = resume_legacy(root)
    from .acquisition import current_source, ensure_successor

    phase = state.get("phase", "collect")
    priority = phase if phase in {"collect", "snapshot", "train", "paper", "waiting"} else "collect"
    atomic(root / "resource-priority.json", dict(phase=priority, at_ns=now))
    succession = ensure_successor(root, now_ns=now)
    source = current_source(root)
    starts = []
    if source != Path(c["source_root"]) and not (source / "STOP").exists():
        starts.append(service.start("source", source_command(root), 270))
        schedule = root / "audit-schedule.json"
        last = read(schedule)["reserved_ns"] if schedule.exists() else 0
        if now - last >= 3600 * NS:
            atomic(schedule, dict(reserved_ns=now, source=str(source)))
            runtime = Path(read(source / "deployment.json")["bundle"]) / "runtime"
            code = (
                "import os,sys;sys.path.insert(0,sys.argv.pop(1));"
                "from memecoin_operational.worker import lease_watchdog;"
                "lease_watchdog(int(os.environ['MEMECOIN_LEASE_FD']));"
                "from memecoin_continuous.runtime import main;main()"
            )
            starts.append(
                service.start(
                    "source-audit",
                    [sys.executable, "-I", "-B", "-c", code, str(runtime), str(source), "audit"],
                    600,
                )
            )
    for e in registry(root, "exams"):
        exam = Path(e["exam_root"])
        # A bounded 150s child must finish before the immutable exam deadline.
        # Drain the final three minutes instead of turning expected expiry into
        # the frozen paper runtime's combined rollout/worker-deadline STOP.
        if now + 180 * NS >= e["expires_ns"] or (exam / "STOP").exists():
            continue
        runtime = Path(read(exam / "deployment.json")["bundle"]) / "runtime"
        starts.append(service.start("exam-" + e["id"], paper_command(runtime, exam), 150))
    return dict(
        status="running",
        source_succession=succession,
        legacy_recovery=legacy_recovery,
        background=processes,
        starts=starts,
        telemetry=observed,
        training_progress=latest_progress(root),
    )


def latest_progress(root):
    paths = sorted((root / "progress").glob("*.json"))
    if not paths:
        return None
    p = paths[-1]
    info = read(p)
    total = info["completed_fits"] * 1000
    checkpoint_id = info.get("fit")
    if info["status"] == "running" and checkpoint_id:
        model = root / "operator/jobs" / p.stem / "artifact/model" / identifier(checkpoint_id)
        checkpoints = sorted(model.glob("step-*/manifest.json"))
        if checkpoints:
            name = checkpoints[-1].parent.name
            total += int(name.split("-")[1])
            checkpoint_id += "-" + name
    return dict(step=total, steps=4000, checkpoint_id=checkpoint_id)


LEGACY_LABELS = (
    "ai.memecoin.brain.b09.source",
    "ai.memecoin.brain.b09.audit",
    "ai.memecoin.brain.b10.paper",
    "ai.memecoin.brain.b10.train",
    "ai.memecoin.brain.b12.paper",
    "ai.memecoin.brain.b12r1.paper",
    "ai.memecoin.brain.b09r1.source",
    "ai.memecoin.brain.b09r1.audit",
    "ai.memecoin.brain.b10r1.paper",
    "ai.memecoin.brain.b10r1.train",
    "ai.memecoin.brain.b10r2.paper",
    "ai.memecoin.brain.b10r2.train",
    "ai.memecoin.brain.b12r2.paper",
)


def resume_legacy(root):
    marker = root / "legacy-paused.json"
    if not marker.exists() or not read(marker).get("paused"):
        return dict(status="ready")
    previous = read(marker)
    attempts = previous.get("resume_attempts", 0)
    if attempts >= 3:
        return dict(status="blocked", reason="service_resume_failed", attempts=attempts)
    if time.time_ns() < previous.get("retry_after_ns", 0):
        return dict(status="cooldown", reason="service_resume_retry")
    previous.update(resume_attempts=attempts + 1, retry_after_ns=time.time_ns() + 60 * NS)
    atomic(marker, previous)
    results = []
    for label in previous["labels"]:
        if label not in LEGACY_LABELS:
            raise ValueError("operator_service_allowlist")
        plist = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        if not plist.exists():
            results.append(dict(label=label, loaded=False, reason="service_plist_missing"))
            break
        try:
            enabled = subprocess.run(
                ["launchctl", "enable", f"gui/{os.getuid()}/{label}"],
                capture_output=True,
                timeout=2,
            )
            started = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                capture_output=True,
                timeout=2,
            )
            loaded = subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                capture_output=True,
                timeout=2,
            )
            success = enabled.returncode == 0 and loaded.returncode == 0
            results.append(dict(label=label, loaded=success, returncode=started.returncode))
            if not success:
                break
        except subprocess.TimeoutExpired:
            results.append(dict(label=label, loaded=False, reason="service_control_timeout"))
            break
    success = len(results) == len(previous["labels"]) and all(r["loaded"] for r in results)
    folder = root / "incidents"
    folder.mkdir(exist_ok=True)
    publish(
        folder / (str(time.time_ns()) + "-operator-resume-all.json"),
        dict(kind="operator_resume_all", at_ns=time.time_ns(), services=results, success=success),
    )
    atomic(marker, dict(previous, paused=not success, checked_ns=time.time_ns(), services=results))
    return dict(status="ready" if success else "blocked", services=results)


def stop(root, full=False):
    root = Path(root)
    awake(root, False)
    background = manager(root).stop()
    if full:
        # Disable persists across macOS login; Resume re-enables only this exact allowlist.
        managed = config(root).get("managed_legacy_labels", list(LEGACY_LABELS))
        if any(label not in LEGACY_LABELS for label in managed):
            raise ValueError("operator_service_allowlist")
        # A loaded job can outlive its plist. Stop every approved label, even when
        # the file is missing; Resume will report that missing prerequisite.
        labels = list(managed)
        atomic(root / "legacy-paused.json", dict(paused=True, at_ns=time.time_ns(), labels=labels))
        results = []
        for label in labels:
            try:
                disabled = subprocess.run(
                    ["launchctl", "disable", f"gui/{os.getuid()}/{label}"],
                    capture_output=True,
                    timeout=2,
                )
                r = subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                    capture_output=True,
                    timeout=2,
                )
                loaded = subprocess.run(
                    ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                    capture_output=True,
                    timeout=2,
                )
                results.append(
                    dict(
                        label=label,
                        returncode=r.returncode,
                        stopped=disabled.returncode == 0 and loaded.returncode != 0,
                    )
                )
            except subprocess.TimeoutExpired:
                results.append(dict(label=label, stopped=False, reason="service_control_timeout"))
                break
        stopped = len(results) == len(labels) and all(r["stopped"] for r in results)
        folder = root / "incidents"
        folder.mkdir(exist_ok=True)
        publish(
            folder / (str(time.time_ns()) + "-operator-stop-all.json"),
            dict(kind="operator_stop_all", at_ns=time.time_ns(), services=results, stopped=stopped),
        )
        return dict(stopped=stopped, legacy_paused=stopped, services=results, background=background)
    return dict(stopped=True, legacy_paused=False, background=background)
