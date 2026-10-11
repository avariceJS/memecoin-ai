from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from tools import brain_senses_activation_preflight_v1 as writer
from tools import brain_senses_activation_preflight_verifier_v1 as verifier
from tools import brain_senses_finite_calendar_v3 as calendar
from tools import brain_senses_finite_calendar_verifier_v3 as calendar_verifier
from tools import brain_senses_initial_entry_calendar_compiler_v1 as initial_compiler
from tools import brain_senses_initial_entry_calendar_verifier_v1 as initial_verifier
from tools import brain_senses_initial_entry_discontinuity_verifier_v1 as discontinuity_verifier
from tools import brain_senses_operation_bundle_v2 as bridge

FLOOR = 50 * 1024**3
OPENSSL = Path(shutil.which("openssl") or "").resolve()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = canonical(value) + b"\n"
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


def keypair(directory: Path, name: str) -> tuple[Path, str]:
    private = directory / f"{name}-private.pem"
    private.write_bytes(
        subprocess.run(
            [str(OPENSSL), "genpkey", "-algorithm", "Ed25519"], check=True, capture_output=True
        ).stdout
    )
    private.chmod(0o600)
    public = subprocess.run(
        [str(OPENSSL), "pkey", "-in", str(private), "-pubout"], check=True, capture_output=True
    ).stdout.decode()
    return private, public


def sign(private: Path, payload: bytes) -> bytes:
    with tempfile_file(private.parent, payload) as message_path:
        return subprocess.run(
            [
                str(OPENSSL),
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private),
                "-rawin",
                "-in",
                str(message_path),
            ],
            check=True,
            capture_output=True,
        ).stdout


class tempfile_file:
    def __init__(self, parent: Path, data: bytes):
        self.path = parent / f".sign-input-{os.urandom(8).hex()}"
        self.data = data

    def __enter__(self):
        self.path.write_bytes(self.data)
        self.path.chmod(0o600)
        return self.path

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)


def signed_ref(
    root: Path, private: Path, payload: dict, *, key_id: str, role: str, label: str
) -> dict:
    payload_bytes = canonical(payload) + b"\n"
    payload_path = root / "evidence" / f"{label}.json"
    sig_path = root / "evidence" / f"{label}.sig"
    payload_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload_path.write_bytes(payload_bytes)
    payload_path.chmod(0o600)
    signature = sign(private, payload_bytes)
    sig_path.write_bytes(signature)
    sig_path.chmod(0o600)
    return {
        "payload_path": str(payload_path.relative_to(root)),
        "payload_sha256": sha(payload_bytes),
        "signature_path": str(sig_path.relative_to(root)),
        "signature_sha256": sha(signature),
        "signer_id": key_id,
        "role": role,
    }


def code_pins(verifier_sha256: str) -> dict[str, str]:
    tool_dir = Path(calendar.__file__).resolve().parent
    paths = {
        "calendar_driver": tool_dir / "brain_senses_finite_calendar_v3.py",
        "calendar_verifier": Path(calendar_verifier.__file__).resolve(),
        "operation_bridge": tool_dir / "brain_senses_operation_bundle_v2.py",
        "operation_verifier": tool_dir / "brain_senses_operation_bundle_verifier_v2.py",
        "preflight_driver": tool_dir / "brain_senses_activation_preflight_v1.py",
        "preflight_verifier": Path(verifier.__file__).resolve(),
    }
    values = {name: sha(path.read_bytes()) for name, path in paths.items()}
    values["preflight_verifier"] = verifier_sha256
    return values


def make_case(
    tmp_path: Path,
    *,
    host_age_ns: int = 0,
    charge_age_ns: int = 0,
    host_free_bytes: int = 60 * 1024**3,
    forged_host_signature: bool = False,
    bad_trust_pin: bool = False,
):
    now = time.time_ns()
    lineage_root = tmp_path / "packet" / "lineage" / "prior"
    (tmp_path / "packet").mkdir(mode=0o700)
    (tmp_path / "packet" / "lineage").mkdir(mode=0o700)
    buckets = [
        {
            "bucket_id": "prior-anchor",
            "kind": "anchor",
            "start_ns": now - 20_000_000_000,
            "end_ns": now - 10_000_000_000,
        }
    ]
    prior_plan = {
        "schema": bridge.SCHEMA,
        "calendar_contract": calendar.contract_binding(),
        "lease_id": "lease-prior",
        "created_ns": now - 32_000_000_000,
        "expires_ns": now - 1_000_000_000,
        "source_sha256": {"test-contract": "a" * 64},
        "caps": {
            "requests": 10,
            "raw_bytes": 10_000_000,
            "stored_bytes": 10_000_000,
            "physical_bytes": FLOOR,
            "width": 100,
            "daily_requests": 10,
            "daily_raw_bytes": 10_000_000,
            "daily_stored_bytes": 10_000_000,
        },
        "buckets": buckets,
        "predecessor": {
            "terminal_sha256": "b" * 64,
            "frontier_slot": 84,
            "gaps": [],
            "unknown_intervals": [],
        },
        "schedule_sha256": sha(canonical(buckets)),
    }
    # The fixture uses fake disk accounting; no host/storage state is read or changed.
    original_usage = shutil.disk_usage
    try:
        calendar.shutil.disk_usage = lambda _: shutil._ntuple_diskusage(
            200 * 1024**3, 100 * 1024**3, 100 * 1024**3
        )
        calendar.initialize(lineage_root, prior_plan)
        calendar.drive_once(lineage_root, now_ns=time.time_ns())
    finally:
        calendar.shutil.disk_usage = original_usage
    prior_handoff = calendar_verifier.verify_handoff(lineage_root)
    successor_now = time.time_ns()

    schedule = [
        {
            "bucket_id": "successor-anchor",
            "kind": "anchor",
            "start_ns": successor_now + 60_000_000_000,
            "end_ns": successor_now + 120_000_000_000,
            "requests": 1,
            "raw_bytes": 2_000_000,
            "stored_bytes": 2_000_000,
            "width": 64,
        }
    ]
    schedule_hash = sha(canonical(schedule))
    successor_plan = {
        "schema": bridge.SCHEMA,
        "calendar_contract": calendar.contract_binding(),
        "lease_id": "lease-successor",
        "created_ns": successor_now,
        "expires_ns": successor_now + 2 * bridge.UTC_DAY_NS,
        "schedule_sha256": schedule_hash,
        "source_sha256": {"contract": "c" * 64, "schema": "d" * 64},
        "caps": {
            "requests": 5,
            "raw_bytes": 5_000_000,
            "stored_bytes": 5_000_000,
            "physical_bytes": FLOOR,
            "width": 100,
            "daily_requests": 5,
            "daily_raw_bytes": 5_000_000,
            "daily_stored_bytes": 5_000_000,
        },
        "buckets": [
            dict((k, v) for k, v in row.items() if k in {"bucket_id", "kind", "start_ns", "end_ns"})
            for row in schedule
        ],
        "predecessor": {
            "terminal_sha256": prior_handoff["handoff_sha256"],
            "frontier_slot": prior_handoff["frontier_slot"],
            "gaps": [gap["bucket_id"] for gap in prior_handoff["gaps"]],
            "unknown_intervals": prior_handoff["unknown_intervals"],
        },
    }
    packet_root = tmp_path / "packet"
    registry_root = tmp_path / "global-claim-registry"
    registry_root.mkdir(mode=0o700)
    verifier.CANONICAL_CLAIM_REGISTRY_ROOT = registry_root
    _, registry_identity = verifier._registry_snapshot(registry_root)
    registry_id = sha(canonical(registry_identity))
    plan_bytes = write_json(packet_root / "successor-plan-v2.json", successor_plan)
    plan_hash = sha(plan_bytes)
    storage_capture = successor_plan["caps"]["raw_bytes"] + successor_plan["caps"]["stored_bytes"]
    storage_metadata = (
        successor_plan["caps"]["requests"] * verifier.METADATA_PER_REQUEST
        + len(schedule) * verifier.METADATA_PER_OPERATION
    )
    required_free = FLOOR + storage_capture + storage_metadata + 1024**3
    capture_stat = packet_root.stat()
    capture_vfs = os.statvfs(packet_root)
    host_body = {
        "schema": "senses-local-host-attestation-v1",
        "sampled_at_ns": successor_now - host_age_ns,
        "disk_sampled_at_ns": successor_now - host_age_ns,
        "clock_now_ns": successor_now,
        "owner_uid": os.getuid(),
        "free_bytes": host_free_bytes,
        "capture_root_path": str(packet_root.resolve()),
        "capture_root_dev": capture_stat.st_dev,
        "capture_root_ino": capture_stat.st_ino,
        "filesystem_id": str(capture_vfs.f_fsid),
        "lease_id": successor_plan["lease_id"],
        "plan_sha256": plan_hash,
        "schedule_sha256": schedule_hash,
        "clock_source_id": "offline-fixture-clock",
    }
    price_model = {
        "schema": "helius-plan-method-price-model-v1",
        "plan_sha256": plan_hash,
        "schedule_sha256": schedule_hash,
        "source_sha256": successor_plan["source_sha256"],
        "method_counts": {"getSlot": 1},
        "units_per_request": {"getSlot": 2},
        "unit": "fixture-credit-v1",
        "source_artifact_sha256": "e" * 64,
    }
    price_table_hash = sha(canonical(price_model))
    charge_body = {
        "schema": "helius-charge-headroom-attestation-v1",
        "sampled_at_ns": successor_now - charge_age_ns,
        "account_id": "helius-test-account",
        "plan_sha256": plan_hash,
        "schedule_sha256": schedule_hash,
        "source_sha256": successor_plan["source_sha256"],
        "available_units": 10_000,
        "committed_units": 500,
        "lease_upper_bound_units": 2,
        "minimum_headroom_units": 100,
        "unit": "fixture-credit-v1",
        "independent_check_method": "signed-fixture-audit",
        "account_artifact_sha256": "e" * 64,
        "method_price_model": price_model,
    }
    priv_dir = tmp_path / "fixture-keys"
    priv_dir.mkdir(mode=0o700)
    host_key, host_pub = keypair(priv_dir, "host")
    helius_key, helius_pub = keypair(priv_dir, "helius")
    sol_key, sol_pub = keypair(priv_dir, "sol")
    trust = {
        "schema": "senses-preflight-trust-store-v1",
        "keys": [
            {"key_id": "host-key", "role": "host_attestor", "public_key_pem": host_pub},
            {
                "key_id": "helius-checker",
                "role": "helius_independent_checker",
                "public_key_pem": helius_pub,
            },
            {
                "key_id": "sol-review-key",
                "role": "sol_reviewer",
                "name": "Sol",
                "public_key_pem": sol_pub,
            },
        ],
    }
    trust_path = tmp_path / "trusted-public-keys.json"
    trust_bytes = write_json(trust_path, trust)
    trust_hash = sha(trust_bytes)
    host_ref = signed_ref(
        packet_root, host_key, host_body, key_id="host-key", role="host_attestor", label="host"
    )
    if forged_host_signature:
        sig_path = packet_root / host_ref["signature_path"]
        sig = bytearray(sig_path.read_bytes())
        sig[0] ^= 1
        sig_path.write_bytes(sig)
        host_ref["signature_sha256"] = sha(bytes(sig))
    charge_ref = signed_ref(
        packet_root,
        helius_key,
        charge_body,
        key_id="helius-checker",
        role="helius_independent_checker",
        label="helius-charge",
    )
    limits = {
        "host_clock": 300_000_000_000,
        "disk": 300_000_000_000,
        "helius_charge": 900_000_000_000,
        "sol_review": 3_600_000_000_000,
    }
    lineage_anchor = {
        "terminal_sha256": "b" * 64,
        "frontier_slot": 84,
        "terminal_at_ns": now - 32_000_000_000,
        "gaps": [],
        "unknown_intervals": [],
    }
    lineage = {
        "lineage_anchor": lineage_anchor,
        "verified_handoffs": [
            {
                "lease_id": prior_handoff["lease_id"],
                "handoff_sha256": prior_handoff["handoff_sha256"],
                "terminal_at_ns": prior_handoff["terminal_at_ns"],
                "gaps": [gap["bucket_id"] for gap in prior_handoff["gaps"]],
                "unknown_intervals": prior_handoff["unknown_intervals"],
            }
        ],
        "terminal_at_ns": prior_handoff["terminal_at_ns"],
        "gaps": [gap["bucket_id"] for gap in prior_handoff["gaps"]],
        "unknown_intervals": prior_handoff["unknown_intervals"],
    }
    openssl_version = subprocess.run(
        [str(OPENSSL), "version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    openssl_hash = sha(OPENSSL.read_bytes())
    verifier_hash = sha(Path(verifier.__file__).resolve().read_bytes())
    pins = code_pins(verifier_hash)
    manifest = {
        "schema": writer.SCHEMA,
        "packet_id": "offline-preflight-test-packet",
        "activation_scope": "offline-preflight-only",
        "claim_registry_id": registry_id,
        "claim_registry_identity": registry_identity,
        "clock_source_id": "offline-fixture-clock",
        "lineage_anchor": lineage_anchor,
        "predecessor_handoffs": [
            {
                "root": "lineage/prior",
                "handoff_sha256": prior_handoff["handoff_sha256"],
            }
        ],
        "successor_plan_path": "successor-plan-v2.json",
        "successor": {
            "lease_id": successor_plan["lease_id"],
            "plan_sha256": plan_hash,
            "schedule_sha256": schedule_hash,
            "source_sha256": sha(canonical(successor_plan["source_sha256"])),
            "caps_sha256": sha(canonical(successor_plan["caps"])),
            "code_sha256": sha(canonical(pins)),
        },
        "code_sha256": pins,
        "schedule": schedule,
        "host_evidence": host_ref,
        "helius_evidence": charge_ref,
        "helius_account_id": "helius-test-account",
        "lease_charge_upper_bound_units": 2,
        "helius_price_source_sha256": "e" * 64,
        "helius_price_table_sha256": price_table_hash,
        "freshness_limits_ns": limits,
        "storage_envelope": {
            "schema": "senses-full-lease-storage-envelope-v1",
            "reserve_policy": "explicit_reviewed_minimum",
            "capture_bound_bytes": storage_capture,
            "metadata_bound_bytes": storage_metadata,
            "reserve_bytes": 1024**3,
            "required_free_bytes": required_free,
        },
        "capture_root_path": str(packet_root.resolve()),
        "crypto_tool": {
            "path": str(OPENSSL),
            "sha256": openssl_hash,
            "version": openssl_version,
        },
    }
    manifest_digest = writer.packet_digest(manifest)
    review_body = {
        "schema": "senses-sol-preflight-decision-v1",
        "decision": "approve_offline_preflight",
        "reviewer_name": "Sol",
        "reviewed_at_ns": successor_now,
        "packet_digest": manifest_digest,
        "lease_id": successor_plan["lease_id"],
        "lineage_sha256": sha(canonical(lineage)),
        "host_evidence_sha256": host_ref["payload_sha256"],
        "helius_evidence_sha256": charge_ref["payload_sha256"],
        "minimum_churn_reserve_bytes": 1024**3,
        "scope": "offline_preflight_eligibility_only",
        "trust_store_sha256": trust_hash,
        "freshness_limits_ns": limits,
    }
    manifest["review_ref"] = signed_ref(
        packet_root,
        sol_key,
        review_body,
        key_id="sol-review-key",
        role="sol_reviewer",
        label="sol-review",
    )
    writer.write_packet(packet_root, manifest)
    return {
        "root": packet_root,
        "registry_root": registry_root,
        "trust_path": trust_path,
        "trust_hash": trust_hash,
        "openssl_path": OPENSSL,
        "openssl_hash": openssl_hash,
        "verifier_hash": verifier_hash,
        "now": successor_now,
        "required_free": required_free,
        "capture_root": packet_root,
        "helius_price_table_hash": price_table_hash,
        "host_key": host_key,
        "helius_key": helius_key,
        "sol_key": sol_key,
        "prior_handoff": prior_handoff,
    }


@contextmanager
def fake_current_disk(case):
    actual = os.statvfs(case["capture_root"])
    frsize = actual.f_frsize
    blocks = (case["required_free"] + frsize - 1) // frsize
    fake = SimpleNamespace(f_bavail=blocks, f_frsize=frsize, f_fsid=actual.f_fsid)
    with mock.patch.object(verifier.os, "statvfs", lambda _path: fake):
        yield


def convert_case_to_initial_entry(case):
    root = case["root"]
    manifest_path = root / "preflight-v1.json"
    manifest = json.loads(manifest_path.read_bytes())
    schedule = manifest["schedule"]
    plan_path = root / "successor-plan-v2.json"
    old_plan = json.loads(plan_path.read_bytes())
    host_payload = json.loads((root / manifest["host_evidence"]["payload_path"]).read_bytes())
    clock_now = host_payload["clock_now_ns"]
    buckets = [
        {key: row[key] for key in ("bucket_id", "kind", "start_ns", "end_ns")} for row in schedule
    ]
    inputs = {
        "lease_id": "initial-entry-successor-fixture",
        "created_ns": clock_now,
        "expires_ns": old_plan["expires_ns"],
        "clock_source_id": manifest["clock_source_id"],
        "clock_observed_ns": clock_now,
        "buckets": buckets,
        "caps": old_plan["caps"],
        "source_sha256": old_plan["source_sha256"],
    }
    certificate_path = (
        Path(discontinuity_verifier.__file__).resolve().parents[1]
        / "docs/evidence/senses-initial-entry-discontinuity-v1-c4-2026-10-11.json"
    )
    candidate = initial_compiler.compile_candidate(certificate_path, inputs)
    candidate_path = root / "initial-entry-candidate-v1.json"
    candidate_raw = write_json(candidate_path, candidate)
    successor_plan = candidate["plan"]
    plan_raw = write_json(plan_path, successor_plan)
    schedule_sha = sha(canonical(schedule))
    plan_sha = sha(plan_raw)
    source_sha = sha(canonical(successor_plan["source_sha256"]))
    caps_sha = sha(canonical(successor_plan["caps"]))
    tool_dir = Path(verifier.__file__).resolve().parent
    pins = code_pins(case["verifier_hash"])
    pins.update(
        {
            "initial_entry_compiler": sha(
                (tool_dir / "brain_senses_initial_entry_calendar_compiler_v1.py").read_bytes()
            ),
            "initial_entry_verifier": sha(Path(initial_verifier.__file__).read_bytes()),
            "discontinuity_verifier": sha(Path(discontinuity_verifier.__file__).read_bytes()),
        }
    )
    manifest.pop("lineage_anchor")
    manifest.pop("predecessor_handoffs")
    manifest["predecessor_digest_kind"] = verifier.INITIAL_ENTRY_PREDECESSOR_KIND
    manifest["initial_entry"] = {
        "certificate_repo_path": verifier.INITIAL_ENTRY_CERTIFICATE_REPO_PATH,
        "certificate_file_sha256": sha(certificate_path.read_bytes()),
        "predecessor_digest_sha256": candidate["predecessor_digest_sha256"],
        "candidate_path": candidate_path.name,
        "candidate_sha256": sha(candidate_raw),
        "candidate_inputs": inputs,
    }
    manifest["code_sha256"] = pins
    manifest["successor"] = {
        "lease_id": successor_plan["lease_id"],
        "plan_sha256": plan_sha,
        "schedule_sha256": schedule_sha,
        "source_sha256": source_sha,
        "caps_sha256": caps_sha,
        "code_sha256": sha(canonical(pins)),
    }
    old_host_ref = manifest["host_evidence"]
    host_body = json.loads((root / old_host_ref["payload_path"]).read_bytes())
    host_body.update(
        {
            "lease_id": successor_plan["lease_id"],
            "plan_sha256": plan_sha,
            "schedule_sha256": schedule_sha,
        }
    )
    manifest["host_evidence"] = signed_ref(
        root,
        case["host_key"],
        host_body,
        key_id="host-key",
        role="host_attestor",
        label="initial-host",
    )
    old_helius_ref = manifest["helius_evidence"]
    charge_body = json.loads((root / old_helius_ref["payload_path"]).read_bytes())
    charge_body.update(
        {
            "plan_sha256": plan_sha,
            "schedule_sha256": schedule_sha,
            "source_sha256": successor_plan["source_sha256"],
        }
    )
    price_model = charge_body["method_price_model"]
    price_model.update(
        {
            "plan_sha256": plan_sha,
            "schedule_sha256": schedule_sha,
            "source_sha256": successor_plan["source_sha256"],
        }
    )
    price_hash = sha(canonical(price_model))
    manifest["helius_price_table_sha256"] = price_hash
    manifest["helius_evidence"] = signed_ref(
        root,
        case["helius_key"],
        charge_body,
        key_id="helius-checker",
        role="helius_independent_checker",
        label="initial-helius-charge",
    )
    case["helius_price_table_hash"] = price_hash
    lineage = verifier._initial_entry_lineage(root.resolve(strict=True), manifest, successor_plan)
    review_body = {
        "schema": "senses-sol-preflight-decision-v1",
        "decision": "approve_offline_preflight",
        "reviewer_name": "Sol",
        "reviewed_at_ns": clock_now,
        "packet_digest": writer.packet_digest(manifest),
        "lease_id": successor_plan["lease_id"],
        "lineage_sha256": sha(canonical(lineage)),
        "host_evidence_sha256": manifest["host_evidence"]["payload_sha256"],
        "helius_evidence_sha256": manifest["helius_evidence"]["payload_sha256"],
        "minimum_churn_reserve_bytes": manifest["storage_envelope"]["reserve_bytes"],
        "scope": "offline_preflight_eligibility_only",
        "trust_store_sha256": case["trust_hash"],
        "freshness_limits_ns": manifest["freshness_limits_ns"],
    }
    manifest["review_ref"] = signed_ref(
        root,
        case["sol_key"],
        review_body,
        key_id="sol-review-key",
        role="sol_reviewer",
        label="initial-sol-review",
    )
    write_json(manifest_path, manifest)
    return case


def verify(case, **overrides):
    args = {
        "registry_root": case["registry_root"],
        "trust_store_path": case["trust_path"],
        "trust_store_sha256": case["trust_hash"],
        "openssl_path": case["openssl_path"],
        "openssl_sha256": case["openssl_hash"],
        "verifier_sha256": case["verifier_hash"],
        "helius_price_source_sha256": "e" * 64,
        "helius_price_table_sha256": case["helius_price_table_hash"],
        "clock_source_id": "offline-fixture-clock",
        "capture_root_path": case["capture_root"],
        "now_ns": case["now"],
    }
    args.update(overrides)
    with fake_current_disk(case):
        return verifier.verify_packet(case["root"], **args)


def claim(case, packet_root: Path | None = None):
    kwargs = {
        "registry_root": case["registry_root"],
        "trust_store_path": case["trust_path"],
        "trust_store_sha256": case["trust_hash"],
        "openssl_path": case["openssl_path"],
        "openssl_sha256": case["openssl_hash"],
        "verifier_sha256": case["verifier_hash"],
        "helius_price_source_sha256": "e" * 64,
        "helius_price_table_sha256": case["helius_price_table_hash"],
        "clock_source_id": "offline-fixture-clock",
        "capture_root_path": case["capture_root"],
    }
    with fake_current_disk(case):
        return writer.claim_successor(packet_root or case["root"], **kwargs)


def claim_kwargs(case):
    return {
        "registry_root": case["registry_root"],
        "trust_store_path": case["trust_path"],
        "trust_store_sha256": case["trust_hash"],
        "openssl_path": case["openssl_path"],
        "openssl_sha256": case["openssl_hash"],
        "verifier_sha256": case["verifier_hash"],
        "helius_price_source_sha256": "e" * 64,
        "helius_price_table_sha256": case["helius_price_table_hash"],
        "clock_source_id": "offline-fixture-clock",
        "capture_root_path": case["capture_root"],
    }


def resign_packet_evidence(case, role: str, payload: dict, *, manifest_updates=None):
    manifest_path = case["root"] / "preflight-v1.json"
    manifest = json.loads(manifest_path.read_bytes())
    if role == "host":
        key, key_id, evidence_role, manifest_field, label = (
            case["host_key"],
            "host-key",
            "host_attestor",
            "host_evidence",
            "host",
        )
    elif role == "helius":
        key, key_id, evidence_role, manifest_field, label = (
            case["helius_key"],
            "helius-checker",
            "helius_independent_checker",
            "helius_evidence",
            "helius-charge",
        )
    else:
        raise AssertionError(role)
    ref = signed_ref(
        case["root"],
        key,
        payload,
        key_id=key_id,
        role=evidence_role,
        label=label,
    )
    manifest[manifest_field] = ref
    manifest.update(manifest_updates or {})
    manifest["review_ref"] = resign_sol_review(case, manifest)
    write_json(manifest_path, manifest)
    return manifest


def resign_sol_review(case, manifest):
    review_path = case["root"] / "evidence" / "sol-review.json"
    review = json.loads(review_path.read_bytes())
    review["packet_digest"] = writer.packet_digest(manifest)
    review["host_evidence_sha256"] = manifest["host_evidence"]["payload_sha256"]
    review["helius_evidence_sha256"] = manifest["helius_evidence"]["payload_sha256"]
    return signed_ref(
        case["root"],
        case["sol_key"],
        review,
        key_id="sol-review-key",
        role="sol_reviewer",
        label="sol-review",
    )


def verify_claim(case, *, now_ns: int):
    with fake_current_disk(case):
        return writer.verify_claim(case["root"], now_ns=now_ns, **claim_kwargs(case))


def test_complete_signed_lineage_and_budget_arithmetic_are_eligible(tmp_path: Path):
    case = make_case(tmp_path)
    result = verify(case)
    assert result["eligible"] is True, result
    assert result["activation_authorized"] is False
    assert result["usage_upper_bound"]["requests"] == 1
    assert result["storage"]["required_free_bytes"] == case["required_free"]
    assert result["storage"]["capture_bound_bytes"] == 10_000_000
    assert result["storage"]["capture_bound_bytes"] > 4_000_000
    assert result["storage"]["required_free_bytes"] > FLOOR
    assert len(result["lineage"]["verified_handoffs"]) == 1


def test_initial_entry_candidate_is_verified_as_distinct_predecessor(tmp_path: Path):
    case = convert_case_to_initial_entry(make_case(tmp_path))
    result = verify(case)
    assert result["eligible"] is True, result
    assert result["activation_authorized"] is False
    assert result["predecessor_digest_kind"] == verifier.INITIAL_ENTRY_PREDECESSOR_KIND
    assert result["predecessor_digest_sha256"] != result["predecessor_claim_key_sha256"]
    assert result["lineage"]["normal_v3_handoff"] is False
    assert result["lineage"]["unknown_interval_closed"] is False
    assert len(result["lineage"]["gaps"]) == 4
    assert result["lineage"]["unknown_intervals"][0]["end_exclusive"] is None


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    [
        ("certificate_hash", "initial_entry_certificate_file_hash_mismatch"),
        ("predecessor_digest", "initial_entry_predecessor_digest_mismatch"),
        ("candidate_hash", "initial_entry_candidate_file_hash_mismatch"),
        (
            "candidate_input_lease",
            "initial_entry_candidate_invalid:candidate_plan_or_pins_mismatch",
        ),
    ],
)
def test_initial_entry_candidate_rebinding_fails_closed(
    tmp_path: Path, mutation: str, expected_failure: str
):
    case = convert_case_to_initial_entry(make_case(tmp_path))
    manifest_path = case["root"] / "preflight-v1.json"
    manifest = json.loads(manifest_path.read_bytes())
    entry = manifest["initial_entry"]
    if mutation == "certificate_hash":
        entry["certificate_file_sha256"] = "0" * 64
    elif mutation == "predecessor_digest":
        entry["predecessor_digest_sha256"] = "1" * 64
    elif mutation == "candidate_hash":
        entry["candidate_sha256"] = "2" * 64
    elif mutation == "candidate_input_lease":
        entry["candidate_inputs"]["lease_id"] = "other-lease"
    write_json(manifest_path, manifest)
    result = verify(case)
    assert result["eligible"] is False
    assert result["activation_authorized"] is False
    assert result["failure"] == expected_failure


def test_initial_entry_claim_is_domain_separated_and_one_use(tmp_path: Path):
    case = convert_case_to_initial_entry(make_case(tmp_path))
    claim_record = claim(case)
    assert claim_record["schema"] == writer.INITIAL_ENTRY_CLAIM_SCHEMA
    assert claim_record["activation_performed"] is False
    assert claim_record["predecessor_digest_kind"] == verifier.INITIAL_ENTRY_PREDECESSOR_KIND
    assert claim_record["predecessor_digest_sha256"] != claim_record["claim_key_sha256"]
    checked = verify_claim(case, now_ns=time.time_ns())
    assert checked["valid"] is True
    assert checked["eligible"] is False
    assert checked["activation_authorized"] is False
    with pytest.raises(writer.PreflightError, match="predecessor_successor_already_claimed"):
        claim(case)


def test_initial_entry_candidate_cannot_escape_packet_root(tmp_path: Path):
    case = convert_case_to_initial_entry(make_case(tmp_path))
    manifest_path = case["root"] / "preflight-v1.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["initial_entry"]["candidate_path"] = "../candidate.json"
    write_json(manifest_path, manifest)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "artifact_path_invalid"


def test_initial_entry_artifact_inode_change_between_checks_fails(tmp_path: Path, monkeypatch):
    case = convert_case_to_initial_entry(make_case(tmp_path))
    original_verify = writer.verifier.verify_packet
    calls = 0

    def replace_after_first_verification(*args, **kwargs):
        nonlocal calls
        result = original_verify(*args, **kwargs)
        calls += 1
        if calls == 1:
            candidate = case["root"] / "initial-entry-candidate-v1.json"
            replacement = candidate.with_suffix(".replacement")
            replacement.write_bytes(candidate.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(candidate)
        return result

    monkeypatch.setattr(writer.verifier, "verify_packet", replace_after_first_verification)
    with pytest.raises(writer.PreflightError, match="successor_preflight_became_ineligible"):
        claim(case)


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    [
        ("unknown_intervals", "successor_predecessor_link_invalid"),
        ("created_before_terminal", "successor_predecessor_link_invalid"),
        ("bucket_before_terminal", "successor_predecessor_link_invalid"),
    ],
)
def test_lineage_requires_exact_unknowns_and_nonoverlapping_terminal_time(
    tmp_path: Path, mutation: str, expected_failure: str
):
    case = make_case(tmp_path)
    manifest = json.loads((case["root"] / "preflight-v1.json").read_bytes())
    plan = json.loads((case["root"] / "successor-plan-v2.json").read_bytes())
    if mutation == "unknown_intervals":
        plan["predecessor"]["unknown_intervals"] = [{"invented": "uncertainty"}]
    elif mutation == "created_before_terminal":
        plan["created_ns"] = case["prior_handoff"]["terminal_at_ns"] - 1
    else:
        plan["buckets"][0]["start_ns"] = case["prior_handoff"]["terminal_at_ns"] - 1
    with pytest.raises(verifier.VerificationError, match=expected_failure):
        verifier._lineage(case["root"], manifest, plan)


def test_every_intermediate_handoff_carries_unknowns_and_terminal_time(tmp_path: Path):
    packet_root = tmp_path / "packet"
    packet_root.mkdir(mode=0o700)
    anchor = {
        "terminal_sha256": "a" * 64,
        "frontier_slot": 10,
        "terminal_at_ns": 100,
        "gaps": ["initial-gap"],
        "unknown_intervals": [{"from": 10, "to": 10}],
    }
    results = []
    prior = anchor
    items = []
    for index in (1, 2):
        lease_root = packet_root / f"hop{index}"
        lease_root.mkdir(mode=0o700)
        lease_id = f"prior-{index}"
        plan = {
            "lease_id": lease_id,
            "created_ns": prior["terminal_at_ns"],
            "buckets": [{"bucket_id": f"b{index}", "start_ns": prior["terminal_at_ns"] + 1}],
            "predecessor": {
                "terminal_sha256": prior["terminal_sha256"],
                "frontier_slot": prior["frontier_slot"],
                "gaps": prior["gaps"],
                "unknown_intervals": prior["unknown_intervals"],
            },
        }
        plan_raw = write_json(lease_root / "plan-v2.json", plan)
        receipt = {
            "valid": True,
            "lease_id": lease_id,
            "handoff_sha256": str(index) * 64,
            "plan_sha256": sha(plan_raw),
            "frontier_slot": 10 + index,
            "terminal_at_ns": 200 + 100 * (index - 1),
            "gaps": [{"bucket_id": f"gap-{index}"}],
            "unknown_intervals": [{"from": 10 + index, "to": 10 + index}],
        }
        results.append(receipt)
        items.append(
            {
                "root": lease_root.name,
                "handoff_sha256": receipt["handoff_sha256"],
            }
        )
        prior = {
            "terminal_sha256": receipt["handoff_sha256"],
            "frontier_slot": receipt["frontier_slot"],
            "terminal_at_ns": receipt["terminal_at_ns"],
            "gaps": [gap["bucket_id"] for gap in receipt["gaps"]],
            "unknown_intervals": receipt["unknown_intervals"],
        }
    successor = {
        "lease_id": "next",
        "created_ns": prior["terminal_at_ns"],
        "buckets": [{"bucket_id": "next-anchor", "start_ns": prior["terminal_at_ns"] + 1}],
        "predecessor": {
            "terminal_sha256": prior["terminal_sha256"],
            "frontier_slot": prior["frontier_slot"],
            "gaps": prior["gaps"],
            "unknown_intervals": prior["unknown_intervals"],
        },
    }
    manifest = {"lineage_anchor": anchor, "predecessor_handoffs": items}
    with mock.patch.object(verifier.calendar_verify, "verify_handoff", side_effect=results):
        lineage = verifier._lineage(packet_root, manifest, successor)
    assert lineage["unknown_intervals"] == prior["unknown_intervals"]
    assert lineage["terminal_at_ns"] == prior["terminal_at_ns"]
    assert len(lineage["verified_handoffs"]) == 2


@pytest.mark.parametrize(
    "mutation", ["unknown_intervals", "created_before_terminal", "bucket_before_terminal"]
)
def test_intermediate_handoff_mismatch_stops_lineage(tmp_path: Path, mutation: str):
    packet_root = tmp_path / "packet"
    packet_root.mkdir(mode=0o700)
    first = packet_root / "first"
    second = packet_root / "second"
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)
    anchor = {
        "terminal_sha256": "a" * 64,
        "frontier_slot": 10,
        "terminal_at_ns": 100,
        "gaps": [],
        "unknown_intervals": [],
    }
    plan1 = {
        "lease_id": "prior-1",
        "created_ns": 100,
        "buckets": [{"bucket_id": "b1", "start_ns": 101}],
        "predecessor": {
            "terminal_sha256": anchor["terminal_sha256"],
            "frontier_slot": anchor["frontier_slot"],
            "gaps": [],
            "unknown_intervals": [],
        },
    }
    plan2 = {
        "lease_id": "prior-2",
        "created_ns": 200,
        "buckets": [{"bucket_id": "b2", "start_ns": 201}],
        "predecessor": {
            "terminal_sha256": "1" * 64,
            "frontier_slot": 11,
            "gaps": ["gap-1"],
            "unknown_intervals": [{"expected": "carry"}],
        },
    }
    if mutation == "unknown_intervals":
        plan2["predecessor"]["unknown_intervals"] = [{"wrong": "lineage"}]
    elif mutation == "created_before_terminal":
        plan2["created_ns"] = 199
    else:
        plan2["buckets"][0]["start_ns"] = 199
    first_raw = write_json(first / "plan-v2.json", plan1)
    second_raw = write_json(second / "plan-v2.json", plan2)
    receipts = [
        {
            "valid": True,
            "lease_id": "prior-1",
            "handoff_sha256": "1" * 64,
            "plan_sha256": sha(first_raw),
            "frontier_slot": 11,
            "terminal_at_ns": 200,
            "gaps": [{"bucket_id": "gap-1"}],
            "unknown_intervals": [{"expected": "carry"}],
        },
        {
            "valid": True,
            "lease_id": "prior-2",
            "handoff_sha256": "2" * 64,
            "plan_sha256": sha(second_raw),
            "frontier_slot": 12,
            "terminal_at_ns": 300,
            "gaps": [],
            "unknown_intervals": [],
        },
    ]
    manifest = {
        "lineage_anchor": anchor,
        "predecessor_handoffs": [
            {"root": "first", "handoff_sha256": "1" * 64},
            {"root": "second", "handoff_sha256": "2" * 64},
        ],
    }
    successor = {
        "lease_id": "next",
        "created_ns": 300,
        "buckets": [{"bucket_id": "next", "start_ns": 301}],
        "predecessor": {
            "terminal_sha256": "2" * 64,
            "frontier_slot": 12,
            "gaps": [],
            "unknown_intervals": [],
        },
    }
    with mock.patch.object(verifier.calendar_verify, "verify_handoff", side_effect=receipts):
        with pytest.raises(verifier.VerificationError, match="lineage_predecessor_link_invalid"):
            verifier._lineage(packet_root, manifest, successor)


def test_cross_midnight_frame_charges_full_possible_use_on_both_utc_days():
    start = verifier.DAY_NS - 10
    plan = {
        "buckets": [
            {
                "bucket_id": "cross-midnight-frame",
                "kind": "frame",
                "start_ns": start,
                "end_ns": verifier.DAY_NS + 10,
            }
        ],
        "caps": {
            "requests": 34,
            "raw_bytes": 100,
            "stored_bytes": 200,
            "width": 10,
            "daily_requests": 34,
            "daily_raw_bytes": 100,
            "daily_stored_bytes": 200,
        },
    }
    entries = [
        {
            **plan["buckets"][0],
            "requests": 34,
            "raw_bytes": 1,
            "stored_bytes": 1,
            "width": 10,
        }
    ]
    totals, result = verifier._validate_schedule(plan, entries)
    assert totals == {"requests": 34, "raw_bytes": 100, "stored_bytes": 200}
    assert result["per_day"][0] == {"requests": 34, "raw_bytes": 100, "stored_bytes": 200}
    assert result["per_day"][1] == {"requests": 34, "raw_bytes": 100, "stored_bytes": 200}
    assert result["method_counts"] == {"getSlot": 1, "getBlocks": 1, "getBlock": 32}


def test_capture_root_must_match_signed_current_volume_identity(tmp_path: Path):
    case = make_case(tmp_path)
    manifest = json.loads((case["root"] / "preflight-v1.json").read_bytes())
    host_ref = manifest["host_evidence"]
    host = json.loads((case["root"] / host_ref["payload_path"]).read_bytes())
    host["capture_root_path"] = str(tmp_path / "different-root")
    resign_packet_evidence(case, "host", host)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "signed_disk_evidence_capture_root_mismatch"


def test_capture_root_must_match_out_of_band_future_runtime_path(tmp_path: Path):
    case = make_case(tmp_path)
    result = verify(case, capture_root_path=tmp_path)
    assert result["eligible"] is False
    assert result["failure"] == "runtime_capture_root_out_of_band_mismatch"


def test_clock_source_id_must_match_out_of_band_pin(tmp_path: Path):
    case = make_case(tmp_path)
    manifest = json.loads((case["root"] / "preflight-v1.json").read_bytes())
    host_ref = manifest["host_evidence"]
    host = json.loads((case["root"] / host_ref["payload_path"]).read_bytes())
    host["clock_source_id"] = "different-clock"
    resign_packet_evidence(case, "host", host)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "trusted_local_clock_or_disk_evidence_stale_or_insufficient"


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("kind", "other"),
        ("first_kind", "frame"),
        ("end_after_expiry", None),
        ("bad_source_digest", None),
    ],
)
def test_plan_shape_is_independently_validated_before_claim(tmp_path: Path, mutation: str, value):
    case = make_case(tmp_path)
    plan_path = case["root"] / "successor-plan-v2.json"
    plan = json.loads(plan_path.read_bytes())
    if mutation in {"kind", "first_kind"}:
        plan["buckets"][0 if mutation == "first_kind" else -1]["kind"] = value
    elif mutation == "end_after_expiry":
        plan["buckets"][-1]["end_ns"] = plan["expires_ns"] + 1
    else:
        plan["source_sha256"]["contract"] = "not-a-digest"
    write_json(plan_path, plan)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "successor_lease_window_or_caps_invalid"


def test_churn_reserve_must_match_the_signed_sol_minimum(tmp_path: Path):
    case = make_case(tmp_path)
    path = case["root"] / "preflight-v1.json"
    manifest = json.loads(path.read_bytes())
    storage = manifest["storage_envelope"]
    storage["reserve_bytes"] = 2 * 1024**3
    storage["required_free_bytes"] = (
        FLOOR
        + storage["capture_bound_bytes"]
        + storage["metadata_bound_bytes"]
        + storage["reserve_bytes"]
    )
    case["required_free"] = storage["required_free_bytes"]
    manifest["review_ref"] = resign_sol_review(case, manifest)
    write_json(path, manifest)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "authenticated_sol_decision_invalid_or_stale"


def test_signed_underpriced_helius_table_fails_out_of_band_table_pin(tmp_path: Path):
    case = make_case(tmp_path)
    manifest = json.loads((case["root"] / "preflight-v1.json").read_bytes())
    ref = manifest["helius_evidence"]
    charge = json.loads((case["root"] / ref["payload_path"]).read_bytes())
    charge["method_price_model"]["units_per_request"]["getSlot"] = 1
    charge["lease_upper_bound_units"] = 1
    manifest = resign_packet_evidence(case, "helius", charge)
    manifest["lease_charge_upper_bound_units"] = 1
    write_json(case["root"] / "preflight-v1.json", manifest)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "helius_signed_method_price_model_binding_invalid"


def test_signed_understated_charge_is_recomputed_from_pinned_method_prices(tmp_path: Path):
    case = make_case(tmp_path)
    manifest = json.loads((case["root"] / "preflight-v1.json").read_bytes())
    ref = manifest["helius_evidence"]
    charge = json.loads((case["root"] / ref["payload_path"]).read_bytes())
    charge["lease_upper_bound_units"] = 1
    resign_packet_evidence(
        case,
        "helius",
        charge,
        manifest_updates={"lease_charge_upper_bound_units": 1},
    )
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "helius_charge_or_headroom_evidence_invalid"


def test_signed_charge_unit_must_match_pinned_price_table_unit(tmp_path: Path):
    case = make_case(tmp_path)
    manifest = json.loads((case["root"] / "preflight-v1.json").read_bytes())
    ref = manifest["helius_evidence"]
    charge = json.loads((case["root"] / ref["payload_path"]).read_bytes())
    charge["unit"] = "arbitrary-unit"
    resign_packet_evidence(case, "helius", charge)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "helius_charge_or_headroom_evidence_invalid"


def test_signed_fresh_evidence_does_not_authenticate_a_forged_host_signature(tmp_path: Path):
    case = make_case(tmp_path, forged_host_signature=True)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "ed25519_signature_invalid"


def test_stale_host_clock_or_disk_evidence_is_ineligible(tmp_path: Path):
    case = make_case(tmp_path, host_age_ns=600_000_000_000)
    result = verify(case)
    assert result["eligible"] is False
    assert "trusted_local_clock_or_disk" in result["failure"]


def test_stale_helius_headroom_evidence_is_ineligible(tmp_path: Path):
    case = make_case(tmp_path, charge_age_ns=1_000_000_000_000)
    result = verify(case)
    assert result["eligible"] is False
    assert "helius_charge_or_headroom" in result["failure"]


def test_full_lease_storage_must_preserve_floor_after_worst_case_capture(tmp_path: Path):
    case = make_case(tmp_path, host_free_bytes=FLOOR + 500_000_000)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "full_lease_storage_floor_not_met"


def test_schedule_edit_breaks_exact_successor_binding(tmp_path: Path):
    case = make_case(tmp_path)
    path = case["root"] / "preflight-v1.json"
    manifest = json.loads(path.read_bytes())
    manifest["schedule"][0]["start_ns"] += 1
    write_json(path, manifest)
    result = verify(case)
    assert result["eligible"] is False


def test_past_or_backfill_bucket_is_not_eligible(tmp_path: Path):
    case = make_case(tmp_path)
    path = case["root"] / "successor-plan-v2.json"
    plan = json.loads(path.read_bytes())
    plan["buckets"][0]["start_ns"] = case["now"] - 1
    write_json(path, plan)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "successor_lease_window_or_caps_invalid"


def test_trust_store_requires_out_of_band_pin(tmp_path: Path):
    case = make_case(tmp_path)
    result = verify(case, trust_store_sha256="f" * 64)
    assert result["eligible"] is False
    assert result["failure"] == "out_of_band_trust_store_pin_mismatch"


def test_trust_store_rejects_public_key_reuse_across_evidence_roles(tmp_path: Path):
    case = make_case(tmp_path)
    trust = json.loads(case["trust_path"].read_bytes())
    host_public_key = trust["keys"][0]["public_key_pem"]
    for row in trust["keys"][1:]:
        row["public_key_pem"] = host_public_key
    case["trust_hash"] = sha(write_json(case["trust_path"], trust))
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "trust_store_public_key_reused_across_roles"


def test_missing_or_changed_out_of_band_price_pins_are_ineligible(tmp_path: Path):
    case = make_case(tmp_path)
    assert verify(case, helius_price_source_sha256="")["eligible"] is False
    assert verify(case, helius_price_table_sha256="f" * 64)["eligible"] is False
    args = claim_kwargs(case)
    args.pop("helius_price_table_sha256")
    with fake_current_disk(case):
        missing = verifier.verify_packet(case["root"], **args)
    assert missing["eligible"] is False
    assert missing["failure"] == "out_of_band_configuration_missing"


def test_packet_is_bound_to_configured_global_claim_registry(tmp_path: Path):
    case = make_case(tmp_path)
    other_registry = tmp_path / "other-registry"
    other_registry.mkdir(mode=0o700)
    result = verify(case, registry_root=other_registry)
    assert result["eligible"] is False
    assert result["failure"] == "claim_registry_path_invalid"


def test_claim_is_write_once_and_claim_verifies_without_activation(tmp_path: Path):
    case = make_case(tmp_path)
    receipt = claim(case)
    assert receipt["activation_performed"] is False
    checked = verify_claim(case, now_ns=time.time_ns())
    assert checked["valid"] is True
    assert checked["eligible_at_claim"] is True
    assert checked["eligible"] is False
    assert verify(case)["failure"] == "predecessor_successor_already_claimed"
    stale = verify(case, now_ns=case["now"] + 2 * 3600 * 1_000_000_000)
    assert stale["eligible"] is False
    historical = verify_claim(case, now_ns=case["now"] + 2 * 3600 * 1_000_000_000)
    assert historical["eligible_at_claim"] is True
    assert historical["eligible"] is False
    with pytest.raises(writer.PreflightError, match="already_claimed"):
        claim(case)


def test_concurrent_claim_invocations_write_exactly_one_successor(tmp_path: Path):
    case = make_case(tmp_path)
    second_root = tmp_path / "packet-copy"
    shutil.copytree(case["root"], second_root)
    kwargs = claim_kwargs(case)

    def try_claim(packet_root):
        try:
            return writer.claim_successor(packet_root, **kwargs)
        except writer.PreflightError as exc:
            return str(exc)

    with fake_current_disk(case):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(try_claim, (case["root"], second_root)))
    assert sum(isinstance(result, dict) for result in results) == 1, results
    registry = json.loads((case["registry_root"] / "successor-claims-v1.json").read_bytes())
    assert len(registry["claims"]) == 1


def test_sequential_claim_from_distinct_packet_root_is_rejected(tmp_path: Path):
    case = make_case(tmp_path)
    second_root = tmp_path / "packet-copy"
    shutil.copytree(case["root"], second_root)
    receipt = claim(case)
    assert receipt["predecessor_terminal_sha256"] == case["prior_handoff"]["handoff_sha256"]
    with pytest.raises(writer.PreflightError, match="already_claimed"):
        claim(case, second_root)


def test_corrupt_global_claim_registry_fails_closed(tmp_path: Path):
    case = make_case(tmp_path)
    receipt = claim(case)
    registry_path = case["registry_root"] / "successor-claims-v1.json"
    registry = json.loads(registry_path.read_bytes())
    registry["claims"][receipt["predecessor_terminal_sha256"]]["lease_id"] = "tampered"
    write_json(registry_path, registry)
    result = verify(case)
    assert result["eligible"] is False
    assert result["failure"] == "claim_registry_record_invalid"


def test_pending_claim_crash_prefix_fails_closed(tmp_path: Path):
    case = make_case(tmp_path)
    pending = case["registry_root"] / ".registry-claim-v1-crashed.pending"
    pending.write_bytes(b"partial")
    pending.chmod(0o600)
    assert verify(case)["eligible"] is False
    with pytest.raises(writer.PreflightError, match="ineligible"):
        claim(case)


def test_future_dated_claim_is_rejected(tmp_path: Path):
    case = make_case(tmp_path)
    payload = claim(case)
    payload["claimed_at_ns"] = case["now"] + 10_000_000_000
    body = dict(payload)
    body.pop("claim_sha256")
    payload["claim_sha256"] = sha(canonical(body))
    registry_path = case["registry_root"] / "successor-claims-v1.json"
    registry = json.loads(registry_path.read_bytes())
    registry["claims"][payload["predecessor_terminal_sha256"]] = payload
    write_json(registry_path, registry)
    with pytest.raises(verifier.VerificationError, match="claim_time_in_future"):
        verify_claim(case, now_ns=case["now"])
