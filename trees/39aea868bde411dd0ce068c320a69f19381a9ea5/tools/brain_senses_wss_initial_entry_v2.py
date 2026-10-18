"""Offline verifier and no-client dry run for a WSS successor to abnormal c4.

This module never reads the private endpoint, creates a WSS client, resolves DNS, or opens a
socket. Evidence signatures are checked against an out-of-band pinned trust store. A successful
dry run is evidence validation only and always returns activation_authorized=false.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import time
import xml.parsers.expat
from pathlib import Path
from typing import Any

from tools import brain_senses_activation_preflight_verifier_v1 as auth
from tools import brain_senses_activation_preflight_v1 as common_claims
from tools import brain_senses_initial_entry_discontinuity_verifier_v1 as discontinuity

from memecoin_collector import wss_campaign, wss_reference

SCHEMA = "brain-senses-wss-initial-entry-gate-v2"
ACCOUNT_SCHEMA = "brain-senses-wss-account-evidence-v2"
HOST_SCHEMA = "brain-senses-wss-host-evidence-v1"
REVIEW_SCHEMA = "brain-senses-wss-review-evidence-v2"
CLAIM_SCHEMA = "brain-senses-initial-entry-successor-claim-v1"
GATE_STATUS = "offline_signed_assertion_only"
GATE_SCOPE = "offline_signed_assertion_contract"
WSS_CONTRACT_VERSIONS = {
    "campaign_authorization_schema_version": 2,
    "session_binding_schema_version": 2,
}
DAY_NS = 86_400_000_000_000
METADATA_RESERVE_BYTES = 16_384
WSS_CREDIT_RATE_NUMERATOR = 2
WSS_CREDIT_RATE_DENOMINATOR_BYTES = 100_000
MAX_BILLABLE_MESSAGES = wss_reference.MAX_BILLABLE_MESSAGES
MAX_BILLABLE_UNCOMPRESSED_BYTES = wss_reference.MAX_BILLABLE_UNCOMPRESSED_BYTES
MAX_ACCOUNT_EVIDENCE_AGE_NS = 15 * 60 * 1_000_000_000
MAX_GATE_AGE_NS = 15 * 60 * 1_000_000_000
QUIESCENCE_PATH = "docs/evidence/helius-c4-launchd-quiescence-2026-10-11.json"
QUIESCENCE_SHA256 = "3b56f0254b6807f68297796d2d6d7075f37052ab83acd7896c1eb82c087f2422"
C4_LABEL = "ai.memecoin.brain.senses.helius-inventory-v3-20261010-4"
C4_PLIST_SHA256 = "9a42474eb00d7bbc253b3f461185bd77a8658e131c45bc73881faa034d7283e8"

SOURCE_FILES = {
    "wss_campaign.py": Path(wss_campaign.__file__),
    "wss_reference.py": Path(wss_reference.__file__),
    "wss_transport.py": Path(wss_reference.__file__).with_name("wss_transport.py"),
    "wss_client.py": Path(wss_reference.__file__).with_name("wss_client.py"),
    "wss_tls.py": Path(wss_reference.__file__).with_name("wss_tls.py"),
    "wss_socket.py": Path(wss_reference.__file__).with_name("wss_socket.py"),
    "private_config.py": Path(wss_reference.__file__).with_name("private_config.py"),
    "initial_entry_discontinuity_verifier.py": Path(discontinuity.__file__),
    "activation_preflight_claim_writer.py": Path(common_claims.__file__),
    "activation_preflight_claim_verifier.py": Path(auth.__file__),
    "brain_senses_wss_resolver.py": Path(__file__).resolve().with_name("brain_senses_wss_resolver.py"),
    "wss_initial_entry_gate_verifier.py": Path(__file__).resolve(),
}


class GateError(ValueError):
    """Signed evidence, lineage, policy or current-host check failed."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def source_bindings() -> dict[str, str]:
    return {name: _sha(path.read_bytes()) for name, path in sorted(SOURCE_FILES.items())}


def _private_directory(path: Path) -> Path:
    if not path.is_absolute() or stat.S_ISLNK(path.lstat().st_mode):
        raise GateError("evidence_root_path_invalid")
    root = path.resolve(strict=True)
    st = root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise GateError("evidence_root_owner_or_mode_invalid")
    return root


def _registry_identity(path: Path) -> tuple[Path, dict[str, Any]]:
    try:
        root, identity = auth._registry_snapshot(path)
    except (auth.VerificationError, OSError) as exc:
        raise GateError("claim_registry_canonical_identity_invalid") from exc
    return root, identity


def _certificate(repo_root: Path) -> tuple[dict[str, Any], str, str]:
    terminal_path = repo_root / discontinuity.RECEIPT_REPO_PATH
    cert_path = repo_root / "docs/evidence/senses-initial-entry-discontinuity-v1-c4-2026-10-11.json"
    terminal_raw = terminal_path.read_bytes()
    certificate_raw = cert_path.read_bytes()
    terminal_sha = _sha(terminal_raw)
    certificate_sha = _sha(certificate_raw)
    if terminal_sha != discontinuity.EXPECTED_RECEIPT_SHA256:
        raise GateError("c4_terminal_receipt_pin_mismatch")
    terminal = auth._json(terminal_raw)
    certificate = auth._json(certificate_raw)
    try:
        discontinuity.verify_certificate_data(certificate, terminal, terminal_sha)
    except (ValueError, TypeError, KeyError) as exc:
        raise GateError("c4_discontinuity_certificate_invalid") from exc
    predecessor_digest = _sha(_canonical(certificate))
    return certificate, certificate_sha, predecessor_digest


def _verify_quiescence(repo_root: Path) -> str:
    path = repo_root / QUIESCENCE_PATH
    raw = path.read_bytes()
    digest = _sha(raw)
    if digest != QUIESCENCE_SHA256:
        raise GateError("c4_quiescence_receipt_pin_mismatch")
    row = auth._json(raw)
    c4_plist = row.get("c4_plist", {})
    if (
        row.get("status") != "all_senses_launchagents_disabled_unloaded"
        or row.get("all_senses_labels_persistently_disabled") is not True
        or row.get("loaded_senses_labels") != []
        or row.get("senses_plist_count") != 15
        or c4_plist.get("label") != C4_LABEL
        or c4_plist.get("plist_sha256") != C4_PLIST_SHA256
    ):
        raise GateError("c4_quiescence_metadata_invalid")
    return digest


def _current_root(root: Path) -> tuple[Path, int, int]:
    if not root.is_absolute() or stat.S_ISLNK(root.lstat().st_mode):
        raise GateError("capture_root_path_invalid")
    resolved = root.resolve(strict=True)
    st = resolved.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise GateError("capture_root_owner_or_type_invalid")
    return resolved, st.st_dev, shutil.disk_usage(resolved).free


def _installed_senses_launchagent_labels() -> list[str]:
    """Read LaunchAgent plist labels only, to detect new enabled-but-unloaded jobs."""
    roots = (
        Path.home() / "Library/LaunchAgents",
        Path("/Library/LaunchAgents"),
    )
    found: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        try:
            entries = sorted(root.glob("*.plist"))
        except OSError as exc:
            raise GateError("launchagent_inventory_unavailable") from exc
        for path in entries:
            try:
                # System LaunchAgents commonly use symlinked plist payloads. Follow the
                # bounded local plist target so a new Senses label cannot hide behind one.
                st = path.stat()
                if not stat.S_ISREG(st.st_mode):
                    raise GateError("launchagent_plist_identity_invalid")
                if st.st_size > 1_000_000:
                    raise GateError("launchagent_plist_size_limit")
                document = plistlib.loads(path.read_bytes())
            except GateError:
                raise
            except (OSError, plistlib.InvalidFileException, ValueError, xml.parsers.expat.ExpatError) as exc:
                raise GateError("launchagent_plist_invalid") from exc
            label = document.get("Label") if isinstance(document, dict) else None
            if isinstance(label, str) and label.startswith("ai.memecoin.brain.senses."):
                found.add(label)
    return sorted(found)


def _current_runtime_census(repo_root: Path) -> dict[str, Any]:
    """Read Senses launchd state and env-key presence for identifiable Helius workers."""
    quiescence = auth._json((repo_root / QUIESCENCE_PATH).read_bytes())
    labels = sorted(row["label"] for row in quiescence["senses_plists"])
    installed_labels = _installed_senses_launchagent_labels()
    try:
        loaded_raw = subprocess.run(
            ["/bin/launchctl", "list"], capture_output=True, text=True, check=True
        ).stdout
        disabled_raw = subprocess.run(
            ["/bin/launchctl", "print-disabled", f"gui/{os.getuid()}"],
            capture_output=True, text=True, check=True,
        ).stdout
        processes = subprocess.run(
            ["/bin/ps", "-axo", "pid=,comm=,command="],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError("current_owner_runtime_census_unavailable") from exc
    loaded = sorted({
        line.split()[-1]
        for line in loaded_raw.splitlines()
        if line.split() and line.split()[-1].startswith("ai.memecoin.brain.senses.")
    })
    disabled = sorted(
        label
        for label in labels
        if re.search(r'"' + re.escape(label) + r'"\s*=>\s*disabled', disabled_raw)
    )
    writer_processes = 0
    provider_key_workers = 0
    writer_scripts = {
        "brain_senses_c5_http_runner_v1.py",
        "brain_senses_service_v1_runner.py",
        "wss_campaign.py",
        "brain_senses_wss_initial_entry_v2.py",
    }
    for line in processes.splitlines():
        columns = line.strip().split(None, 2)
        if len(columns) != 3:
            raise GateError("current_owner_process_inventory_malformed")
        pid, comm, argv = columns
        try:
            argv_tokens = shlex.split(argv)
        except ValueError:
            # An unparseable process command line cannot be safely classified.
            raise GateError("current_owner_process_argv_unparseable")
        if (
            Path(comm).name.startswith("python")
            and any(Path(token).name in writer_scripts for token in argv_tokens)
        ):
            writer_processes += 1
        executable = Path(comm).name.lower()
        basenames = {Path(token).name.lower() for token in argv_tokens}
        provider_process = (
            executable in {"node", "nodejs"}
            and (
                "helius-mcp" in basenames
                or any("helius-mcp@" in token.lower() for token in argv_tokens)
            )
        ) or (
            executable in {"npm", "npx"}
            and any("helius-mcp@" in token.lower() for token in argv_tokens)
        ) or any(
            Path(token).name.lower() in {"helius-mcp-secure-wrapper", "helius-mcp-secure-wrapper.py"}
            for token in argv_tokens
        )
        if not provider_process:
            continue
        try:
            env_result = subprocess.run(
                ["/bin/ps", "eww", "-p", pid],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateError("current_process_environment_census_unavailable") from exc
        if env_result.returncode != 0:
            raise GateError("current_process_environment_census_unavailable")
        env = env_result.stdout
        if not env.strip():
            raise GateError("current_process_environment_census_empty")
        if re.search(r"(?:^|\s)HELIUS_API_KEY=", env):
            provider_key_workers += 1
    return {
        "loaded_senses_labels": loaded,
        "disabled_senses_labels": disabled,
        "expected_senses_labels": labels,
        "installed_senses_labels": installed_labels,
        "active_senses_writer_process_count": writer_processes,
        "provider_key_process_count": provider_key_workers,
    }


def write_gate_receipt_create_only(packet_root: Path, manifest: dict[str, Any]) -> None:
    """Create a receipt once after the signed evidence artifacts have been staged."""
    packet_root = _private_directory(packet_root)
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("status") != GATE_STATUS
        or manifest.get("authorization_scope") != GATE_SCOPE
        or manifest.get("live_gate_qualified") is not False
        or manifest.get("activation_authorized") is not False
    ):
        raise GateError("gate_manifest_not_review_only")
    payload = _canonical(manifest) + b"\n"
    path = packet_root / "gate.json"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError as exc:
        raise GateError("gate_receipt_already_exists") from exc
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    dfd = os.open(packet_root, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _read_signed(
    packet_root: Path,
    ref: Any,
    *,
    keyset: dict[str, dict[str, str]],
    openssl_path: Path,
    role: str,
) -> tuple[dict[str, Any], str]:
    try:
        value, _, payload_sha = auth._evidence(
            packet_root, ref, keyset=keyset, openssl_path=openssl_path, role=role
        )
    except (auth.VerificationError, OSError, TypeError) as exc:
        raise GateError(f"signed_{role}_evidence_invalid") from exc
    return value, payload_sha


def _credits_for_limits(
    *, message_count: int, byte_count: int, sessions: int, max_message_bytes: int,
    rate_credits: int, chunk_bytes: int, rounding_scope: str,
    fixed_credits_per_session: int, control_credits_per_session: int,
) -> int:
    """Conservative billable-unit bound for the source-declared rounding model."""
    if rounding_scope == "aggregate":
        chunks = math.ceil(byte_count / chunk_bytes) if byte_count else 0
    elif rounding_scope == "session":
        chunks = (
            math.ceil(byte_count / chunk_bytes) + max(0, sessions - 1)
            if byte_count and sessions else 0
        )
    elif rounding_scope == "message":
        # At most N positive messages: aggregate chunks plus at most one extra per additional message.
        chunks = (
            math.ceil(byte_count / chunk_bytes) + max(0, message_count - 1)
            if byte_count and message_count else 0
        )
    else:
        raise GateError("billing_rounding_scope_unsupported")
    return (
        chunks * rate_credits
        + sessions * fixed_credits_per_session
        + sessions * control_credits_per_session
    )


def _validate_account_budget(
    account: dict[str, Any], *, window: dict[str, int], limits: dict[str, int],
    now_ns: int, assertion_verifier_sha256: str,
) -> dict[str, Any]:
    required = {
        "schema": ACCOUNT_SCHEMA,
        "plan_status": "active",
        "wss_billing_unit": "uncompressed_decimal_megabyte",
        "overage_disabled_or_hard_capped": True,
        "autoscaling_disabled": True,
        "shared_use_included_through_expiry": True,
        "all_shared_clients_finitely_bounded": True,
        "paid_overage_enabled": False,
        "autoscaling_enabled": False,
    }
    if any(account.get(key) != value for key, value in required.items()):
        raise GateError("account_evidence_policy_mismatch")
    if not isinstance(account.get("plan_id"), str) or not account["plan_id"]:
        raise GateError("account_plan_id_missing")
    for key in ("account_fingerprint_sha256", "endpoint_key_fingerprint_sha256"):
        value = account.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise GateError("account_evidence_source_pin_missing")
    for name in ("account_source", "price_source"):
        source = account.get(name)
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("identity"), str) or not source["identity"]
            or not isinstance(source.get("raw_sha256"), str)
            or len(source["raw_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in source["raw_sha256"])
            or type(source.get("sampled_at_ns")) is not int
            or type(source.get("received_at_ns")) is not int
            or source["sampled_at_ns"] > source["received_at_ns"]
            or source["received_at_ns"] > now_ns
            or now_ns - source["sampled_at_ns"] > MAX_ACCOUNT_EVIDENCE_AGE_NS
        ):
            raise GateError("account_or_price_source_identity_or_freshness_invalid")
    billing = account.get("billing")
    if (
        not isinstance(billing, dict)
        or billing.get("billable_classes") != [
            "logsSubscribe.notification", "logsSubscribe.response"
        ]
        or billing.get("rate_credits") != WSS_CREDIT_RATE_NUMERATOR
        or billing.get("chunk_bytes") != WSS_CREDIT_RATE_DENOMINATOR_BYTES
        or billing.get("chunk_unit") != "uncompressed_decimal_bytes"
        or billing.get("rounding_scope") not in {"aggregate", "session", "message"}
        or billing.get("control_byte_billing") != "session_fixed_covers_bounded_control_frames"
        or type(billing.get("fixed_credits_per_session")) is not int
        or billing["fixed_credits_per_session"] < 0
        or type(billing.get("control_credits_per_session")) is not int
        or billing["control_credits_per_session"] < 0
        or billing.get("price_source_sha256") != account["price_source"].get("raw_sha256")
    ):
        raise GateError("account_billing_model_invalid_or_ambiguous")
    parsed = account["price_source"].get("parsed_billing")
    expected_parsed = {
        "billable_classes": billing["billable_classes"],
        "rate_credits": billing["rate_credits"],
        "chunk_bytes": billing["chunk_bytes"],
        "chunk_unit": billing["chunk_unit"],
        "rounding_scope": billing["rounding_scope"],
        "fixed_credits_per_session": billing["fixed_credits_per_session"],
        "control_credits_per_session": billing["control_credits_per_session"],
        "control_byte_billing": billing["control_byte_billing"],
    }
    if (
        parsed != expected_parsed
        or account["price_source"].get("assertion_verifier_sha256") != assertion_verifier_sha256
        or account["price_source"].get("raw_sha256") != billing["price_source_sha256"]
    ):
        raise GateError("price_source_parsed_facts_or_parser_binding_mismatch")
    shared = account.get("shared_clients")
    if (
        not isinstance(shared, dict)
        or shared.get("bounded_through_expiry") is not True
        or shared.get("enforcement") not in {"provider_enforced_allocation", "suspended"}
        or type(shared.get("max_billable_messages")) is not int
        or shared["max_billable_messages"] < 0
        or type(shared.get("max_billable_uncompressed_bytes")) is not int
        or shared["max_billable_uncompressed_bytes"] < 0
        or type(shared.get("max_sessions")) is not int
        or shared["max_sessions"] < 0
        or type(shared.get("valid_until_ns")) is not int
        or shared["valid_until_ns"] < window["expires_at_ns"]
    ):
        raise GateError("shared_client_consumption_unbounded_or_expired")
    if (
        shared["max_billable_uncompressed_bytes"] > 0
        and (shared["max_billable_messages"] == 0 or shared["max_sessions"] == 0)
    ):
        raise GateError("shared_client_caps_dimensionally_incomplete")
    if shared["enforcement"] == "suspended" and any(
        shared[name] != 0
        for name in ("max_billable_messages", "max_billable_uncompressed_bytes", "max_sessions")
    ):
        raise GateError("suspended_shared_clients_must_have_zero_caps")
    for key in ("remaining_wss_credits", "control_tls_reconnect_overhead_credits", "reset_at_ns", "valid_until_ns"):
        if type(account.get(key)) is not int or account[key] < 0:
            raise GateError("account_credit_fields_invalid")
    if account["valid_until_ns"] < window["expires_at_ns"] or account["reset_at_ns"] < window["expires_at_ns"]:
        raise GateError("account_credit_evidence_expires_before_window")
    if account.get("overage_ceiling_credits") != 0:
        raise GateError("paid_overage_not_zero_capped")
    kwargs = {
        "rate_credits": billing["rate_credits"], "chunk_bytes": billing["chunk_bytes"],
        "rounding_scope": billing["rounding_scope"],
        "fixed_credits_per_session": billing["fixed_credits_per_session"],
        "control_credits_per_session": billing["control_credits_per_session"],
        "max_message_bytes": limits["max_message_bytes"],
    }
    own = _credits_for_limits(
        message_count=limits["max_billable_messages"],
        byte_count=limits["max_billable_uncompressed_bytes"],
        sessions=limits["max_sessions"], **kwargs,
    )
    if limits["max_billable_messages"] <= 0 or limits["max_billable_uncompressed_bytes"] <= 0:
        raise GateError("own_billable_caps_must_be_positive")
    shared_cost = _credits_for_limits(
        message_count=shared["max_billable_messages"],
        byte_count=shared["max_billable_uncompressed_bytes"],
        sessions=shared["max_sessions"], **kwargs,
    )
    required_credits = own + shared_cost + account["control_tls_reconnect_overhead_credits"]
    if account["remaining_wss_credits"] < required_credits:
        raise GateError("account_credit_headroom_insufficient")
    return {
        "own_worst_case_credits": own,
        "shared_worst_case_credits": shared_cost,
        "required_credits": required_credits,
        "shared_client_enforcement": shared["enforcement"],
        "shared_client_caps": {
            "max_billable_messages": shared["max_billable_messages"],
            "max_billable_uncompressed_bytes": shared["max_billable_uncompressed_bytes"],
            "max_sessions": shared["max_sessions"],
        },
    }


def _verify_manifest(
    packet_root: Path,
    *,
    trust_store_path: Path,
    trust_store_sha256: str,
    openssl_path: Path,
    openssl_sha256: str,
    openssl_version: str,
    verifier_sha256: str,
    repo_root: Path,
    capture_root: Path,
    claim_registry_root: Path,
    clock_source_id: str,
    now_ns: int,
    monotonic_now_ns: int | None = None,
    phase: str = "preauthorize",
    expected_endpoint_key_fingerprint: str | None = None,
) -> dict[str, Any]:
    packet_root = _private_directory(packet_root)
    actual_verifier_sha = _sha(Path(__file__).resolve().read_bytes())
    if verifier_sha256 != actual_verifier_sha:
        raise GateError("wss_gate_verifier_pin_mismatch")
    try:
        auth._openssl_identity(openssl_path, openssl_sha256, openssl_version)
        keyset = auth._keyset(trust_store_path, trust_store_sha256)
    except (auth.VerificationError, OSError) as exc:
        raise GateError("out_of_band_trust_or_crypto_pin_invalid") from exc
    manifest_raw = auth._read(packet_root / "gate.json", owner=os.getuid())
    manifest = auth._json(manifest_raw)
    required_manifest_keys = {
        "schema", "status", "predecessor", "c4_quiescence_receipt_sha256", "window", "limits", "source_bindings",
        "wss_contract_versions",
        "authorization_scope", "live_gate_qualified",
        "gate_verifier_sha256", "trust_store_sha256", "clock_source_id", "claim_registry",
        "account_evidence", "host_evidence", "review_evidence", "review_evidence_sha256",
        "activation_authorized",
    }
    if (
        auth._canonical(manifest) + b"\n" != manifest_raw
        or manifest.get("schema") != SCHEMA
        or set(manifest) != required_manifest_keys
    ):
        raise GateError("gate_manifest_noncanonical_or_schema_invalid")
    if (
        manifest.get("activation_authorized") is not False
        or manifest.get("status") != GATE_STATUS
        or manifest.get("authorization_scope") != GATE_SCOPE
        or manifest.get("live_gate_qualified") is not False
    ):
        raise GateError("gate_cannot_authorize_activation")
    if (
        manifest.get("gate_verifier_sha256") != actual_verifier_sha
        or manifest.get("trust_store_sha256") != trust_store_sha256
        or manifest.get("clock_source_id") != clock_source_id
    ):
        raise GateError("reviewed_verifier_trust_or_clock_binding_mismatch")
    registry_root, registry_identity = _registry_identity(claim_registry_root)
    if manifest.get("claim_registry") != registry_identity:
        raise GateError("reviewed_claim_registry_identity_mismatch")
    certificate, certificate_sha, predecessor_digest = _certificate(repo_root)
    quiescence_sha = _verify_quiescence(repo_root)
    if manifest.get("c4_quiescence_receipt_sha256") != quiescence_sha:
        raise GateError("c4_quiescence_binding_mismatch")
    predecessor = manifest.get("predecessor")
    if predecessor != {
        "kind": discontinuity.CERTIFICATE_SCHEMA,
        "certificate_file_sha256": certificate_sha,
        "predecessor_digest_sha256": predecessor_digest,
        "frontier_slot": certificate["frontier_slot"],
        "gap_ids": [row["id"] for row in certificate["gaps"]],
        "unknown_intervals": certificate["unknown_intervals"],
    }:
        raise GateError("abnormal_c4_predecessor_binding_mismatch")
    if manifest.get("source_bindings") != source_bindings():
        raise GateError("wss_source_binding_mismatch")
    if manifest.get("wss_contract_versions") != WSS_CONTRACT_VERSIONS:
        raise GateError("wss_contract_version_mismatch")
    window = manifest.get("window")
    if (
        not isinstance(window, dict)
        or type(window.get("authorized_at_ns")) is not int
        or type(window.get("expires_at_ns")) is not int
        or window["expires_at_ns"] <= window["authorized_at_ns"]
        or window["expires_at_ns"] - window["authorized_at_ns"] > 7 * DAY_NS
    ):
        raise GateError("wss_first_entry_window_invalid")
    if phase not in {"preauthorize", "pre_dns", "pre_subscribe"} or type(now_ns) is not int or now_ns < 0:
        raise GateError("wss_first_entry_phase_or_clock_invalid")
    if phase == "preauthorize":
        if now_ns >= window["authorized_at_ns"]:
            raise GateError("wss_candidate_preauthorization_not_future")
    elif not window["authorized_at_ns"] <= now_ns < window["expires_at_ns"]:
        raise GateError("wss_due_window_check_outside_authorized_interval")
    limits = manifest.get("limits")
    expected_limits = {
        "max_sessions": wss_campaign.MAX_SESSIONS,
        "max_session_ns": wss_reference.MAX_SESSION_NS,
        "raw_limit_bytes": wss_reference.RAW_LIMIT_BYTES,
        "stored_limit_bytes": wss_reference.STORED_LIMIT_BYTES,
        "max_message_bytes": wss_reference.MAX_MESSAGE_BYTES,
        "minimum_free_disk_bytes": wss_reference.DISK_FLOOR_BYTES,
        "max_billable_messages": MAX_BILLABLE_MESSAGES,
        "max_billable_uncompressed_bytes": MAX_BILLABLE_UNCOMPRESSED_BYTES,
        "max_transport_control_frames": MAX_BILLABLE_MESSAGES,
        "max_transport_control_wire_bytes": wss_reference.MAX_TRANSPORT_CONTROL_WIRE_BYTES,
    }
    if limits != expected_limits:
        raise GateError("wss_first_entry_limits_mismatch")
    root, device, free = _current_root(capture_root)
    account, account_sha = _read_signed(
        packet_root, manifest.get("account_evidence"), keyset=keyset,
        openssl_path=openssl_path, role="helius_independent_checker"
    )
    host, host_sha = _read_signed(
        packet_root, manifest.get("host_evidence"), keyset=keyset,
        openssl_path=openssl_path, role="host_attestor"
    )
    review, review_sha = _read_signed(
        packet_root, manifest.get("review_evidence"), keyset=keyset,
        openssl_path=openssl_path, role="sol_reviewer"
    )
    if expected_endpoint_key_fingerprint is not None and account.get("endpoint_key_fingerprint_sha256") != expected_endpoint_key_fingerprint:
        raise GateError("endpoint_key_account_binding_mismatch")
    credit_requirements = _validate_account_budget(
        account, window=window, limits=limits, now_ns=now_ns,
        assertion_verifier_sha256=actual_verifier_sha,
    )
    required_root = (
        wss_reference.DISK_FLOOR_BYTES
        + wss_reference.STORED_LIMIT_BYTES
        + wss_reference.MAX_MESSAGE_BYTES
        + METADATA_RESERVE_BYTES
        + (host.get("reviewed_host_churn_reserve_bytes") if type(host.get("reviewed_host_churn_reserve_bytes")) is int else 0)
    )
    if (
        host.get("schema") != HOST_SCHEMA
        or host.get("capture_root") != str(root)
        or host.get("device_id") != device
        or host.get("filesystem") != "apfs"
        or host.get("durable_create_only_fsync_lock_qualified") is not True
        or host.get("no_competing_mac_or_writer") is not True
        or host.get("competing_writer_count") != 0
        or host.get("shared_host_lock_clear") is not True
        or not isinstance(host.get("process_census_sha256"), str)
        or len(host["process_census_sha256"]) != 64
        or type(host.get("reviewed_host_churn_reserve_bytes")) is not int
        or host["reviewed_host_churn_reserve_bytes"] <= 0
        or host.get("required_free_bytes") != required_root
        or host.get("sampled_free_bytes") < required_root
        or free < required_root
        or type(host.get("sampled_at_ns")) is not int
        or host["sampled_at_ns"] > now_ns
        or now_ns - host["sampled_at_ns"] > MAX_GATE_AGE_NS
        or host.get("clock_source_id") != clock_source_id
        or not isinstance(host.get("clock_boot_id"), str) or not host["clock_boot_id"]
        or type(host.get("sampled_monotonic_ns")) is not int
        or type(monotonic_now_ns) is not int
        or monotonic_now_ns < host["sampled_monotonic_ns"]
        or monotonic_now_ns - host["sampled_monotonic_ns"] > MAX_GATE_AGE_NS
        or abs((now_ns - host["sampled_at_ns"]) - (monotonic_now_ns - host["sampled_monotonic_ns"])) > 5_000_000_000
        or not isinstance(host.get("trusted_clock_evidence_sha256"), str)
        or len(host["trusted_clock_evidence_sha256"]) != 64
    ):
        raise GateError("signed_host_evidence_or_current_disk_failed")
    runtime = _current_runtime_census(repo_root)
    if (
        runtime["loaded_senses_labels"]
        or runtime["disabled_senses_labels"] != runtime["expected_senses_labels"]
        or runtime["installed_senses_labels"] != runtime["expected_senses_labels"]
        or runtime["active_senses_writer_process_count"] != 0
        or host["process_census_sha256"] != _sha(_canonical(runtime))
    ):
        raise GateError("current_owner_or_competing_provider_process_failed")
    if (
        credit_requirements["shared_client_enforcement"] == "suspended"
        and runtime["provider_key_process_count"] != 0
    ):
        raise GateError("suspended_shared_clients_key_worker_present")
    unsigned_manifest = dict(manifest)
    unsigned_manifest.pop("review_evidence", None)
    unsigned_manifest.pop("review_evidence_sha256", None)
    expected_review = {
        "schema": REVIEW_SCHEMA,
        "status": GATE_STATUS,
        "manifest_sha256": _sha(_canonical(unsigned_manifest)),
        "predecessor_digest_sha256": predecessor_digest,
        "c4_quiescence_receipt_sha256": quiescence_sha,
        "account_evidence_sha256": account_sha,
        "host_evidence_sha256": host_sha,
        "gate_verifier_sha256": actual_verifier_sha,
        "trust_store_sha256": trust_store_sha256,
        "claim_registry": registry_identity,
        "source_bindings": source_bindings(),
        "wss_contract_versions": WSS_CONTRACT_VERSIONS,
        "authorization_scope": GATE_SCOPE,
        "live_gate_qualified": False,
        "clock_source_id": clock_source_id,
        "window": window,
        "limits": expected_limits,
        **credit_requirements,
        "activation_authorized": False,
    }
    if review != expected_review or manifest.get("review_evidence_sha256") != review_sha:
        raise GateError("independent_review_binding_mismatch")
    if now_ns - host["sampled_at_ns"] > MAX_GATE_AGE_NS:
        raise GateError("host_evidence_stale")
    return {
        "valid": True,
        "status": GATE_STATUS,
        "authorization_scope": GATE_SCOPE,
        "live_gate_qualified": False,
        "raw_account_price_source_verified": False,
        "activation_authorized": False,
        "gate_manifest_sha256": _sha(manifest_raw),
        "predecessor_digest_sha256": predecessor_digest,
        "account_evidence_sha256": account_sha,
        "host_evidence_sha256": host_sha,
        "review_evidence_sha256": review_sha,
        "capture_root": str(root),
        "capture_device_id": device,
        "claim_registry": registry_identity,
        "current_free_bytes": free,
        "required_free_bytes": required_root,
        "provider_key_process_count": runtime["provider_key_process_count"],
        "window": window,
        "phase": phase,
        **credit_requirements,
    }


def dry_run_activation(
    packet_root: Path,
    **verification_inputs: Any,
) -> dict[str, Any]:
    """Consume a signed gate in a no-client path; never loads credentials or opens a socket."""
    now_ns = verification_inputs.pop("now_ns", time.time_ns())
    monotonic_now_ns = verification_inputs.pop("monotonic_now_ns", time.monotonic_ns())
    evidence = _verify_manifest(
        packet_root, now_ns=now_ns, monotonic_now_ns=monotonic_now_ns,
        phase="preauthorize", **verification_inputs
    )
    return {
        "status": "offline_signed_assertion_preauthorization_no_client",
        "live_gate_qualified": False,
        "raw_account_price_source_verified": False,
        "preauthorization_valid": True,
        "pre_dns_gate_valid": False,
        "pre_subscribe_gate_valid": False,
        "activation_authorized": False,
        "client_created": False,
        "credential_loaded": False,
        "dns_or_wss_request_performed": False,
        "evidence": evidence,
    }


def dry_run_due_window_checks(packet_root: Path, **verification_inputs: Any) -> dict[str, Any]:
    """Check due-window evidence twice, as separate pre-DNS and pre-subscribe phases; no client exists."""
    first_now = verification_inputs.pop("pre_dns_now_ns")
    first_mono = verification_inputs.pop("pre_dns_monotonic_ns")
    second_now = verification_inputs.pop("pre_subscribe_now_ns")
    second_mono = verification_inputs.pop("pre_subscribe_monotonic_ns")
    first = _verify_manifest(
        packet_root, now_ns=first_now, monotonic_now_ns=first_mono,
        phase="pre_dns", **verification_inputs,
    )
    second = _verify_manifest(
        packet_root, now_ns=second_now, monotonic_now_ns=second_mono,
        phase="pre_subscribe", **verification_inputs,
    )
    if second_now < first_now or second_mono < first_mono:
        raise GateError("due_phase_clock_regression")
    return {
        "status": "offline_signed_assertion_due_window_checks_no_client",
        "live_gate_qualified": False,
        "raw_account_price_source_verified": False,
        "preauthorization_valid": False,
        "pre_dns_gate_valid": True,
        "pre_subscribe_gate_valid": True,
        "activation_authorized": False,
        "client_created": False,
        "credential_loaded": False,
        "dns_or_wss_request_performed": False,
        "pre_dns_evidence": first,
        "pre_subscribe_evidence": second,
    }


def claim_first_entry(
    registry_root: Path,
    packet_root: Path,
    **verification_inputs: Any,
) -> None:
    """Fail closed until raw account/price sources are independently qualified.

    V2 is an inert signed-assertion contract. It has no release switch for durable claims.
    """
    raise GateError("source_qualified_live_gate_required")
