#!/usr/bin/env python3
"""Offline writer for a one-successor preflight packet and durable claim.

This code never activates a collector, creates a provider, reads credentials, or sends RPC.
The claim is a local one-successor reservation only. Ed25519 signatures authenticate evidence
relative to an out-of-band pinned public-key trust store; they do not prove evidence truth.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

from tools import brain_senses_activation_preflight_verifier_v1 as verifier

SCHEMA = "brain-senses-activation-preflight-v1"
CLAIM_SCHEMA = "brain-senses-successor-claim-v1"
REGISTRY_SCHEMA = "brain-senses-successor-claim-registry-v1"


class PreflightError(ValueError):
    """Preflight packet or one-successor claim could not be safely written."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def packet_digest(manifest: dict[str, Any]) -> str:
    body = dict(manifest)
    body.pop("review_ref", None)
    return hashlib.sha256(_canonical(body)).hexdigest()


def write_packet(root: Path, manifest: dict[str, Any]) -> None:
    """Write a caller-authored immutable packet manifest; no schedule is synthesized."""
    if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("successor"), dict):
        raise PreflightError("packet_shape_invalid")
    root = Path(root)
    _private_root(root, create=True)
    data = _canonical(manifest) + b"\n"
    _write_new(root / "preflight-v1.json", data)


def _private_root(root: Path, *, create: bool = False) -> None:
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if stat.S_ISLNK(root.lstat().st_mode):
            raise PreflightError("preflight_root_symlink_forbidden")
    except OSError as exc:
        raise PreflightError("preflight_root_unavailable") from exc
    st = root.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise PreflightError("preflight_root_owner_or_mode_invalid")


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _registry_lock(root: Path):
    _private_root(root, create=True)
    lock_path = root / ".successor-claim-registry-v1.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    lock = os.fdopen(fd, "r+")
    st = os.fstat(lock.fileno())
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or st.st_nlink != 1
        or stat.S_IMODE(st.st_mode) != 0o600
    ):
        lock.close()
        raise PreflightError("claim_registry_lock_identity_invalid")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise PreflightError("successor_claim_registry_already_active") from exc
    return lock


def claim_successor(
    root: Path,
    *,
    registry_root: Path,
    trust_store_path: Path,
    trust_store_sha256: str,
    openssl_path: Path,
    openssl_sha256: str,
    verifier_sha256: str,
    helius_price_source_sha256: str,
    helius_price_table_sha256: str,
    clock_source_id: str,
    capture_root_path: Path,
) -> dict[str, Any]:
    """Verify offline evidence and reserve one predecessor in the configured owner registry.

    The registry path is a caller-pinned owner-local coordination root. Cross-host exclusion
    requires all hosts to use the same trustworthy shared durable filesystem, which this module
    neither discovers nor provisions. The reservation performs no activation.
    """
    root = Path(root)
    _private_root(root)
    registry_root = Path(registry_root)
    _private_root(registry_root, create=True)
    eligibility = verifier.verify_packet(
        root,
        registry_root=registry_root,
        trust_store_path=trust_store_path,
        trust_store_sha256=trust_store_sha256,
        openssl_path=openssl_path,
        openssl_sha256=openssl_sha256,
        verifier_sha256=verifier_sha256,
        helius_price_source_sha256=helius_price_source_sha256,
        helius_price_table_sha256=helius_price_table_sha256,
        clock_source_id=clock_source_id,
        capture_root_path=capture_root_path,
    )
    if not eligibility.get("eligible"):
        if eligibility.get("failure") == "predecessor_successor_already_claimed":
            raise PreflightError("predecessor_successor_already_claimed")
        raise PreflightError("successor_preflight_ineligible")
    registry_root = registry_root.resolve(strict=True)
    lock = _registry_lock(registry_root)
    try:
        claims = verifier._registry_claims(registry_root)
        predecessor_hash = eligibility["predecessor_terminal_sha256"]
        if predecessor_hash in claims:
            raise PreflightError("predecessor_successor_already_claimed")
        # Recheck all evidence while holding the exclusive owner lock.
        eligibility = verifier.verify_packet(
            root,
            registry_root=registry_root,
            trust_store_path=trust_store_path,
            trust_store_sha256=trust_store_sha256,
            openssl_path=openssl_path,
            openssl_sha256=openssl_sha256,
            verifier_sha256=verifier_sha256,
            helius_price_source_sha256=helius_price_source_sha256,
            helius_price_table_sha256=helius_price_table_sha256,
            clock_source_id=clock_source_id,
            capture_root_path=capture_root_path,
        )
        if not eligibility.get("eligible"):
            raise PreflightError("successor_preflight_became_ineligible")
        payload = {
            "schema": CLAIM_SCHEMA,
            "lease_id": eligibility["lease_id"],
            "predecessor_terminal_sha256": eligibility["predecessor_terminal_sha256"],
            "packet_digest": eligibility["packet_digest"],
            "plan_sha256": eligibility["plan_sha256"],
            "schedule_sha256": eligibility["schedule_sha256"],
            "review_signature_sha256": eligibility["evidence_sha256"]["sol_signature"],
            "claimed_at_ns": time.time_ns(),
            "activation_performed": False,
        }
        payload["claim_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
        claims[eligibility["predecessor_terminal_sha256"]] = payload
        registry_data = _canonical({"schema": REGISTRY_SCHEMA, "claims": claims}) + b"\n"
        pending = registry_root / f".registry-claim-v1-{uuid.uuid4().hex}.pending"
        _write_new(pending, registry_data)
        os.replace(pending, registry_root / "successor-claims-v1.json")
        dfd = os.open(registry_root, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return payload
    finally:
        lock.close()


def verify_claim(root: Path, **verification_args: Any) -> dict[str, Any]:
    """Read-only claim check. It never makes a successor eligible or starts it."""
    return verifier.verify_claim(root, **verification_args)
