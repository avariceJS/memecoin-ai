"""Explicit reviewed recovery of unrequested work; never retries an attempted market request."""

import json
import os
import sqlite3
import time

from memecoin_experience.storage import (
    atomic_json,
    digest,
    file_hash,
    lock,
    safe_path,
    sync_dir,
    verify_files,
)

from .acquisition import validate


def reviewed(root, plan, key, prefix):
    stopped = dict(reason="transport_failure", request=key, automatic_retry=False)
    pin = digest(stopped)
    archive = root / "recoveries" / (pin + ".stop.json")
    receipt = root / "recoveries" / (pin + ".json")
    if not archive.exists() or not receipt.exists():
        return False
    if file_hash(archive) != pin:
        raise ValueError("acquisition_recovery_archive_binding")
    report = json.loads(receipt.read_text())
    expected = dict(
        plan=digest(plan),
        stop=pin,
        prefix=digest(prefix),
        attempted=len(prefix),
        remaining=len(plan["jobs"]) - len(prefix),
        action="resume_unrequested_only",
        retried_requests=0,
        expires_ns=plan["expires_ns"],
    )
    if any(report.get(k) != v for k, v in expected.items()):
        raise ValueError("acquisition_recovery_binding")
    return True


def require_reviewed_failures(root, plan, db):
    """Recover STOP even if death occurred after terminal DB commit but before STOP publication."""
    failures = db.execute(
        "select ordinal,id,status from requests where status in "
        "('transport_failure','provider_stop') order by ordinal"
    ).fetchall()
    for ordinal, key, status in failures:
        prefix = []
        for request, state, path in db.execute(
            "select id,status,capture from requests where ordinal<=? order by ordinal", (ordinal,)
        ):
            cap = safe_path(path)
            verify_files(cap)
            prefix.append(
                dict(request=request, status=state, capture=file_hash(cap / "manifest.json"))
            )
        if status == "transport_failure" and reviewed(root, plan, key, prefix):
            continue
        atomic_json(root / "STOP.json", dict(reason=status, request=key, automatic_retry=False))
        raise ValueError("acquisition_unreviewed_terminal_failure")


def reconcile_transport_stop(root, *, stop_pin, reason):
    """Archive a sealed transport STOP after full prefix reconciliation, under the capture lock.

    This is an explicit operator action, never called by capture or its automatic supervisor.
    Provider stops and ambiguous/unsealed reservations require a different review and fail closed.
    """
    root = safe_path(root)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("acquisition_recovery_reason")
    from memecoin_experience.storage import PIN

    if not isinstance(stop_pin, str) or not PIN.fullmatch(stop_pin):
        raise ValueError("acquisition_recovery_pin")
    with lock(root / ".capture.lock"):
        plan = json.loads((root / "plan.json").read_text())
        validate(plan)
        stop = root / "STOP.json"
        history = root / "recoveries"
        history.mkdir(exist_ok=True)
        receipt_path = history / (stop_pin + ".json")
        archive = history / (stop_pin + ".stop.json")
        if not stop.exists():
            if not archive.exists() or not receipt_path.exists() or file_hash(archive) != stop_pin:
                raise ValueError("acquisition_recovery_missing_stop")
            result = json.loads(receipt_path.read_text())
            if result["stop"] != stop_pin or result["plan"] != digest(plan):
                raise ValueError("acquisition_recovery_binding")
            return result
        if file_hash(stop) != stop_pin:
            raise ValueError("acquisition_recovery_changed_stop")
        stopped = json.loads(stop.read_text())
        if (
            stopped.get("reason") != "transport_failure"
            or stopped.get("automatic_retry") is not False
        ):
            raise ValueError("acquisition_recovery_requires_other_review")
        now = time.time_ns()
        if not plan["created_ns"] <= now < plan["expires_ns"]:
            raise ValueError("acquisition_recovery_expired")
        db = sqlite3.connect((root / "requests.sqlite").as_uri() + "?mode=ro", uri=True)
        try:
            db.execute("BEGIN")
            prefix = []
            for key, ordinal, raw, status, started, path, result in db.execute(
                "select * from requests order by ordinal"
            ):
                if ordinal != len(prefix) or ordinal >= len(plan["jobs"]):
                    raise ValueError("acquisition_recovery_prefix")
                job = plan["jobs"][ordinal]
                cap = safe_path(path)
                if (
                    key != digest(job)
                    or json.loads(raw) != job
                    or cap != root / "requests" / f"{ordinal:07d}"
                    or status not in {"received", "unsupported", "transport_failure"}
                ):
                    raise ValueError("acquisition_recovery_unresolved")
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
                    or receipt["status"] != status
                    or outcome["http_status"] != receipt["http_status"]
                    or receipt["response_sha256"] != file_hash(cap / "response.bin")
                    or outcome["bytes"] != (cap / "response.bin").stat().st_size
                ):
                    raise ValueError("acquisition_recovery_capture_binding")
                prefix.append(
                    dict(request=key, status=status, capture=file_hash(cap / "manifest.json"))
                )
                if status == "transport_failure" and key != stopped["request"]:
                    if not reviewed(root, plan, key, prefix):
                        raise ValueError("acquisition_recovery_unreviewed_prior_failure")
            if (
                not prefix
                or prefix[-1]["request"] != stopped["request"]
                or prefix[-1]["status"] != "transport_failure"
            ):
                raise ValueError("acquisition_recovery_stop_not_terminal")
        finally:
            db.close()
        binding = dict(
            plan=digest(plan),
            stop=stop_pin,
            prefix=digest(prefix),
            attempted=len(prefix),
            remaining=len(plan["jobs"]) - len(prefix),
            action="resume_unrequested_only",
            retried_requests=0,
            expires_ns=plan["expires_ns"],
        )
        if receipt_path.exists():
            report = json.loads(receipt_path.read_text())
            if any(report[k] != v for k, v in binding.items()):
                raise ValueError("acquisition_recovery_binding")
        else:
            report = dict(binding, reviewed_ns=now, reason=reason)
            atomic_json(receipt_path, report)
        if archive.exists():
            if file_hash(archive) != stop_pin:
                raise ValueError("acquisition_recovery_archive_binding")
            # A power loss can leave both directory entries. Preserve the verified archive.
            stop.unlink()
        else:
            os.rename(stop, archive)
        sync_dir(history)
        sync_dir(root)
        return report
