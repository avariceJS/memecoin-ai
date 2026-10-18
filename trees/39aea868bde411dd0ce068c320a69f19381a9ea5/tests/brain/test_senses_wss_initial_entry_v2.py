from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from tools import brain_senses_wss_initial_entry_v2 as gate


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def reserve_wss_claim_fixture(
    registry: Path, *, predecessor_digest_sha256: str,
    gate_manifest_sha256: str = "b" * 64, claimed_at_ns: int = 123,
    review_signature_sha256: str = "0" * 64,
) -> dict:
    """Write a shared-registry WSS-shaped claim only inside disposable tests."""
    try:
        lock = gate.common_claims._registry_lock(registry)
    except gate.common_claims.PreflightError as exc:
        raise gate.GateError("shared_successor_claim_registry_unavailable_or_locked") from exc
    try:
        claims = gate.auth._registry_claims(registry)
        key = gate.auth._initial_entry_claim_key(predecessor_digest_sha256)
        if key in claims:
            raise gate.GateError("first_entry_predecessor_already_claimed_by_any_modality")
        record = {
            "schema": gate.auth.INITIAL_ENTRY_CLAIM_SCHEMA,
            "lease_id": f"fixture-wss-{gate_manifest_sha256[:16]}",
            "predecessor_digest_kind": gate.auth.INITIAL_ENTRY_PREDECESSOR_KIND,
            "predecessor_digest_sha256": predecessor_digest_sha256,
            "claim_key_sha256": key,
            "packet_digest": gate_manifest_sha256,
            "plan_sha256": gate_manifest_sha256,
            "schedule_sha256": "c" * 64,
            "review_signature_sha256": review_signature_sha256,
            "claimed_at_ns": claimed_at_ns,
            "activation_performed": False,
        }
        record["claim_sha256"] = sha(canonical(record))
        claims[key] = record
        raw = canonical({"schema": gate.auth.REGISTRY_SCHEMA, "claims": claims}) + b"\n"
        pending = registry / f".registry-claim-v1-fixture-{uuid.uuid4().hex}.pending"
        gate.common_claims._write_new(pending, raw)
        os.replace(pending, registry / gate.auth.REGISTRY_CLAIMS_FILENAME)
        fd = os.open(registry, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return record
    finally:
        lock.close()


def make_gate_case(tmp_path: Path, monkeypatch, *, now_ns: int = 10_000_000_000):
    packet = private_dir(tmp_path / "packet")
    capture = private_dir(tmp_path / "capture")
    registry = private_dir(tmp_path / "claim-registry")
    gate.auth.CANONICAL_CLAIM_REGISTRY_ROOT = registry
    registry_identity = gate._registry_identity(registry)[1]
    source_pins = {"transport": "1" * 64}
    cert_sha = "2" * 64
    predecessor_digest = "3" * 64
    quiescence_sha = "4" * 64
    certificate = {
        "frontier_slot": 455430160,
        "gaps": [{"id": f"gap-{i}"} for i in range(4)],
        "unknown_intervals": [{"end_exclusive": None, "end_known": False}],
    }
    monkeypatch.setattr(gate, "_certificate", lambda _: (certificate, cert_sha, predecessor_digest))
    monkeypatch.setattr(gate, "_verify_quiescence", lambda _: quiescence_sha)
    monkeypatch.setattr(gate, "source_bindings", lambda: source_pins)
    monkeypatch.setattr(gate.auth, "_openssl_identity", lambda *args: None)
    monkeypatch.setattr(gate.auth, "_keyset", lambda *args: {})
    monkeypatch.setattr(gate.auth, "_read", lambda path, **kwargs: Path(path).read_bytes())
    monkeypatch.setattr(gate, "_private_directory", lambda path: Path(path))

    limits = {
        "max_sessions": gate.wss_campaign.MAX_SESSIONS,
        "max_session_ns": gate.wss_reference.MAX_SESSION_NS,
        "raw_limit_bytes": gate.wss_reference.RAW_LIMIT_BYTES,
        "stored_limit_bytes": gate.wss_reference.STORED_LIMIT_BYTES,
        "max_message_bytes": gate.wss_reference.MAX_MESSAGE_BYTES,
        "minimum_free_disk_bytes": gate.wss_reference.DISK_FLOOR_BYTES,
        "max_billable_messages": gate.MAX_BILLABLE_MESSAGES,
        "max_billable_uncompressed_bytes": gate.MAX_BILLABLE_UNCOMPRESSED_BYTES,
        "max_transport_control_frames": gate.MAX_BILLABLE_MESSAGES,
        "max_transport_control_wire_bytes": gate.wss_reference.MAX_TRANSPORT_CONTROL_WIRE_BYTES,
    }
    window = {"authorized_at_ns": now_ns + 10_000, "expires_at_ns": now_ns + 2_000_000_000}
    clock_source = "signed-clock-source-v1"
    runtime = {
        "loaded_senses_labels": [],
        "disabled_senses_labels": ["senses-a"],
        "expected_senses_labels": ["senses-a"],
        "installed_senses_labels": ["senses-a"],
        "active_senses_writer_process_count": 0,
        "provider_key_process_count": 0,
    }
    monkeypatch.setattr(gate, "_current_runtime_census", lambda _: runtime)
    free_bytes = 100 * 1024**3
    monkeypatch.setattr(gate, "_current_root", lambda root: (Path(root).resolve(), 123, free_bytes))
    required_bytes = (
        gate.wss_reference.DISK_FLOOR_BYTES
        + gate.wss_reference.STORED_LIMIT_BYTES
        + gate.wss_reference.MAX_MESSAGE_BYTES
        + gate.METADATA_RESERVE_BYTES
        + 1_000_000
    )
    account = {
        "schema": gate.ACCOUNT_SCHEMA,
        "plan_status": "active",
        "wss_billing_unit": "uncompressed_decimal_megabyte",
        "overage_disabled_or_hard_capped": True,
        "autoscaling_disabled": True,
        "shared_use_included_through_expiry": True,
        "plan_id": "synthetic-plan",
        "account_fingerprint_sha256": "5" * 64,
        "endpoint_key_fingerprint_sha256": "6" * 64,
        "remaining_wss_credits": 10_000_000,
        "control_tls_reconnect_overhead_credits": 50,
        "reset_at_ns": window["expires_at_ns"] + 1,
        "valid_until_ns": window["expires_at_ns"] + 1,
        "overage_ceiling_credits": 0,
        "all_shared_clients_finitely_bounded": True,
        "paid_overage_enabled": False,
        "autoscaling_enabled": False,
        "account_source": {
            "identity": "synthetic-account-source-v2",
            "raw_sha256": "1a" * 32,
            "sampled_at_ns": now_ns - 1_000_000_000,
            "received_at_ns": now_ns - 500_000_000,
        },
        "price_source": {
            "identity": "synthetic-price-source-v2",
            "raw_sha256": "2b" * 32,
            "sampled_at_ns": now_ns - 1_000_000_000,
            "received_at_ns": now_ns - 500_000_000,
            "assertion_verifier_sha256": sha(Path(gate.__file__).read_bytes()),
            "parsed_billing": {
                "billable_classes": ["logsSubscribe.notification", "logsSubscribe.response"],
                "rate_credits": 2,
                "chunk_bytes": 100_000,
                "chunk_unit": "uncompressed_decimal_bytes",
                "rounding_scope": "aggregate",
                "fixed_credits_per_session": 1,
                "control_credits_per_session": 2,
                "control_byte_billing": "session_fixed_covers_bounded_control_frames",
            },
        },
        "billing": {
            "billable_classes": ["logsSubscribe.notification", "logsSubscribe.response"],
            "rate_credits": 2,
            "chunk_bytes": 100_000,
            "chunk_unit": "uncompressed_decimal_bytes",
            "rounding_scope": "aggregate",
            "fixed_credits_per_session": 1,
            "control_credits_per_session": 2,
            "control_byte_billing": "session_fixed_covers_bounded_control_frames",
            "price_source_sha256": "2b" * 32,
        },
        "shared_clients": {
            "bounded_through_expiry": True,
            "enforcement": "suspended",
            "max_billable_messages": 0,
            "max_billable_uncompressed_bytes": 0,
            "max_sessions": 0,
            "valid_until_ns": window["expires_at_ns"],
        },
    }
    host = {
        "schema": gate.HOST_SCHEMA,
        "capture_root": str(capture.resolve()),
        "device_id": 123,
        "filesystem": "apfs",
        "durable_create_only_fsync_lock_qualified": True,
        "no_competing_mac_or_writer": True,
        "competing_writer_count": 0,
        "shared_host_lock_clear": True,
        "process_census_sha256": sha(canonical(runtime)),
        "reviewed_host_churn_reserve_bytes": 1_000_000,
        "required_free_bytes": required_bytes,
        "sampled_free_bytes": free_bytes,
        "sampled_at_ns": now_ns - 1_000_000_000,
        "clock_source_id": clock_source,
        "clock_boot_id": "synthetic-boot-id",
        "sampled_monotonic_ns": now_ns - 1_000_000_000,
        "trusted_clock_evidence_sha256": "3d" * 32,
    }
    evidence_refs = {
        "account_evidence": {"role": "helius_independent_checker"},
        "host_evidence": {"role": "host_attestor"},
        "review_evidence": {"role": "sol_reviewer"},
    }
    manifest = {
        "schema": gate.SCHEMA,
        "status": gate.GATE_STATUS,
        "predecessor": {
            "kind": gate.discontinuity.CERTIFICATE_SCHEMA,
            "certificate_file_sha256": cert_sha,
            "predecessor_digest_sha256": predecessor_digest,
            "frontier_slot": certificate["frontier_slot"],
            "gap_ids": [row["id"] for row in certificate["gaps"]],
            "unknown_intervals": certificate["unknown_intervals"],
        },
        "c4_quiescence_receipt_sha256": quiescence_sha,
        "window": window,
        "limits": limits,
        "source_bindings": source_pins,
        "wss_contract_versions": gate.WSS_CONTRACT_VERSIONS,
        "authorization_scope": gate.GATE_SCOPE,
        "live_gate_qualified": False,
        "gate_verifier_sha256": sha(Path(gate.__file__).read_bytes()),
        "trust_store_sha256": "a" * 64,
        "clock_source_id": clock_source,
        "claim_registry": registry_identity,
        **evidence_refs,
        "review_evidence_sha256": "c" * 64,
        "activation_authorized": False,
    }
    unsigned = dict(manifest)
    unsigned.pop("review_evidence")
    unsigned.pop("review_evidence_sha256")
    review = {
        "schema": gate.REVIEW_SCHEMA,
        "status": gate.GATE_STATUS,
        "manifest_sha256": sha(canonical(unsigned)),
        "predecessor_digest_sha256": predecessor_digest,
        "c4_quiescence_receipt_sha256": quiescence_sha,
        "account_evidence_sha256": "b" * 64,
        "host_evidence_sha256": "d" * 64,
        "gate_verifier_sha256": manifest["gate_verifier_sha256"],
        "trust_store_sha256": manifest["trust_store_sha256"],
        "claim_registry": registry_identity,
        "source_bindings": source_pins,
        "wss_contract_versions": gate.WSS_CONTRACT_VERSIONS,
        "authorization_scope": gate.GATE_SCOPE,
        "live_gate_qualified": False,
        "clock_source_id": clock_source,
        "window": window,
        "limits": limits,
        **gate._validate_account_budget(
            account, window=window, limits=limits, now_ns=now_ns,
            assertion_verifier_sha256=sha(Path(gate.__file__).read_bytes()),
        ),
        "activation_authorized": False,
    }
    signed_values = {
        "helius_independent_checker": (account, "b" * 64),
        "host_attestor": (host, "d" * 64),
        "sol_reviewer": (review, "c" * 64),
    }
    monkeypatch.setattr(gate, "_read_signed", lambda _root, _ref, role, **kwargs: signed_values[role])
    (packet / "gate.json").write_bytes(canonical(manifest) + b"\n")
    (packet / "gate.json").chmod(0o600)
    inputs = {
        "trust_store_path": tmp_path / "trust.json",
        "trust_store_sha256": "a" * 64,
        "openssl_path": tmp_path / "openssl",
        "openssl_sha256": "f" * 64,
        "openssl_version": "synthetic",
        "verifier_sha256": manifest["gate_verifier_sha256"],
        "repo_root": tmp_path,
        "capture_root": capture,
        "claim_registry_root": registry,
        "clock_source_id": clock_source,
        "now_ns": now_ns,
        "monotonic_now_ns": now_ns,
    }
    return packet, manifest, account, host, inputs


def test_runtime_census_matches_exact_script_and_node_key_presence(monkeypatch, tmp_path: Path):
    labels = ["ai.memecoin.brain.senses.test"]
    repo_root = tmp_path
    monkeypatch.setattr(gate, "_installed_senses_launchagent_labels", lambda: labels)
    evidence = repo_root / gate.QUIESCENCE_PATH
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(canonical({"senses_plists": [{"label": labels[0]}]}))

    def run(args, **kwargs):
        if args[:2] == ["/bin/launchctl", "list"]:
            return subprocess.CompletedProcess(args, 0, "PID Status Label\n- 0 other.label\n", "")
        if args[:2] == ["/bin/launchctl", "print-disabled"]:
            return subprocess.CompletedProcess(args, 0, f'"{labels[0]}" => disabled\n', "")
        if args[:2] == ["/bin/ps", "-axo"]:
            return subprocess.CompletedProcess(
                args,
                0,
                "101 node /usr/local/bin/node /opt/helius-mcp\n"
                "102 python3 /usr/bin/pytest tests/brain/test_senses_wss_initial_entry_v1.py\n"
                "103 python3 /repo/tools/brain_senses_service_v1_runner.py run\n"
                "104 python3 -c \"print('brain_senses_service_v1_runner.py')\"\n",
                "",
            )
        if args[:2] == ["/bin/ps", "eww"]:
            pid = args[-1]
            env = "HELIUS_API_KEY=redacted\n" if pid == "101" else "ordinary-env\n"
            return subprocess.CompletedProcess(args, 0, env, "")
        raise AssertionError(f"unexpected command: {args!r}")

    monkeypatch.setattr(gate.subprocess, "run", run)
    result = gate._current_runtime_census(repo_root)
    assert result["active_senses_writer_process_count"] == 1
    assert result["provider_key_process_count"] == 1
    assert result["loaded_senses_labels"] == []
    assert result["disabled_senses_labels"] == labels
    assert "redacted" not in repr(result)


def test_runtime_census_fails_closed_on_unparseable_process_argv(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(gate, "_installed_senses_launchagent_labels", lambda: [])
    evidence = tmp_path / gate.QUIESCENCE_PATH
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(canonical({"senses_plists": []}))

    def run(args, **kwargs):
        if args[:2] == ["/bin/launchctl", "list"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["/bin/launchctl", "print-disabled"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "1 python3 'unterminated\n", "")

    monkeypatch.setattr(gate.subprocess, "run", run)
    with pytest.raises(gate.GateError, match="current_owner_process_argv_unparseable"):
        gate._current_runtime_census(tmp_path)


def test_claim_is_create_only_and_one_use(tmp_path: Path):
    registry = private_dir(tmp_path / "claims")
    gate.auth.CANONICAL_CLAIM_REGISTRY_ROOT = registry
    kwargs = {
        "predecessor_digest_sha256": "a" * 64,
        "gate_manifest_sha256": "b" * 64,
        "claimed_at_ns": 123,
    }
    claim = reserve_wss_claim_fixture(registry, **kwargs)
    assert claim["schema"] == gate.CLAIM_SCHEMA
    assert claim["activation_performed"] is False
    assert claim["claim_key_sha256"] == gate.auth._initial_entry_claim_key(kwargs["predecessor_digest_sha256"])
    claim_path = registry / "successor-claims-v1.json"
    assert claim_path.stat().st_mode & 0o777 == 0o600
    lock_path = registry / ".successor-claim-registry-v1.lock"
    assert lock_path.exists()
    assert lock_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(gate.GateError, match="already_claimed"):
        reserve_wss_claim_fixture(registry, **kwargs)


def test_concurrent_claim_has_one_winner(tmp_path: Path):
    registry = private_dir(tmp_path / "claims")
    gate.auth.CANONICAL_CLAIM_REGISTRY_ROOT = registry
    kwargs = {
        "predecessor_digest_sha256": "c" * 64,
        "gate_manifest_sha256": "d" * 64,
        "claimed_at_ns": 456,
    }

    def attempt(_):
        try:
            return reserve_wss_claim_fixture(registry, **kwargs)["claim_key_sha256"]
        except gate.GateError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sum(value is not None for value in outcomes) == 1
    assert len(list(registry.glob("successor-claims-v1.json"))) == 1


def test_persistent_unlocked_registry_lock_recovers_after_process_restart(tmp_path: Path):
    registry = private_dir(tmp_path / "claims")
    gate.auth.CANONICAL_CLAIM_REGISTRY_ROOT = registry
    stale_lock = registry / ".successor-claim-registry-v1.lock"
    stale_lock.write_bytes(b"")
    stale_lock.chmod(0o600)
    claim = reserve_wss_claim_fixture(
        registry,
        predecessor_digest_sha256="e" * 64,
        gate_manifest_sha256="f" * 64,
        claimed_at_ns=789,
    )
    assert claim["activation_performed"] is False
    assert stale_lock.exists()


def test_shared_http_wss_predecessor_claim_identity_blocks_both_orderings(tmp_path: Path):
    digest = "e" * 64
    key = gate.auth._initial_entry_claim_key(digest)
    registry_wss_first = private_dir(tmp_path / "wss-first")
    gate.auth.CANONICAL_CLAIM_REGISTRY_ROOT = registry_wss_first
    wss_claim = reserve_wss_claim_fixture(
        registry_wss_first, predecessor_digest_sha256=digest,
        gate_manifest_sha256="f" * 64, claimed_at_ns=789,
    )
    assert wss_claim["claim_key_sha256"] == key
    shared = gate.auth._registry_claims(registry_wss_first)
    assert shared[key]["schema"] == gate.auth.INITIAL_ENTRY_CLAIM_SCHEMA
    with pytest.raises(gate.GateError, match="already_claimed"):
        reserve_wss_claim_fixture(
            registry_wss_first, predecessor_digest_sha256=digest,
            gate_manifest_sha256="1" * 64, claimed_at_ns=790,
        )

    registry_http_first = private_dir(tmp_path / "http-first")
    gate.auth.CANONICAL_CLAIM_REGISTRY_ROOT = registry_http_first
    http_claim = {
        "schema": gate.auth.INITIAL_ENTRY_CLAIM_SCHEMA,
        "lease_id": "http-preflight-synthetic",
        "predecessor_digest_kind": gate.auth.INITIAL_ENTRY_PREDECESSOR_KIND,
        "predecessor_digest_sha256": digest,
        "claim_key_sha256": key,
        "packet_digest": "a" * 64,
        "plan_sha256": "b" * 64,
        "schedule_sha256": "c" * 64,
        "review_signature_sha256": "d" * 64,
        "claimed_at_ns": 900,
        "activation_performed": False,
    }
    http_claim["claim_sha256"] = sha(canonical(http_claim))
    (registry_http_first / "successor-claims-v1.json").write_bytes(
        canonical({"schema": gate.auth.REGISTRY_SCHEMA, "claims": {key: http_claim}}) + b"\n"
    )
    (registry_http_first / "successor-claims-v1.json").chmod(0o600)
    with pytest.raises(gate.GateError, match="already_claimed"):
        reserve_wss_claim_fixture(
            registry_http_first, predecessor_digest_sha256=digest,
            gate_manifest_sha256="1" * 64, claimed_at_ns=901,
        )


@pytest.mark.parametrize("first", ["http", "wss"])
def test_http_api_and_wss_claim_writer_share_one_predecessor(tmp_path: Path, monkeypatch, first: str):
    digest = "9" * 64
    key = gate.auth._initial_entry_claim_key(digest)
    registry = private_dir(tmp_path / "canonical-registry")
    monkeypatch.setattr(gate.auth, "CANONICAL_CLAIM_REGISTRY_ROOT", registry)
    packet = private_dir(tmp_path / "gate-packet")
    (packet / "gate.json").write_bytes(b"reviewed-gate")
    registry_identity = gate.auth._registry_snapshot(registry)[1]
    wss_claim = lambda: reserve_wss_claim_fixture(
        registry, predecessor_digest_sha256=digest,
        gate_manifest_sha256="f" * 64, claimed_at_ns=100,
        review_signature_sha256="8" * 64,
    )

    def verify_http(_root, **kwargs):
        assert kwargs["registry_root"] == registry
        return {
            "eligible": True,
            "predecessor_digest_kind": gate.auth.INITIAL_ENTRY_PREDECESSOR_KIND,
            "predecessor_digest_sha256": digest,
            "predecessor_claim_key_sha256": key,
            "lease_id": "fixture-lease",
            "packet_digest": "1" * 64,
            "plan_sha256": "2" * 64,
            "schedule_sha256": "3" * 64,
            "evidence_sha256": {"sol_signature": "4" * 64},
            "claim_registry_identity": registry_identity,
            "lineage": {"artifact_identity": {"manifest_sha256": "5" * 64}},
        }

    monkeypatch.setattr(gate.common_claims.verifier, "verify_packet", verify_http)
    http_claim = lambda: gate.common_claims.claim_successor(
        packet, registry_root=registry, trust_store_path=tmp_path / "trust.json",
        trust_store_sha256="0" * 64, openssl_path=tmp_path / "openssl",
        openssl_sha256="0" * 64, verifier_sha256="0" * 64,
        helius_price_source_sha256="6" * 64, helius_price_table_sha256="7" * 64,
        clock_source_id="fixture-clock", capture_root_path=tmp_path,
    )

    if first == "http":
        assert http_claim()["claim_key_sha256"] == key
        with pytest.raises(gate.GateError, match="already_claimed"):
            wss_claim()
    else:
        assert wss_claim()["claim_key_sha256"] == key
        with pytest.raises(gate.common_claims.PreflightError, match="predecessor_successor_already_claimed"):
            http_claim()


@pytest.mark.parametrize("modality", ["http", "wss_private_claim_writer"])
def test_actual_claim_apis_fail_closed_on_pending_crash_prefix(tmp_path: Path, monkeypatch, modality: str):
    registry = private_dir(tmp_path / "canonical-registry")
    monkeypatch.setattr(gate.auth, "CANONICAL_CLAIM_REGISTRY_ROOT", registry)
    pending = registry / ".registry-claim-v1-crashed.pending"
    pending.write_bytes(b"partial claim write")
    pending.chmod(0o600)
    packet = private_dir(tmp_path / "packet")
    (packet / "gate.json").write_bytes(b"manifest")
    if modality == "wss_private_claim_writer":
        call = lambda: reserve_wss_claim_fixture(
            registry, predecessor_digest_sha256="a" * 64,
            gate_manifest_sha256="b" * 64, claimed_at_ns=123,
        )
        expected = gate.auth.VerificationError
    else:
        key = gate.auth._initial_entry_claim_key("a" * 64)
        registry_identity = gate.auth._registry_snapshot(registry)[1]
        monkeypatch.setattr(
            gate.common_claims.verifier, "verify_packet",
            lambda *_args, **_kwargs: {
                "eligible": True,
                "predecessor_digest_kind": gate.auth.INITIAL_ENTRY_PREDECESSOR_KIND,
                "predecessor_digest_sha256": "a" * 64,
                "predecessor_claim_key_sha256": key,
                "lease_id": "lease", "packet_digest": "c" * 64,
                "plan_sha256": "d" * 64, "schedule_sha256": "e" * 64,
                "evidence_sha256": {"sol_signature": "f" * 64},
                "claim_registry_identity": registry_identity,
                "lineage": {"artifact_identity": {"manifest_sha256": "1" * 64}},
            },
        )
        call = lambda: gate.common_claims.claim_successor(
            packet, registry_root=registry, trust_store_path=tmp_path / "trust",
            trust_store_sha256="0" * 64, openssl_path=tmp_path / "openssl",
            openssl_sha256="0" * 64, verifier_sha256="0" * 64,
            helius_price_source_sha256="2" * 64, helius_price_table_sha256="3" * 64,
            clock_source_id="clock", capture_root_path=tmp_path,
        )
        expected = gate.auth.VerificationError
    with pytest.raises(expected, match="crash_prefix"):
        call()


@pytest.mark.parametrize("alias", ["alternate", "symlink"])
def test_http_claim_api_rejects_alternate_or_symlink_registry(
    tmp_path: Path, monkeypatch, alias: str
):
    canonical_root = private_dir(tmp_path / "canonical")
    monkeypatch.setattr(gate.auth, "CANONICAL_CLAIM_REGISTRY_ROOT", canonical_root)
    alternate = private_dir(tmp_path / "alternate")
    supplied = alternate if alias == "alternate" else tmp_path / "registry-alias"
    if alias == "symlink":
        supplied.symlink_to(canonical_root, target_is_directory=True)
    packet = private_dir(tmp_path / "packet")
    (packet / "gate.json").write_bytes(b"manifest")
    call = lambda: gate.common_claims.claim_successor(
        packet, registry_root=supplied, trust_store_path=tmp_path / "trust",
        trust_store_sha256="0" * 64, openssl_path=tmp_path / "openssl",
        openssl_sha256="0" * 64, verifier_sha256="0" * 64,
        helius_price_source_sha256="2" * 64, helius_price_table_sha256="3" * 64,
        clock_source_id="clock", capture_root_path=tmp_path,
    )
    expected = gate.common_claims.PreflightError
    reason = "claim_registry_not_canonical_owner_root"
    with pytest.raises(expected, match=reason):
        call()


def test_http_claim_api_rejects_registry_inode_drift_under_lock(tmp_path: Path, monkeypatch):
    registry = private_dir(tmp_path / "canonical")
    monkeypatch.setattr(gate.auth, "CANONICAL_CLAIM_REGISTRY_ROOT", registry)
    original_identity = gate.auth._registry_snapshot(registry)[1]
    replacement = tmp_path / "replacement"

    def replace_registry():
        registry.rename(replacement)
        private_dir(registry)

    packet = private_dir(tmp_path / "packet")
    (packet / "gate.json").write_bytes(b"manifest")
    calls = {"count": 0}

    def verify_http(*_args, **_kwargs):
        calls["count"] += 1
        identity = original_identity
        if calls["count"] == 2:
            replace_registry()
            identity = gate.auth._registry_snapshot(registry)[1]
        return {
            "eligible": True,
            "predecessor_digest_kind": gate.auth.INITIAL_ENTRY_PREDECESSOR_KIND,
            "predecessor_digest_sha256": "a" * 64,
            "predecessor_claim_key_sha256": gate.auth._initial_entry_claim_key("a" * 64),
            "lease_id": "lease", "packet_digest": "c" * 64,
            "plan_sha256": "d" * 64, "schedule_sha256": "e" * 64,
            "evidence_sha256": {"sol_signature": "f" * 64},
            "claim_registry_identity": identity,
            "lineage": {"artifact_identity": {"manifest_sha256": "1" * 64}},
        }

    monkeypatch.setattr(gate.common_claims.verifier, "verify_packet", verify_http)
    with pytest.raises(gate.common_claims.PreflightError, match="claim_registry_identity_changed_under_lock"):
        gate.common_claims.claim_successor(
            packet, registry_root=registry, trust_store_path=tmp_path / "trust",
            trust_store_sha256="0" * 64, openssl_path=tmp_path / "openssl",
            openssl_sha256="0" * 64, verifier_sha256="0" * 64,
            helius_price_source_sha256="2" * 64, helius_price_table_sha256="3" * 64,
            clock_source_id="clock", capture_root_path=tmp_path,
        )


@pytest.mark.parametrize("scope", ["aggregate", "session", "message"])
def test_credit_bound_monotone_and_chunk_ceilings(scope: str):
    # Inputs are billable uncompressed bytes and message counts; result is integer provider credits.
    params = {
        "sessions": 2,
        "max_message_bytes": 4 * 1024 * 1024,
        "rate_credits": 2,
        "chunk_bytes": 100_000,
        "rounding_scope": scope,
        "fixed_credits_per_session": 3,
        "control_credits_per_session": 5,
    }
    by_bytes = [
        gate._credits_for_limits(message_count=7, byte_count=n, **params)
        for n in (0, 1, 99_999, 100_000, 100_001, 200_000)
    ]
    by_messages = [
        gate._credits_for_limits(message_count=n, byte_count=100_001, **params)
        for n in (0, 1, 2, 3, 4)
    ]
    assert by_bytes == sorted(by_bytes)
    assert by_messages == sorted(by_messages)
    if scope in {"aggregate", "session"}:
        expected_chunks = (2 if scope == "aggregate" else 3)
        assert gate._credits_for_limits(message_count=1, byte_count=100_001, **params) == (
            expected_chunks * 2 + 2 * 3 + 2 * 5
        )
    else:
        assert gate._credits_for_limits(message_count=1, byte_count=100_001, **params) == (
            2 * 2 + 2 * 3 + 2 * 5
        )


def test_v2_claim_fails_before_registry_access_or_mutation(tmp_path: Path, monkeypatch):
    registry = private_dir(tmp_path / "canonical-registry")
    monkeypatch.setattr(gate.auth, "CANONICAL_CLAIM_REGISTRY_ROOT", registry)
    claims = registry / gate.auth.REGISTRY_CLAIMS_FILENAME
    claims.write_bytes(b'{"claims":{},"schema":"brain-senses-successor-claim-registry-v1"}\n')
    claims.chmod(0o600)
    lock = registry / ".successor-claim-registry-v1.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    before = {path.name: (path.read_bytes(), path.stat().st_mode & 0o777) for path in registry.iterdir()}

    def forbidden_access(_path):
        pytest.fail("inert v2 claim path touched registry identity")

    monkeypatch.setattr(gate, "_registry_identity", forbidden_access)
    with pytest.raises(gate.GateError, match="source_qualified_live_gate_required"):
        gate.claim_first_entry(registry, tmp_path / "packet", now_ns=88)
    after = {path.name: (path.read_bytes(), path.stat().st_mode & 0o777) for path in registry.iterdir()}
    assert after == before


def test_process_environment_census_failure_fails_closed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(gate, "_installed_senses_launchagent_labels", lambda: [])
    evidence = tmp_path / gate.QUIESCENCE_PATH
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(canonical({"senses_plists": []}))

    def run(args, **kwargs):
        if args[:2] == ["/bin/launchctl", "list"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["/bin/launchctl", "print-disabled"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["/bin/ps", "-axo"]:
            return subprocess.CompletedProcess(args, 0, "201 node /usr/local/bin/node /opt/helius-mcp\n", "")
        return subprocess.CompletedProcess(args, 1, "", "permission denied")

    monkeypatch.setattr(gate.subprocess, "run", run)
    with pytest.raises(gate.GateError, match="current_process_environment_census_unavailable"):
        gate._current_runtime_census(tmp_path)


def test_census_detects_new_loaded_senses_namespace_label(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(gate, "_installed_senses_launchagent_labels", lambda: ["ai.memecoin.brain.senses.known"])
    evidence = tmp_path / gate.QUIESCENCE_PATH
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(canonical({"senses_plists": [{"label": "ai.memecoin.brain.senses.known"}]}))

    def run(args, **kwargs):
        if args[:2] == ["/bin/launchctl", "list"]:
            return subprocess.CompletedProcess(
                args, 0, "PID Status Label\n- 0 ai.memecoin.brain.senses.new-label\n", ""
            )
        if args[:2] == ["/bin/launchctl", "print-disabled"]:
            return subprocess.CompletedProcess(args, 0, '"ai.memecoin.brain.senses.known" => disabled\n', "")
        if args[:2] == ["/bin/ps", "-axo"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected command: {args!r}")

    monkeypatch.setattr(gate.subprocess, "run", run)
    result = gate._current_runtime_census(tmp_path)
    assert result["loaded_senses_labels"] == ["ai.memecoin.brain.senses.new-label"]


def test_launchagent_inventory_detects_new_unloaded_senses_plist(monkeypatch, tmp_path: Path):
    launchagents = tmp_path / "Library" / "LaunchAgents"
    launchagents.mkdir(parents=True)
    plist = launchagents / "unlisted-senses-job.plist"
    plist.write_bytes(plistlib.dumps({"Label": "ai.memecoin.brain.senses.unlisted"}))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    labels = gate._installed_senses_launchagent_labels()
    assert "ai.memecoin.brain.senses.unlisted" in labels


def test_gate_rejects_new_installed_senses_label_even_when_unloaded(tmp_path: Path, monkeypatch):
    packet, _, _, _, inputs = make_gate_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gate,
        "_current_runtime_census",
        lambda _: {
            "loaded_senses_labels": [],
            "disabled_senses_labels": ["senses-a"],
            "expected_senses_labels": ["senses-a"],
            "installed_senses_labels": ["senses-a", "senses-new"],
            "active_senses_writer_process_count": 0,
            "provider_key_process_count": 0,
        },
    )
    with pytest.raises(gate.GateError, match="current_owner_or_competing_provider_process_failed"):
        gate._verify_manifest(packet, **inputs)


def test_provider_enforced_shared_allocation_keeps_worker_count_diagnostic(
    tmp_path: Path, monkeypatch
):
    packet, manifest, account, host, inputs = make_gate_case(tmp_path, monkeypatch)
    account["shared_clients"]["enforcement"] = "provider_enforced_allocation"
    runtime = gate._current_runtime_census(inputs["repo_root"])
    runtime["provider_key_process_count"] = 2
    host["process_census_sha256"] = sha(canonical(runtime))
    monkeypatch.setattr(gate, "_current_runtime_census", lambda _root: runtime)
    read_signed = gate._read_signed

    def adjusted_read_signed(root, ref, role, **kwargs):
        row, row_sha = read_signed(root, ref, role, **kwargs)
        if role == "sol_reviewer":
            row["shared_client_enforcement"] = "provider_enforced_allocation"
            row["shared_client_caps"] = {
                "max_billable_messages": 0,
                "max_billable_uncompressed_bytes": 0,
                "max_sessions": 0,
            }
        return row, row_sha

    monkeypatch.setattr(gate, "_read_signed", adjusted_read_signed)
    result = gate._verify_manifest(packet, **inputs)
    assert result["provider_key_process_count"] == 2
    assert result["live_gate_qualified"] is False


def test_create_only_gate_receipt_is_private_and_no_activation(tmp_path: Path):
    packet = private_dir(tmp_path / "packet")
    manifest = {
        "schema": gate.SCHEMA,
        "activation_authorized": False,
        "status": gate.GATE_STATUS,
        "authorization_scope": gate.GATE_SCOPE,
        "live_gate_qualified": False,
    }
    gate.write_gate_receipt_create_only(packet, manifest)
    path = packet / "gate.json"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(gate.GateError, match="gate_receipt_already_exists"):
        gate.write_gate_receipt_create_only(packet, manifest)


def test_candidate_preauthorization_does_not_claim_due_phase(monkeypatch, tmp_path: Path):
    calls = []
    def verify(packet_root, *, now_ns, monotonic_now_ns, phase, **kwargs):
        calls.append((now_ns, monotonic_now_ns, phase))
        return {"valid": True}
    monkeypatch.setattr(gate, "_verify_manifest", verify)
    result = gate.dry_run_activation(tmp_path, now_ns=77, monotonic_now_ns=88)
    assert calls == [(77, 88, "preauthorize")]
    assert result["preauthorization_valid"] is True
    assert result["pre_dns_gate_valid"] is False
    assert result["activation_authorized"] is False


def test_due_window_runs_distinct_pre_dns_and_pre_subscribe_checks(monkeypatch, tmp_path: Path):
    calls = []
    def verify(packet_root, *, now_ns, monotonic_now_ns, phase, **kwargs):
        calls.append((now_ns, monotonic_now_ns, phase))
        return {"phase": phase}
    monkeypatch.setattr(gate, "_verify_manifest", verify)
    result = gate.dry_run_due_window_checks(
        tmp_path, pre_dns_now_ns=100, pre_dns_monotonic_ns=200,
        pre_subscribe_now_ns=101, pre_subscribe_monotonic_ns=201,
    )
    assert calls == [(100, 200, "pre_dns"), (101, 201, "pre_subscribe")]
    assert result["pre_dns_gate_valid"] is result["pre_subscribe_gate_valid"] is True
    assert result["activation_authorized"] is False


def test_due_window_rejects_clock_regression(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(gate, "_verify_manifest", lambda *_args, **kwargs: {"valid": True})
    with pytest.raises(gate.GateError, match="due_phase_clock_regression"):
        gate.dry_run_due_window_checks(
            tmp_path, pre_dns_now_ns=100, pre_dns_monotonic_ns=200,
            pre_subscribe_now_ns=99, pre_subscribe_monotonic_ns=201,
        )


def test_signed_external_evidence_requires_trusted_role_signature(tmp_path: Path):
    openssl = Path(shutil.which("openssl") or "")
    assert openssl.is_file()
    private = tmp_path / "attestor-private.pem"
    private.write_bytes(
        subprocess.run(
            [str(openssl), "genpkey", "-algorithm", "Ed25519"],
            check=True,
            capture_output=True,
        ).stdout
    )
    private.chmod(0o600)
    public = subprocess.run(
        [str(openssl), "pkey", "-in", str(private), "-pubout"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    payload = {"schema": gate.ACCOUNT_SCHEMA, "remaining_wss_credits": 5000}
    payload_raw = canonical(payload) + b"\n"
    signature = subprocess.run(
        [str(openssl), "pkeyutl", "-sign", "-inkey", str(private), "-rawin", "-in", "/dev/stdin"],
        input=payload_raw,
        check=True,
        capture_output=True,
    ).stdout
    root = private_dir(tmp_path / "packet")
    evidence = root / "evidence"
    evidence.mkdir(mode=0o700)
    payload_path = evidence / "account.json"
    signature_path = evidence / "account.sig"
    payload_path.write_bytes(payload_raw)
    signature_path.write_bytes(signature)
    payload_path.chmod(0o600)
    signature_path.chmod(0o600)
    ref = {
        "payload_path": "evidence/account.json",
        "payload_sha256": sha(payload_raw),
        "signature_path": "evidence/account.sig",
        "signature_sha256": sha(signature),
        "signer_id": "account-checker",
        "role": "helius_independent_checker",
    }
    keyset = {
        "account-checker": {
            "role": "helius_independent_checker",
            "public_key_pem": public,
        }
    }
    result, payload_hash = gate._read_signed(
        root,
        ref,
        keyset=keyset,
        openssl_path=openssl,
        role="helius_independent_checker",
    )
    assert result == payload
    assert payload_hash == sha(payload_raw)

    payload_path.write_bytes(canonical({**payload, "remaining_wss_credits": 1}) + b"\n")
    with pytest.raises(gate.GateError, match="signed_helius_independent_checker_evidence_invalid"):
        gate._read_signed(
            root,
            ref,
            keyset=keyset,
            openssl_path=openssl,
            role="helius_independent_checker",
        )


def test_full_offline_dry_run_revalidates_without_client_or_credentials(tmp_path: Path, monkeypatch):
    packet, _, _, _, inputs = make_gate_case(tmp_path, monkeypatch)
    result = gate.dry_run_activation(packet, **inputs)
    assert result["status"] == "offline_signed_assertion_preauthorization_no_client"
    assert result["activation_authorized"] is False
    assert result["live_gate_qualified"] is False
    assert result["raw_account_price_source_verified"] is False
    assert result["client_created"] is False
    assert result["credential_loaded"] is False
    assert result["dns_or_wss_request_performed"] is False


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("c4_quiescence", "c4_quiescence_binding_mismatch"),
        ("c4_gap_ids", "abnormal_c4_predecessor_binding_mismatch"),
        ("c4_unknown_interval", "abnormal_c4_predecessor_binding_mismatch"),
        ("future_window", "wss_candidate_preauthorization_not_future"),
        ("window_expiry", "wss_first_entry_window_invalid"),
        ("credit", "account_credit_headroom_insufficient"),
        ("disk", "signed_host_evidence_or_current_disk_failed"),
        ("limit_sessions", "wss_first_entry_limits_mismatch"),
        ("limit_raw", "wss_first_entry_limits_mismatch"),
        ("limit_stored", "wss_first_entry_limits_mismatch"),
        ("limit_message", "wss_first_entry_limits_mismatch"),
        ("limit_billable_messages", "wss_first_entry_limits_mismatch"),
        ("limit_billable_bytes", "wss_first_entry_limits_mismatch"),
        ("source_freshness", "account_or_price_source_identity_or_freshness_invalid"),
        ("billing_ambiguous", "account_billing_model_invalid_or_ambiguous"),
        ("price_parser", "price_source_parsed_facts_or_parser_binding_mismatch"),
        ("shared_unbounded", "shared_client_consumption_unbounded_or_expired"),
        ("shared_common_lock", "shared_client_consumption_unbounded_or_expired"),
        ("suspended_shared_cap", "suspended_shared_clients_must_have_zero_caps"),
        ("suspended_worker", "suspended_shared_clients_key_worker_present"),
        ("clock_drift", "signed_host_evidence_or_current_disk_failed"),
        ("trust", "reviewed_verifier_trust_or_clock_binding_mismatch"),
        ("verifier", "reviewed_verifier_trust_or_clock_binding_mismatch"),
        ("registry", "reviewed_claim_registry_identity_mismatch"),
        ("wss_contract_versions", "wss_contract_version_mismatch"),
    ],
)
def test_full_gate_rejects_review_or_capacity_mutations(
    tmp_path: Path, monkeypatch, mutation: str, reason: str
):
    packet, manifest, account, host, inputs = make_gate_case(tmp_path, monkeypatch)
    if mutation == "c4_quiescence":
        manifest["c4_quiescence_receipt_sha256"] = "0" * 64
    elif mutation == "c4_gap_ids":
        manifest["predecessor"]["gap_ids"] = ["gap-1", "gap-2", "gap-3"]
    elif mutation == "c4_unknown_interval":
        manifest["predecessor"]["unknown_intervals"] = []
    elif mutation == "future_window":
        manifest["window"]["authorized_at_ns"] = inputs["now_ns"] - 1
    elif mutation == "window_expiry":
        manifest["window"]["expires_at_ns"] = manifest["window"]["authorized_at_ns"]
    elif mutation == "credit":
        account["remaining_wss_credits"] = 0
    elif mutation == "disk":
        host["sampled_free_bytes"] = host["required_free_bytes"] - 1
    elif mutation == "limit_sessions":
        manifest["limits"]["max_sessions"] += 1
    elif mutation == "limit_raw":
        manifest["limits"]["raw_limit_bytes"] -= 1
    elif mutation == "limit_stored":
        manifest["limits"]["stored_limit_bytes"] -= 1
    elif mutation == "limit_message":
        manifest["limits"]["max_message_bytes"] -= 1
    elif mutation == "limit_billable_messages":
        manifest["limits"]["max_billable_messages"] -= 1
    elif mutation == "limit_billable_bytes":
        manifest["limits"]["max_billable_uncompressed_bytes"] -= 1
    elif mutation == "source_freshness":
        account["price_source"]["sampled_at_ns"] = inputs["now_ns"] - gate.MAX_ACCOUNT_EVIDENCE_AGE_NS - 1
    elif mutation == "billing_ambiguous":
        account["billing"]["rounding_scope"] = "unknown"
    elif mutation == "price_parser":
        account["price_source"]["assertion_verifier_sha256"] = "0" * 64
    elif mutation == "shared_unbounded":
        account["shared_clients"]["bounded_through_expiry"] = False
    elif mutation == "shared_common_lock":
        account["shared_clients"]["enforcement"] = "common_lock"
    elif mutation == "suspended_shared_cap":
        account["shared_clients"]["max_billable_messages"] = 1
    elif mutation == "suspended_worker":
        runtime = gate._current_runtime_census(inputs["repo_root"])
        runtime["provider_key_process_count"] = 1
        host["process_census_sha256"] = sha(canonical(runtime))
        monkeypatch.setattr(gate, "_current_runtime_census", lambda _root: runtime)
    elif mutation == "clock_drift":
        inputs["monotonic_now_ns"] += 10_000_000_000
    elif mutation == "trust":
        manifest["trust_store_sha256"] = "0" * 64
    elif mutation == "verifier":
        manifest["gate_verifier_sha256"] = "0" * 64
    elif mutation == "registry":
        manifest["claim_registry"]["device_id"] += 1
    elif mutation == "wss_contract_versions":
        manifest["wss_contract_versions"] = {
            "campaign_authorization_schema_version": 1,
            "session_binding_schema_version": 1,
        }
    (packet / "gate.json").write_bytes(canonical(manifest) + b"\n")
    with pytest.raises(gate.GateError, match=reason):
        gate._verify_manifest(packet, **inputs)


@settings(max_examples=100, deadline=None)
@given(st.lists(st.integers(min_value=1, max_value=1_000_000), min_size=1, max_size=8))
def test_rounding_credit_bound_covers_direct_positive_partitions(parts: list[int]):
    """Check the declared local model, not provider billing truth."""
    chunk_bytes = 100_000
    rate_credits = 2
    sessions = len(parts)
    message_count = len(parts)
    total_bytes = sum(parts)
    fixed = 3
    control = 5
    base = {
        "sessions": sessions,
        "max_message_bytes": 4 * 1024 * 1024,
        "rate_credits": rate_credits,
        "chunk_bytes": chunk_bytes,
        "fixed_credits_per_session": fixed,
        "control_credits_per_session": control,
    }
    direct_chunks = sum((part + chunk_bytes - 1) // chunk_bytes for part in parts)
    fixed_total = sessions * (fixed + control)
    for scope in ("session", "message"):
        credits = gate._credits_for_limits(
            message_count=message_count, byte_count=total_bytes,
            rounding_scope=scope, **base,
        )
        assert (credits - fixed_total) // rate_credits >= direct_chunks
    credits = gate._credits_for_limits(
        message_count=message_count, byte_count=total_bytes,
        rounding_scope="aggregate", **base,
    )
    aggregate_chunks = (total_bytes + chunk_bytes - 1) // chunk_bytes
    assert (credits - fixed_total) // rate_credits == aggregate_chunks
