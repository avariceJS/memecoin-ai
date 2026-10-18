"""Source-bound cursor continuation for a Gecko successor handoff.

This module is intentionally separate from the frozen v1 chain. It preserves the v1
population and selector but starts its cursor from an independently verified handoff
certificate instead of resetting the rotating selector to zero.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, cast

from tools import brain_senses_gecko_capture_only_v2_handoff_v1 as handoff
from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools import brain_senses_gecko_scout_chain_v1 as frozen_chain
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SCHEMA = "brain-senses-gecko-scout-chain-v2"
CERTIFICATE_SCHEMA = f"{SCHEMA}-predecessor-cutover"
GENESIS_SCHEMA = f"{SCHEMA}-genesis"
PLAN_SCHEMA = f"{SCHEMA}-plan"
GAP_SCHEMA = f"{SCHEMA}-gap"
ROOT_PREFIX = "services/gecko-market-v1/capture-only-v2"
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
ROOT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
MAX_GAP_RECORDS_PER_RECOVERY = 17_280
PLAN_WINDOW_END_NS = 180 * scout.NS


class ChainV2Error(ValueError):
    """Invalid cutover certificate or v2 selection chain."""


def _fail(ok: bool, reason: str) -> None:
    if not ok:
        raise ChainV2Error(reason)


def canonical_json(value: Any) -> bytes:
    return scout.canonical_json(value)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash_obj(value: Any) -> str:
    return sha256(canonical_json(value))


def _record(core: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(core)
    return {**row, "record_sha256": _hash_obj(row)}


def _valid_hash(value: Any) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def _root_id(root: str) -> str:
    path = PurePosixPath(root)
    _fail(
        not path.is_absolute()
        and path.as_posix() == root
        and len(path.parts) == 4
        and path.parts[:3] == tuple(ROOT_PREFIX.split("/"))
        and ROOT_ID_RE.fullmatch(path.parts[3]) is not None
        and root != handoff.OLD_ROOT,
        "v2_root_invalid",
    )
    return path.parts[3]


def _verify_policy(policy: Mapping[str, Any]) -> tuple[list[dict[str, str]], str]:
    try:
        population, policy_sha = (
            scout.pool_order(
                cast(Sequence[Mapping[str, Any]], policy["population"]),
                cast(str, cast(Mapping[str, Any], policy["source"])["source_pin"]),
            ),
            policy.get("policy_sha256"),
        )
        core = dict(policy)
        claimed = core.pop("policy_sha256", None)
    except (KeyError, TypeError, ValueError, scout.SelectionError) as exc:
        raise ChainV2Error("v2_policy_invalid") from exc
    _fail(
        type(policy) is dict and claimed == _hash_obj(core) and policy_sha == claimed,
        "v2_policy_hash",
    )
    _fail(
        policy.get("schema") == scout.SCHEMA
        and policy.get("policy_id") == scout.POLICY_ID
        and policy.get("provider_io") is False
        and policy.get("activation_authorized") is False
        and policy.get("market_source_qualified") is False
        and policy.get("training_admission") is False
        and policy.get("economic_admission") is False
        and policy.get("population_count") == scout.POOL_COUNT
        and policy.get("population_sha256") == scout.population_digest(population),
        "v2_policy_semantics",
    )
    return population, cast(str, claimed)


def build_cutover_certificate(
    *,
    handoff_raw: bytes,
    successor_root: str,
    expected_handoff_genesis: Mapping[str, Any],
    predecessor_attempts: Sequence[Mapping[str, Any]],
    predecessor_slots: Sequence[Mapping[str, Any]],
    predecessor_chain_records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    predecessor_file_prefix_sha256: str,
    predecessor_last_plan_sha256: str,
    coordinator_prefix_sha256: str,
    coordinator_usage_sha256: str,
    preinit_census_sha256: str,
    preinit_snapshot_set_sha256: str,
    observed_now_ns: int,
) -> dict[str, Any]:
    """Compile the immutable source certificate from independently replayed inputs."""
    _root_id(successor_root)
    population, policy_sha = _verify_policy(policy)
    try:
        predecessor_chain = frozen_chain.verify_chain(policy, predecessor_chain_records)
    except (KeyError, TypeError, ValueError, frozen_chain.ChainError) as exc:
        raise ChainV2Error("v2_predecessor_chain_invalid") from exc
    last_plans = [
        row for row in predecessor_chain_records if row.get("schema") == frozen_chain.PLAN_SCHEMA
    ]
    _fail(bool(last_plans), "v2_predecessor_plan_missing")
    last_plan = last_plans[-1]
    _fail(
        last_plan.get("record_sha256") == predecessor_chain_records[-1].get("record_sha256")
        and last_plan.get("cursor_out") == 12
        and predecessor_chain.get("last_record_sha256") == last_plan.get("record_sha256"),
        "v2_predecessor_last_plan_invalid",
    )
    replay = handoff.verify_journal(
        handoff_raw,
        successor_root=successor_root,
        expected_genesis=expected_handoff_genesis,
        predecessor_attempts=predecessor_attempts,
        predecessor_slots=predecessor_slots,
        observed_now_ns=observed_now_ns,
    )
    pins = {
        "predecessor_file_prefix_sha256": predecessor_file_prefix_sha256,
        "predecessor_last_plan_sha256": predecessor_last_plan_sha256,
        "coordinator_prefix_sha256": coordinator_prefix_sha256,
        "coordinator_usage_sha256": coordinator_usage_sha256,
        "preinit_census_sha256": preinit_census_sha256,
        "preinit_snapshot_set_sha256": preinit_snapshot_set_sha256,
    }
    _fail(all(_valid_hash(value) for value in pins.values()), "v2_cutover_pin_shape")
    genesis = expected_handoff_genesis
    _fail(
        pins["predecessor_file_prefix_sha256"] == genesis.get("predecessor_file_prefix_sha256")
        and pins["coordinator_prefix_sha256"] == genesis.get("coordinator_prefix_sha256")
        and pins["coordinator_usage_sha256"] == genesis.get("coordinator_usage_sha256")
        and pins["preinit_census_sha256"] == genesis.get("preinit_census_sha256")
        and pins["preinit_snapshot_set_sha256"] == genesis.get("preinit_snapshot_set_sha256"),
        "v2_cutover_handoff_pin_mismatch",
    )
    _fail(
        predecessor_last_plan_sha256 == last_plan.get("record_sha256")
        and genesis.get("predecessor_chain_tail_sha256") == predecessor_chain["last_record_sha256"],
        "v2_cutover_predecessor_chain_binding",
    )
    storage = dict(cast(Mapping[str, Any], genesis["storage"]))
    core = {
        "schema": CERTIFICATE_SCHEMA,
        "schema_version": 1,
        "successor_root": successor_root,
        "successor_release_id": _root_id(successor_root),
        "policy_sha256": policy_sha,
        "population_sha256": policy["population_sha256"],
        "storage": storage,
        "handoff_journal_sha256": sha256(handoff_raw),
        "handoff_tail_sha256": replay["handoff_tail_sha256"],
        "partial_cutover_sha256": replay["partial_cutover_sha256"],
        "handoff_gap_sha256s": replay["unplanned_gap_record_sha256s"],
        "handoff_gap_count": replay["unplanned_gap_count"],
        **pins,
        "predecessor_chain_tail_sha256": genesis["predecessor_chain_tail_sha256"],
        "predecessor_attempt_tail_sha256": genesis["predecessor_attempt_tail_sha256"],
        "coordinator_usage_totals_sha256": pins["coordinator_usage_sha256"],
        "cursor_in": 6,
        "cursor_out": 12,
        "retention": [],
        "provider_io": False,
        "dispatch_authorized": False,
    }
    return {**core, "certificate_sha256": _hash_obj(core)}


def verify_cutover_certificate(certificate: Mapping[str, Any], **inputs: Any) -> dict[str, Any]:
    expected = build_cutover_certificate(**inputs)
    _fail(
        type(certificate) is dict and canonical_json(dict(certificate)) == canonical_json(expected),
        "v2_cutover_certificate_mismatch",
    )
    return expected


def build_genesis(policy: Mapping[str, Any], certificate: Mapping[str, Any]) -> dict[str, Any]:
    _, policy_sha = _verify_policy(policy)
    _fail(certificate.get("schema") == CERTIFICATE_SCHEMA, "v2_cutover_certificate_schema")
    cert_core = dict(certificate)
    cert_sha = cert_core.pop("certificate_sha256", None)
    _fail(_valid_hash(cert_sha) and cert_sha == _hash_obj(cert_core), "v2_cutover_certificate_hash")
    _fail(
        certificate.get("policy_sha256") == policy_sha
        and certificate.get("population_sha256") == policy.get("population_sha256")
        and type(certificate.get("cursor_out")) is int
        and certificate.get("cursor_out") == 12
        and 12 < scout.POOL_COUNT
        and certificate.get("retention") == [],
        "v2_cutover_policy_or_cursor",
    )
    first_close = handoff.UNPLANNED_GAP_CLOSE_NS
    core = {
        "schema": GENESIS_SCHEMA,
        "schema_version": 2,
        "policy_sha256": policy_sha,
        "population_sha256": policy["population_sha256"],
        "cutover_certificate_sha256": cert_sha,
        "handoff_tail_sha256": certificate["handoff_tail_sha256"],
        "first_bucket_close_ns": first_close,
        "next_bucket_close_ns": first_close,
        "cursor": 12,
        "retention": [],
        "plan_count": 0,
        "sequence": 0,
        "provider_io": False,
    }
    return _record(core)


def _cursor(record: Mapping[str, Any]) -> int:
    value = record.get("cursor_out", record.get("cursor"))
    _fail(type(value) is int and 0 <= value < scout.POOL_COUNT, "v2_cursor_invalid")
    return cast(int, value)


def _verify_prev(previous: Mapping[str, Any], policy_sha: str, population_sha: str) -> None:
    core = dict(previous)
    claimed = core.pop("record_sha256", None)
    _fail(_valid_hash(claimed) and claimed == _hash_obj(core), "v2_previous_hash")
    _fail(
        previous.get("schema") in {GENESIS_SCHEMA, GAP_SCHEMA, PLAN_SCHEMA}
        and previous.get("policy_sha256") == policy_sha
        and previous.get("population_sha256") == population_sha
        and type(previous.get("next_bucket_close_ns")) is int
        and previous["next_bucket_close_ns"] % scout.BUCKET_NS == 0,
        "v2_previous_shape",
    )
    _cursor(previous)
    _fail(
        type(previous.get("plan_count")) is int and previous["plan_count"] >= 0,
        "v2_plan_count",
    )


def append_gap(
    policy: Mapping[str, Any], previous: Mapping[str, Any], *, bucket_close_ns: int
) -> dict[str, Any]:
    _, policy_sha = _verify_policy(policy)
    _verify_prev(previous, policy_sha, cast(str, policy["population_sha256"]))
    _fail(bucket_close_ns == previous.get("next_bucket_close_ns"), "v2_gap_not_next")
    retention = previous.get("retention", [])
    core = {
        "schema": GAP_SCHEMA,
        "schema_version": 2,
        "sequence": previous["sequence"] + 1,
        "previous_record_sha256": previous["record_sha256"],
        "policy_sha256": policy_sha,
        "population_sha256": policy["population_sha256"],
        "bucket_close_ns": bucket_close_ns,
        "next_bucket_close_ns": bucket_close_ns + scout.BUCKET_NS,
        "cursor": _cursor(previous),
        "retention": retention,
        "plan_count": previous["plan_count"],
        "reason": "missed_window_no_dispatch",
        "provider_io": False,
        "retry": False,
        "backfill": False,
    }
    return _record(core)


def append_plan(
    policy: Mapping[str, Any],
    previous: Mapping[str, Any],
    *,
    bucket_close_ns: int,
    prior_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    population, policy_sha = _verify_policy(policy)
    _verify_prev(previous, policy_sha, cast(str, policy["population_sha256"]))
    _fail(bucket_close_ns == previous.get("next_bucket_close_ns"), "v2_plan_not_next")
    _fail(previous.get("schema") != GENESIS_SCHEMA, "v2_initial_gap_required")
    if previous["plan_count"] == 0:
        _fail(
            not prior_evidence and previous.get("retention") == [],
            "v2_cutover_prior_state_forbidden",
        )
    cursor = _cursor(previous)
    prior_rows: list[dict[str, Any]] = []
    hashes: list[str] = []
    for item in prior_evidence:
        _fail(
            type(item) is dict and set(item) == {"evidence", "evidence_sha256"}, "v2_evidence_shape"
        )
        evidence = item["evidence"]
        _fail(
            type(evidence) is dict and item["evidence_sha256"] == _hash_obj(evidence),
            "v2_evidence_hash",
        )
        _fail(
            evidence.get("evidence_bucket_close_ns") == bucket_close_ns - scout.BUCKET_NS,
            "v2_evidence_not_prior",
        )
        prior_rows.append(dict(evidence))
        hashes.append(cast(str, item["evidence_sha256"]))
    selection = scout.plan_bucket(
        population,
        source_pin=cast(str, cast(Mapping[str, Any], policy["source"])["source_pin"]),
        bucket_close_ns=bucket_close_ns,
        cursor=cursor,
        prior_retention=cast(Sequence[Mapping[str, Any]], previous.get("retention", [])),
        prior_evidence=prior_rows,
    )
    core = {
        "schema": PLAN_SCHEMA,
        "schema_version": 2,
        "sequence": previous["sequence"] + 1,
        "previous_record_sha256": previous["record_sha256"],
        "policy_sha256": policy_sha,
        "population_sha256": policy["population_sha256"],
        "bucket_close_ns": bucket_close_ns,
        "next_bucket_close_ns": bucket_close_ns + scout.BUCKET_NS,
        "cursor_in": cursor,
        "cursor_out": selection["next_cursor"],
        "plan_count": previous["plan_count"] + 1,
        "prior_evidence": [
            {"evidence": row, "evidence_sha256": digest}
            for row, digest in zip(prior_rows, hashes, strict=True)
        ],
        "prior_evidence_sha256s": hashes,
        "selection": selection,
        "provider_io": False,
        "retry": False,
        "backfill": False,
    }
    return _record(core)


def verify_chain(
    policy: Mapping[str, Any],
    certificate: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    certificate_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    _, policy_sha = _verify_policy(policy)
    _fail(type(records) in {list, tuple} and bool(records), "v2_chain_empty")
    verify_cutover_certificate(certificate, **dict(certificate_inputs))
    expected_genesis = build_genesis(policy, certificate)
    _fail(
        canonical_json(dict(records[0])) == canonical_json(expected_genesis), "v2_genesis_mismatch"
    )
    previous: Mapping[str, Any] = expected_genesis
    for row in records[1:]:
        _verify_prev(row, policy_sha, cast(str, policy["population_sha256"]))
        _fail(
            row.get("sequence") == previous.get("sequence", 0) + 1
            and row.get("previous_record_sha256") == previous.get("record_sha256")
            and row.get("bucket_close_ns") == previous.get("next_bucket_close_ns"),
            "v2_chain_ancestry",
        )
        if row.get("schema") == GAP_SCHEMA:
            expected = append_gap(
                policy, previous, bucket_close_ns=cast(int, row["bucket_close_ns"])
            )
        elif row.get("schema") == PLAN_SCHEMA:
            expected = append_plan(
                policy,
                previous,
                bucket_close_ns=cast(int, row["bucket_close_ns"]),
                prior_evidence=cast(Sequence[Mapping[str, Any]], row["prior_evidence"]),
            )
        else:
            raise ChainV2Error("v2_chain_record_schema")
        _fail(canonical_json(dict(row)) == canonical_json(expected), "v2_chain_record_replay")
        previous = row
    return {
        "status": "verified_chain_v2",
        "record_count": len(records),
        "plan_count": sum(row.get("schema") == PLAN_SCHEMA for row in records),
        "gap_count": sum(row.get("schema") == GAP_SCHEMA for row in records),
        "last_record_sha256": previous["record_sha256"],
        "next_bucket_close_ns": previous["next_bucket_close_ns"],
        "cursor": _cursor(previous),
        "cutover_certificate_sha256": certificate["certificate_sha256"],
        "provider_io": False,
        "dispatch_authorized": False,
    }


class DescriptorScoutJournalV2:
    """Create-only descriptor journal rooted at a verified handoff certificate."""

    def __init__(
        self,
        writer: DescriptorWriter,
        *,
        root: str,
        policy: Mapping[str, Any],
        certificate: Mapping[str, Any],
        certificate_inputs: Mapping[str, Any],
    ) -> None:
        _root_id(root)
        population, policy_sha = _verify_policy(policy)
        del population
        _fail(certificate.get("schema") == CERTIFICATE_SCHEMA, "v2_certificate_schema")
        _fail(certificate.get("policy_sha256") == policy_sha, "v2_certificate_policy")
        _fail(certificate.get("successor_root") == root, "v2_certificate_root")
        _fail(type(certificate_inputs) is dict, "v2_certificate_inputs")
        verify_cutover_certificate(certificate, **dict(certificate_inputs))
        _fail(
            type(certificate.get("storage")) is dict
            and certificate["storage"].get("device") == writer.device
            and certificate["storage"].get("inode") == os.fstat(writer.root_fd).st_ino,
            "v2_certificate_storage",
        )
        self.writer = writer
        self.root = root
        self.policy = dict(policy)
        self.certificate = dict(certificate)
        self.certificate_inputs = dict(certificate_inputs)
        self.genesis = build_genesis(self.policy, self.certificate)
        binding_core = {
            "schema": f"{SCHEMA}-binding",
            "schema_version": 2,
            "root": root,
            "storage": dict(certificate["storage"]),
            "policy_sha256": policy_sha,
            "population_sha256": policy["population_sha256"],
            "cutover_certificate_sha256": certificate["certificate_sha256"],
            "provider_io": False,
            "dispatch_authorized": False,
        }
        self.binding = {**binding_core, "binding_sha256": _hash_obj(binding_core)}

    def _path(self, suffix: str) -> str:
        return f"{self.root}/{suffix}"

    def initialize(self) -> dict[str, Any]:
        verify_cutover_certificate(self.certificate, **self.certificate_inputs)
        _fail(
            self.certificate["storage"].get("device") == self.writer.device
            and self.certificate["storage"].get("inode") == os.fstat(self.writer.root_fd).st_ino,
            "v2_certificate_storage_changed",
        )
        self.writer.mkdir(self.root)
        self.writer.mkdir(self._path("chain"))
        with self.writer.lock(self._path(".runner.lock"), blocking=False):
            _fail(
                set(self.writer.list_names(self.root))
                <= {
                    ".runner.lock",
                    "binding.json",
                    "policy.json",
                    "predecessor-cutover.json",
                    "chain",
                },
                "v2_root_extra_entries",
            )
            self._create_exact("binding.json", self.binding)
            self._create_exact("policy.json", self.policy)
            self._create_exact("predecessor-cutover.json", self.certificate)
            names = self.writer.list_names(self._path("chain"))
            _fail(set(names) <= {"00000000.json"}, "v2_chain_not_initialized")
            if not names:
                self._write_record(self.genesis, 0)
            else:
                _fail(
                    canonical_json(self._read("chain/00000000.json"))
                    == canonical_json(self.genesis),
                    "v2_genesis_conflict",
                )
        return {
            "status": "initialized",
            "genesis_sha256": self.genesis["record_sha256"],
            "provider_io": False,
        }

    def _create_exact(self, suffix: str, value: Mapping[str, Any]) -> None:
        payload = canonical_json(dict(value))
        path = self._path(suffix)
        if self.writer.exists(path):
            _fail(self.writer.read_bytes(path) == payload, "v2_immutable_conflict")
            return
        self.writer.atomic_write(path, payload, create_only=True)

    def _read(self, suffix: str) -> dict[str, Any]:
        raw = self.writer.read_bytes(self._path(suffix), max_bytes=2 * 1024 * 1024)
        value = json.loads(raw)
        _fail(type(value) is dict and canonical_json(value) == raw, "v2_record_not_canonical")
        return cast(dict[str, Any], value)

    def records(self) -> list[dict[str, Any]]:
        _fail(self._read("binding.json") == self.binding, "v2_binding_changed")
        _fail(self._read("policy.json") == self.policy, "v2_policy_changed")
        _fail(self._read("predecessor-cutover.json") == self.certificate, "v2_certificate_changed")
        verify_cutover_certificate(self.certificate, **self.certificate_inputs)
        names = self.writer.list_names(self._path("chain"))
        expected = tuple(f"{index:08d}.json" for index in range(len(names)))
        _fail(names == expected, "v2_chain_file_closure")
        rows = [self._read(f"chain/{name}") for name in expected]
        verify_chain(
            self.policy,
            self.certificate,
            rows,
            certificate_inputs=self.certificate_inputs,
        )
        return rows

    def _write_record(self, record: Mapping[str, Any], index: int) -> None:
        current = self.records() if index else []
        if index:
            _fail(index == len(current), "v2_chain_race")
        self.writer.atomic_write(
            self._path(f"chain/{index:08d}.json"), canonical_json(dict(record)), create_only=True
        )

    def ensure_plan_for_current_bucket(
        self,
        *,
        now_ns: int,
        bucket_close_ns: int,
        prior_evidence: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        _fail(type(now_ns) is int and now_ns >= 0, "v2_clock_invalid")
        _fail(
            type(bucket_close_ns) is int and bucket_close_ns % scout.BUCKET_NS == 0,
            "v2_bucket_invalid",
        )
        with self.writer.lock(self._path(".runner.lock"), blocking=False):
            records = self.records()
            state = verify_chain(
                self.policy,
                self.certificate,
                records,
                certificate_inputs=self.certificate_inputs,
            )
            last = records[-1]
            if last.get("schema") == PLAN_SCHEMA and last.get("bucket_close_ns") == bucket_close_ns:
                return dict(last)
            _fail(bucket_close_ns >= state["next_bucket_close_ns"], "v2_bucket_not_next")
            missed = (bucket_close_ns - state["next_bucket_close_ns"]) // scout.BUCKET_NS
            _fail(missed <= MAX_GAP_RECORDS_PER_RECOVERY, "v2_gap_cap")
            previous: Mapping[str, Any] = last
            while previous["next_bucket_close_ns"] < bucket_close_ns:
                gap = append_gap(
                    self.policy, previous, bucket_close_ns=previous["next_bucket_close_ns"]
                )
                self._write_record(gap, len(records))
                records.append(gap)
                previous = gap
            if now_ns >= bucket_close_ns + PLAN_WINDOW_END_NS:
                gap = append_gap(self.policy, previous, bucket_close_ns=bucket_close_ns)
                self._write_record(gap, len(records))
                raise ChainV2Error("v2_bucket_elapsed")
            _fail(
                now_ns >= bucket_close_ns + scout.DISPATCH_EARLIEST_NS,
                "v2_window_not_open",
            )
            plan = append_plan(
                self.policy,
                previous,
                bucket_close_ns=bucket_close_ns,
                prior_evidence=prior_evidence,
            )
            self._write_record(plan, len(records))
            verify_chain(
                self.policy,
                self.certificate,
                [*records, plan],
                certificate_inputs=self.certificate_inputs,
            )
            return plan


__all__ = [
    "CERTIFICATE_SCHEMA",
    "ChainV2Error",
    "DescriptorScoutJournalV2",
    "GAP_SCHEMA",
    "GENESIS_SCHEMA",
    "PLAN_SCHEMA",
    "SCHEMA",
    "append_gap",
    "append_plan",
    "build_cutover_certificate",
    "build_genesis",
    "verify_chain",
    "verify_cutover_certificate",
]
