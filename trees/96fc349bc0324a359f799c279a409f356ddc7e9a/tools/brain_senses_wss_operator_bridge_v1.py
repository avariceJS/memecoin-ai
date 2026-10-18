"""Inert, source-bound operator harness for one WSS campaign invocation.

The command-line interface is read-only. Execution is available only through the
Python API with an explicitly injected client factory; this module has no live
adapter, credential loader, socket, DNS, or provider dependency.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from memecoin_collector import wss_campaign, wss_reference

SCHEMA = "brain-senses-wss-operator-bridge-v1"
PLAN_SCHEMA = "brain-senses-wss-operator-plan-v1"
DISK_FLOOR_BYTES = wss_reference.DISK_FLOOR_BYTES
MAX_INPUT_BYTES = 128 * 1024
_CODE_PATHS = {
    "operator_bridge": Path(__file__).resolve(),
    "wss_campaign": Path(wss_campaign.__file__).resolve(),
    "wss_reference": Path(wss_reference.__file__).resolve(),
    "wss_client": Path(__file__).resolve().parents[1] / "src/memecoin_collector/wss_client.py",
    "wss_transport": Path(__file__).resolve().parents[1]
    / "src/memecoin_collector/wss_transport.py",
    "wss_initial_entry_verifier": Path(__file__)
    .resolve()
    .with_name("brain_senses_wss_initial_entry_v2.py"),
}


class BridgeError(ValueError):
    """Sanitized operator preflight or journal failure."""


class OneShotClient(Protocol):
    def run_one_shot(self) -> None: ...


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stop_sha(row: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in row.items() if key != "sha256"}
    return row.get("sha256") or _sha(wss_campaign._canonical(unsigned))


def source_bindings() -> dict[str, str]:
    return {name: _sha(path.read_bytes()) for name, path in sorted(_CODE_PATHS.items())}


def make_offline_plan(root: Path, authorization: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic test/operator plan; it never authorizes live activation."""
    wss_campaign._validate_authorization(authorization)
    if not root.is_absolute():
        raise BridgeError("operator_root_must_be_absolute")
    return {
        "schema": PLAN_SCHEMA,
        "root": str(root),
        "authorization_sha256": _sha(_canonical(authorization)),
        "source_bindings": source_bindings(),
        "activation_authorized": False,
        "provider_io_authorized": False,
    }


def _load_json_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if not path.is_absolute():
        raise BridgeError(f"{label}_path_invalid")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise BridgeError(f"{label}_no_follow_unavailable")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise BridgeError(f"{label}_unavailable_or_invalid") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > MAX_INPUT_BYTES
        ):
            raise BridgeError(f"{label}_identity_invalid")
        raw_buffer = bytearray()
        while len(raw_buffer) <= MAX_INPUT_BYTES:
            part = os.read(fd, min(4096, MAX_INPUT_BYTES + 1 - len(raw_buffer)))
            if not part:
                break
            raw_buffer.extend(part)
        after = os.fstat(fd)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (
            len(raw_buffer) > MAX_INPUT_BYTES
            or identity_before != identity_after
            or len(raw_buffer) != after.st_size
        ):
            raise BridgeError(f"{label}_changed_or_oversized")
        raw = bytes(raw_buffer)
        row = json.loads(raw)
    except BridgeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BridgeError(f"{label}_unavailable_or_invalid") from exc
    finally:
        os.close(fd)
    if not isinstance(row, dict):
        raise BridgeError(f"{label}_shape_invalid")
    return row, raw


def _validate_plan(
    plan_path: Path,
    authorization_path: Path,
    root: Path,
    *,
    now_ns: int,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    plan, plan_raw = _load_json_file(plan_path, label="operator_plan")
    authorization, _auth_raw = _load_json_file(authorization_path, label="campaign_authorization")
    if (
        plan.get("schema") != PLAN_SCHEMA
        or plan.get("root") != str(root)
        or plan.get("activation_authorized") is not False
        or plan.get("provider_io_authorized") is not False
        or plan.get("source_bindings") != source_bindings()
    ):
        raise BridgeError("operator_plan_binding_mismatch")
    try:
        wss_campaign._validate_authorization(authorization)
    except (ValueError, TypeError, KeyError) as exc:
        raise BridgeError("campaign_authorization_invalid") from exc
    if plan.get("authorization_sha256") != _sha(_canonical(authorization)):
        raise BridgeError("campaign_authorization_hash_mismatch")
    if type(now_ns) is not int or now_ns < 0:
        raise BridgeError("clock_invalid")
    if not authorization["authorized_at_ns"] <= now_ns < authorization["expires_at_ns"]:
        raise BridgeError("authorization_expired_or_not_yet_active")
    return plan, authorization, _sha(plan_raw)


def _private_root_parent(root: Path) -> Path:
    if not root.is_absolute() or root.name in {"", ".", ".."} or root.is_symlink():
        raise BridgeError("operator_root_path_invalid")
    parent = root.parent
    if not parent.is_dir() or parent.is_symlink():
        raise BridgeError("operator_root_parent_invalid")
    parent_stat = parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid():
        raise BridgeError("operator_root_parent_owner_invalid")
    return parent


def _lock(parent: Path) -> int:
    lock_path = parent / ".brain-senses-wss-operator-v1.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise BridgeError("operator_lock_unavailable") from exc
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
        ):
            raise BridgeError("operator_lock_identity_invalid")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, BridgeError) as exc:
        os.close(fd)
        if isinstance(exc, BridgeError):
            raise
        raise BridgeError("operator_writer_active") from exc


def _fresh_preflight(
    plan_path: Path,
    authorization_path: Path,
    root: Path,
    *,
    clock_ns: Callable[[], int],
    free_disk_bytes: Callable[[Path], int],
) -> tuple[dict[str, Any], dict[str, Any], str, int, int]:
    now_ns = clock_ns()
    plan, authorization, plan_sha = _validate_plan(
        plan_path, authorization_path, root, now_ns=now_ns
    )
    free = free_disk_bytes(root.parent)
    if type(free) is not int or free < DISK_FLOOR_BYTES:
        raise BridgeError("disk_floor")
    # Recheck time after the potentially slow filesystem probe. No client exists yet.
    checked_ns = clock_ns()
    if (
        checked_ns < now_ns
        or not authorization["authorized_at_ns"] <= checked_ns < authorization["expires_at_ns"]
    ):
        raise BridgeError("clock_or_expiry_changed")
    return plan, authorization, plan_sha, checked_ns, free


def _receipt(
    *,
    status: str,
    root: Path,
    plan_sha256: str | None,
    authorization_sha256: str,
    external_authorization_sha256: str | None,
    external_authorization_matches_campaign: bool | None,
    at_ns: int,
    client_factory_called: bool,
    campaign_stop: dict[str, Any] | None,
    reason: str | None = None,
    dispatch_denial_reasons: list[str] | None = None,
) -> dict[str, Any]:
    stop_sha256 = None if campaign_stop is None else _stop_sha(campaign_stop)
    row = {
        "schema": SCHEMA,
        "status": status,
        "root": str(root),
        "plan_sha256": plan_sha256,
        "authorization_sha256": authorization_sha256,
        "external_authorization_sha256": external_authorization_sha256,
        "external_authorization_matches_campaign": external_authorization_matches_campaign,
        "decision_time_ns": at_ns,
        "client_factory_called": client_factory_called,
        "activation_authorized": False,
        "provider_io_performed_by_bridge": False,
        "injected_client_io_observation": "not_invoked"
        if not client_factory_called
        else "unknown_not_observed",
        "campaign_stop_sha256": stop_sha256,
        "campaign_stop_reason": None if campaign_stop is None else campaign_stop.get("reason"),
        "reason": reason,
        "dispatch_denial_reasons": dispatch_denial_reasons or [],
        "no_resume": True,
        "no_backfill": True,
    }
    return dict(row, sha256=_sha(_canonical(row)))


def _reopen_result(
    *,
    root: Path,
    at_ns: int,
    existing_receipt: dict[str, Any],
    existing_receipt_raw: bytes,
    campaign_stop: dict[str, Any],
    authorization_sha256: str,
    external_authorization_sha256: str | None,
    external_authorization_matches_campaign: bool | None,
    dispatch_denial_reasons: list[str],
) -> dict[str, Any]:
    row = {
        "schema": SCHEMA,
        "status": "existing_terminal_campaign_no_dispatch",
        "root": str(root),
        "invocation_time_ns": at_ns,
        "client_factory_called": False,
        "activation_authorized": False,
        "provider_io_performed_by_bridge": False,
        "injected_client_io_observation": "not_invoked",
        "campaign_authorization_sha256": authorization_sha256,
        "external_authorization_sha256": external_authorization_sha256,
        "external_authorization_matches_campaign": external_authorization_matches_campaign,
        "campaign_stop_sha256": _stop_sha(campaign_stop),
        "original_operator_receipt_sha256": _sha(existing_receipt_raw),
        "original_receipt_client_factory_called": existing_receipt.get("client_factory_called"),
        "dispatch_denial_reasons": dispatch_denial_reasons,
        "no_resume": True,
        "no_backfill": True,
    }
    return dict(row, sha256=_sha(_canonical(row)))


def _write_receipt(path: Path, row: dict[str, Any]) -> None:
    raw = _canonical(row)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def run_injected_once(
    root: Path,
    plan_path: Path,
    authorization_path: Path,
    *,
    client_factory: Callable[[wss_campaign.WssReferenceCampaign], OneShotClient],
    clock_ns: Callable[[], int] = time.time_ns,
    free_disk_bytes: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
) -> dict[str, Any]:
    """Run one explicitly injected client after all gates; never retries or resumes.

    This API is for controlled synthetic/offline harnesses. There is intentionally no
    production factory or CLI execution mode.
    """
    parent = _private_root_parent(root)
    lock_fd = _lock(parent)
    campaign: wss_campaign.WssReferenceCampaign | None = None
    factory_called = False
    plan_sha = ""
    auth_sha = ""
    at_ns = 0
    try:
        # Recovery owns the root's original identity and is terminal even after the
        # external dispatch lease expires or current free disk falls below the floor.
        if root.exists():
            at_ns = clock_ns()
            if type(at_ns) is not int or at_ns < 0:
                raise BridgeError("clock_invalid")
            root_authorization, _root_auth_raw = _load_json_file(
                root / "authorization.json", label="root_campaign_authorization"
            )
            try:
                wss_campaign._validate_authorization(root_authorization)
            except (ValueError, TypeError, KeyError) as exc:
                raise BridgeError("root_campaign_authorization_invalid") from exc
            auth_sha = _sha(_canonical(root_authorization))
            stop = wss_campaign.WssReferenceCampaign.recover_interrupted(root, at_ns=at_ns)

            external_plan_sha: str | None = None
            external_auth_sha: str | None = None
            external_auth_matches: bool | None = None
            plan_matches = False
            try:
                ext_plan, ext_plan_raw = _load_json_file(plan_path, label="operator_plan")
                external_plan_sha = _sha(ext_plan_raw)
                ext_auth, _ext_auth_raw = _load_json_file(
                    authorization_path, label="campaign_authorization"
                )
                external_auth_sha = _sha(_canonical(ext_auth))
                external_auth_matches = external_auth_sha == auth_sha
                plan_matches = ext_plan == make_offline_plan(root, ext_auth)
            except BridgeError:
                plan_matches = False
            except (ValueError, TypeError, KeyError):
                plan_matches = False

            denial_reasons = ["existing_campaign_terminal_no_replay"]
            if not external_auth_matches:
                denial_reasons.append("external_authorization_mismatch")
            if not plan_matches:
                denial_reasons.append("external_plan_or_source_mismatch")
            if (
                external_auth_matches
                and isinstance(ext_auth, dict)
                and (
                    type(ext_auth.get("authorized_at_ns")) is int
                    and type(ext_auth.get("expires_at_ns")) is int
                    and not ext_auth["authorized_at_ns"] <= at_ns < ext_auth["expires_at_ns"]
                )
            ):
                denial_reasons.append("external_authorization_expired_or_not_yet_active")
            try:
                if free_disk_bytes(parent) < DISK_FLOOR_BYTES:
                    denial_reasons.append("disk_floor")
            except Exception:
                denial_reasons.append("disk_status_unknown")

            receipt = _receipt(
                status="terminal_recovery_no_resume",
                root=root,
                plan_sha256=external_plan_sha,
                authorization_sha256=auth_sha,
                external_authorization_sha256=external_auth_sha,
                external_authorization_matches_campaign=external_auth_matches,
                at_ns=at_ns,
                client_factory_called=False,
                campaign_stop=stop,
                reason="existing_campaign_recovered_no_dispatch",
                dispatch_denial_reasons=denial_reasons,
            )
            receipt_path = parent / f"{root.name}.operator-receipt.json"
            if receipt_path.exists():
                existing, existing_raw = _load_json_file(receipt_path, label="operator_receipt")
                unsigned = {key: value for key, value in existing.items() if key != "sha256"}
                if (
                    existing.get("sha256") != _sha(_canonical(unsigned))
                    or existing.get("root") != str(root)
                    or existing.get("authorization_sha256") != auth_sha
                    or existing.get("campaign_stop_sha256") != _stop_sha(stop)
                ):
                    raise BridgeError("operator_receipt_already_sealed")
                return _reopen_result(
                    root=root,
                    at_ns=at_ns,
                    existing_receipt=existing,
                    existing_receipt_raw=existing_raw,
                    campaign_stop=stop,
                    authorization_sha256=auth_sha,
                    external_authorization_sha256=external_auth_sha,
                    external_authorization_matches_campaign=external_auth_matches,
                    dispatch_denial_reasons=denial_reasons,
                )
            _write_receipt(receipt_path, receipt)
            return receipt

        _plan, authorization, plan_sha, at_ns, _free = _fresh_preflight(
            plan_path,
            authorization_path,
            root,
            clock_ns=clock_ns,
            free_disk_bytes=free_disk_bytes,
        )
        auth_sha = _sha(_canonical(authorization))
        campaign = wss_campaign.WssReferenceCampaign(root, authorization, opened_ns=at_ns)
        # Revalidate immutable source, lease, wall clock and disk immediately before factory.
        _plan, _authorization, _plan_sha, session_ns, fresh_free = _fresh_preflight(
            plan_path,
            authorization_path,
            root,
            clock_ns=clock_ns,
            free_disk_bytes=free_disk_bytes,
        )
        campaign.start_session(at_ns=session_ns, free_disk_bytes=fresh_free)

        def reject_second_window(*_args: Any, **_kwargs: Any) -> None:
            latest_session_ns = (
                campaign.sessions[-1].last_event_ns if campaign.sessions else session_ns
            )
            stop_at = max(clock_ns(), latest_session_ns)
            if not campaign.terminal:
                campaign.stop("one_window_cap", stop_at)
            raise BridgeError("operator_one_window_cap")

        # The shared campaign permits two windows; this bridge's injected client
        # receives an instance with the narrower one-window contract.
        campaign.start_session = reject_second_window  # type: ignore[method-assign]
        # A factory is an injected test/operator dependency only; no default exists.
        factory_called = True
        client = client_factory(campaign)
        client.run_one_shot()
        if not campaign.terminal:
            campaign.stop(
                "injected_one_shot_complete",
                max(clock_ns(), campaign.active.last_event_ns if campaign.active else at_ns),
            )
        stop_row = json.loads((root / "CAMPAIGN_STOP.json").read_text())
        receipt = _receipt(
            status="terminal_injected_run",
            root=root,
            plan_sha256=plan_sha,
            authorization_sha256=auth_sha,
            external_authorization_sha256=auth_sha,
            external_authorization_matches_campaign=True,
            at_ns=clock_ns(),
            client_factory_called=True,
            campaign_stop=stop_row,
        )
        _write_receipt(parent / f"{root.name}.operator-receipt.json", receipt)
        return receipt
    except BaseException as exc:
        if campaign is not None and not campaign.terminal:
            try:
                campaign.stop(
                    "injected_client_failure_unknown"
                    if factory_called
                    else "pre_factory_preflight_denied",
                    max(clock_ns(), campaign.active.last_event_ns if campaign.active else at_ns),
                )
            except Exception:
                pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        reason = exc.args[0] if isinstance(exc, BridgeError) and exc.args else "operator_run_failed"
        if root.exists() and (root / "CAMPAIGN_STOP.json").exists():
            stop = json.loads((root / "CAMPAIGN_STOP.json").read_text())
            receipt = _receipt(
                status="terminal_or_denied",
                root=root,
                plan_sha256=plan_sha or None,
                authorization_sha256=auth_sha,
                external_authorization_sha256=auth_sha or None,
                external_authorization_matches_campaign=None if not auth_sha else True,
                at_ns=max(clock_ns(), at_ns),
                client_factory_called=factory_called,
                campaign_stop=stop,
                reason=str(reason),
            )
            receipt_path = parent / f"{root.name}.operator-receipt.json"
            if not receipt_path.exists():
                _write_receipt(receipt_path, receipt)
        raise BridgeError(str(reason)) from None
    finally:
        if campaign is not None:
            campaign.close()
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def read_status(root: Path) -> dict[str, Any]:
    """Return an explicitly unverified, read-only filesystem projection."""
    if not root.is_absolute() or root.is_symlink():
        raise BridgeError("status_root_path_invalid")
    exists = root.exists()
    stop_sha = None
    stop_reason = None
    if exists:
        stop_path = root / "CAMPAIGN_STOP.json"
        if stop_path.is_file() and not stop_path.is_symlink():
            try:
                stop_raw = stop_path.read_bytes()
                stop_row = json.loads(stop_raw)
                stop_sha = _sha(stop_raw)
                stop_reason = stop_row.get("reason") if isinstance(stop_row, dict) else None
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                stop_reason = "unreadable_or_invalid"
    return {
        "schema": SCHEMA,
        "status": "read_only_projection_unverified",
        "root_exists": exists,
        "campaign_stop_sha256": stop_sha,
        "campaign_stop_reason": stop_reason,
        "activation_authorized": False,
        "provider_io_performed_by_bridge": False,
        "provider_io_status_for_existing_tree": "not_inferred_from_filesystem",
        "filesystem_mutated": False,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--status-only", action="store_true", help="read-only status projection")
    args = parser.parse_args(argv)
    if not args.status_only:
        parser.error("execution is unavailable from the CLI; use status-only")
    print(_canonical(read_status(args.root)).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
