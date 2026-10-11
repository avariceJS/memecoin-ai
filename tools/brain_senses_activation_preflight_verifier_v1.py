#!/usr/bin/env python3
"""Independent read-only verifier for offline successor activation-preflight packets.

Authentication is limited to Ed25519 signatures by identities in a separately pinned public-key
trust store. OpenSSL is an explicit, SHA-256-pinned executable dependency. Valid signatures bind
artifact bytes and claims; they cannot prove the provider, host, accountant, or reviewer told the
truth. In particular a Sol signature attests complete lineage, the reviewed storage reserve,
claim-registry identity, and this exact preflight digest only. Helius price source/table hashes
must also be supplied out of band; no provider price table or reviewer trust root is shipped here.
The durable registry is owner-local unless callers configure the same shared durable filesystem;
this verifier cannot establish cross-host locking or durability.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from tools import brain_senses_finite_calendar_verifier_v3 as calendar_verify
from tools import brain_senses_initial_entry_calendar_verifier_v1 as initial_candidate_verify
from tools import brain_senses_initial_entry_discontinuity_verifier_v1 as discontinuity_verify

SCHEMA = "brain-senses-activation-preflight-v1"
CLAIM_SCHEMA = "brain-senses-successor-claim-v1"
INITIAL_ENTRY_CLAIM_SCHEMA = "brain-senses-initial-entry-successor-claim-v1"
REGISTRY_SCHEMA = "brain-senses-successor-claim-registry-v1"
INITIAL_ENTRY_PREDECESSOR_KIND = "initial_entry_discontinuity_certificate_v1"
INITIAL_ENTRY_CERTIFICATE_REPO_PATH = (
    "docs/evidence/senses-initial-entry-discontinuity-v1-c4-2026-10-11.json"
)
DAY_NS = 86_400_000_000_000
MINIMUM_FLOOR = 50 * 1024**3
METADATA_PER_REQUEST = 64 * 1024
METADATA_PER_OPERATION = 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON_BYTES = 8_000_000


class VerificationError(ValueError):
    """Packet, evidence, authentication, lineage, or claim failed verification."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = {}
        for key, item in pairs:
            if key in value:
                raise VerificationError("duplicate_json_key")
            value[key] = item
        return value

    if len(raw) > MAX_JSON_BYTES:
        raise VerificationError("json_size_bound_exceeded")
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except VerificationError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VerificationError("json_invalid") from exc


def _read(path: Path, *, owner: int, max_bytes: int = MAX_JSON_BYTES) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        body = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != owner
        or before.st_nlink != 1
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        or len(body) != after.st_size
        or len(body) > max_bytes
    ):
        raise VerificationError("evidence_file_identity_or_size_invalid")
    return body


def _registry_identity(path: Path) -> tuple[Path, str]:
    original = Path(path)
    if not original.is_absolute() or stat.S_ISLNK(original.lstat().st_mode):
        raise VerificationError("claim_registry_path_invalid")
    root = original.resolve(strict=True)
    st = root.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise VerificationError("claim_registry_owner_or_mode_invalid")
    return root, _sha(str(root).encode("utf-8"))


def _registry_claims(root: Path) -> dict[str, dict[str, Any]]:
    pending = list(root.glob(".registry-claim-v1-*.pending"))
    if pending:
        raise VerificationError("claim_registry_crash_prefix_present")
    path = root / "successor-claims-v1.json"
    try:
        st = path.lstat()
    except FileNotFoundError:
        return {}
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o600
        or st.st_nlink != 1
    ):
        raise VerificationError("claim_registry_file_identity_invalid")
    raw = _read(path, owner=os.getuid())
    value = _json(raw)
    if (
        _canonical(value) + b"\n" != raw
        or value.get("schema") != REGISTRY_SCHEMA
        or not isinstance(value.get("claims"), dict)
        or any(
            not HEX64.fullmatch(key) or not isinstance(claim, dict)
            for key, claim in value.get("claims", {}).items()
        )
    ):
        raise VerificationError("claim_registry_format_invalid")
    for predecessor_hash, claim in value["claims"].items():
        body = dict(claim)
        supplied = body.pop("claim_sha256", None)
        common_invalid = (
            supplied != _sha(_canonical(body))
            or not isinstance(claim.get("lease_id"), str)
            or not claim.get("lease_id")
            or not isinstance(claim.get("packet_digest"), str)
            or claim.get("activation_performed") is not False
            or type(claim.get("claimed_at_ns")) is not int
        )
        normal_invalid = (
            claim.get("schema") == CLAIM_SCHEMA
            and claim.get("predecessor_terminal_sha256") == predecessor_hash
        )
        initial_invalid = (
            claim.get("schema") == INITIAL_ENTRY_CLAIM_SCHEMA
            and claim.get("predecessor_digest_kind") == INITIAL_ENTRY_PREDECESSOR_KIND
            and isinstance(claim.get("predecessor_digest_sha256"), str)
            and HEX64.fullmatch(claim["predecessor_digest_sha256"])
            and claim.get("claim_key_sha256") == predecessor_hash
            and predecessor_hash == _initial_entry_claim_key(claim["predecessor_digest_sha256"])
        )
        if common_invalid or not (normal_invalid or initial_invalid):
            raise VerificationError("claim_registry_record_invalid")
    return value["claims"]


def _initial_entry_claim_key(predecessor_digest_sha256: str) -> str:
    return _sha(
        _canonical(
            {
                "domain": "brain-senses-initial-entry-successor-claim-key-v1",
                "predecessor_digest_kind": INITIAL_ENTRY_PREDECESSOR_KIND,
                "predecessor_digest_sha256": predecessor_digest_sha256,
            }
        )
    )


def _predecessor_claim_identity(plan: dict[str, Any]) -> dict[str, str]:
    predecessor = plan.get("predecessor")
    digest = predecessor.get("terminal_sha256") if isinstance(predecessor, dict) else None
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        raise VerificationError("predecessor_terminal_hash_invalid")
    kind = plan.get("predecessor_digest_kind")
    if kind is None:
        return {
            "kind": "normal_terminal_handoff_sha256_v1",
            "digest": digest,
            "claim_key": digest,
        }
    if kind != INITIAL_ENTRY_PREDECESSOR_KIND:
        raise VerificationError("predecessor_digest_kind_unsupported")
    return {
        "kind": kind,
        "digest": digest,
        "claim_key": _initial_entry_claim_key(digest),
    }


def _inside(root: Path, relative: Any) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise VerificationError("artifact_path_invalid")
    path = root
    for part in Path(relative).parts:
        path = path / part
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise VerificationError("artifact_symlink_forbidden")
        if stat.S_ISDIR(st.st_mode) and (
            st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700
        ):
            raise VerificationError("artifact_directory_owner_or_mode_invalid")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise VerificationError("artifact_outside_packet_root") from exc
    return resolved


def _keyset(path: Path, expected_hash: str) -> dict[str, dict[str, str]]:
    raw = _read(path, owner=os.getuid())
    if _sha(raw) != expected_hash:
        raise VerificationError("out_of_band_trust_store_pin_mismatch")
    value = _json(raw)
    if _canonical(value) + b"\n" != raw or value.get("schema") != "senses-preflight-trust-store-v1":
        raise VerificationError("trust_store_format_invalid")
    keys = value.get("keys")
    if not isinstance(keys, list) or not keys:
        raise VerificationError("trust_store_empty")
    result = {}
    public_key_roles: dict[bytes, str] = {}
    for row in keys:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("key_id"), str)
            or not row["key_id"]
            or row["key_id"] in result
            or row.get("role")
            not in {"host_attestor", "helius_independent_checker", "sol_reviewer"}
            or not isinstance(row.get("public_key_pem"), str)
        ):
            raise VerificationError("trust_store_key_invalid")
        try:
            der = base64.b64decode(
                "".join(
                    line
                    for line in row["public_key_pem"].splitlines()
                    if not line.startswith("-----")
                ),
                validate=True,
            )
        except Exception as exc:
            raise VerificationError("trust_store_public_key_invalid") from exc
        # Ed25519 SubjectPublicKeyInfo DER is 44 bytes with this fixed prefix.
        if len(der) != 44 or der[:12] != bytes.fromhex("302a300506032b6570032100"):
            raise VerificationError("trust_store_key_not_ed25519")
        public_key = der[12:]
        if public_key in public_key_roles:
            raise VerificationError("trust_store_public_key_reused_across_roles")
        public_key_roles[public_key] = row["role"]
        result[row["key_id"]] = row
    return result


def _openssl_identity(path: Path, expected_hash: str, expected_version: str) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        raise VerificationError("openssl_tool_unavailable") from exc
    if (
        not path.is_absolute()
        or stat.S_ISLNK(st.st_mode)
        or not stat.S_ISREG(st.st_mode)
        or not st.st_mode & 0o111
        or _sha(_read(path, owner=st.st_uid, max_bytes=128 * 1024 * 1024)) != expected_hash
    ):
        raise VerificationError("openssl_tool_hash_or_type_invalid")
    result = subprocess.run([str(path), "version"], capture_output=True, text=True, check=False)
    version = result.stdout.strip()
    if result.returncode or version != expected_version:
        raise VerificationError("openssl_tool_version_mismatch")


def _verify_signature(
    *,
    openssl_path: Path,
    public_key_pem: str,
    message: bytes,
    signature: bytes,
) -> None:
    if len(signature) != 64:
        raise VerificationError("ed25519_signature_size_invalid")
    with tempfile.TemporaryDirectory(prefix="senses-preflight-") as temporary:
        base = Path(temporary)
        key_file = base / "trusted-public.pem"
        message_file = base / "message.bin"
        signature_file = base / "signature.bin"
        key_file.write_text(public_key_pem, encoding="ascii")
        message_file.write_bytes(message)
        signature_file.write_bytes(signature)
        for path in (key_file, message_file, signature_file):
            path.chmod(0o600)
        result = subprocess.run(
            [
                str(openssl_path),
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(key_file),
                "-sigfile",
                str(signature_file),
                "-rawin",
                "-in",
                str(message_file),
            ],
            capture_output=True,
            check=False,
        )
    if result.returncode != 0:
        raise VerificationError("ed25519_signature_invalid")


def _evidence(
    root: Path,
    ref: Any,
    *,
    keyset: dict[str, dict[str, str]],
    openssl_path: Path,
    role: str,
) -> tuple[dict[str, Any], bytes, str]:
    if not isinstance(ref, dict):
        raise VerificationError("evidence_reference_missing")
    payload_path = _inside(root, ref.get("payload_path"))
    signature_path = _inside(root, ref.get("signature_path"))
    payload = _read(payload_path, owner=os.getuid())
    signature = _read(signature_path, owner=os.getuid(), max_bytes=128)
    if _sha(payload) != ref.get("payload_sha256") or _sha(signature) != ref.get("signature_sha256"):
        raise VerificationError("evidence_reference_hash_mismatch")
    signer = keyset.get(ref.get("signer_id"))
    if signer is None or signer.get("role") != role or ref.get("role") != role:
        raise VerificationError("evidence_signer_role_untrusted")
    _verify_signature(
        openssl_path=openssl_path,
        public_key_pem=signer["public_key_pem"],
        message=payload,
        signature=signature,
    )
    value = _json(payload)
    if _canonical(value) + b"\n" != payload:
        raise VerificationError("signed_evidence_noncanonical")
    return value, signature, _sha(payload)


def _digest_without_review(manifest: dict[str, Any]) -> str:
    body = dict(manifest)
    body.pop("review_ref", None)
    return _sha(_canonical(body))


def _validate_schedule(plan: dict[str, Any], entries: Any) -> tuple[dict[str, int], dict[str, Any]]:
    buckets = plan.get("buckets")
    if (
        not isinstance(buckets, list)
        or not isinstance(entries, list)
        or len(entries) != len(buckets)
    ):
        raise VerificationError("schedule_envelope_roster_mismatch")
    totals = {k: 0 for k in ("requests", "raw_bytes", "stored_bytes")}
    per_day: dict[int, dict[str, int]] = {}
    method_counts: Counter[str] = Counter()
    request_total = 0
    for bucket, row in zip(buckets, entries, strict=True):
        if not isinstance(row, dict) or any(
            row.get(key) != bucket.get(key) for key in ("bucket_id", "kind", "start_ns", "end_ns")
        ):
            raise VerificationError("schedule_envelope_bucket_mismatch")
        request_count = 1 if bucket["kind"] == "anchor" else 34
        for key in ("raw_bytes", "stored_bytes", "width"):
            if type(row.get(key)) is not int or row[key] <= 0:
                raise VerificationError("schedule_envelope_cap_invalid")
        if row.get("requests") != request_count or row["width"] > plan["caps"]["width"]:
            raise VerificationError("schedule_envelope_operation_contract_invalid")
        totals["requests"] += request_count
        request_total += request_count
        if bucket["kind"] == "anchor":
            methods = Counter({"getSlot": 1})
        else:
            methods = Counter({"getSlot": 1, "getBlocks": 1, "getBlock": 32})
        method_counts.update(methods)
        first_day = bucket["start_ns"] // DAY_NS
        last_day = (bucket["end_ns"] - 1) // DAY_NS
        for day in range(first_day, last_day + 1):
            group = per_day.setdefault(day, {key: 0 for key in totals})
            group["requests"] += request_count
            # Schedule row byte values are descriptive only. Use enforceable parent caps
            # as the possible daily capture bound on every UTC day the bucket intersects.
            group["raw_bytes"] += plan["caps"]["raw_bytes"]
            group["stored_bytes"] += plan["caps"]["stored_bytes"]
    caps = plan["caps"]
    totals["raw_bytes"] = caps["raw_bytes"]
    totals["stored_bytes"] = caps["stored_bytes"]
    if totals["requests"] > caps["requests"]:
        raise VerificationError("schedule_envelope_exceeds_lease_caps")
    daily_caps = {
        "requests": caps["daily_requests"],
        "raw_bytes": caps["daily_raw_bytes"],
        "stored_bytes": caps["daily_stored_bytes"],
    }
    if any(value[key] > daily_caps[key] for value in per_day.values() for key in daily_caps):
        raise VerificationError("schedule_envelope_exceeds_daily_caps")
    return totals, {
        "per_day": per_day,
        "request_count": request_total,
        "method_counts": dict(method_counts),
    }


def _lineage(
    root: Path, manifest: dict[str, Any], successor_plan: dict[str, Any]
) -> dict[str, Any]:
    predecessor_kind = manifest.get("predecessor_digest_kind")
    if predecessor_kind is not None:
        if predecessor_kind != INITIAL_ENTRY_PREDECESSOR_KIND:
            raise VerificationError("predecessor_digest_kind_unsupported")
        return _initial_entry_lineage(root, manifest, successor_plan)
    if (
        "initial_entry" in manifest
        or successor_plan.get("predecessor_digest_kind") == INITIAL_ENTRY_PREDECESSOR_KIND
    ):
        raise VerificationError("initial_entry_manifest_kind_missing")
    anchor = manifest.get("lineage_anchor")
    rows = manifest.get("predecessor_handoffs")
    if (
        not isinstance(anchor, dict)
        or not HEX64.fullmatch(str(anchor.get("terminal_sha256", "")))
        or type(anchor.get("frontier_slot")) is not int
        or type(anchor.get("terminal_at_ns")) is not int
        or not isinstance(anchor.get("gaps"), list)
        or any(not isinstance(g, str) or not g for g in anchor["gaps"])
        or not isinstance(anchor.get("unknown_intervals"), list)
        or not isinstance(rows, list)
        or not rows
    ):
        raise VerificationError("lineage_anchor_or_chain_missing")
    prior_terminal = anchor["terminal_sha256"]
    prior_frontier = anchor["frontier_slot"]
    prior_gaps = anchor["gaps"]
    prior_unknown = anchor["unknown_intervals"]
    prior_at_ns = anchor["terminal_at_ns"]
    seen_leases = set()
    verified = []
    for item in rows:
        if not isinstance(item, dict):
            raise VerificationError("lineage_entry_invalid")
        lease_root = _inside(root, item.get("root"))
        result = calendar_verify.verify_handoff(lease_root)
        if (
            not result.get("valid")
            or result.get("handoff_sha256") != item.get("handoff_sha256")
            or result["lease_id"] in seen_leases
        ):
            raise VerificationError("lineage_handoff_invalid_or_duplicate")
        plan_path = _inside(lease_root, "plan-v2.json")
        plan_raw = _read(plan_path, owner=os.getuid())
        handoff_plan = _json(plan_raw)
        if _canonical(handoff_plan) + b"\n" != plan_raw or _sha(plan_raw) != result.get(
            "plan_sha256"
        ):
            raise VerificationError("lineage_plan_noncanonical")
        predecessor = handoff_plan["predecessor"]
        if (
            predecessor.get("terminal_sha256") != prior_terminal
            or predecessor.get("frontier_slot") != prior_frontier
            or predecessor.get("gaps") != prior_gaps
            or predecessor.get("unknown_intervals") != prior_unknown
            or type(handoff_plan.get("created_ns")) is not int
            or handoff_plan["created_ns"] < prior_at_ns
            or not handoff_plan.get("buckets")
            or handoff_plan["buckets"][0].get("start_ns", 0) < prior_at_ns
        ):
            raise VerificationError("lineage_predecessor_link_invalid")
        prior_terminal = result["handoff_sha256"]
        prior_frontier = result["frontier_slot"]
        prior_gaps = [gap["bucket_id"] for gap in result["gaps"]]
        prior_unknown = result["unknown_intervals"]
        prior_at_ns = result["terminal_at_ns"]
        seen_leases.add(result["lease_id"])
        verified.append(
            {
                "lease_id": result["lease_id"],
                "handoff_sha256": prior_terminal,
                "terminal_at_ns": prior_at_ns,
                "gaps": prior_gaps,
                "unknown_intervals": prior_unknown,
            }
        )
    predecessor = successor_plan.get("predecessor")
    if (
        predecessor.get("terminal_sha256") != prior_terminal
        or predecessor.get("frontier_slot") != prior_frontier
        or predecessor.get("gaps") != prior_gaps
        or predecessor.get("unknown_intervals") != prior_unknown
        or successor_plan.get("created_ns", 0) < prior_at_ns
        or not successor_plan.get("buckets")
        or successor_plan["buckets"][0].get("start_ns", 0) < prior_at_ns
        or successor_plan.get("lease_id") in seen_leases
    ):
        raise VerificationError("successor_predecessor_link_invalid")
    return {
        "lineage_anchor": anchor,
        "verified_handoffs": verified,
        "terminal_at_ns": prior_at_ns,
        "gaps": prior_gaps,
        "unknown_intervals": prior_unknown,
    }


def _file_identity(path: Path) -> dict[str, int]:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1:
        raise VerificationError("initial_entry_artifact_identity_invalid")
    return {
        "device": st.st_dev,
        "inode": st.st_ino,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }


def _initial_entry_lineage(
    root: Path, manifest: dict[str, Any], successor_plan: dict[str, Any]
) -> dict[str, Any]:
    entry = manifest.get("initial_entry")
    required = {
        "certificate_repo_path",
        "certificate_file_sha256",
        "predecessor_digest_sha256",
        "candidate_path",
        "candidate_sha256",
        "candidate_inputs",
    }
    if (
        not isinstance(entry, dict)
        or set(entry) != required
        or entry.get("certificate_repo_path") != INITIAL_ENTRY_CERTIFICATE_REPO_PATH
        or "lineage_anchor" in manifest
        or "predecessor_handoffs" in manifest
    ):
        raise VerificationError("initial_entry_manifest_schema_invalid")
    repository_root = Path(__file__).resolve().parents[1]
    certificate_path = repository_root / INITIAL_ENTRY_CERTIFICATE_REPO_PATH
    if certificate_path.is_symlink():
        raise VerificationError("initial_entry_certificate_symlink_forbidden")
    certificate_raw = _read(certificate_path, owner=os.getuid())
    certificate_sha = _sha(certificate_raw)
    if certificate_sha != entry.get("certificate_file_sha256"):
        raise VerificationError("initial_entry_certificate_file_hash_mismatch")
    certificate = _json(certificate_raw)
    source_receipt_raw = _read(discontinuity_verify.RECEIPT, owner=os.getuid())
    source_receipt = _json(source_receipt_raw)
    source_receipt_sha = _sha(source_receipt_raw)
    discontinuity_verify.verify_certificate_data(certificate, source_receipt, source_receipt_sha)
    predecessor_digest = _sha(discontinuity_verify._canonical(certificate))
    if predecessor_digest != entry.get("predecessor_digest_sha256"):
        raise VerificationError("initial_entry_predecessor_digest_mismatch")

    candidate_path = _inside(root, entry.get("candidate_path"))
    if candidate_path == _inside(root, manifest.get("successor_plan_path")):
        raise VerificationError("initial_entry_candidate_plan_path_aliased")
    candidate_raw = _read(candidate_path, owner=os.getuid())
    if _sha(candidate_raw) != entry.get("candidate_sha256"):
        raise VerificationError("initial_entry_candidate_file_hash_mismatch")
    candidate = _json(candidate_raw)
    if _canonical(candidate) + b"\n" != candidate_raw:
        raise VerificationError("initial_entry_candidate_noncanonical")
    try:
        candidate_proof = initial_candidate_verify.verify_candidate_data(
            candidate,
            certificate,
            certificate_sha,
            entry.get("candidate_inputs"),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise VerificationError(f"initial_entry_candidate_invalid:{exc}") from exc
    if candidate.get("plan") != successor_plan:
        raise VerificationError("initial_entry_candidate_plan_mismatch")
    if (
        successor_plan.get("predecessor_digest_kind") != INITIAL_ENTRY_PREDECESSOR_KIND
        or successor_plan.get("initial_entry_certificate", {}).get("certificate_file_sha256")
        != certificate_sha
        or successor_plan.get("initial_entry_certificate", {}).get("predecessor_digest_sha256")
        != predecessor_digest
        or successor_plan.get("predecessor", {}).get("terminal_sha256") != predecessor_digest
        or successor_plan.get("predecessor", {}).get("certificate_file_sha256") != certificate_sha
        or successor_plan.get("activation_authorized") is not False
    ):
        raise VerificationError("initial_entry_typed_predecessor_binding_invalid")
    plan_path = _inside(root, manifest.get("successor_plan_path"))
    plan_raw = _read(plan_path, owner=os.getuid())
    if _canonical(successor_plan) + b"\n" != plan_raw:
        raise VerificationError("initial_entry_plan_bytes_changed")
    return {
        "lineage_kind": INITIAL_ENTRY_PREDECESSOR_KIND,
        "predecessor_digest_kind": INITIAL_ENTRY_PREDECESSOR_KIND,
        "predecessor_digest_sha256": predecessor_digest,
        "certificate_file_sha256": certificate_sha,
        "source_receipt_sha256": source_receipt_sha,
        "candidate_sha256": candidate_proof["candidate_sha256"],
        "candidate_plan_sha256": candidate_proof["plan_sha256"],
        "terminal_at_ns": certificate["terminal_observation_ns"],
        "frontier_slot": certificate["frontier_slot"],
        "gaps": [gap["id"] for gap in certificate["gaps"]],
        "unknown_intervals": certificate["unknown_intervals"],
        "normal_v3_handoff": False,
        "unknown_interval_closed": False,
        "artifact_identity": {
            "certificate": _file_identity(certificate_path),
            "candidate": _file_identity(candidate_path),
            "plan": _file_identity(plan_path),
        },
    }


def _verify_packet(
    root: Path,
    *,
    registry_root: Path | None,
    trust_store_path: Path | None,
    trust_store_sha256: str | None,
    openssl_path: Path | None,
    openssl_sha256: str | None,
    verifier_sha256: str | None,
    helius_price_source_sha256: str | None,
    helius_price_table_sha256: str | None,
    clock_source_id: str | None,
    capture_root_path: Path | None,
    now_ns: int,
    allow_existing_claim: bool = False,
) -> dict[str, Any]:
    if any(
        value is None
        for value in (
            registry_root,
            trust_store_path,
            trust_store_sha256,
            openssl_path,
            openssl_sha256,
            verifier_sha256,
            helius_price_source_sha256,
            helius_price_table_sha256,
            clock_source_id,
            capture_root_path,
        )
    ):
        raise VerificationError("out_of_band_configuration_missing")
    root = Path(root)
    if stat.S_ISLNK(root.lstat().st_mode):
        raise VerificationError("packet_root_symlink_forbidden")
    root = root.resolve(strict=True)
    root_stat = root.lstat()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != os.getuid()
        or stat.S_IMODE(root_stat.st_mode) != 0o700
    ):
        raise VerificationError("packet_root_owner_or_mode_invalid")
    registry_root, registry_id = _registry_identity(registry_root)
    registry_claims = _registry_claims(registry_root)
    if not HEX64.fullmatch(trust_store_sha256) or not HEX64.fullmatch(openssl_sha256):
        raise VerificationError("out_of_band_source_pin_invalid")
    if not HEX64.fullmatch(helius_price_source_sha256) or not HEX64.fullmatch(
        helius_price_table_sha256
    ):
        raise VerificationError("helius_price_source_pin_missing_or_invalid")
    if not isinstance(clock_source_id, str) or not clock_source_id:
        raise VerificationError("trusted_clock_source_pin_missing_or_invalid")
    if not isinstance(capture_root_path, (Path, str)) or not str(capture_root_path):
        raise VerificationError("runtime_capture_root_pin_missing_or_invalid")
    actual_verifier_sha = _sha(Path(__file__).resolve().read_bytes())
    if verifier_sha256 != actual_verifier_sha:
        raise VerificationError("independent_verifier_source_pin_mismatch")
    keyset = _keyset(Path(trust_store_path), trust_store_sha256)
    manifest_raw = _read(root / "preflight-v1.json", owner=os.getuid())
    manifest = _json(manifest_raw)
    if _canonical(manifest) + b"\n" != manifest_raw or manifest.get("schema") != SCHEMA:
        raise VerificationError("packet_manifest_noncanonical_or_schema_invalid")
    if manifest.get("activation_scope") != "offline-preflight-only":
        raise VerificationError("activation_scope_invalid")
    if manifest.get("claim_registry_id") != registry_id:
        raise VerificationError("claim_registry_out_of_band_identity_mismatch")
    toolchain = manifest.get("crypto_tool")
    if (
        not isinstance(toolchain, dict)
        or toolchain.get("path") != str(openssl_path)
        or toolchain.get("sha256") != openssl_sha256
    ):
        raise VerificationError("crypto_tool_packet_binding_invalid")
    _openssl_identity(Path(openssl_path), openssl_sha256, toolchain.get("version", ""))
    plan_rel = manifest.get("successor_plan_path")
    plan_path = _inside(root, plan_rel)
    plan_bytes = _read(plan_path, owner=os.getuid())
    plan = _json(plan_bytes)
    if _canonical(plan) + b"\n" != plan_bytes:
        raise VerificationError("successor_plan_noncanonical")
    if plan.get("schema") != "brain-senses-prospective-operation-envelope-v2":
        raise VerificationError("successor_plan_schema_invalid")
    initial_entry_plan = plan.get("predecessor_digest_kind") == INITIAL_ENTRY_PREDECESSOR_KIND
    buckets = plan.get("buckets")
    if (
        type(plan.get("created_ns")) is not int
        or type(plan.get("expires_ns")) is not int
        or not plan["created_ns"] <= now_ns < plan["expires_ns"]
        or not isinstance(plan.get("lease_id"), str)
        or not plan["lease_id"]
        or not isinstance(plan.get("source_sha256"), dict)
        or not plan["source_sha256"]
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or not HEX64.fullmatch(digest)
            for name, digest in plan["source_sha256"].items()
        )
        or not isinstance(plan.get("caps"), dict)
        or not {
            "requests",
            "raw_bytes",
            "stored_bytes",
            "physical_bytes",
            "width",
            "daily_requests",
            "daily_raw_bytes",
            "daily_stored_bytes",
        }.issubset(plan["caps"])
        or any(type(value) is not int or value <= 0 for value in plan["caps"].values())
        or plan["caps"].get("physical_bytes", 0) < MINIMUM_FLOOR
        or not isinstance(buckets, list)
        or not buckets
        or any(
            not isinstance(bucket, dict)
            or not isinstance(bucket.get("bucket_id"), str)
            or not bucket["bucket_id"]
            or bucket.get("kind") not in {"anchor", "frame"}
            or type(bucket.get("start_ns")) is not int
            or type(bucket.get("end_ns")) is not int
            or bucket["start_ns"] <= now_ns
            or bucket["start_ns"] < plan["created_ns"]
            or bucket["end_ns"] <= bucket["start_ns"]
            or bucket["end_ns"] > plan["expires_ns"]
            for bucket in buckets
        )
        or buckets[0].get("kind") != "anchor"
        or len({bucket.get("bucket_id") for bucket in buckets if isinstance(bucket, dict)})
        != len(buckets)
        or any(
            buckets[index]["start_ns"] < buckets[index - 1]["end_ns"]
            for index in range(1, len(buckets))
        )
    ):
        raise VerificationError("successor_lease_window_or_caps_invalid")
    successor = manifest.get("successor")
    schedule = manifest.get("schedule")
    if (
        not isinstance(successor, dict)
        or manifest.get("successor_plan_path") != "successor-plan-v2.json"
    ):
        raise VerificationError("successor_binding_missing")
    schedule_sha = _sha(_canonical(schedule))
    plan_schedule_sha = (
        _sha(_canonical(plan.get("buckets"))) if initial_entry_plan else schedule_sha
    )
    caps_sha = _sha(_canonical(plan.get("caps")))
    source_sha = _sha(_canonical(plan.get("source_sha256")))
    plan_sha = _sha(plan_bytes)
    code = plan.get("calendar_contract")
    if not isinstance(code, dict):
        raise VerificationError("successor_code_binding_missing")
    code_files = {
        "calendar_driver": Path(calendar_verify.__file__).with_name(
            "brain_senses_finite_calendar_v3.py"
        ),
        "calendar_verifier": Path(calendar_verify.__file__).resolve(),
        "operation_bridge": Path(__file__).with_name("brain_senses_operation_bundle_v2.py"),
        "operation_verifier": Path(__file__).with_name(
            "brain_senses_operation_bundle_verifier_v2.py"
        ),
        "preflight_driver": Path(__file__).with_name("brain_senses_activation_preflight_v1.py"),
        "preflight_verifier": Path(__file__).resolve(),
    }
    actual_code = {key: _sha(path.read_bytes()) for key, path in code_files.items()}
    if initial_entry_plan:
        tools_root = Path(__file__).resolve().parent
        initial_code_files = {
            "initial_entry_compiler": tools_root
            / "brain_senses_initial_entry_calendar_compiler_v1.py",
            "initial_entry_verifier": Path(initial_candidate_verify.__file__).resolve(),
            "discontinuity_verifier": Path(discontinuity_verify.__file__).resolve(),
        }
        actual_code.update(
            {key: _sha(path.read_bytes()) for key, path in initial_code_files.items()}
        )
    expected_contract = {
        "schema": calendar_verify.CALENDAR_SCHEMA,
        "handoff_schema": calendar_verify.HANDOFF_SCHEMA,
        "verifier_protocol": calendar_verify.HANDOFF_VERIFIER,
        "driver_sha256": actual_code["calendar_driver"],
        "handoff_verifier_sha256": actual_code["calendar_verifier"],
        "bridge_sha256": actual_code["operation_bridge"],
        "bridge_verifier_sha256": actual_code["operation_verifier"],
    }
    if any(code.get(key) != value for key, value in expected_contract.items()):
        raise VerificationError("successor_calendar_code_source_mismatch")
    actual_code["preflight_verifier"] = verifier_sha256
    if manifest.get("code_sha256") != actual_code:
        raise VerificationError("preflight_code_source_binding_invalid")
    code_sha = _sha(_canonical(actual_code))
    if (
        successor.get("lease_id") != plan.get("lease_id")
        or successor.get("plan_sha256") != plan_sha
        or successor.get("schedule_sha256") != schedule_sha
        or (not initial_entry_plan and plan.get("schedule_sha256") != plan_schedule_sha)
        or successor.get("caps_sha256") != caps_sha
        or successor.get("source_sha256") != source_sha
        or successor.get("code_sha256") != code_sha
    ):
        raise VerificationError("successor_plan_schedule_source_code_or_caps_binding_invalid")
    totals, daily = _validate_schedule(plan, schedule)
    lineage = _lineage(root, manifest, plan)
    predecessor_identity = _predecessor_claim_identity(plan)
    predecessor_terminal = predecessor_identity["digest"]
    predecessor_claim_key = predecessor_identity["claim_key"]
    existing_claim = registry_claims.get(predecessor_claim_key)
    if predecessor_identity["kind"] == INITIAL_ENTRY_PREDECESSOR_KIND and any(
        claim.get("schema") == CLAIM_SCHEMA
        and claim.get("predecessor_terminal_sha256") == predecessor_terminal
        for claim in registry_claims.values()
    ):
        raise VerificationError("initial_entry_predecessor_legacy_claim_alias")
    if existing_claim is not None and not allow_existing_claim:
        raise VerificationError("predecessor_successor_already_claimed")
    if any(
        key != predecessor_claim_key and claim.get("lease_id") == plan["lease_id"]
        for key, claim in registry_claims.items()
    ):
        raise VerificationError("successor_lease_id_already_claimed")
    host, _, host_sha = _evidence(
        root,
        manifest.get("host_evidence"),
        keyset=keyset,
        openssl_path=Path(openssl_path),
        role="host_attestor",
    )
    charge, _, charge_sha = _evidence(
        root,
        manifest.get("helius_evidence"),
        keyset=keyset,
        openssl_path=Path(openssl_path),
        role="helius_independent_checker",
    )
    freshness = manifest.get("freshness_limits_ns")
    if not isinstance(freshness, dict) or any(
        type(freshness.get(k)) is not int or freshness[k] <= 0
        for k in ("host_clock", "disk", "helius_charge", "sol_review")
    ):
        raise VerificationError("freshness_policy_invalid")
    clock_now = host.get("clock_now_ns")
    host_sample = host.get("sampled_at_ns")
    disk_sample = host.get("disk_sampled_at_ns")
    if (
        host.get("schema") != "senses-local-host-attestation-v1"
        or type(clock_now) is not int
        or type(host_sample) is not int
        or type(disk_sample) is not int
        or host.get("owner_uid") != os.getuid()
        or host.get("lease_id") != plan["lease_id"]
        or host.get("plan_sha256") != plan_sha
        or host.get("schedule_sha256") != schedule_sha
        or host.get("clock_source_id") != clock_source_id
        or manifest.get("clock_source_id") != clock_source_id
        or host.get("free_bytes", 0) < MINIMUM_FLOOR
        or abs(clock_now - now_ns) > freshness["host_clock"]
        or not 0 <= now_ns - host_sample <= freshness["host_clock"]
        or not 0 <= now_ns - disk_sample <= freshness["disk"]
    ):
        raise VerificationError("trusted_local_clock_or_disk_evidence_stale_or_insufficient")
    if initial_entry_plan and (
        plan.get("clock_source_id") != clock_source_id
        or plan.get("clock_observed_ns") != clock_now
        or plan.get("created_ns", 0) < clock_now
        or not plan.get("buckets")
        or plan["buckets"][0].get("start_ns", 0) <= clock_now
    ):
        raise VerificationError("initial_entry_clock_or_first_anchor_not_future")
    pinned_capture_root = Path(capture_root_path)
    if not pinned_capture_root.is_absolute():
        raise VerificationError("runtime_capture_root_pin_missing_or_invalid")
    pinned_capture_root = pinned_capture_root.resolve(strict=True)
    capture_root_value = manifest.get("capture_root_path")
    if not isinstance(capture_root_value, str) or not Path(capture_root_value).is_absolute():
        raise VerificationError("capture_root_identity_missing")
    capture_root = Path(capture_root_value)
    if stat.S_ISLNK(capture_root.lstat().st_mode):
        raise VerificationError("capture_root_symlink_forbidden")
    capture_root = capture_root.resolve(strict=True)
    if capture_root != pinned_capture_root:
        raise VerificationError("runtime_capture_root_out_of_band_mismatch")
    capture_stat = capture_root.stat()
    if (
        not stat.S_ISDIR(capture_stat.st_mode)
        or capture_stat.st_uid != os.getuid()
        or str(capture_root) != capture_root_value
    ):
        raise VerificationError("capture_root_identity_invalid")
    current_vfs = os.statvfs(capture_root)
    current_free_bytes = current_vfs.f_bavail * current_vfs.f_frsize
    current_filesystem_id = str(getattr(current_vfs, "f_fsid", ""))
    if (
        host.get("capture_root_path") != str(capture_root)
        or host.get("capture_root_dev") != capture_stat.st_dev
        or host.get("capture_root_ino") != capture_stat.st_ino
        or host.get("filesystem_id") != current_filesystem_id
        or not current_filesystem_id
    ):
        raise VerificationError("signed_disk_evidence_capture_root_mismatch")
    storage = manifest.get("storage_envelope")
    if (
        not isinstance(storage, dict)
        or storage.get("schema") != "senses-full-lease-storage-envelope-v1"
        or storage.get("reserve_policy") != "explicit_reviewed_minimum"
        or type(storage.get("reserve_bytes")) is not int
        or storage["reserve_bytes"] <= 0
    ):
        raise VerificationError("storage_envelope_invalid")
    capture_bound = plan["caps"]["raw_bytes"] + plan["caps"]["stored_bytes"]
    metadata_bound = (
        plan["caps"]["requests"] * METADATA_PER_REQUEST + len(schedule) * METADATA_PER_OPERATION
    )
    floor = max(MINIMUM_FLOOR, plan["caps"]["physical_bytes"])
    required_free = floor + capture_bound + metadata_bound + storage["reserve_bytes"]
    if (
        storage.get("capture_bound_bytes") != capture_bound
        or storage.get("metadata_bound_bytes") != metadata_bound
        or storage.get("required_free_bytes") != required_free
        or host.get("free_bytes", 0) < required_free
        or current_free_bytes < required_free
    ):
        raise VerificationError("full_lease_storage_floor_not_met")
    charge_sample = charge.get("sampled_at_ns")
    price_model = charge.get("method_price_model")
    method_counts = daily["method_counts"]
    if not isinstance(price_model, dict):
        raise VerificationError("helius_signed_method_price_model_missing")
    prices = price_model.get("units_per_request")
    if (
        price_model.get("schema") != "helius-plan-method-price-model-v1"
        or price_model.get("plan_sha256") != plan_sha
        or price_model.get("schedule_sha256") != schedule_sha
        or price_model.get("source_sha256") != plan.get("source_sha256")
        or price_model.get("method_counts") != method_counts
        or price_model.get("source_artifact_sha256") != helius_price_source_sha256
        or not isinstance(price_model.get("unit"), str)
        or not price_model["unit"]
        or manifest.get("helius_price_source_sha256") != helius_price_source_sha256
        or _sha(_canonical(price_model)) != helius_price_table_sha256
        or manifest.get("helius_price_table_sha256") != helius_price_table_sha256
        or not isinstance(prices, dict)
        or set(prices) != set(method_counts)
        or any(type(value) is not int or value <= 0 for value in prices.values())
    ):
        raise VerificationError("helius_signed_method_price_model_binding_invalid")
    recomputed_charge_upper = sum(
        method_counts[method] * prices[method] for method in method_counts
    )
    if (
        charge.get("schema") != "helius-charge-headroom-attestation-v1"
        or type(charge_sample) is not int
        or not 0 <= now_ns - charge_sample <= freshness["helius_charge"]
        or charge.get("account_id") != manifest.get("helius_account_id")
        or charge.get("plan_sha256") != plan_sha
        or charge.get("schedule_sha256") != schedule_sha
        or charge.get("source_sha256") != plan.get("source_sha256")
        or not isinstance(charge.get("unit"), str)
        or charge.get("unit") != price_model.get("unit")
        or not isinstance(charge.get("independent_check_method"), str)
        or not charge["independent_check_method"]
        or not HEX64.fullmatch(str(charge.get("account_artifact_sha256", "")))
        or type(charge.get("available_units")) is not int
        or type(charge.get("committed_units")) is not int
        or type(charge.get("lease_upper_bound_units")) is not int
        or type(charge.get("minimum_headroom_units")) is not int
        or min(
            charge["available_units"], charge["committed_units"], charge["lease_upper_bound_units"]
        )
        < 0
        or charge["minimum_headroom_units"] <= 0
        or charge["lease_upper_bound_units"] <= 0
        or charge["lease_upper_bound_units"] != recomputed_charge_upper
        or charge["lease_upper_bound_units"] != manifest.get("lease_charge_upper_bound_units")
        or charge["available_units"] - charge["committed_units"]
        < charge["lease_upper_bound_units"] + charge["minimum_headroom_units"]
    ):
        raise VerificationError("helius_charge_or_headroom_evidence_invalid")
    manifest_digest = _digest_without_review(manifest)
    review, review_signature, review_sha = _evidence(
        root,
        manifest.get("review_ref"),
        keyset=keyset,
        openssl_path=Path(openssl_path),
        role="sol_reviewer",
    )
    review_ref = manifest["review_ref"]
    reviewer = keyset.get(review_ref.get("signer_id"))
    if (
        review.get("schema") != "senses-sol-preflight-decision-v1"
        or review.get("decision") != "approve_offline_preflight"
        or review.get("reviewer_name") != "Sol"
        or not isinstance(reviewer, dict)
        or reviewer.get("name") != "Sol"
        or review.get("packet_digest") != manifest_digest
        or review.get("lease_id") != plan["lease_id"]
        or review.get("lineage_sha256") != _sha(_canonical(lineage))
        or review.get("host_evidence_sha256") != host_sha
        or review.get("helius_evidence_sha256") != charge_sha
        or review.get("minimum_churn_reserve_bytes") != storage["reserve_bytes"]
        or type(review.get("reviewed_at_ns")) is not int
        or not 0 <= now_ns - review["reviewed_at_ns"] <= freshness["sol_review"]
        or review.get("scope") != "offline_preflight_eligibility_only"
        or review.get("trust_store_sha256") != trust_store_sha256
        or review.get("freshness_limits_ns") != freshness
    ):
        raise VerificationError("authenticated_sol_decision_invalid_or_stale")
    return {
        "eligible": True,
        "activation_authorized": False,
        "lease_id": plan["lease_id"],
        "predecessor_terminal_sha256": predecessor_terminal,
        "predecessor_digest_kind": predecessor_identity["kind"],
        "predecessor_digest_sha256": predecessor_terminal,
        "predecessor_claim_key_sha256": predecessor_claim_key,
        "claim_exists": existing_claim is not None,
        "packet_digest": manifest_digest,
        "plan_sha256": plan_sha,
        "schedule_sha256": schedule_sha,
        "source_sha256": source_sha,
        "code_sha256": code_sha,
        "caps_sha256": caps_sha,
        "lineage": lineage,
        "usage_upper_bound": totals,
        "daily_upper_bound": daily["per_day"],
        "storage": {
            "capture_bound_bytes": capture_bound,
            "metadata_bound_bytes": metadata_bound,
            "required_free_bytes": required_free,
            "observed_free_bytes": host["free_bytes"],
        },
        "helius_charge": {
            "available_units": charge["available_units"],
            "committed_units": charge["committed_units"],
            "lease_upper_bound_units": charge["lease_upper_bound_units"],
            "minimum_headroom_units": charge["minimum_headroom_units"],
        },
        "evidence_sha256": {
            "host": host_sha,
            "helius": charge_sha,
            "sol_review": review_sha,
            "sol_signature": _sha(review_signature),
        },
        "authentication_scope": (
            "Ed25519 signatures validated against the caller-pinned trust store; signed claims "
            "bind packet/evidence bytes but do not prove the claims truthful or "
            "lineage completeness "
            "beyond the externally authenticated Sol review."
        ),
        "runtime_capture_root_binding": (
            "The signed disk sample was checked against this caller-pinned path. This offline "
            "verifier is not wired into the calendar or bridge; any eventual adapter must be "
            "separately reviewed to consume this exact path."
        ),
        "clock_source_binding": (
            "The signed host clock_source_id must match the caller-supplied out-of-band pin and "
            "the reviewed manifest value."
        ),
    }


def verify_packet(
    root: Path,
    *,
    registry_root: Path | None = None,
    trust_store_path: Path | None = None,
    trust_store_sha256: str | None = None,
    openssl_path: Path | None = None,
    openssl_sha256: str | None = None,
    verifier_sha256: str | None = None,
    helius_price_source_sha256: str | None = None,
    helius_price_table_sha256: str | None = None,
    clock_source_id: str | None = None,
    capture_root_path: Path | None = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Check offline evidence against pinned clock and future runtime capture-root identities.

    Eligibility is an offline preflight result only. It never authorizes or performs activation;
    the calendar/bridge do not consume this gate, so a future adapter must separately bind and
    use the same capture_root_path.
    """
    try:
        return _verify_packet(
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
            now_ns=time.time_ns() if now_ns is None else now_ns,
        )
    except Exception as exc:
        return {
            "eligible": False,
            "activation_authorized": False,
            "failure": str(exc) if isinstance(exc, VerificationError) else type(exc).__name__,
        }


def verify_claim(
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
    now_ns: int | None = None,
) -> dict[str, Any]:
    root = Path(root)
    registry_root, _ = _registry_identity(registry_root)
    plan_raw = _read(root / "successor-plan-v2.json", owner=os.getuid())
    plan = _json(plan_raw)
    predecessor_identity = _predecessor_claim_identity(plan)
    predecessor_terminal = predecessor_identity["digest"]
    claim_key = predecessor_identity["claim_key"]
    claims = _registry_claims(registry_root)
    claim = claims.get(claim_key)
    if not isinstance(claim, dict):
        raise VerificationError("successor_claim_missing")
    if type(claim.get("claimed_at_ns")) is not int:
        raise VerificationError("claim_time_invalid")
    current_time = time.time_ns() if now_ns is None else now_ns
    if claim["claimed_at_ns"] > current_time:
        raise VerificationError("claim_time_in_future")
    packet = _verify_packet(
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
        now_ns=claim["claimed_at_ns"],
        allow_existing_claim=True,
    )
    body = dict(claim)
    supplied = body.pop("claim_sha256", None)
    if predecessor_identity["kind"] == INITIAL_ENTRY_PREDECESSOR_KIND:
        identity_invalid = (
            claim.get("schema") != INITIAL_ENTRY_CLAIM_SCHEMA
            or claim.get("predecessor_digest_kind") != INITIAL_ENTRY_PREDECESSOR_KIND
            or claim.get("predecessor_digest_sha256") != predecessor_terminal
            or claim.get("claim_key_sha256") != claim_key
        )
    else:
        identity_invalid = (
            claim.get("schema") != CLAIM_SCHEMA
            or claim.get("predecessor_terminal_sha256") != predecessor_terminal
        )
    if (
        supplied != _sha(_canonical(body))
        or identity_invalid
        or claim.get("lease_id") != packet["lease_id"]
        or claim.get("packet_digest") != packet["packet_digest"]
        or claim.get("plan_sha256") != packet["plan_sha256"]
        or claim.get("schedule_sha256") != packet["schedule_sha256"]
        or claim.get("review_signature_sha256") != packet["evidence_sha256"]["sol_signature"]
        or claim.get("activation_performed") is not False
    ):
        raise VerificationError("claim_binding_invalid")
    return {
        "valid": True,
        "eligible": False,
        "eligible_at_claim": True,
        "current_eligibility_requires_fresh_packet_verification": True,
        "activation_authorized": False,
        **claim,
    }
