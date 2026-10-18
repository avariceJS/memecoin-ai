"""Pure hash-chained plan/cursor records for the forward-only Gecko scout."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, cast

from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SCHEMA = "brain-senses-gecko-scout-chain-v1"
GENESIS_SCHEMA = f"{SCHEMA}-genesis"
PLAN_SCHEMA = f"{SCHEMA}-plan"
GAP_SCHEMA = f"{SCHEMA}-gap"
ROOT_PREFIX = "services/gecko-market-v1/capture-only-v2"
ROOT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_GAP_RECORDS_PER_RECOVERY = 17_280


class ChainError(ValueError):
    """Invalid policy, plan chain, cursor, or missed-bucket record."""


def canonical_json(value: Any) -> bytes:
    return scout.canonical_json(value)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fail(condition: bool, reason: str) -> None:
    if not condition:
        raise ChainError(reason)


def _record(core: dict[str, Any]) -> dict[str, Any]:
    return {**core, "record_sha256": sha256(canonical_json(core))}


def _cursor_of(record: Mapping[str, Any]) -> int:
    cursor = record.get("cursor_out", record.get("cursor"))
    _fail(type(cursor) is int and 0 <= cursor < scout.POOL_COUNT, "cursor_invalid")
    return cast(int, cursor)


def _verify_policy(policy: Mapping[str, Any]) -> tuple[list[dict[str, str]], str]:
    _fail(type(policy) is dict, "policy_shape_invalid")
    core = dict(policy)
    claimed = core.pop("policy_sha256", None)
    _fail(claimed == sha256(canonical_json(core)), "policy_hash_invalid")
    _fail(policy.get("schema") == scout.SCHEMA, "policy_schema_invalid")
    _fail(policy.get("policy_id") == scout.POLICY_ID, "policy_id_invalid")
    _fail(policy.get("provider_io") is False, "policy_provider_io_enabled")
    _fail(policy.get("activation_authorized") is False, "policy_activation_enabled")
    _fail(policy.get("market_source_qualified") is False, "policy_source_qualified")
    _fail(policy.get("training_admission") is False, "policy_training_enabled")
    _fail(policy.get("economic_admission") is False, "policy_economic_enabled")
    population = policy.get("population")
    source = policy.get("source")
    _fail(type(source) is dict, "policy_source_invalid")
    source_pin = cast(dict[str, Any], source).get("source_pin")
    _fail(type(source_pin) is str, "policy_source_pin_invalid")
    _fail(type(population) is list, "policy_population_invalid")
    try:
        rows = scout.pool_order(
            cast(Sequence[Mapping[str, Any]], population), cast(str, source_pin)
        )
    except (KeyError, TypeError, scout.SelectionError) as exc:
        raise ChainError("policy_population_invalid") from exc
    _fail(policy.get("population_count") == scout.POOL_COUNT, "policy_population_count_invalid")
    _fail(
        policy.get("population_sha256") == scout.population_digest(rows),
        "policy_population_hash_invalid",
    )
    return rows, cast(str, claimed)


def build_genesis(policy: Mapping[str, Any], first_bucket_close_ns: int) -> dict[str, Any]:
    """Anchor cursor zero immediately before the first scheduled bucket."""
    _, policy_hash = _verify_policy(policy)
    _fail(type(first_bucket_close_ns) is int, "genesis_close_invalid")
    _fail(
        first_bucket_close_ns > 0 and first_bucket_close_ns % scout.BUCKET_NS == 0,
        "genesis_close_invalid",
    )
    return _record(
        {
            "schema": GENESIS_SCHEMA,
            "schema_version": 1,
            "policy_sha256": policy_hash,
            "population_sha256": policy["population_sha256"],
            "first_bucket_close_ns": first_bucket_close_ns,
            "next_bucket_close_ns": first_bucket_close_ns,
            "cursor": 0,
            "sequence": 0,
            "provider_io": False,
        }
    )


def _verify_record_hash(row: Mapping[str, Any]) -> None:
    _fail(type(row) is dict and isinstance(row.get("record_sha256"), str), "record_shape_invalid")
    core = dict(row)
    claimed = core.pop("record_sha256")
    _fail(claimed == sha256(canonical_json(core)), "record_hash_invalid")


def _verify_previous(previous: Mapping[str, Any], policy_hash: str, population_hash: str) -> None:
    _verify_record_hash(previous)
    _fail(
        previous.get("schema") in {GENESIS_SCHEMA, PLAN_SCHEMA, GAP_SCHEMA},
        "previous_record_schema_invalid",
    )
    _fail(type(previous.get("sequence")) is int, "previous_sequence_invalid")
    _fail(
        type(previous.get("next_bucket_close_ns")) is int
        and previous["next_bucket_close_ns"] % scout.BUCKET_NS == 0,
        "previous_close_invalid",
    )
    _fail(previous.get("policy_sha256") == policy_hash, "chain_policy_mismatch")
    _fail(previous.get("population_sha256") == population_hash, "chain_population_mismatch")
    _cursor_of(previous)


def append_plan(
    policy: Mapping[str, Any],
    previous: Mapping[str, Any],
    *,
    bucket_close_ns: int,
    prior_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Create one immutable plan record for exactly the next bucket in the chain."""
    population, policy_hash = _verify_policy(policy)
    _verify_previous(previous, policy_hash, policy["population_sha256"])
    _fail(
        type(bucket_close_ns) is int and bucket_close_ns == previous.get("next_bucket_close_ns"),
        "bucket_not_next",
    )
    cursor = _cursor_of(previous)
    prior_rows: list[dict[str, Any]] = []
    evidence_hashes: list[str] = []
    expected_evidence_close = bucket_close_ns - scout.BUCKET_NS
    for item in prior_evidence:
        _fail(
            type(item) is dict and set(item) == {"evidence", "evidence_sha256"},
            "evidence_record_invalid",
        )
        evidence = item["evidence"]
        _fail(type(evidence) is dict, "evidence_record_invalid")
        digest = sha256(canonical_json(evidence))
        _fail(item["evidence_sha256"] == digest, "evidence_hash_invalid")
        _fail(
            evidence.get("evidence_bucket_close_ns") == expected_evidence_close,
            "evidence_not_immediately_prior",
        )
        prior_rows.append(dict(evidence))
        evidence_hashes.append(digest)
    selected = scout.plan_bucket(
        population,
        source_pin=policy["source"]["source_pin"],
        bucket_close_ns=bucket_close_ns,
        cursor=cursor,
        prior_retention=previous.get(
            "selection", {"retention": previous.get("retention", [])}
        ).get("retention", []),
        prior_evidence=prior_rows,
    )
    return _record(
        {
            "schema": PLAN_SCHEMA,
            "schema_version": 1,
            "sequence": previous["sequence"] + 1,
            "previous_record_sha256": previous["record_sha256"],
            "policy_sha256": policy_hash,
            "population_sha256": policy["population_sha256"],
            "bucket_close_ns": bucket_close_ns,
            "next_bucket_close_ns": bucket_close_ns + scout.BUCKET_NS,
            "cursor_in": cursor,
            "cursor_out": selected["next_cursor"],
            "prior_evidence": [
                {"evidence": row, "evidence_sha256": digest}
                for row, digest in zip(prior_rows, evidence_hashes, strict=True)
            ],
            "prior_evidence_sha256s": evidence_hashes,
            "selection": selected,
            "provider_io": False,
            "retry": False,
            "backfill": False,
        }
    )


def append_missed_bucket_gap(
    policy: Mapping[str, Any], previous: Mapping[str, Any], *, bucket_close_ns: int
) -> dict[str, Any]:
    """Record one elapsed bucket without dispatch; preserve the cursor and never backfill."""
    _, policy_hash = _verify_policy(policy)
    _verify_previous(previous, policy_hash, policy["population_sha256"])
    _fail(
        type(bucket_close_ns) is int and bucket_close_ns == previous.get("next_bucket_close_ns"),
        "bucket_not_next",
    )
    cursor = _cursor_of(previous)
    _fail(
        previous.get("population_sha256") == policy.get("population_sha256"),
        "chain_population_mismatch",
    )
    return _record(
        {
            "schema": GAP_SCHEMA,
            "schema_version": 1,
            "sequence": previous["sequence"] + 1,
            "previous_record_sha256": previous["record_sha256"],
            "policy_sha256": policy_hash,
            "population_sha256": policy["population_sha256"],
            "bucket_close_ns": bucket_close_ns,
            "next_bucket_close_ns": bucket_close_ns + scout.BUCKET_NS,
            "cursor": cursor,
            "retention": previous.get(
                "selection", {"retention": previous.get("retention", [])}
            ).get("retention", []),
            "reason": "missed_window_no_dispatch",
            "provider_io": False,
            "retry": False,
            "backfill": False,
        }
    )


def verify_chain(policy: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Independently replay genesis, explicit gaps, plan selections and cursor transitions."""
    _, policy_hash = _verify_policy(policy)
    _fail(type(records) in {list, tuple} and len(records) >= 1, "chain_empty")
    first_close = (
        records[0].get("first_bucket_close_ns") if isinstance(records[0], Mapping) else None
    )
    _fail(type(first_close) is int, "genesis_close_invalid")
    expected = build_genesis(policy, cast(int, first_close))
    _fail(canonical_json(records[0]) == canonical_json(expected), "genesis_mismatch")
    previous: Mapping[str, Any] = expected
    for row in records[1:]:
        _verify_record_hash(row)
        _fail(row.get("sequence") == previous.get("sequence", 0) + 1, "sequence_gap")
        _fail(row.get("previous_record_sha256") == previous["record_sha256"], "ancestry_gap")
        _fail(row.get("policy_sha256") == policy_hash, "chain_policy_mismatch")
        _fail(
            row.get("population_sha256") == policy["population_sha256"],
            "chain_population_mismatch",
        )
        _fail(
            row.get("bucket_close_ns") == previous.get("next_bucket_close_ns"),
            "bucket_gap_unrecorded",
        )
        if row.get("schema") == GAP_SCHEMA:
            expected_gap = append_missed_bucket_gap(
                policy, previous, bucket_close_ns=row["bucket_close_ns"]
            )
            _fail(canonical_json(row) == canonical_json(expected_gap), "gap_record_mismatch")
        elif row.get("schema") == PLAN_SCHEMA:
            expected_plan = append_plan(
                policy,
                previous,
                bucket_close_ns=row["bucket_close_ns"],
                prior_evidence=row["prior_evidence"],
            )
            _fail(canonical_json(row) == canonical_json(expected_plan), "plan_replay_mismatch")
        else:
            raise ChainError("record_schema_invalid")
        previous = row
    return {
        "status": "verified_chain",
        "record_count": len(records),
        "plan_count": sum(row.get("schema") == PLAN_SCHEMA for row in records),
        "gap_count": sum(row.get("schema") == GAP_SCHEMA for row in records),
        "last_record_sha256": previous["record_sha256"],
        "next_bucket_close_ns": previous["next_bucket_close_ns"],
        "cursor": _cursor_of(previous),
        "provider_io": False,
    }


def recover_existing_plan(
    policy: Mapping[str, Any], records: Sequence[Mapping[str, Any]], *, bucket_close_ns: int
) -> dict[str, Any]:
    """Return only a chain-verified persisted plan; never reconstruct a missing plan on recovery."""
    verify_chain(policy, records)
    _fail(type(bucket_close_ns) is int, "bucket_close_invalid")
    last = records[-1]
    _fail(
        last.get("schema") == PLAN_SCHEMA and last.get("bucket_close_ns") == bucket_close_ns,
        "persisted_plan_missing",
    )
    return dict(last)


class DescriptorScoutJournal:
    """Create-only plan chain rooted at an already-open descriptor-bound data volume.

    This journal records sampling decisions only. It has no provider transport, intent, retry,
    activation, source qualification, training, or economic-admission operation.
    """

    def __init__(
        self,
        writer: DescriptorWriter,
        *,
        root: str,
        policy: Mapping[str, Any],
        storage_binding: Mapping[str, Any],
        release_sha256: str,
    ) -> None:
        _fail(type(root) is str, "journal_root_invalid")
        path = PurePosixPath(root)
        _fail(
            not path.is_absolute()
            and path.as_posix() == root
            and len(path.parts) == 4
            and path.parts[:3] == tuple(ROOT_PREFIX.split("/"))
            and bool(re.fullmatch(r"[a-f0-9]{32}", path.parts[3])),
            "journal_root_invalid",
        )
        _, policy_hash = _verify_policy(policy)
        _fail(type(storage_binding) is dict, "storage_binding_invalid")
        _fail(
            set(storage_binding) == {"root_id", "volume_uuid", "device", "inode"},
            "storage_binding_invalid",
        )
        _fail(
            type(storage_binding["root_id"]) is str
            and ROOT_ID_RE.fullmatch(storage_binding["root_id"]) is not None,
            "storage_binding_invalid",
        )
        _fail(
            type(storage_binding["volume_uuid"]) is str
            and UUID_RE.fullmatch(storage_binding["volume_uuid"]) is not None,
            "storage_binding_invalid",
        )
        _fail(
            type(storage_binding["device"]) is int
            and storage_binding["device"] == writer.device,
            "storage_device_mismatch",
        )
        writer_stat = os.fstat(writer.root_fd)
        _fail(
            type(storage_binding["inode"]) is int
            and storage_binding["inode"] == writer_stat.st_ino,
            "storage_inode_mismatch",
        )
        _fail(
            type(release_sha256) is str and HASH_RE.fullmatch(release_sha256) is not None,
            "release_hash_invalid",
        )
        self.writer = writer
        self.root = root
        self.policy = dict(policy)
        self.policy_sha256 = policy_hash
        self.binding = {
            "schema": f"{SCHEMA}-binding",
            "schema_version": 1,
            "root": root,
            "storage": dict(storage_binding),
            "policy_sha256": policy_hash,
            "population_sha256": policy["population_sha256"],
            "release_sha256": release_sha256,
            "provider_io": False,
            "activation_authorized": False,
        }
        self.binding["binding_sha256"] = sha256(canonical_json(self.binding))

    def _path(self, relative: str) -> str:
        return f"{self.root}/{relative}"

    def initialize(self, *, first_bucket_close_ns: int) -> dict[str, Any]:
        """Idempotently initialize only matching immutable binding/policy/genesis bytes."""
        genesis = build_genesis(self.policy, first_bucket_close_ns)
        self.writer.mkdir(self.root)
        self.writer.mkdir(self._path("chain"))
        allowed = {".runner.lock", "binding.json", "policy.json", "chain"}
        _fail(set(self.writer.list_names(self.root)) <= allowed, "journal_root_not_empty")
        with self.writer.lock(self._path(".runner.lock"), blocking=False):
            self._create_exact("binding.json", self.binding)
            self._create_exact("policy.json", self.policy)
            chain_names = self.writer.list_names(self._path("chain"))
            _fail(set(chain_names) <= {"00000000.json"}, "journal_chain_not_initialized")
            if not chain_names:
                self._create_exact("chain/00000000.json", genesis)
            else:
                existing = self._read_record("00000000.json")
                _fail(
                    canonical_json(existing) == canonical_json(genesis),
                    "journal_genesis_conflict",
                )
        return {
            "status": "initialized",
            "provider_io": False,
            "genesis_sha256": genesis["record_sha256"],
        }

    def _create_exact(self, relative: str, value: Mapping[str, Any]) -> None:
        payload = canonical_json(dict(value))
        path = self._path(relative)
        if self.writer.exists(path):
            _fail(self.writer.read_bytes(path) == payload, "journal_immutable_conflict")
            return
        self.writer.atomic_write(path, payload, create_only=True)

    def _read_record(self, name: str) -> dict[str, Any]:
        raw = self.writer.read_bytes(self._path(f"chain/{name}"), max_bytes=1024 * 1024)
        value = json.loads(raw)
        _fail(type(value) is dict and canonical_json(value) == raw, "journal_record_not_canonical")
        return cast(dict[str, Any], value)

    def _assert_binding(self) -> None:
        _fail(
            self.writer.read_bytes(self._path("binding.json"), max_bytes=8192)
            == canonical_json(self.binding),
            "journal_binding_changed",
        )
        _fail(
            self.writer.read_bytes(self._path("policy.json"), max_bytes=2 * 1024 * 1024)
            == canonical_json(self.policy),
            "journal_policy_changed",
        )

    def records(self) -> list[dict[str, Any]]:
        self._assert_binding()
        names = self.writer.list_names(self._path("chain"))
        expected = [f"{index:08d}.json" for index in range(len(names))]
        _fail(list(names) == expected, "journal_record_inventory_invalid")
        return [self._read_record(name) for name in expected]

    def ensure_plan_for_current_bucket(
        self,
        *,
        now_ns: int,
        bucket_close_ns: int,
        prior_evidence: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Recover the exact plan, mark elapsed windows as gaps, or persist the current plan.

        `now_ns` is an observed callback time. The caller must still perform the independent
        current-clock/deadline check before any request; this method never dispatches or reserves.
        """
        _fail(type(now_ns) is int and now_ns >= 0, "clock_invalid")
        _fail(type(bucket_close_ns) is int, "bucket_close_invalid")
        window_open = bucket_close_ns + scout.DISPATCH_EARLIEST_NS
        latest_plan_ns = bucket_close_ns + scout.REQUEST_START_END_EXCLUSIVE_NS
        with self.writer.lock(self._path(".runner.lock"), blocking=False):
            records = self.records()
            verified = verify_chain(self.policy, records)
            _fail(records[0].get("policy_sha256") == self.policy_sha256, "journal_policy_mismatch")
            last = records[-1]
            if last.get("schema") == PLAN_SCHEMA and last.get("bucket_close_ns") == bucket_close_ns:
                return recover_existing_plan(self.policy, records, bucket_close_ns=bucket_close_ns)
            if last.get("schema") == GAP_SCHEMA and last.get("bucket_close_ns") == bucket_close_ns:
                raise ChainError("current_bucket_already_missed")
            _fail(bucket_close_ns >= records[0]["first_bucket_close_ns"], "bucket_before_genesis")
            _fail(bucket_close_ns % scout.BUCKET_NS == 0, "bucket_close_invalid")
            _fail(
                bucket_close_ns >= verified["next_bucket_close_ns"],
                "bucket_not_next_or_recoverable",
            )
            missed_count = (bucket_close_ns - verified["next_bucket_close_ns"]) // scout.BUCKET_NS
            _fail(missed_count <= MAX_GAP_RECORDS_PER_RECOVERY, "missed_window_gap_cap")
            previous = last
            while previous["next_bucket_close_ns"] < bucket_close_ns:
                gap = append_missed_bucket_gap(
                    self.policy,
                    previous,
                    bucket_close_ns=previous["next_bucket_close_ns"],
                )
                self._persist_record(gap, len(records))
                records.append(gap)
                previous = gap
            if now_ns >= latest_plan_ns:
                gap = append_missed_bucket_gap(
                    self.policy, previous, bucket_close_ns=bucket_close_ns
                )
                self._persist_record(gap, len(records))
                raise ChainError("current_bucket_window_elapsed")
            _fail(now_ns >= window_open, "current_bucket_window_not_open")
            plan = append_plan(
                self.policy,
                previous,
                bucket_close_ns=bucket_close_ns,
                prior_evidence=prior_evidence,
            )
            self._persist_record(plan, len(records))
            return plan

    def _persist_record(self, record: Mapping[str, Any], index: int) -> None:
        current = self.records()
        verify_chain(self.policy, current)
        _fail(index == len(current), "journal_sequence_race")
        candidate = [*current, dict(record)]
        verify_chain(self.policy, candidate)
        self.writer.atomic_write(
            self._path(f"chain/{index:08d}.json"),
            canonical_json(dict(record)),
            create_only=True,
        )


__all__ = [
    "ChainError",
    "GAP_SCHEMA",
    "GENESIS_SCHEMA",
    "PLAN_SCHEMA",
    "append_missed_bucket_gap",
    "append_plan",
    "build_genesis",
    "recover_existing_plan",
    "verify_chain",
]
