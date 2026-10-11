"""Fail-closed, finite c5 HTTP runner shell.

Importing this module is inert. It does not create a root, inspect process state,
read private configuration, or contact a provider. Operational execution requires
an independently supplied activation gate; offline plan artifacts cannot open it.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import brain_senses_block_epoch_v3_schedule as schedule_v3  # noqa: E402
from tools import brain_senses_c5_forward_pilot_v1 as policy  # noqa: E402
from tools import brain_senses_service_v1_runner as service_v1_runner  # noqa: E402

SCHEMA = "brain-senses-c5-http-runner-v1"
LEDGER_SCHEMA = 1
C4_TERMINAL_RECEIPT = "docs/evidence/helius-inventory-v3-c4-abnormal-terminal-2026-10-11.json"
C4_QUIESCENCE_RECEIPT = "docs/evidence/helius-c4-launchd-quiescence-2026-10-11.json"
C4_QUIESCENCE_RECEIPT_SHA256 = "3b56f0254b6807f68297796d2d6d7075f37052ab83acd7896c1eb82c087f2422"
C4_INSTALLED_PLIST_SHA256 = "9a42474eb00d7bbc253b3f461185bd77a8658e131c45bc73881faa034d7283e8"
C4_CAMPAIGN_ROOT = (
    "/Users/root1/Developer/memecoin-research/work/brain-senses-live-helius-inventory-v3-20261010-4"
)
C4_LABEL = "ai.memecoin.brain.senses.helius-inventory-v3-20261010-4"
C5_LABEL = "ai.memecoin.brain.senses.c5-forward-pilot-v1"
C5_SERVICE_ROOT = REPO_ROOT / "work/brain-senses-c5-forward-pilot-v1/service"
C5_PLAN_PATH = REPO_ROOT / "work/brain-senses-c5-forward-pilot-v1/plan.json"
C5_GATE_PATH = REPO_ROOT / "work/brain-senses-c5-forward-pilot-v1/activation-gate.json"
C5_PRIVATE_CONFIG_PATH = REPO_ROOT / ".env.local"
KNOWN_DISABLED_SENSES_LABELS = frozenset(
    {
        "ai.memecoin.brain.senses.helius-decode-watch",
        "ai.memecoin.brain.senses.helius-inventory-controller-v1",
        "ai.memecoin.brain.senses.helius-inventory-v3-20261010-1",
        "ai.memecoin.brain.senses.helius-watch-v2",
        "ai.memecoin.brain.senses.helius-inventory-v3-20261010-2",
        "ai.memecoin.brain.senses.block-epochs",
        "ai.memecoin.brain.senses.decode-watch",
        "ai.memecoin.brain.senses.helius-watch-v3",
        "ai.memecoin.brain.senses.helius-inventory-v3-20261010-3",
        "ai.memecoin.brain.senses.helius-watch",
        "ai.memecoin.brain.senses.program-watch",
        "ai.memecoin.brain.senses.independent-verifier-512-pair64",
        "ai.memecoin.brain.senses.independent-verifier-512",
        C4_LABEL,
        "ai.memecoin.brain.senses.program-watch-v3",
    }
)
C5_EXPECTED_ARGV = (
    str(REPO_ROOT / ".venv/bin/python"),
    str(REPO_ROOT / "tools/brain_senses_c5_http_runner_v1.py"),
    "run",
    "--plan",
    str(C5_PLAN_PATH),
    "--gate",
    str(C5_GATE_PATH),
    "--root",
    str(C5_SERVICE_ROOT),
    "--private-config",
    str(C5_PRIVATE_CONFIG_PATH),
)
HOST_WRITER_RELATIVE_PATH = service_v1_runner.HOST_WRITER_LOCK.relative_to(REPO_ROOT)
V3_DISK_ENVELOPE_BYTES = (
    schedule_v3.DISK_FLOOR_BYTES
    + schedule_v3.FRAME_RAW_BYTES
    + schedule_v3.FRAME_STORED_BYTES
    + schedule_v3.PEAK_WRITE_MARGIN_BYTES
)
C5_RECORDED_COMPOSITE_DISK_REQUIRED_BYTES = 61_834_628_800
# These pins stay unset until official raw account/disk source parsers and a
# separately frozen reviewer decision receipt are available. Production runs fail closed.
C5_ACCOUNT_SOURCE_PARSER_SHA256: str | None = None
C5_DISK_SOURCE_PARSER_SHA256: str | None = None
C5_REVIEW_DECISION_SHA256: str | None = None
DISK_REQUIRED_BYTES = max(
    V3_DISK_ENVELOPE_BYTES + policy.BOOTSTRAP_RAW_BYTES + policy.BOOTSTRAP_STORED_BYTES,
    C5_RECORDED_COMPOSITE_DISK_REQUIRED_BYTES,
)
GATE_EVIDENCE_MAX_AGE_NS = 60 * 60 * 1_000_000_000
PLIST_TEMPLATE = "ops/launchd/ai.memecoin.brain.senses.c5-forward-pilot-v1.plist.template"
PINNED_ADAPTERS = (
    "tools/brain_senses_c5_http_runner_v1.py",
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_epoch_v3_schedule.py",
    "tools/brain_senses_block_epoch_v3_launch.py",
    "tools/brain_senses_block_provider_pilot.py",
    "tools/brain_senses_program_watch.py",
    "tools/brain_senses_c5_forward_pilot_v1.py",
    "tools/brain_senses_service_v1_runner.py",
    "src/memecoin_ai/domain.py",
    "src/memecoin_ai/sources.py",
    "src/memecoin_collector/private_config.py",
)
C4_SOURCE_FILES = (
    "ops/launchd/ai.memecoin.brain.senses.helius-inventory-controller.plist",
    "src/memecoin_ai/domain.py",
    "src/memecoin_ai/sources.py",
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_epoch_v3_launch.py",
    "tools/brain_senses_block_epoch_v3_schedule.py",
    "tools/brain_senses_block_provider_pilot.py",
    "tools/brain_senses_campaign_controller.py",
    "tools/brain_senses_program_watch.py",
)
C4_SOURCE_SHA256 = dict(
    zip(
        C4_SOURCE_FILES,
        (
            "694f6853606411b38691f52e72bfd88a6470061482adc5f01f3832db4fa67b93",
            "8123b98462fb822ec6c27d3f7eadbea0a6daf782165e92d369c1a9a77526a1c2",
            "7957340a16896bb9cec6ba744590c1bb75c9d02c21dc3fb1877eb1375297f861",
            "c3d1d0e4d9824ebd6c67c5e292471b5378249e96c26f16c70cd6f4f26ed33555",
            "457f68b68aba1b1b7071bb371306b0548a8929d488890df788b12f664058e0f3",
            "ee4e643ab194d736b5ebfdbf684cbb9caee4e6b78f3673af5bf483be452c2633",
            "c2a56de9e0f3ef740967345908dca2a120022e11434ce9ec4ca8251e095ebe3f",
            "9799323d3de4d1b2c72d40dfb85354697a0c73f89e57fb6228f1d30c97999bc7",
            "1d8fdd1bd055a1b859ff95b5f5082fd533f4b37467c317cc34102d04b0d82917",
        ),
        strict=True,
    )
)


class RunnerError(ValueError):
    """Sanitized fail-closed runner error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.name == "STOP.json":
            try:
                os.link(tmp, path)
            except FileExistsError:
                return
            tmp.unlink()
        else:
            os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError("artifact_unreadable_or_malformed") from exc
    if not isinstance(value, dict):
        raise RunnerError("artifact_shape_invalid")
    return value


def source_bindings(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Hash only explicit c5 source/metadata paths, never c4 capture roots."""
    paths = [
        *PINNED_ADAPTERS,
        *C4_SOURCE_FILES,
        C4_TERMINAL_RECEIPT,
        C4_QUIESCENCE_RECEIPT,
        PLIST_TEMPLATE,
    ]
    result: dict[str, str] = {}
    for rel in paths:
        path = repo_root / rel
        if not path.is_file():
            raise RunnerError("pinned_source_missing")
        result[rel] = _sha_file(path)
    if result[C4_TERMINAL_RECEIPT] != policy.C4_LINEAGE["terminal_receipt_sha256"]:
        raise RunnerError("c4_terminal_receipt_pin_mismatch")
    if result[C4_QUIESCENCE_RECEIPT] != C4_QUIESCENCE_RECEIPT_SHA256:
        raise RunnerError("c4_quiescence_receipt_pin_mismatch")
    if any(result[path] != digest for path, digest in C4_SOURCE_SHA256.items()):
        raise RunnerError("c4_source_or_adapter_pin_mismatch")
    terminal = _read_json(repo_root / C4_TERMINAL_RECEIPT).get("evidence", {})
    if (
        terminal.get("campaign_root") != C4_CAMPAIGN_ROOT
        or terminal.get("campaign_id") != policy.C4_LINEAGE["campaign_id"]
        or terminal.get("launchagent_label") != C4_LABEL
        or terminal.get("stop", {}).get("sha256") != policy.C4_LINEAGE["stop_sha256"]
        or terminal.get("last_committed", {}).get("frontier_slot")
        != policy.C4_LINEAGE["last_committed"]["frontier_slot"]
    ):
        raise RunnerError("c4_terminal_metadata_mismatch")
    quiescence = _read_json(repo_root / C4_QUIESCENCE_RECEIPT)
    if quiescence.get("status") != "all_senses_launchagents_disabled_unloaded":
        raise RunnerError("c4_launchd_quiescence_not_proven")
    if quiescence.get("all_senses_labels_persistently_disabled") is not True:
        raise RunnerError("c4_labels_not_disabled")
    c4_plist = quiescence.get("c4_plist", {})
    if (
        c4_plist.get("label") != C4_LABEL
        or c4_plist.get("plist_sha256") != C4_INSTALLED_PLIST_SHA256
    ):
        raise RunnerError("c4_installed_plist_binding_mismatch")
    return dict(sorted(result.items()))


def build_plan(
    *,
    pilot_id: str,
    service_id: str,
    bootstrap_bucket: str,
    frame_bucket: str,
    authorized_at_ns: int,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Build an offline policy artifact; never creates a service root."""
    plan = policy.build_plan(
        pilot_id=pilot_id,
        service_id=service_id,
        bootstrap_bucket=bootstrap_bucket,
        frame_bucket=frame_bucket,
        authorized_at_ns=authorized_at_ns,
        source_bindings=source_bindings(repo_root),
    )
    plan["external_predecessor_binding"] = {
        "campaign_root": C4_CAMPAIGN_ROOT,
        "campaign_id": policy.C4_LINEAGE["campaign_id"],
        "launchd_label": C4_LABEL,
        "installed_plist_sha256": C4_INSTALLED_PLIST_SHA256,
        "source_manifest_sha256": _sha_bytes(_canonical(C4_SOURCE_SHA256)),
        "service_root": str(C5_SERVICE_ROOT),
    }
    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    plan["plan_sha256"] = _sha_bytes(_canonical(body))
    return plan


def verify_plan(plan: dict[str, Any], repo_root: Path = REPO_ROOT) -> None:
    policy.verify_plan(plan, current_bindings=source_bindings(repo_root))
    if plan.get("activation_gates") != {
        "account_credit_and_overage_verified": False,
        "disk_floor_and_peak_margin_verified": False,
        "activation_authorized": False,
    }:
        raise RunnerError("offline_plan_cannot_authorize_activation")
    if plan.get("external_predecessor_binding") != {
        "campaign_root": C4_CAMPAIGN_ROOT,
        "campaign_id": policy.C4_LINEAGE["campaign_id"],
        "launchd_label": C4_LABEL,
        "installed_plist_sha256": C4_INSTALLED_PLIST_SHA256,
        "source_manifest_sha256": _sha_bytes(_canonical(C4_SOURCE_SHA256)),
        "service_root": str(C5_SERVICE_ROOT),
    }:
        raise RunnerError("external_predecessor_binding_mismatch")


def _read_private_json(path: Path, *, error: str) -> dict[str, Any]:
    try:
        data = _read_private_bytes(path)
        value = json.loads(data)
    except OSError as exc:
        raise RunnerError(error) from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError(error) from exc
    if not isinstance(value, dict):
        raise RunnerError(error)
    return value


def _read_private_bytes(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise RunnerError("private_receipt_file_not_owned_regular_0600")
        chunks = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def create_activation_receipt(path: Path, receipt: dict[str, Any]) -> None:
    """Create a private activation receipt once; never replace an existing decision."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise RunnerError("activation_receipt_already_exists") from exc
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical(receipt) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_dir(path.parent)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise RunnerError("activation_receipt_create_failed") from None


def _bound_evidence(ref: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise RunnerError("activation_gate_evidence_binding_missing")
    path_value, expected_sha = ref.get("path"), ref.get("sha256")
    if (
        not isinstance(path_value, str)
        or not path_value
        or not isinstance(expected_sha, str)
        or len(expected_sha) != 64
        or any(char not in "0123456789abcdef" for char in expected_sha)
    ):
        raise RunnerError("activation_gate_evidence_binding_missing")
    path = Path(path_value)
    try:
        data = _read_private_bytes(path)
    except OSError as exc:
        raise RunnerError("activation_gate_evidence_missing") from exc
    except RunnerError as exc:
        raise RunnerError("activation_gate_evidence_invalid") from exc
    actual_sha = _sha_bytes(data)
    if actual_sha != expected_sha:
        raise RunnerError("activation_gate_evidence_hash_mismatch")
    try:
        value = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError("activation_gate_evidence_invalid") from exc
    if not isinstance(value, dict):
        raise RunnerError("activation_gate_evidence_invalid")
    return value


def _verify_activation_gate_evidence_predicates(
    path: Path, plan: dict[str, Any], *, now_ns: int
) -> int:
    """Check local fixture predicates; not sufficient evidence for production activation."""
    gate = _read_private_json(path, error="activation_gate_missing_or_mismatched")
    schedule = plan["schedule"]
    frame_expiry_ns = int(
        (policy._utc_bucket(schedule["frame_bucket"]).timestamp() + 16 * 60) * 1_000_000_000
    )
    required = {
        "schema": "c5-independent-activation-gate-v1",
        "status": "activation_approved_after_review",
        "plan_sha256": plan.get("plan_sha256"),
        "source_bindings": plan.get("source_bindings"),
        "plist_sha256": plan.get("source_bindings", {}).get(PLIST_TEMPLATE),
        "service_root": plan.get("external_predecessor_binding", {}).get("service_root"),
        "allowed_buckets": [schedule["bootstrap_bucket"], schedule["frame_bucket"]],
        "expires_at_ns": frame_expiry_ns,
        "predecessor": {
            "terminal_receipt_sha256": policy.C4_LINEAGE["terminal_receipt_sha256"],
            "campaign_root": C4_CAMPAIGN_ROOT,
            "stop_sha256": policy.C4_LINEAGE["stop_sha256"],
            "launchagent_label": C4_LABEL,
        },
        "limits": plan["limits"],
        "execution_controls": {
            "no_retry": True,
            "no_backfill": True,
            "no_replacement_bucket": True,
            "no_manual_tick": True,
            "no_third_bucket": True,
            "max_requests": policy.MAX_REQUESTS,
        },
    }
    if any(gate.get(key) != value for key, value in required.items()):
        raise RunnerError("activation_gate_missing_or_mismatched")
    reviewer = gate.get("reviewer")
    decision = gate.get("decision")
    decision_ns = gate.get("decision_time_ns")
    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or decision != "approve_c5_two_bucket_pilot"
        or type(decision_ns) is not int
        or decision_ns <= 0
        or decision_ns < plan["authorized_at_ns"]
        or decision_ns > now_ns
        or now_ns > frame_expiry_ns
        or decision_ns >= int(policy._utc_bucket(schedule["bootstrap_bucket"]).timestamp() * 1e9)
    ):
        raise RunnerError("activation_gate_decision_or_expiry_invalid")
    review = _bound_evidence(gate.get("review_evidence"), label="review")
    account = _bound_evidence(gate.get("account_evidence"), label="account")
    disk = _bound_evidence(gate.get("disk_evidence"), label="disk")
    churn = _bound_evidence(gate.get("host_churn_evidence"), label="host_churn")
    if (
        review.get("schema") != "c5-review-evidence-v1"
        or review.get("reviewer") != reviewer
        or review.get("decision") != decision
        or review.get("plan_sha256") != plan["plan_sha256"]
        or review.get("decision_time_ns") != decision_ns
        or review.get("reviewed_evidence_sha256s")
        != {
            "account": gate["account_evidence"]["sha256"],
            "disk": gate["disk_evidence"]["sha256"],
            "host_churn": gate["host_churn_evidence"]["sha256"],
        }
    ):
        raise RunnerError("activation_gate_review_evidence_invalid")
    if (
        account.get("schema") != "c5-account-budget-evidence-v1"
        or account.get("status") != "verified"
        or account.get("single_observation_no_retry") is not True
        or type(account.get("observed_at_ns")) is not int
        or account["observed_at_ns"] > decision_ns
        or decision_ns - account["observed_at_ns"] > GATE_EVIDENCE_MAX_AGE_NS
        or type(account.get("valid_until_ns")) is not int
        or account["valid_until_ns"] < frame_expiry_ns
        or account["valid_until_ns"] < now_ns
        or now_ns - account["observed_at_ns"] > 2 * GATE_EVIDENCE_MAX_AGE_NS
        or not (
            type(account.get("remaining_request_headroom")) is int
            and account["remaining_request_headroom"] >= policy.MAX_REQUESTS
            or account.get("provider_enforced_no_overage") is True
        )
    ):
        raise RunnerError("activation_gate_account_predicate_failed")
    if (
        disk.get("schema") != "c5-disk-budget-evidence-v1"
        or disk.get("status") != "verified"
        or disk.get("disk_floor_bytes") != schedule_v3.DISK_FLOOR_BYTES
        or disk.get("full_frame_peak_bytes")
        != schedule_v3.FRAME_RAW_BYTES
        + schedule_v3.FRAME_STORED_BYTES
        + schedule_v3.PEAK_WRITE_MARGIN_BYTES
        or type(disk.get("remaining_pilot_physical_stored_allowance_bytes")) is not int
        or disk["remaining_pilot_physical_stored_allowance_bytes"]
        < policy.BOOTSTRAP_STORED_BYTES + policy.FRAME_STORED_BYTES
        or type(disk.get("adverse_host_churn_margin_bytes")) is not int
        or disk["adverse_host_churn_margin_bytes"] <= 0
        or type(disk.get("measured_free_bytes")) is not int
        or type(disk.get("measured_at_ns")) is not int
        or disk["measured_at_ns"] > decision_ns
        or decision_ns - disk["measured_at_ns"] > GATE_EVIDENCE_MAX_AGE_NS
        or type(disk.get("valid_until_ns")) is not int
        or disk["valid_until_ns"] < frame_expiry_ns
        or disk["valid_until_ns"] < now_ns
        or now_ns - disk["measured_at_ns"] > 2 * GATE_EVIDENCE_MAX_AGE_NS
    ):
        raise RunnerError("activation_gate_disk_predicate_failed")
    if (
        churn.get("schema") != "c5-host-churn-review-v1"
        or churn.get("status") != "reviewed_and_substantiated"
        or churn.get("reviewer") != reviewer
        or churn.get("decision_time_ns") != decision_ns
        or churn.get("margin_bytes") != disk.get("adverse_host_churn_margin_bytes")
        or not isinstance(churn.get("method"), str)
        or not churn["method"].strip()
        or not isinstance(churn.get("basis"), str)
        or not churn["basis"].strip()
    ):
        raise RunnerError("activation_gate_churn_margin_unsubstantiated")
    dynamic_required = max(
        C5_RECORDED_COMPOSITE_DISK_REQUIRED_BYTES,
        disk["disk_floor_bytes"]
        + disk["full_frame_peak_bytes"]
        + disk["remaining_pilot_physical_stored_allowance_bytes"]
        + disk["adverse_host_churn_margin_bytes"],
    )
    if (
        disk.get("computed_required_free_bytes") != dynamic_required
        or disk["measured_free_bytes"] < dynamic_required
    ):
        raise RunnerError("activation_gate_disk_headroom_insufficient")
    return dynamic_required


def verify_activation_gate(path: Path, plan: dict[str, Any], *, now_ns: int) -> int:
    """Production gate; local summaries cannot establish account, disk, or reviewer facts."""
    _verify_activation_gate_evidence_predicates(path, plan, now_ns=now_ns)
    if not all(
        (
            C5_ACCOUNT_SOURCE_PARSER_SHA256,
            C5_DISK_SOURCE_PARSER_SHA256,
            C5_REVIEW_DECISION_SHA256,
        )
    ):
        raise RunnerError("activation_gate_source_provenance_unavailable")
    # Deliberately fail closed until the corresponding pinned source parsers are installed.
    # This branch is unreachable in the current implementation because the parser pins above
    # are unset; it prevents a self-authored evidence summary from authorizing a request.
    raise RunnerError("activation_gate_source_parser_not_implemented")


def _append_ledger(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    rows = _read_ledger(path)
    previous = rows[-1]["row_sha256"] if rows else "0" * 64
    body = {"sequence": len(rows) + 1, "previous_sha256": previous, **event}
    row = {**body, "row_sha256": _sha_bytes(_canonical(body))}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, "ab") as stream:
            stream.write(_canonical(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_dir(path.parent)
    except Exception:
        raise
    return row


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_bytes().splitlines()
        rows = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError("request_ledger_corrupt") from exc
    previous = "0" * 64
    for i, row in enumerate(rows, start=1):
        if (
            not isinstance(row, dict)
            or row.get("sequence") != i
            or row.get("previous_sha256") != previous
        ):
            raise RunnerError("request_ledger_chain_invalid")
        body = {k: v for k, v in row.items() if k != "row_sha256"}
        digest = _sha_bytes(_canonical(body))
        if row.get("row_sha256") != digest:
            raise RunnerError("request_ledger_hash_invalid")
        previous = digest
    return rows


def _reconcile_runner_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    """Prove that state and the external ledger permit exactly one next phase."""
    phase = state.get("phase")
    if rows and rows[-1].get("kind") == "reservation":
        return "interrupted_reserved_attempt"
    if phase == "awaiting_bootstrap":
        return None if not rows else "state_ledger_bootstrap_mismatch"
    if phase == "awaiting_frame":
        if len(rows) != 2:
            return "state_ledger_frame_mismatch"
        first, second = rows
        if (
            first.get("kind") != "reservation"
            or first.get("phase") != "awaiting_bootstrap"
            or first.get("request_count") != 1
            or second.get("kind") != "seal"
            or second.get("phase") != "bootstrap"
            or second.get("reservation_sha256") != first.get("row_sha256")
        ):
            return "state_ledger_bootstrap_seal_mismatch"
        return None
    return "runner_state_not_actionable"


def _controller_lease_expiry_ns(plan: dict[str, Any]) -> int:
    frame_tick_start = policy._utc_bucket(plan["schedule"]["frame_bucket"])
    return int((frame_tick_start + timedelta(minutes=16)).timestamp() * 1_000_000_000)


def _require_current_tick(bucket: str, wall_clock: Callable[[], datetime]) -> datetime:
    current = wall_clock()
    if (
        current.tzinfo is None
        or current.utcoffset() != UTC.utcoffset(current)
        or current.strftime("%Y%m%dT%H0000Z") != bucket
        or not 5 <= current.minute <= 15
    ):
        raise RunnerError("tick_window_expired_before_dispatch")
    return current


def _validate_bootstrap_lineage(lineage: dict[str, Any], plan: dict[str, Any]) -> None:
    if (
        lineage.get("service_plan_sha256") != plan.get("plan_sha256")
        or type(lineage.get("controller_lease_expires_ns")) is not int
        or lineage["controller_lease_expires_ns"] != _controller_lease_expiry_ns(plan)
    ):
        raise RunnerError("bootstrap_lineage_plan_or_lease_mismatch")


def _stop_if_planned_tick_expired(
    *, plan: dict[str, Any], root: Path, now: datetime, lock_root: Path
) -> bool:
    """Seal an existing campaign when its remaining pinned tick has expired."""
    if not root.exists():
        return False
    try:
        host_lock = _host_writer_lock_path(lock_root)
        with _exclusive_lock(host_lock), _exclusive_lock(root.parent / f".{root.name}.writer.lock"):
            if (root / "STOP.json").exists():
                return True
            try:
                manifest = _read_json(root / "manifest.json")
                state = _read_json(root / "state.json")
            except RunnerError:
                _atomic_json(
                    root / "STOP.json",
                    {
                        "reason": "missed_tick_state_uncertain",
                        "gap": _unknown_ended_gap({}, "missed_tick_state_uncertain"),
                    },
                )
                return True
            if manifest.get("plan_sha256") != plan.get("plan_sha256"):
                return False
            expected_bucket = (
                plan["schedule"]["bootstrap_bucket"]
                if state.get("phase") == "awaiting_bootstrap"
                else plan["schedule"]["frame_bucket"]
            )
            scheduled = policy._utc_bucket(expected_bucket)
            deadline = scheduled.replace(minute=policy.SCHEDULED_TICK_MINUTE_MAX, second=59)
            if now <= deadline:
                return False
            try:
                rows = _read_ledger(root / "request-ledger.jsonl")
            except RunnerError:
                _atomic_json(
                    root / "STOP.json",
                    {
                        "reason": "missed_tick_journal_uncertain",
                        "gap": _unknown_ended_gap(state, "missed_tick_journal_uncertain"),
                    },
                )
                return True
            failure = _reconcile_runner_state(state, rows)
            gap = {
                "start": state.get(
                    "frontier_slot", policy.C4_LINEAGE["unknown_ended_gap"]["start"]
                ),
                "end_exclusive": None,
                "reason": "missed_pre_pinned_bucket_no_replay",
            }
            _atomic_json(
                root / "STOP.json",
                {
                    "reason": failure or "missed_pre_pinned_bucket",
                    "expected_bucket": expected_bucket,
                    "decision_time_ns": int(now.timestamp() * 1_000_000_000),
                    "gap": gap,
                },
            )
            return True
    except RunnerError:
        # A writer-lock conflict is never permission to mutate state without the lock.
        return True


@contextmanager
def _exclusive_lock(path: Path):
    """0600 no-follow lock; one service writer spans reserve through seal."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            raise RunnerError("writer_lock_permissions_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunnerError("writer_already_active") from exc
        yield
    finally:
        os.close(fd)


def _host_writer_lock_path(lock_root: Path) -> Path:
    """Map test roots to the same relative lock used by the v1 service writer."""
    return lock_root / HOST_WRITER_RELATIVE_PATH


def status_only(plan_path: Path, root: Path, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Read-only status. Missing roots remain missing."""
    plan = _read_json(plan_path)
    verify_plan(plan, repo_root)
    if not root.exists():
        return {
            "status": "not_initialized",
            "plan_sha256": plan["plan_sha256"],
            "root_exists": False,
        }
    manifest = _read_json(root / "manifest.json")
    if manifest.get("plan_sha256") != plan["plan_sha256"]:
        raise RunnerError("service_manifest_plan_mismatch")
    rows = _read_ledger(root / "request-ledger.jsonl")
    stop = _read_json(root / "STOP.json") if (root / "STOP.json").exists() else None
    state = _read_json(root / "state.json") if (root / "state.json").exists() else None
    reservations = [row for row in rows if row.get("kind") == "reservation"]
    seals = [row for row in rows if row.get("kind") == "seal"]
    request_count = sum(int(row.get("request_count", 0)) for row in reservations)
    raw_bytes = stored_bytes = 0
    for row in seals:
        result = row.get("result", {})
        if isinstance(result, dict):
            raw_bytes += int(result.get("raw_bytes", 0) or 0)
            stored_bytes += int(result.get("stored_bytes", 0) or 0)
    open_gap = (stop or {}).get("gap")
    if open_gap is None and state and state.get("phase") == "awaiting_bootstrap":
        open_gap = {
            "start": policy.C4_LINEAGE["unknown_ended_gap"]["start"],
            "end_exclusive": None,
            "reason": "awaiting_future_finalized_anchor",
        }
    return {
        "status": "stopped" if stop else "active_or_incomplete",
        "plan_sha256": plan["plan_sha256"],
        "root_exists": True,
        "request_rows": len(rows),
        "reserved_requests": request_count,
        "raw_bytes": raw_bytes,
        "stored_bytes": stored_bytes,
        "phase": state.get("phase") if state else "unknown",
        "gaps": open_gap,
        "last_row_sha256": rows[-1]["row_sha256"] if rows else None,
        "stop": stop,
        "next_action": (
            "terminal"
            if stop
            else "frame_bucket_only"
            if state and state.get("phase") == "awaiting_frame"
            else "reconcile_before_any_action"
        ),
    }


def _preflight_processes(
    expected_labels: set[str], inventory: Callable[[], list[dict[str, Any]]]
) -> None:
    """Require exact GUI label state and a launchd-owned path-only c5 process."""
    observed = inventory()
    if not isinstance(observed, list) or any(not isinstance(row, dict) for row in observed):
        raise RunnerError("senses_runtime_inventory_malformed")
    namespaces = [row for row in observed if row.get("kind") == "namespace"]
    loaded = [row for row in observed if row.get("kind") == "launchd"]
    disabled = [row for row in observed if row.get("kind") == "disabled"]
    processes = [row for row in observed if row.get("kind") == "process"]
    if any(
        row.get("kind") not in {"namespace", "launchd", "disabled", "process"} for row in observed
    ):
        raise RunnerError("senses_runtime_inventory_malformed")
    disabled_map = {row.get("label"): row.get("disabled") for row in disabled}
    loaded_map = {row.get("label"): row.get("pid") for row in loaded}
    process = processes[0] if len(processes) == 1 else {}
    if (
        namespaces != [{"kind": "namespace", "namespace": f"gui/{os.getuid()}"}]
        or len(loaded_map) != len(loaded)
        or set(loaded_map) != expected_labels
        or C4_LABEL in loaded_map
        or len(disabled_map) != len(disabled)
        or not set(disabled_map).issubset(KNOWN_DISABLED_SENSES_LABELS | {C5_LABEL})
        or any(value is not True for label, value in disabled_map.items() if label != C5_LABEL)
        or disabled_map.get(C4_LABEL) is not True
        or disabled_map.get(C5_LABEL, False) is not False
        or len(processes) != 1
        or process.get("label") != C5_LABEL
        or type(process.get("pid")) is not int
        or process.get("pid", 0) <= 0
        or loaded_map.get(C5_LABEL) != process.get("pid")
        or process.get("ppid") != 1
        or process.get("runner_path") != str(REPO_ROOT / "tools/brain_senses_c5_http_runner_v1.py")
        or process.get("argv_matches_expected") is not True
    ):
        raise RunnerError("senses_runtime_preflight_mismatch")


_LAUNCHD_SERVICE_ROW = re.compile(r"^\s*(\d+)\s+(?:\(([^)]*)\)|(-|[-+]?\d+))\s+(\S+)\s*$")
_DISABLED_SERVICE_ROW = re.compile(r'^\s*"([^"]+)"\s*=>\s*(disabled|enabled)\s*,?\s*$')


def _host_process_inventory() -> list[dict[str, Any]]:
    """Read exact GUI services and wide process metadata without returning argv."""
    try:
        launchd = subprocess.run(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        ps = subprocess.run(
            ["/bin/ps", "-ww", "-axo", "pid=,ppid=,command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        disabled_output = subprocess.run(
            ["/bin/launchctl", "print-disabled", f"gui/{os.getuid()}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerError("senses_runtime_inventory_unavailable") from exc
    if not all(isinstance(value, str) for value in (launchd, ps, disabled_output)):
        raise RunnerError("senses_runtime_inventory_malformed")
    launch_lines = launchd.splitlines()
    if not launch_lines or launch_lines[0].strip() != f"gui/{os.getuid()} = {{":
        raise RunnerError("senses_runtime_inventory_malformed")
    service_count_rows = [
        line for line in launch_lines if re.fullmatch(r"\s*service count = \d+\s*", line)
    ]
    service_headers = [
        index for index, line in enumerate(launch_lines) if line.strip() == "services = {"
    ]
    if len(service_count_rows) != 1 or len(service_headers) != 1:
        raise RunnerError("senses_runtime_inventory_malformed")
    declared_count = int(re.search(r"\d+", service_count_rows[0]).group(0))
    services = []
    for line in launch_lines[service_headers[0] + 1 :]:
        if line.strip() == "}":
            break
        if not line.strip():
            continue
        match = _LAUNCHD_SERVICE_ROW.fullmatch(line)
        if match is None:
            raise RunnerError("senses_runtime_inventory_malformed")
        pid_text, _status, _placeholder, label = match.groups()
        services.append((int(pid_text), label))
    if len(services) != declared_count or len({label for _, label in services}) != len(services):
        raise RunnerError("senses_runtime_inventory_malformed")
    records = [{"kind": "namespace", "namespace": f"gui/{os.getuid()}"}]
    records.extend(
        {"kind": "launchd", "label": label, "pid": pid if pid > 0 else None}
        for pid, label in services
        if label.startswith("ai.memecoin.brain.senses.")
    )
    disabled_lines = disabled_output.splitlines()
    body_start = next((index for index, line in enumerate(disabled_lines) if line.strip()), None)
    if (
        body_start is None
        or disabled_lines[body_start].strip() != "disabled services = {"
        or disabled_lines[-1].strip() != "}"
    ):
        raise RunnerError("senses_runtime_inventory_malformed")
    seen_disabled = set()
    for line in disabled_lines[body_start + 1 : -1]:
        if "ai.memecoin.brain.senses." not in line:
            continue
        match = _DISABLED_SERVICE_ROW.fullmatch(line)
        if match is None:
            raise RunnerError("senses_runtime_inventory_malformed")
        label, value = match.groups()
        if label in seen_disabled:
            raise RunnerError("senses_runtime_inventory_malformed")
        seen_disabled.add(label)
        records.append({"kind": "disabled", "label": label, "disabled": value == "disabled"})
    for line in ps.splitlines():
        parts = line.strip().split(None, 2)
        if not line.strip():
            continue
        if len(parts) != 3:
            raise RunnerError("senses_runtime_inventory_malformed")
        lowered_command = parts[2].lower()
        if "senses" in lowered_command or "brain_senses_" in lowered_command:
            pid, ppid, command = parts
            try:
                argv = tuple(shlex.split(command))
                pid_value, ppid_value = int(pid), int(ppid)
            except (ValueError, shlex.ValueError) as exc:
                raise RunnerError("senses_runtime_inventory_malformed") from exc
            expected_argv = C5_EXPECTED_ARGV
            is_runner = str(REPO_ROOT / "tools/brain_senses_c5_http_runner_v1.py") in argv
            records.append(
                {
                    "kind": "process",
                    "label": C5_LABEL if is_runner else "unclassified_senses_process",
                    "pid": pid_value,
                    "ppid": ppid_value,
                    "runner_path": str(REPO_ROOT / "tools/brain_senses_c5_http_runner_v1.py")
                    if is_runner
                    else "unknown",
                    "argv_matches_expected": is_runner and argv == expected_argv,
                }
            )
    return records


def _load_endpoint(config_path: Path) -> str:
    """Private config access is lazy and occurs only after offline run preflight."""
    endpoint = service_v1_runner._load_private_endpoint(config_path)
    if endpoint is None:
        raise RunnerError("private_endpoint_config_invalid")
    return endpoint


def _unknown_ended_gap(state: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "start": state.get("frontier_slot", policy.C4_LINEAGE["unknown_ended_gap"]["start"]),
        "end_exclusive": None,
        "reason": reason,
    }


def _recheck_process_inventory(
    inventory: Callable[[], list[dict[str, Any]]], *, root: Path, state: dict[str, Any]
) -> None:
    try:
        _preflight_processes({C5_LABEL}, inventory)
    except RunnerError:
        _atomic_json(
            root / "STOP.json",
            {
                "reason": "senses_runtime_preflight_changed_after_reconciliation",
                "gap": _unknown_ended_gap(state, "runtime_preflight_blocked_dispatch"),
            },
        )
        raise


def _verify_disk_headroom(path: Path, required_bytes: int = DISK_REQUIRED_BYTES) -> int:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    free = shutil.disk_usage(candidate).free
    if free < required_bytes:
        raise RunnerError("disk_floor_plus_full_pilot_envelope_not_met")
    return free


def _default_bootstrap_adapter(
    *, root: Path, bucket: str, endpoint: str, schedule_sha256: str
) -> dict[str, Any]:
    """Future adapter: one v2 bootstrap getSlot, with v2 reservation/no-replay journal."""
    from tools import brain_senses_block_epoch_v2 as v2

    epoch_id = f"c5-bootstrap-{bucket}"
    plan = v2.build_epoch_plan(
        epoch_id=epoch_id,
        bucket=bucket,
        schedule_id="c5-bootstrap-v1",
        schedule_sha256=schedule_sha256,
        seed="c5-bootstrap",
        bootstrap=True,
        next_start=None,
        frame_width_cap=1,
        selection_count=0,
        request_budget=1,
        raw_byte_budget=1_000_000,
        stored_byte_budget=1_000_000,
        minimum_free_disk_bytes=50 * 1024**3,
        capture_storage_codec="zstd",
        max_response_bytes=1_000_000,
    )
    # This call is reachable only in a future authorized tick; tests use injected fakes.
    epoch_root = root / epoch_id
    bootstrap_client = v2.ReadOnlyRPC(
        endpoint,
        "helius-free",
        timeout=45,
        max_bytes=1_000_000,
        keep_alive=True,
    )
    try:
        summary = v2.run_epoch(epoch_root, plan, clients={"helius-free": bootstrap_client})
    finally:
        bootstrap_client.close()
    rows = v2._validate_journal(epoch_root, plan)
    if summary.get("status") != "bootstrap_complete" or len(rows) != 1:
        raise RunnerError("v2_bootstrap_not_cleanly_sealed")
    row = rows[0]
    reservation_path = epoch_root / "requests" / "000000000001.json"
    receipt_path = epoch_root / row["capture_path"] / "receipt.json"
    result_value = v2._capture_result(epoch_root, row)
    return {
        "status": summary["status"],
        "anchor_slot": summary.get("bootstrap_slot"),
        "commitment": "finalized",
        "raw_bytes": row.get("response_bytes"),
        "stored_bytes": row.get("stored_response_bytes"),
        "receipt_ns": row.get("receipt_ns"),
        "availability_ns": row.get("availability_ns"),
        "request_sha256": _read_json(reservation_path).get("request_sha256"),
        "response_sha256": row.get("response_sha256"),
        "capture_receipt_sha256": _sha_file(receipt_path),
        "parsed_slot": result_value,
        "summary_sha256": _sha_file(epoch_root / "epoch-summary.json"),
        "adapter": "v2-bootstrap",
        "request_limit": 1,
    }


def _default_frame_adapter(
    *, root: Path, lineage_path: Path, now: datetime, endpoint: str
) -> dict[str, Any]:
    """Future adapter delegates one future bucket to the unchanged v3 scheduler."""
    from tools import brain_senses_block_epoch_v3_schedule as v3
    from tools.brain_senses_block_epoch_v2 import ReadOnlyRPC

    frame_client = ReadOnlyRPC(
        endpoint,
        "helius-free",
        timeout=45,
        max_bytes=4_000_000,
        keep_alive=True,
    )
    try:
        result = v3.run_bucket(
            root, lineage_path=lineage_path, now=now, clients={"helius-free": frame_client}
        )
    finally:
        frame_client.close()
    if (
        result.get("status") != "epoch_finished"
        or result.get("epoch", {}).get("status") != "frame_complete"
    ):
        return {
            "status": result.get("epoch", {}).get("status", result.get("status")),
            "gap": result.get("frontier", {}).get("gap_intervals"),
        }
    epoch = root / "epochs" / now.strftime("%Y%m%dT%H0000Z")
    summary_path = epoch / "epoch-summary.json"
    summary = _read_json(summary_path)
    request_files = sorted((epoch / "requests").glob("*.json"))
    result_files = sorted((epoch / "request-results").glob("*.json"))
    if len(request_files) != policy.FRAME_REQUESTS or len(result_files) != policy.FRAME_REQUESTS:
        return {"status": "frame_journal_incomplete"}
    journal_material = [
        (_sha_file(path), _sha_file(epoch / "request-results" / path.name))
        for path in request_files
    ]
    capture_files = sorted(path for path in (epoch / "captures").rglob("*") if path.is_file())
    capture_material = [(str(path.relative_to(epoch)), _sha_file(path)) for path in capture_files]
    status = v3.read_status(root)
    if (
        status.get("attempts") != 1
        or status.get("requests") != policy.FRAME_REQUESTS
        or status.get("gapped_epochs") != 0
    ):
        return {"status": "frame_status_reconciliation_failed", "gap": status.get("gap_intervals")}
    return {
        "status": "frame_complete",
        "commitment": "finalized",
        "request_count": len(request_files),
        "raw_bytes": summary.get("captured_response_bytes"),
        "stored_bytes": summary.get("stored_response_bytes"),
        "request_journal_sha256": _sha_bytes(_canonical(journal_material)),
        "capture_manifest_sha256": _sha_bytes(_canonical(capture_material)),
        "summary_sha256": _sha_file(summary_path),
        "anchor_slot": summary.get("anchor_slot"),
        "start_slot": summary.get("start"),
        "frontier_slot": status.get("observed_frontier"),
    }


def _validate_bootstrap_result(result: dict[str, Any]) -> None:
    if not isinstance(result, dict) or result.get("status") != "bootstrap_complete":
        raise RunnerError("bootstrap_result_not_complete")
    if result.get("commitment") != "finalized" or result.get("request_limit") != 1:
        raise RunnerError("bootstrap_result_commitment_or_count_invalid")
    if (
        type(result.get("anchor_slot")) is not int
        or type(result.get("raw_bytes")) is not int
        or type(result.get("stored_bytes")) is not int
    ):
        raise RunnerError("bootstrap_result_shape_invalid")
    if result["anchor_slot"] < policy.C4_LINEAGE["last_committed"]["frontier_slot"]:
        raise RunnerError("bootstrap_anchor_regression")
    if result.get("parsed_slot") != result["anchor_slot"]:
        raise RunnerError("bootstrap_capture_slot_mismatch")
    if (
        not 0 <= result["raw_bytes"] <= policy.BOOTSTRAP_RAW_BYTES
        or not 0 <= result["stored_bytes"] <= policy.BOOTSTRAP_STORED_BYTES
    ):
        raise RunnerError("bootstrap_byte_budget_exceeded")
    if (
        type(result.get("receipt_ns")) is not int
        or type(result.get("availability_ns")) is not int
        or result["availability_ns"] < result["receipt_ns"]
    ):
        raise RunnerError("bootstrap_timestamp_invalid")
    for name in ("request_sha256", "response_sha256", "capture_receipt_sha256"):
        value = result.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise RunnerError("bootstrap_hash_binding_incomplete")


def _validate_frame_result(result: dict[str, Any], expected_start: int) -> None:
    if not isinstance(result, dict) or result.get("status") != "frame_complete":
        raise RunnerError("frame_result_incomplete_or_gapped")
    if (
        result.get("commitment") != "finalized"
        or result.get("request_count") != policy.FRAME_REQUESTS
    ):
        raise RunnerError("frame_request_or_commitment_invalid")
    if (
        type(result.get("raw_bytes")) is not int
        or not 0 <= result["raw_bytes"] <= policy.FRAME_RAW_BYTES
    ):
        raise RunnerError("frame_raw_budget_invalid")
    if (
        type(result.get("stored_bytes")) is not int
        or not 0 <= result["stored_bytes"] <= policy.FRAME_STORED_BYTES
    ):
        raise RunnerError("frame_stored_budget_invalid")
    anchor = result.get("anchor_slot")
    if (
        type(anchor) is not int
        or result.get("start_slot") != expected_start
        or result.get("frontier_slot") != anchor + 1
        or not 1 <= anchor + 1 - expected_start <= policy.FRAME_WIDTH_MAX
    ):
        raise RunnerError("frame_frontier_or_width_invalid")
    for name in ("request_journal_sha256", "capture_manifest_sha256", "summary_sha256"):
        value = result.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise RunnerError("frame_hash_binding_incomplete")


def run_scheduled_tick(
    *,
    plan_path: Path,
    gate_path: Path,
    root: Path,
    config_path: Path,
    now: datetime,
    process_inventory: Callable[[], list[dict[str, Any]]],
    bootstrap_executor: Callable[..., dict[str, Any]],
    frame_executor: Callable[..., dict[str, Any]],
    wall_clock: Callable[[], datetime] | None = None,
    repo_root: Path = REPO_ROOT,
    lock_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Single scheduled transition; dependency injection keeps tests offline."""
    plan = _read_json(plan_path)
    verify_plan(plan, repo_root)
    if root.resolve() != C5_SERVICE_ROOT.resolve():
        raise RunnerError("service_root_does_not_match_pinned_production_path")
    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
        raise RunnerError("tick_time_must_be_utc")
    bucket = now.strftime("%Y%m%dT%H0000Z")
    current_clock = wall_clock or (lambda: datetime.now(UTC))
    expected = {plan["schedule"]["bootstrap_bucket"], plan["schedule"]["frame_bucket"]}
    if bucket not in expected or not 5 <= now.minute <= 15:
        _stop_if_planned_tick_expired(plan=plan, root=root, now=now, lock_root=lock_root)
        raise RunnerError("tick_not_preplanned_or_outside_window")
    initial_now = _require_current_tick(bucket, current_clock)
    required_free_bytes = verify_activation_gate(
        gate_path, plan, now_ns=int(initial_now.timestamp() * 1_000_000_000)
    )
    _preflight_processes({C5_LABEL}, process_inventory)
    _verify_disk_headroom(root.parent, required_free_bytes)
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    host_lock = _host_writer_lock_path(lock_root)
    with _exclusive_lock(host_lock), _exclusive_lock(root.parent / f".{root.name}.writer.lock"):
        if root.exists():
            if (root / "STOP.json").exists():
                raise RunnerError("service_stop_is_terminal")
            try:
                manifest = _read_json(root / "manifest.json")
            except RunnerError:
                state_for_gap = {}
                try:
                    state_for_gap = _read_json(root / "state.json")
                except RunnerError:
                    pass
                _atomic_json(
                    root / "STOP.json",
                    {
                        "reason": "service_manifest_uncertain_requires_review",
                        "gap": _unknown_ended_gap(
                            state_for_gap, "manifest_uncertain_after_restart"
                        ),
                    },
                )
                raise RunnerError("service_manifest_uncertain_stopped") from None
            if manifest.get("plan_sha256") != plan["plan_sha256"]:
                _atomic_json(
                    root / "STOP.json",
                    {
                        "reason": "service_manifest_plan_mismatch",
                        "gap": _unknown_ended_gap({}, "manifest_plan_mismatch"),
                    },
                )
                raise RunnerError("service_manifest_plan_mismatch")
            try:
                rows = _read_ledger(root / "request-ledger.jsonl")
            except RunnerError:
                state_for_gap = {}
                try:
                    state_for_gap = _read_json(root / "state.json")
                except RunnerError:
                    pass
                _atomic_json(
                    root / "STOP.json",
                    {
                        "reason": "journal_uncertain_requires_review",
                        "gap": _unknown_ended_gap(
                            state_for_gap, "journal_uncertain_after_reconciliation"
                        ),
                    },
                )
                raise RunnerError("journal_uncertain_stopped") from None
        else:
            # Publish initial state as one directory rename. A crash before rename
            # leaves a recognizable marker and cannot reserve/send a request.
            initializing = root.with_name(f".{root.name}.initializing")
            if initializing.exists():
                raise RunnerError("interrupted_initialization_requires_review")
            initializing.mkdir(parents=True, mode=0o700)
            try:
                _atomic_json(
                    initializing / "manifest.json",
                    {
                        "schema": SCHEMA,
                        "plan_sha256": plan["plan_sha256"],
                        "pilot_id": plan["pilot_id"],
                        "service_id": plan["service_id"],
                    },
                )
                _atomic_json(
                    initializing / "state.json",
                    {
                        "phase": "awaiting_bootstrap",
                        "request_count": 0,
                        "frontier_slot": policy.C4_LINEAGE["last_committed"]["frontier_slot"],
                    },
                )
                os.replace(initializing, root)
                _fsync_dir(root.parent)
            except Exception:
                raise RunnerError("service_initialization_incomplete_requires_review") from None
        if not (root / "manifest.json").is_file() or not (root / "state.json").is_file():
            _atomic_json(
                root / "STOP.json", {"reason": "service_initialization_incomplete_requires_review"}
            )
            raise RunnerError("service_initialization_incomplete_requires_review")
        state = _read_json(root / "state.json")
        rows = _read_ledger(root / "request-ledger.jsonl")
        reconciliation_failure = _reconcile_runner_state(state, rows)
        if reconciliation_failure:
            _atomic_json(
                root / "STOP.json",
                {
                    "reason": reconciliation_failure,
                    "reservation_sha256": rows[-1]["row_sha256"] if rows else None,
                    "gap": _unknown_ended_gap(state, "interrupted_or_inconsistent_attempt"),
                },
            )
            raise RunnerError("interrupted_attempt_stopped_without_replay")
        if state.get("phase") == "awaiting_frame":
            if (
                not (root / "lineage.json").is_file()
                or not (root / "v3" / "schedule-v3.json").is_file()
            ):
                _atomic_json(
                    root / "STOP.json",
                    {"reason": "bootstrap_lineage_initialization_incomplete"},
                )
                raise RunnerError("bootstrap_lineage_initialization_incomplete_stopped")
            try:
                _validate_bootstrap_lineage(_read_json(root / "lineage.json"), plan)
            except RunnerError:
                _atomic_json(
                    root / "STOP.json",
                    {
                        "reason": "bootstrap_lineage_plan_or_lease_mismatch",
                        "gap": _unknown_ended_gap(state, "bootstrap_lineage_invalid"),
                    },
                )
                raise
        expected_bucket = (
            plan["schedule"]["bootstrap_bucket"]
            if state["phase"] == "awaiting_bootstrap"
            else plan["schedule"]["frame_bucket"]
        )
        if bucket != expected_bucket:
            _atomic_json(
                root / "STOP.json",
                {
                    "reason": "missed_or_intervening_bucket",
                    "actual_bucket": bucket,
                    "expected_bucket": expected_bucket,
                    "gap": _unknown_ended_gap(state, "missed_or_intervening_bucket"),
                },
            )
            raise RunnerError("missed_or_intervening_bucket")
        _verify_disk_headroom(root.parent, required_free_bytes)
        # Credentials are read only after immutable source/plan/gate, time, process,
        # root reconciliation and lock checks. They are passed in-process only.
        _recheck_process_inventory(process_inventory, root=root, state=state)
        try:
            endpoint = _load_endpoint(config_path)
        except Exception:
            gap = {
                "start": state.get(
                    "frontier_slot", policy.C4_LINEAGE["unknown_ended_gap"]["start"]
                ),
                "end_exclusive": None,
                "reason": "credential_unavailable_before_request",
            }
            _atomic_json(
                root / "STOP.json",
                {"reason": "credential_unavailable_no_rpc", "gap": gap},
            )
            raise RunnerError("credential_unavailable_no_rpc") from None
        # Close the process-inventory race between config parsing and reservation.
        _recheck_process_inventory(process_inventory, root=root, state=state)
        _verify_disk_headroom(root.parent, required_free_bytes)
        try:
            _require_current_tick(bucket, current_clock)
        except RunnerError:
            _atomic_json(
                root / "STOP.json",
                {
                    "reason": "tick_window_expired_before_reservation",
                    "gap": _unknown_ended_gap(state, "tick_window_expired_before_reservation"),
                },
            )
            raise
        _recheck_process_inventory(process_inventory, root=root, state=state)
        reserve = _append_ledger(
            root / "request-ledger.jsonl",
            {
                "kind": "reservation",
                "bucket": bucket,
                "phase": state["phase"],
                "reserved_ns": time.time_ns(),
                "request_count": 1 if state["phase"] == "awaiting_bootstrap" else 34,
            },
        )
        try:
            _recheck_process_inventory(process_inventory, root=root, state=state)
            _verify_disk_headroom(root.parent, required_free_bytes)
            dispatch_now = _require_current_tick(bucket, current_clock)
            if state["phase"] == "awaiting_bootstrap":
                result = bootstrap_executor(
                    root=root / "bootstrap",
                    bucket=bucket,
                    endpoint=endpoint,
                    schedule_sha256=plan["plan_sha256"],
                )
                _validate_bootstrap_result(result)
                seal = {
                    "kind": "seal",
                    "bucket": bucket,
                    "phase": "bootstrap",
                    "reservation_sha256": reserve["row_sha256"],
                    "result": result,
                }
                _append_ledger(root / "request-ledger.jsonl", seal)
                _atomic_json(
                    root / "state.json",
                    {
                        "phase": "awaiting_frame",
                        "request_count": 1,
                        "bootstrap_sha256": _sha_bytes(_canonical(result)),
                        "frontier_slot": result["anchor_slot"] + 1,
                    },
                )
                lineage = {
                    "schema_version": 1,
                    "provider": "helius-free",
                    "service_plan_sha256": plan["plan_sha256"],
                    "controller_lease_expires_ns": _controller_lease_expiry_ns(plan),
                    "campaign_id": f"helius-inventory-v3-c5-{plan['pilot_id']}",
                    "last_observed_anchor_slot": result["anchor_slot"],
                    "last_observed_anchor_receipt_ns": result["receipt_ns"],
                    "last_observed_anchor_availability_ns": result["availability_ns"],
                    "last_attempt_bucket": bucket,
                    "last_inventoried_interval": policy.C4_LINEAGE["last_committed"][
                        "last_inventoried_interval"
                    ],
                    "gap_intervals": [
                        *policy.C4_LINEAGE["predecessor_gaps"],
                        {
                            "start": policy.C4_LINEAGE["unknown_ended_gap"]["start"],
                            "end_exclusive": result["anchor_slot"] + 1,
                            "reason": "closed_by_c5_finalized_anchor",
                        },
                    ],
                }
                _validate_bootstrap_lineage(lineage, plan)
                _atomic_json(root / "lineage.json", lineage)
                from tools import brain_senses_block_epoch_v3_schedule as v3

                v3.initialize(root / "v3", root / "lineage.json")
                return {
                    "status": "bootstrap_sealed",
                    "bucket": bucket,
                    "request_reservation_sha256": reserve["row_sha256"],
                }
            result = frame_executor(
                root=root / "v3",
                lineage_path=root / "lineage.json",
                bucket=bucket,
                now=dispatch_now,
                endpoint=endpoint,
            )
            _validate_frame_result(result, state["frontier_slot"])
            _append_ledger(
                root / "request-ledger.jsonl",
                {
                    "kind": "seal",
                    "bucket": bucket,
                    "phase": "frame",
                    "reservation_sha256": reserve["row_sha256"],
                    "result": result,
                },
            )
            _atomic_json(
                root / "STOP.json",
                {
                    "reason": "two_bucket_pilot_complete"
                    if result.get("status") == "frame_complete"
                    else "frame_failed_or_ambiguous",
                    "result_sha256": _sha_bytes(_canonical(result)),
                    "gap": result.get("gap"),
                },
            )
            _atomic_json(
                root / "state.json",
                {
                    "phase": "complete"
                    if result.get("status") == "frame_complete"
                    else "stopped_for_review",
                    "request_count": 35,
                },
            )
            return {
                "status": "two_bucket_pilot_complete"
                if result.get("status") == "frame_complete"
                else "stopped_for_review",
                "bucket": bucket,
            }
        except Exception as exc:
            gap = (
                {
                    "start": policy.C4_LINEAGE["unknown_ended_gap"]["start"],
                    "end_exclusive": None,
                    "reason": "bootstrap_uncovered_after_failed_anchor",
                }
                if state["phase"] == "awaiting_bootstrap"
                else {
                    "start": state.get("frontier_slot"),
                    "end_exclusive": None,
                    "reason": "frame_unknown_after_reserved_attempt",
                    "adapter_gap": result.get("gap")
                    if isinstance(locals().get("result"), dict)
                    else None,
                }
            )
            _atomic_json(
                root / "STOP.json",
                {
                    "reason": "reserved_attempt_failed_or_ambiguous",
                    "reservation_sha256": reserve["row_sha256"],
                    "error_type": type(exc).__name__,
                    "gap": gap,
                },
            )
            raise RunnerError("reserved_attempt_failed_or_ambiguous_stopped") from None


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="read-only reconciliation; never reads credentials")
    status.add_argument("--plan", type=Path, required=True)
    status.add_argument("--root", type=Path, required=True)
    run = sub.add_parser("run", help="one scheduled tick; requires separate activation gate")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--gate", type=Path, required=True)
    run.add_argument("--root", type=Path, required=True)
    run.add_argument("--private-config", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status_only(args.plan, args.root), sort_keys=True, indent=2))
        return 0
    if (
        args.plan.resolve() != C5_PLAN_PATH.resolve()
        or args.gate.resolve() != C5_GATE_PATH.resolve()
        or args.root.resolve() != C5_SERVICE_ROOT.resolve()
        or args.private_config.resolve() != C5_PRIVATE_CONFIG_PATH.resolve()
    ):
        raise RunnerError("scheduled_runner_paths_are_fixed")
    now = datetime.now(UTC)
    result = run_scheduled_tick(
        plan_path=args.plan,
        gate_path=args.gate,
        root=args.root,
        config_path=args.private_config,
        now=now,
        process_inventory=_host_process_inventory,
        bootstrap_executor=_default_bootstrap_adapter,
        frame_executor=_default_frame_adapter,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_cli())
    except RunnerError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
