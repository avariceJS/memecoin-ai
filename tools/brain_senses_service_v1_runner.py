#!/usr/bin/env python3
"""Path-only launchd entry point for the finite Senses service v1.

The runner has no credential in its command line or environment contract. It
reconciles the local service ledger before the activation gate or credential
loader can be reached, then reserves at most the current UTC hourly bucket.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.domain import canonical  # noqa: E402
from memecoin_ai.sources import ReadOnlyRPC  # noqa: E402
from tools import brain_senses_block_epoch_v3_schedule as schedule_v3  # noqa: E402
from tools import brain_senses_service_v1 as service  # noqa: E402

SCHEMA_VERSION = 1
SERVICE_LABEL = "ai.memecoin.brain.senses.helius-service-v1"
DEFAULT_PLIST = REPO_ROOT / "ops/launchd/ai.memecoin.brain.senses.helius-service-v1.plist"
BUCKET_NS = 3_600_000_000_000
TICK_MINUTE_START = 5
TICK_MINUTE_END = 15  # inclusive
RUNNER_BINDING = "runner-binding-v1.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _binding(plist_path: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "runner_sha256": _sha(Path(__file__).read_bytes()),
        "plist_sha256": _sha(plist_path.read_bytes()),
        "label": SERVICE_LABEL,
        "policy": "one current UTC bucket; no catch-up; status-only has no side effects",
    }


def _write_binding(root: Path, plist_path: Path) -> dict[str, Any]:
    """Create the immutable runner/plist pin before initializing a service root."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected = _binding(plist_path)
    path = root / RUNNER_BINDING
    if path.exists():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("runner_binding_file_not_private_regular_file")
        if json.loads(path.read_text()) != expected:
            raise ValueError("runner_or_plist_binding_changed")
    else:
        payload = (canonical(expected) + "\n").encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        dfd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    return expected


def _verify_binding(root: Path, plist_path: Path) -> bool:
    path = root / RUNNER_BINDING
    try:
        metadata = path.lstat()
        return (
            stat.S_ISREG(metadata.st_mode)
            and stat.S_IMODE(metadata.st_mode) == 0o600
            and json.loads(path.read_text()) == _binding(plist_path)
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def initialize_service(
    root: Path,
    *,
    plist_path: Path,
    service_id: str,
    authorized_at_ns: int,
    expires_at_ns: int,
    predecessor_stop_path: Path,
    terminal_receipt_path: Path,
) -> dict[str, Any]:
    (root / "supervisor").mkdir(parents=True, exist_ok=True, mode=0o700)
    binding = _write_binding(root, plist_path)
    manifest = service.initialize(
        root,
        service_id=service_id,
        authorized_at_ns=authorized_at_ns,
        expires_at_ns=expires_at_ns,
        predecessor_stop_path=predecessor_stop_path,
        terminal_receipt_path=terminal_receipt_path,
    )
    result = {"status": "initialized_offline", "runner_binding": binding, "service": manifest}
    return result


def _hour_bucket(now: datetime) -> tuple[int, str]:
    now = now.astimezone(UTC)
    hour = now.replace(minute=0, second=0, microsecond=0)
    bucket_ns = int(hour.timestamp() * 1_000_000_000)
    return bucket_ns, hour.strftime("%Y%m%dT%H0000Z")


def _in_tick_window(now: datetime) -> bool:
    current = now.astimezone(UTC)
    return TICK_MINUTE_START <= current.minute <= TICK_MINUTE_END


def _activation_gate(
    gate_path: Path,
    predecessor_stop_path: Path,
    terminal_receipt_path: Path,
    *,
    unloaded_probe: Callable[[str], bool],
) -> tuple[bool, str]:
    """Verify the externally reviewed activation dossier without loading credentials."""
    try:
        proof = service._predecessor_proof(predecessor_stop_path, terminal_receipt_path)
        gate = json.loads(gate_path.read_text())
        stop_hash = _sha(predecessor_stop_path.read_bytes())
        receipt_hash = _sha(terminal_receipt_path.read_bytes())
        approved = (
            isinstance(gate, dict)
            and gate.get("schema_version") == 1
            and gate.get("status") == "activation_approved_after_review"
            and gate.get("predecessor_stop_sha256") == stop_hash
            and gate.get("terminal_receipt_sha256") == receipt_hash
            and gate.get("post_stop_rpc_requests") == 0
            and gate.get("measured_tails_qualified") is True
            and gate.get("account_limit_verified") is True
            and gate.get("independent_review_complete") is True
            and gate.get("reviewer_authorized_activation") is True
        )
        if not approved:
            return False, "activation_gate_incomplete"
        if not unloaded_probe("ai.memecoin.brain.senses.helius-inventory-v3"):
            return False, "predecessor_launchagent_not_proven_unloaded"
        if proof.get("committed_frontier") is None:
            return False, "predecessor_frontier_missing"
        return True, "activation_gate_passed"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False, "activation_gate_evidence_invalid"


def _default_unloaded_probe(label: str) -> bool:
    target = f"gui/{os.getuid()}/{label}"
    result = subprocess.run(
        ["/bin/launchctl", "print", target],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    # launchctl print is successful for loaded services. Any failure is treated
    # as uncertain here; a reviewed gate must prove the service is unloaded.
    return result.returncode != 0 and "Could not find service" in result.stderr


def _load_private_endpoint(env_file: Path | None = None) -> str | None:
    """Read the endpoint only in-process from the private config; never export it."""
    path = env_file or (REPO_ROOT / ".env.local")
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            return None
        for line in path.read_text().splitlines():
            key, sep, raw = line.partition("=")
            if sep and key.strip() == "MEMECOIN_HELIUS_RPC_URL":
                value = raw.strip().strip("\"'")
                parsed = urlsplit(value)
                if (
                    parsed.scheme == "https"
                    and parsed.hostname
                    and (
                        parsed.hostname.endswith(".helius-rpc.com")
                        or parsed.hostname == "rpc.helius.xyz"
                    )
                    and parsed.username is None
                    and parsed.password is None
                ):
                    return value
                return None
    except OSError:
        return None
    return None


def _default_client_loader() -> dict[str, Any] | None:
    endpoint = _load_private_endpoint()
    if endpoint is None:
        return None
    # Endpoint remains in memory only. It is never printed, passed as argv, or
    # installed into the environment inherited by zstd and other child tools.
    client = ReadOnlyRPC(endpoint, "helius-free", timeout=30, max_bytes=32_000_000)
    return {"helius-free": client}


def _reconcile_outstanding(root: Path, at_ns: int) -> dict[str, Any] | None:
    events = service._load_ledger(root)
    reservations = {
        event["reservation_id"]: event for event in events if event["kind"] == "reservation"
    }
    sealed = {event["reservation_id"] for event in events if event["kind"] == "seal"}
    pending = [key for key in reservations if key not in sealed]
    writer_path = root / "writer-active-v2.json"
    writer = service._read(writer_path) if writer_path.is_file() else None
    if not pending and writer is None:
        return None
    if writer is not None:
        writer_id = writer.get("reservation_id")
        if writer_id not in reservations or (pending and pending != [writer_id]):
            raise ValueError("service_writer_reservation_mismatch")
        # This also repairs the append-before-controller/marker crash prefix.
        return service.seal_from_epoch(root, reservation_id=writer_id, at_ns=at_ns)
    if len(pending) != 1:
        raise ValueError("multiple_outstanding_reservations")
    return service.seal_from_epoch(root, reservation_id=pending[0], at_ns=at_ns)


def _bootout_exact_label(
    *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> bool:
    result = runner(
        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_LABEL}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def run_tick(
    root: Path,
    *,
    plist_path: Path = DEFAULT_PLIST,
    gate_path: Path,
    predecessor_stop_path: Path,
    terminal_receipt_path: Path,
    now: datetime | None = None,
    client_loader: Callable[[], dict[str, Any] | None] = _default_client_loader,
    unloaded_probe: Callable[[str], bool] = _default_unloaded_probe,
    bootout: Callable[[], bool] = _bootout_exact_label,
    service_api: Any = service,
    scheduler: Callable[..., dict[str, Any]] = schedule_v3.run_bucket,
) -> dict[str, Any]:
    """Run one reserved current bucket; never replay an old identity or catch up."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root / ".runner-v1.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("service_runner_already_active") from exc

        current = now or datetime.now(UTC)
        at_ns = int(current.timestamp() * 1_000_000_000)
        if (root / "STOP").is_file():
            bootout_ok = bootout()
            return {"status": "service_stopped", "bootout_succeeded": bootout_ok}
        if not _verify_binding(root, plist_path):
            return {"status": "no_rpc", "reason": "runner_binding_missing_or_changed"}

        try:
            status = service_api.read_status(root)
        except (OSError, ValueError, json.JSONDecodeError):
            return {"status": "no_rpc", "reason": "service_reconciliation_failed"}
        if status.get("reconciliation_errors"):
            return {
                "status": "no_rpc",
                "reason": "service_reconciliation_failed",
                "reconciliation_errors": status["reconciliation_errors"],
            }
        if status.get("status") == "stopped":
            bootout_ok = bootout()
            return {"status": "service_stopped", "bootout_succeeded": bootout_ok}

        usage = status.get("usage") or {}
        if usage.get("outstanding", 0) or status.get("writer_pending_reconciliation"):
            try:
                seal = _reconcile_outstanding(root, at_ns)
            except Exception:
                if (root / "STOP").is_file():
                    return {
                        "status": "service_stopped",
                        "bootout_succeeded": bootout(),
                    }
                return {"status": "no_rpc", "reason": "pending_epoch_reconciliation_failed"}
            return {
                "status": "reconciled_no_rpc",
                "outcome": seal.get("outcome") if seal else None,
                "reservation_id": seal.get("reservation_id") if seal else None,
            }

        if not _in_tick_window(current):
            return {
                "status": "no_rpc",
                "reason": "outside_scheduled_tick_window",
                "bucket": _hour_bucket(current)[1],
            }
        bucket_ns, bucket = _hour_bucket(current)
        load_ledger = getattr(service_api, "_load_ledger", None)
        if callable(load_ledger):
            existing_buckets = {
                event.get("bucket_ns")
                for event in load_ledger(root)
                if event.get("kind") == "reservation"
            }
            if bucket_ns in existing_buckets:
                return {
                    "status": "no_rpc",
                    "reason": "bucket_identity_already_spent_or_reserved",
                    "bucket": bucket,
                }
        gate_ok, gate_reason = _activation_gate(
            gate_path,
            predecessor_stop_path,
            terminal_receipt_path,
            unloaded_probe=unloaded_probe,
        )
        if not gate_ok:
            return {"status": "no_rpc", "reason": gate_reason}
        if not status.get("source_bindings_match", False):
            return {"status": "no_rpc", "reason": "service_source_hash_mismatch"}

        clients = client_loader()
        if not clients or "helius-free" not in clients:
            return {"status": "no_rpc", "reason": "provider_credential_unavailable"}

        if now is None:
            refreshed = datetime.now(UTC)
            if not _in_tick_window(refreshed) or _hour_bucket(refreshed)[0] != bucket_ns:
                return {
                    "status": "no_rpc",
                    "reason": "tick_window_elapsed_before_reservation",
                    "bucket": bucket,
                }

        try:
            if status.get("status") == "awaiting_bootstrap":
                reservation = service_api.reserve_bootstrap(root, at_ns=at_ns)
                seal = service_api.run_reserved_bootstrap(
                    root,
                    reservation_id=reservation["reservation_id"],
                    at_ns=at_ns,
                    clients=clients,
                )
                stopped = (root / "STOP").is_file()
                return {
                    "status": "service_stopped" if stopped else "bucket_sealed",
                    "action": "initial_bootstrap",
                    "bucket": bucket,
                    "outcome": seal.get("outcome"),
                    "reservation_id": reservation["reservation_id"],
                    "bootout_succeeded": bootout() if stopped else None,
                }

            reservation = service_api.reserve_bucket(
                root, bucket_ns=bucket_ns, request_time_ns=at_ns
            )
            reservation_id = reservation["reservation_id"]
            if reservation["action"] != "frame":
                seal = service_api.run_reserved_bootstrap(
                    root,
                    reservation_id=reservation_id,
                    at_ns=at_ns,
                    clients=clients,
                )
                stopped = (root / "STOP").is_file()
                return {
                    "status": "service_stopped" if stopped else "bucket_sealed",
                    "action": reservation["action"],
                    "bucket": bucket,
                    "outcome": seal.get("outcome"),
                    "reservation_id": reservation_id,
                    "bootout_succeeded": bootout() if stopped else None,
                }

            service_api.mark_epoch_started(root, reservation_id=reservation_id, at_ns=at_ns)
            campaign_root = root / "campaigns" / reservation["campaign_id"]
            result = scheduler(
                campaign_root,
                lineage_path=campaign_root / "predecessor-lineage-v1.json",
                now=current,
                clients=clients,
            )
            seal = service_api.seal_from_epoch(
                root, reservation_id=reservation_id, at_ns=time.time_ns()
            )
            stopped = (root / "STOP").is_file()
            return {
                "status": "service_stopped" if stopped else "bucket_sealed",
                "action": reservation["action"],
                "bucket": bucket,
                "epoch_status": result.get("status"),
                "outcome": seal.get("outcome"),
                "reservation_id": reservation_id,
                "bootout_succeeded": bootout() if stopped else None,
            }
        except Exception as exc:
            if (root / "STOP").is_file():
                return {"status": "service_stopped", "bootout_succeeded": bootout()}
            return {
                "status": "reconciliation_required",
                "error_class": type(exc).__name__,
                "reservation_id": locals().get("reservation", {}).get("reservation_id"),
            }


def _status(root: Path) -> dict[str, Any]:
    """Read-only status. This path has no credential, gate, or provider dependency."""
    try:
        state = service.read_status(root)
        status = state.get("status")
        binding_match = _verify_binding(root, DEFAULT_PLIST)
        next_action = (
            "review_reconciliation_errors"
            if state.get("reconciliation_errors")
            else "review_runner_or_plist_binding"
            if not binding_match
            else "none_service_stopped"
            if status == "stopped"
            else "reconcile_outstanding_epoch_no_rpc"
            if status == "reconciliation_required"
            else "reserve_initial_bootstrap_after_activation_gate"
            if status == "awaiting_bootstrap"
            else "wait_for_scheduled_tick"
            if status == "policy_ready"
            else "review_service_state"
        )
        return {
            "runner": "service-v1",
            "runner_binding_match": binding_match,
            "next_action": next_action,
            "service": state,
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"runner": "service-v1", "status": "review_required", "error": type(exc).__name__}


def _parse_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return int(parsed.astimezone(UTC).timestamp() * 1_000_000_000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status-only", action="store_true")
    mode.add_argument("--initialize", action="store_true")
    mode.add_argument("--scheduled-tick", action="store_true")
    parser.add_argument("--plist", type=Path, default=DEFAULT_PLIST)
    parser.add_argument("--service-id")
    parser.add_argument("--authorized-at", type=_parse_ns)
    parser.add_argument("--expires-at", type=_parse_ns)
    parser.add_argument("--predecessor-stop", type=Path)
    parser.add_argument("--terminal-receipt", type=Path)
    parser.add_argument("--gate-receipt", type=Path)
    args = parser.parse_args(argv)
    if args.status_only:
        result = _status(args.root)
    elif args.initialize:
        required = (
            args.service_id,
            args.authorized_at,
            args.expires_at,
            args.predecessor_stop,
            args.terminal_receipt,
        )
        if any(value is None for value in required):
            parser.error(
                "--initialize requires service id, authorization bounds, and predecessor paths"
            )
        result = initialize_service(
            args.root,
            plist_path=args.plist,
            service_id=args.service_id,
            authorized_at_ns=args.authorized_at,
            expires_at_ns=args.expires_at,
            predecessor_stop_path=args.predecessor_stop,
            terminal_receipt_path=args.terminal_receipt,
        )
    else:
        if not args.predecessor_stop or not args.terminal_receipt or not args.gate_receipt:
            parser.error("--scheduled-tick requires predecessor and gate receipt paths")
        result = run_tick(
            args.root,
            plist_path=args.plist,
            gate_path=args.gate_receipt,
            predecessor_stop_path=args.predecessor_stop,
            terminal_receipt_path=args.terminal_receipt,
        )
    print(canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
