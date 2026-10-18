"""Filesystem repair for the known 600s source-audit deadline. No LaunchAgents or network.

The continuous journal binding is ``{config_sha, scope: B08_source}``. A new
service prefix changes ``config.json``, so the old event chain cannot be opened
by the source supervisor (``ledger_binding_changed``). The approved repair is a
new genesis that carries the last journal state and the old head. Counters are
not reset, and the old hash chain is not rewritten.
"""

import fcntl
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from memecoin_continuous.hot import authorization
from memecoin_continuous.runtime import guard_source
from memecoin_continuous.source import seed_state
from memecoin_continuous.source import state as journal_state
from memecoin_continuous.store import audit
from memecoin_ml.data import read, sha
from memecoin_paper.audit import replay
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import Ledger, publish

AUDIT_DEADLINE_S = 600.0
DEADLINE_TOLERANCE_S = 5.0
SOURCE_LOCKS = (".source-supervisor.lock", ".audit-supervisor.lock", ".source.lock")
PAPER_LOCKS = (".paper.lock", ".train.lock")
BINDING_FILES = ("config.json", "universe.json", "provider-seed.json", "seed.json")
SOURCE_CONFIG_KEYS = frozenset({"service_prefix", "migration_sha"})
PAPER_CONFIG_KEYS = frozenset(
    {"source", "source_runtime_pin", "service_prefix", "port", "migration_sha"}
)
PRESERVED_SOURCE_KEYS = (
    "expires_ns",
    "max_requests",
    "max_background_requests",
    "hot_interval_s",
    "cold_interval_s",
)
PAPER_DIRS = ("ledger", "training", "candidate-shadows", "candidate-ready", "recovery", "soak")
PAPER_FILES = ("activation.json", "projection.json", "qualification.json")
INDEX_TABLES = (
    (
        "firsts",
        "select pool,open_s,available,pin,payload,checksum from firsts order by pool,open_s",
    ),
    (
        "versions",
        "select pool,open_s,available,pin,payload,checksum from versions order by pool,open_s,pin",
    ),
    (
        "captures",
        "select pin,path,available,status,n,record,checksum from captures order by pin",
    ),
    ("audited", "select pin,at_ns from audited order by pin"),
)
PIN_INDEX = "versions_pin_open"
PIN_QUERY = "select payload,checksum from versions where pin=? order by open_s"


def stage_bundle(root, bundle):
    """Seal a source bundle the same way guard_source checks it, without LaunchAgents."""
    root, bundle = Path(root), Path(bundle)
    if bundle.exists() or bundle.is_symlink():
        raise ValueError("repair_bundle_exists")
    for name in BINDING_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("repair_bundle_files")
    bundle.mkdir()
    src = Path(__file__).resolve().parents[1]
    copied = False
    for path in src.glob("memecoin_*/*.py"):
        dest = bundle / "runtime" / path.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        copied = True
    if not copied:
        raise ValueError("repair_bundle_empty")
    publish(
        bundle / "binding.json",
        dict(
            root=str(root.resolve()),
            python_sha=sha(Path(sys.executable).resolve()),
            files={name: sha(root / name) for name in BINDING_FILES},
        ),
    )
    seal(bundle)
    pin = sha(bundle / "manifest.json")
    publish(root / "deployment.json", dict(bundle=str(bundle.resolve()), pin=pin))
    guard_source(root)
    return pin


def recover_source(old, new, bundle, prefix):
    """Copy an audited source revision. The old STOP, expiry and request budget stay."""
    old, new, bundle = Path(old), Path(new), Path(bundle)
    prefix = _service_prefix(prefix)
    if new.exists() or bundle.exists():
        raise ValueError("repair_destination_exists")
    with _exclusive(old, SOURCE_LOCKS):
        stop_evidence = _require_recoverable_source_stop(old)
        config = guard_source(old)
        if time.time_ns() >= config["expires_ns"]:
            raise ValueError("repair_source_expired")
        for key in PRESERVED_SOURCE_KEYS:
            if type(config.get(key)) is not int:
                raise ValueError("repair_config_incomplete")
        stop_bytes = (old / "STOP").read_bytes()
        last_state, old_head = _last_journal_state(old)
        with _snapshot(old / "index.sqlite") as db:
            index_was_present = PIN_INDEX in _index_names(db)
            before_hash, before_counts = _logical_index(db)
            new.mkdir()
            (new / "requests").mkdir()
            target = sqlite3.connect(new / "index.sqlite")
            try:
                db.backup(target)
            finally:
                target.close()
        plan, timing = _pin_index(new / "index.sqlite")
        after_hash, after_counts = _logical_file(new / "index.sqlite")
        if before_hash != after_hash or before_counts != after_counts:
            raise ValueError("repair_index_membership")
        _copy_file(old / "universe.json", new / "universe.json")
        _copy_file(old / "qualification.json", new / "qualification.json")
        health = old / "health.json"
        if health.is_file() and not health.is_symlink():
            shutil.copyfile(health, new / "health.json")
        if read(new / "qualification.json").get("approved") is not True:
            raise ValueError("repair_qualification_not_approved")
        samples = old / "qualification-samples"
        if samples.exists():
            if samples.is_symlink() or not samples.is_dir():
                raise ValueError("repair_qualification_samples")
            shutil.copytree(samples, new / "qualification-samples")
        cutoff = time.time_ns()
        raw_audit = audit(new, cutoff)
        if raw_audit.get("exact") is not True:
            raise ValueError("repair_audit_not_exact")
        _publish_source_seed(new, old, last_state, old_head)
        receipt = _source_receipt(
            old,
            config,
            old_head,
            last_state,
            before_hash,
            before_counts,
            raw_audit,
            plan,
            timing,
            cutoff,
            prefix,
            stop_evidence,
            index_was_present,
        )
        publish(new / "migration.json", receipt)
        fresh = dict(config)
        fresh["service_prefix"] = prefix
        fresh["migration_sha"] = sha(new / "migration.json")
        _config_drift(config, fresh, SOURCE_CONFIG_KEYS, "repair_source_config_drift")
        publish(new / "config.json", fresh)
        new_head = _genesis(new, last_state, old_head, old)
        if seed_state(new) != last_state or journal_state(new) != last_state:
            raise ValueError("repair_state_not_exact")
        pin = stage_bundle(new, bundle)
        publish(
            new / "activation.json",
            dict(
                config_sha=sha(new / "config.json"),
                runtime_pin=pin,
                qualification_sha=sha(new / "qualification.json"),
                inherited_qualification=True,
                qualification_rerun=False,
                scope="technical_repair",
                repair_sha=sha(new / "migration.json"),
                new_journal_head=new_head,
                old_ledger_head=old_head,
                paid_budget_usd=0,
            ),
        )
        authorization(new, read(new / "config.json"), time.time_ns(), last_state["attempts"])
        guard_source(new)
        if (old / "STOP").read_bytes() != stop_bytes or _logical_file(old / "index.sqlite") != (
            before_hash,
            before_counts,
        ):
            raise ValueError("repair_old_source_mutated")
        if (new / "STOP").exists() or (
            (PIN_INDEX in _index_names_on(old / "index.sqlite")) != index_was_present
        ):
            raise ValueError("repair_old_source_mutated")
    return read(new / "migration.json")


def recover_paper(old, new, source, prefix, port):
    """Point a quiescent paper root at an audited source repair. Does not activate it."""
    old, new, source = Path(old), Path(new), Path(source)
    prefix = _service_prefix(prefix)
    # Port 0 is valid for centrally managed exams that deliberately expose no
    # independent local HTTP server.
    if type(port) is not int or not 0 <= port < 65536:
        raise ValueError("repair_port")
    if new.exists():
        raise ValueError("repair_destination_exists")
    with _exclusive(old, PAPER_LOCKS):
        binding, head, count = _paper_prefix(old)
        _config, pin, _receipt = _audited_source(source)
        new.mkdir()
        for name in PAPER_DIRS:
            path = old / name
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_dir():
                raise ValueError("paper_state_path")
            shutil.copytree(
                path,
                new / name,
            )
        for manifest in (new / "training").glob("*/manifest.json"):
            verify(manifest.parent)
        for name in PAPER_FILES:
            path = old / name
            if path.is_symlink():
                raise ValueError("paper_state_path")
            if path.is_file():
                shutil.copyfile(path, new / name)
        if (old / "TRAIN_STOP").is_file() and not (old / "TRAIN_STOP").is_symlink():
            shutil.copyfile(old / "TRAIN_STOP", new / "TRAIN_STOP")
            incident = read(old / "TRAIN_STOP").get("incident")
            if (
                isinstance(incident, str)
                and Path(incident).name == incident
                and (old / incident).is_file()
                and not (old / incident).is_symlink()
            ):
                shutil.copyfile(old / incident, new / incident)
        for path in sorted(old.glob("*.sqlite")):
            if path.is_file() and not path.is_symlink():
                _backup_readonly(path, new / path.name)
        before = replay(old, head)
        after = replay(new, head)
        if before != after:
            raise ValueError("paper_migration_not_exact")
        stop_path = old / "STOP"
        parent_stop = sha(stop_path) if stop_path.is_file() and not stop_path.is_symlink() else None
        publish(
            new / "migration.json",
            dict(
                parent_root=str(old.resolve()),
                created_ns=time.time_ns(),
                prefix_head=head,
                prefix_events=count,
                prefix_state_sha=before["state_sha"],
                ledger_binding=binding,
                carried=before,
                parent_config_sha=sha(old / "config.json"),
                parent_stop_sha=parent_stop,
                parent_migration_sha=sha(old / "migration.json")
                if (old / "migration.json").is_file()
                else None,
                source_root=str(source.resolve()),
                source_runtime_pin=pin,
                source_repair_sha=sha(source / "migration.json"),
                reason="explicit_source_repair",
                no_cash_reset=True,
                no_synthetic_exit=True,
                automatic_activation=False,
            ),
        )
        fresh = dict(read(old / "config.json"))
        fresh.update(
            source=str(source.resolve()),
            source_runtime_pin=pin,
            service_prefix=prefix,
            port=port,
            migration_sha=sha(new / "migration.json"),
        )
        _config_drift(
            read(old / "config.json"), fresh, PAPER_CONFIG_KEYS, "repair_paper_config_drift"
        )
        publish(new / "config.json", fresh)
        if (new / "STOP").exists() or (stop_path.exists() and parent_stop != sha(stop_path)):
            raise ValueError("repair_paper_stop")
    return before


@contextmanager
def _exclusive(root, names):
    with ExitStack() as stack:
        for name in names:
            handle = stack.enter_context((root / name).open("a"))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("repair_inflight") from exc
        yield


def _service_prefix(prefix):
    if type(prefix) is not str or not prefix or any(char in prefix for char in "/\\\n"):
        raise ValueError("repair_prefix")
    return prefix


def _require_recoverable_source_stop(old):
    stop_path = old / "STOP"
    if stop_path.is_symlink() or not stop_path.is_file():
        raise ValueError("repair_stop_missing")
    stop = read(stop_path)
    if (
        not isinstance(stop, dict)
        or set(stop) != {"incident", "reason"}
        or stop["reason"] != "source_worker_failure"
    ):
        raise ValueError("repair_stop_not_recoverable_worker")
    name = stop.get("incident")
    if (
        type(name) is not str
        or Path(name).name != name
        or not name.startswith("incident-")
        or not name.endswith(".json")
    ):
        raise ValueError("repair_stop_incident")
    found = [
        path.name
        for path in old.iterdir()
        if path.is_file() and path.name.startswith("incident-") and path.suffix == ".json"
    ]
    if found != [name]:
        raise ValueError("repair_competing_incident")
    incident_path = old / name
    if incident_path.is_symlink():
        raise ValueError("repair_stop_incident")
    incident = read(incident_path)
    if not isinstance(incident, dict) or set(incident) != {
        "category",
        "elapsed_seconds",
        "exit_code",
    }:
        raise ValueError("repair_competing_error")
    elapsed = incident["elapsed_seconds"]
    if type(incident["exit_code"]) is not int or incident["exit_code"] != -9:
        raise ValueError("repair_stop_not_recoverable_worker")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        raise ValueError("repair_stop_not_recoverable_worker")
    if incident["category"] == "source_stop_or_deadline":
        if abs(float(elapsed) - AUDIT_DEADLINE_S) > DEADLINE_TOLERANCE_S:
            raise ValueError("repair_stop_not_audit_deadline")
        phase = "audit"
        kind = "source_audit_deadline_repair"
    elif incident["category"] == "clock_jump_or_suspend":
        if not 0 < float(elapsed) <= 240 + DEADLINE_TOLERANCE_S:
            raise ValueError("repair_stop_not_suspend")
        phase = "source"
        kind = "source_suspend_recovery"
    else:
        raise ValueError("repair_stop_not_recoverable_worker")
    token = name.removeprefix("incident-").removesuffix(".json")
    if not token.isdigit():
        raise ValueError("repair_stop_incident")
    incident_ns = int(token)
    starts = []
    for path in old.glob(f"{phase}-worker-*.log"):
        if path.is_symlink() or not path.is_file():
            raise ValueError("repair_stop_not_recoverable_worker")
        stamp = path.name.removeprefix(f"{phase}-worker-").removesuffix(".log")
        if not stamp.isdigit():
            raise ValueError("repair_stop_not_recoverable_worker")
        starts.append(int(stamp))
    prior = [stamp for stamp in starts if stamp <= incident_ns]
    if not prior:
        raise ValueError("repair_stop_not_recoverable_worker")
    start = max(prior)
    delta = (incident_ns - start) / 1_000_000_000
    if phase == "audit":
        if (
            abs(delta - float(elapsed)) > DEADLINE_TOLERANCE_S
            or abs(delta - AUDIT_DEADLINE_S) > DEADLINE_TOLERANCE_S
        ):
            raise ValueError("repair_stop_not_audit_deadline")
        for path in old.glob("audit-*.json"):
            stamp = path.name.removeprefix("audit-").removesuffix(".json")
            if stamp.isdigit() and int(stamp) >= start:
                raise ValueError("repair_competing_error")
    elif delta < float(elapsed) - DEADLINE_TOLERANCE_S:
        # Wall time may advance during sleep while monotonic elapsed time does not.
        raise ValueError("repair_stop_not_suspend")
    health_path = old / "failure-health.json"
    if health_path.exists():
        if health_path.is_symlink() or not health_path.is_file():
            raise ValueError("repair_competing_error")
        health = read(health_path)
        allowed = {"at_ns", "category", "elapsed_seconds", "exit_code"}
        if (
            not isinstance(health, dict)
            or set(health) - allowed
            or health.get("category") != incident["category"]
            or health.get("exit_code") != -9
            or health.get("elapsed_seconds") != elapsed
        ):
            raise ValueError("repair_competing_error")
    return dict(
        kind=kind,
        category=incident["category"],
        incident_ns=incident_ns,
        worker_started_ns=start,
        elapsed_seconds=float(elapsed),
    )


def _last_journal_state(old):
    binding_path = old / "journal" / "binding.json"
    if binding_path.is_symlink() or not binding_path.is_file():
        raise ValueError("repair_journal_binding")
    binding = read(binding_path)
    expected = dict(config_sha=sha(old / "config.json"), scope="B08_source")
    if binding != expected:
        raise ValueError("repair_journal_binding")
    with Ledger(old / "journal", binding) as ledger:
        if not ledger.events:
            raise ValueError("repair_journal_empty")
        payload = ledger.events[-1].get("payload")
        if not isinstance(payload, dict) or "state" not in payload:
            raise ValueError("repair_provider_state")
        state = json.loads(json.dumps(payload["state"]))
        head = ledger.head
    _validate_carried_state(old, state)
    if state["attempts"] != payload["state"]["attempts"]:
        raise ValueError("repair_attempts_reset")
    return state, head


def _validate_carried_state(old, state):
    if not isinstance(state, dict):
        raise ValueError("repair_provider_state")
    for key in ("attempts", "bytes", "cooldown_ns", "failures", "last_ns"):
        if type(state.get(key)) is not int or state[key] < 0:
            raise ValueError("repair_provider_state")
    events = state.get("rate_events")
    if not isinstance(events, list) or any(type(item) is not int or item < 0 for item in events):
        raise ValueError("repair_provider_state")
    pools = state.get("pools")
    names = {row["pool"] for row in read(old / "universe.json")["pools"]}
    if not isinstance(pools, dict) or set(pools) != names:
        raise ValueError("repair_pool_mismatch")
    for quality in pools.values():
        if not isinstance(quality, dict) or quality.get("status") not in ("active", "quarantined"):
            raise ValueError("repair_provider_state")
        cursor = quality.get("cursor")
        if cursor is not None and type(cursor) is not int:
            raise ValueError("repair_provider_state")


def _publish_source_seed(new, old, state, old_head):
    pools = json.loads(json.dumps(state["pools"]))
    quality = {
        pool: dict(
            streak=row.get("streak", 0),
            probes=row.get("probes", 0),
            status=row["status"],
            next_probe_ns=row.get("next_ns", 0),
        )
        for pool, row in pools.items()
    }
    publish(new / "provider-seed.json", json.loads(json.dumps(state)))
    publish(
        new / "seed.json",
        dict(
            quality=quality,
            scheduler_state=pools,
            lineage="technical_repair_genesis",
            parent=str(old.resolve()),
            inherited_journal_head=old_head,
        ),
    )


def _source_receipt(
    old,
    config,
    old_head,
    state,
    index_hash,
    counts,
    raw_audit,
    plan,
    timing,
    cutoff,
    prefix,
    stop_evidence,
    index_was_present,
):
    return dict(
        kind=stop_evidence["kind"],
        stop_evidence=stop_evidence,
        parent_root=str(old.resolve()),
        created_ns=time.time_ns(),
        old_config_sha=sha(old / "config.json"),
        old_stop_sha=sha(old / "STOP"),
        old_ledger_head=old_head,
        last_state_sha=digest(state),
        old_attempts=state["attempts"],
        new_attempts=state["attempts"],
        expires_ns=config["expires_ns"],
        max_requests=config["max_requests"],
        max_background_requests=config["max_background_requests"],
        hot_interval_s=config["hot_interval_s"],
        cold_interval_s=config["cold_interval_s"],
        expiry_unchanged=True,
        budgets_unchanged=True,
        service_prefix=prefix,
        logical_index_sha=index_hash,
        logical_index_counts=counts,
        raw_audit=raw_audit,
        audit_cutoff_ns=cutoff,
        query_plan=plan,
        query_timing_s=timing,
        index_added=not index_was_present,
        index_reused=index_was_present,
        lineage="technical_repair_genesis",
        old_chain_rewritten=False,
        inherited_qualification_sha=sha(old / "qualification.json"),
        qualification_rerun=False,
    )


def _genesis(new, state, old_head, old):
    binding = dict(config_sha=sha(new / "config.json"), scope="B08_source")
    with Ledger(new / "journal", binding) as ledger:
        ledger.append(
            "repair_genesis",
            state=state,
            inherited_head=old_head,
            parent_root=str(old.resolve()),
            lineage="technical_repair_genesis",
        )
        return ledger.head


def _audited_source(source):
    config = guard_source(source)
    deployment = read(source / "deployment.json")
    path = source / "migration.json"
    if path.is_symlink() or not path.is_file() or config.get("migration_sha") != sha(path):
        raise ValueError("paper_source_not_audited_repair")
    receipt = read(path)
    audit_body = receipt.get("raw_audit") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or receipt.get("kind")
        not in {
            "source_audit_deadline_repair",
            "source_suspend_recovery",
        }
        or not isinstance(audit_body, dict)
        or audit_body.get("exact") is not True
        or receipt.get("old_attempts") != receipt.get("new_attempts")
        or config.get("expires_ns") != receipt.get("expires_ns")
        or config.get("max_requests") != receipt.get("max_requests")
        or receipt.get("old_chain_rewritten") is not False
    ):
        raise ValueError("paper_source_not_audited_repair")
    return config, deployment["pin"], receipt


def _paper_prefix(old):
    path = old / "ledger" / "binding.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("paper_ledger_missing")
    binding = read(path)
    with Ledger(old / "ledger", binding) as ledger:
        if not ledger.events:
            raise ValueError("paper_ledger_empty")
        return binding, ledger.head, len(ledger.events)


def _config_drift(old, new, allowed, error):
    changed = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
    if not changed <= allowed:
        raise ValueError(error)


def _copy_file(src, dest):
    if src.is_symlink() or not src.is_file():
        raise ValueError("repair_file_missing")
    shutil.copyfile(src, dest)


def _index_files(root):
    found = {}
    for name in ("index.sqlite", "index.sqlite-wal", "index.sqlite-shm"):
        path = root / name
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("repair_index_sidecar")
            found[name] = path.read_bytes()
    if "index.sqlite" not in found:
        raise ValueError("repair_index_missing")
    return found


def _index_names(db):
    return {row[0] for row in db.execute("select name from sqlite_master where type='index'")}


def _index_names_on(path):
    with _snapshot(path) as db:
        return _index_names(db)


def _logical_file(path):
    with _snapshot(path) as db:
        return _logical_index(db)


def _logical_index(db):
    present = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
    hasher = hashlib.sha256()
    counts = {}
    for name, sql in INDEX_TABLES:
        if name == "audited" and name not in present:
            continue
        if name not in present:
            raise ValueError("repair_index_schema")
        count = 0
        for row in db.execute(sql):
            hasher.update(json.dumps(list(row), separators=(",", ":")).encode())
            hasher.update(b"\n")
            count += 1
        counts[name] = count
    return hasher.hexdigest(), counts


def _pin_index(path):
    db = sqlite3.connect(path)
    try:
        db.execute(f"create index if not exists {PIN_INDEX} on versions(pin, open_s)")
        db.commit()
        sample = db.execute("select pin from versions limit 1").fetchone()
        pin = sample[0] if sample else ""
        plan = [list(row) for row in db.execute("explain query plan " + PIN_QUERY, (pin,))]
        started = time.perf_counter()
        db.execute(PIN_QUERY, (pin,)).fetchall()
        timing = time.perf_counter() - started
    finally:
        db.close()
    if not any(PIN_INDEX in str(item) for row in plan for item in row):
        raise ValueError("repair_query_plan")
    return plan, timing


@contextmanager
def _snapshot(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("repair_index_missing")
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    memory = sqlite3.connect(":memory:")
    try:
        source.execute("PRAGMA query_only=ON")
        source.backup(memory)
    except Exception:
        memory.close()
        raise
    finally:
        source.close()
    try:
        memory.execute("PRAGMA query_only=ON")
        yield memory
    finally:
        memory.close()


def _backup_readonly(src, dest):
    if dest.exists():
        raise ValueError("repair_destination_exists")
    with _snapshot(src) as db:
        target = sqlite3.connect(dest)
        try:
            db.backup(target)
        finally:
            target.close()
