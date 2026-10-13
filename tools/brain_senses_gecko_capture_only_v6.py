"""Descriptor-only local journal and budget kernel for the Gecko capture-only v6 epoch.

This module has no HTTP client, credential reader, launchctl call, coordinator-v2 import,
or root-creation-on-import behavior. A caller must supply a source-pinned selector and
an already verified storage guard. The tests use only disposable temporary roots.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, cast

from tools import brain_senses_gecko_lane_policy_v6 as policy_v6
from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json
from tools.brain_senses_gecko_lane_engine_v3 import parse_response

ROOT_PREFIX = "services/gecko-market-v1/capture-only-v6"
EPOCH_SCHEMA = "brain-senses-gecko-capture-epoch-v6"
GENESIS_SCHEMA = "brain-senses-gecko-capture-genesis-v6"
PLAN_SCHEMA = "brain-senses-gecko-capture-plan-v6"
EVENT_SCHEMA = "brain-senses-gecko-capture-event-v6"
EVENTS_PATH = "events.jsonl"
GENESIS_PATH = "genesis.json"
EPOCH_PATH = "epoch.json"
POLICY_PATH = "policy.json"
PLAN_DIRECTORY = "plans"
REQUEST_DIRECTORY = "requests"
INTENT_DIRECTORY = "intents"
RAW_DIRECTORY = "responses"
CONFIRMATION_DIRECTORY = "confirmations"
NS = policy_v6.NS
ROLLING_WINDOW_NS = 30 * 24 * 60 * 60 * NS
DAY_NS = 24 * 60 * 60 * NS
ROOT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
QUIESCENCE_SCHEMA = "brain-senses-gecko-transport-quiescence-v6"
TRANSPORT_WORKER_SCHEMA = "brain-senses-gecko-v6-transport-worker"
GLOBAL_LANE_LOCK_PATH = "control/.gecko-capture-v6-global.lock"


class CaptureV6Error(ValueError):
    """Malformed source binding, local journal, budget, or descriptor transaction."""


class CrossReleaseLaneLeaseV6:
    """One fixed descriptor-bound lock shared by all v6 capture releases."""

    def __init__(self, data_root_writer: DescriptorWriter) -> None:
        self.writer = data_root_writer
        self._owner_thread: int | None = None

    @contextlib.contextmanager
    def hold(self) -> Iterator[None]:
        if self._owner_thread is not None:
            raise CaptureV6Error("v6_global_lane_lease_reentrant")
        with self.writer.lock(GLOBAL_LANE_LOCK_PATH, blocking=False):
            self._owner_thread = threading.get_ident()
            try:
                yield
            finally:
                self._owner_thread = None

    def held_by_current_thread(self) -> bool:
        return self._owner_thread == threading.get_ident()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(value: Any) -> bool:
    return type(value) is str and SHA_RE.fullmatch(value) is not None


def _object(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureV6Error(reason) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise CaptureV6Error(reason)
    return value


def _prior_usage_rows(raw: bytes) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureV6Error("v6_prior_usage_json") from exc
    if (
        type(value) is not list
        or canonical_json(value) != raw
        or any(type(row) is not dict for row in value)
    ):
        raise CaptureV6Error("v6_prior_usage_shape")
    for row in value:
        if (
            set(row) != {"intent_ns", "raw_bytes_upper_bound", "physical_bytes_upper_bound"}
            or type(row.get("intent_ns")) is not int
            or row["intent_ns"] <= 0
            or type(row.get("raw_bytes_upper_bound")) is not int
            or row["raw_bytes_upper_bound"] < 0
            or type(row.get("physical_bytes_upper_bound")) is not int
            or row["physical_bytes_upper_bound"] < 0
        ):
            raise CaptureV6Error("v6_prior_usage_row_invalid")
    return value


def _load_pinned_prior_usage(
    writer: DescriptorWriter, epoch: Mapping[str, Any]
) -> tuple[bytes, list[dict[str, Any]]]:
    binding = epoch.get("prior_usage")
    if type(binding) is not dict or set(binding) != {"path", "sha256"}:
        raise CaptureV6Error("v6_prior_usage_binding")
    try:
        raw = writer.read_bytes(binding["path"], max_bytes=4 * 1024 * 1024)
    except Exception as exc:
        raise CaptureV6Error("v6_prior_usage_missing") from exc
    if sha256(raw) != binding["sha256"]:
        raise CaptureV6Error("v6_prior_usage_hash")
    return raw, _prior_usage_rows(raw)


def _verify_quiescence_proof(
    proof: Any, *, request_id: str, dispatch: Mapping[str, Any]
) -> dict[str, Any]:
    required = {
        "schema",
        "request_id",
        "child_pid",
        "process_group_id",
        "child_birth_id",
        "attempt_nonce",
        "worker_source_sha256",
        "child_executable_path",
        "child_uid",
        "absence_verified",
        "pid_reuse_observed",
        "observed_birth_id",
        "process_group_empty",
        "descendant_count",
        "request_started_ns",
        "request_started_monotonic_ns",
        "quiesced_ns",
        "quiesced_monotonic_ns",
        "clock_pair_verified",
    }
    if (
        type(proof) is not dict
        or set(proof) != required
        or proof.get("schema") != QUIESCENCE_SCHEMA
        or proof.get("request_id") != request_id
        or type(proof.get("child_pid")) is not int
        or proof["child_pid"] <= 0
        or proof.get("child_pid") != dispatch.get("worker_pid")
        or proof.get("process_group_id") != dispatch.get("worker_pgid")
        or proof.get("child_birth_id") != dispatch.get("worker_birth_id")
        or proof.get("attempt_nonce") != dispatch.get("attempt_nonce")
        or proof.get("worker_source_sha256") != dispatch.get("worker_source_sha256")
        or proof.get("child_executable_path") != dispatch.get("worker_executable_path")
        or proof.get("child_uid") != dispatch.get("worker_uid")
        or proof.get("absence_verified") is not True
        or type(proof.get("pid_reuse_observed")) is not bool
        or (
            proof.get("pid_reuse_observed") is True
            and (
                type(proof.get("observed_birth_id")) is not str
                or re.fullmatch(r"[0-9]+\.[0-9]{6}", proof["observed_birth_id"]) is None
                or proof["observed_birth_id"] == proof.get("child_birth_id")
            )
        )
        or (proof.get("pid_reuse_observed") is False and proof.get("observed_birth_id") is not None)
        or proof.get("process_group_empty") is not True
        or proof.get("descendant_count") != 0
        or proof.get("clock_pair_verified") is not True
        or proof.get("request_started_ns") != dispatch.get("request_started_ns")
        or proof.get("request_started_monotonic_ns") != dispatch.get("monotonic_started_ns")
        or type(proof.get("quiesced_ns")) is not int
        or proof["quiesced_ns"] < proof["request_started_ns"]
        or type(proof.get("quiesced_monotonic_ns")) is not int
        or proof["quiesced_monotonic_ns"] < proof["request_started_monotonic_ns"]
        or abs(
            (proof["quiesced_ns"] - proof["request_started_ns"])
            - (proof["quiesced_monotonic_ns"] - proof["request_started_monotonic_ns"])
        )
        > 2_000_000_000
    ):
        raise CaptureV6Error("v6_transport_quiescence_unproven")
    return dict(proof)


def _safe_release_id(value: Any) -> bool:
    return type(value) is str and ROOT_ID_RE.fullmatch(value) is not None


def _storage_shape(storage: Any) -> bool:
    data_root = storage.get("data_root") if type(storage) is dict else None
    service_root = storage.get("service_root") if type(storage) is dict else None
    return (
        type(storage) is dict
        and set(storage) == {"root_id", "volume_uuid", "data_root", "service_root"}
        and _safe_release_id(storage.get("root_id"))
        and type(storage.get("volume_uuid")) is str
        and UUID_RE.fullmatch(cast(str, storage.get("volume_uuid")).lower()) is not None
        and type(data_root) is dict
        and set(data_root) == {"device", "inode"}
        and type(data_root["device"]) is int
        and data_root["device"] >= 0
        and type(data_root["inode"]) is int
        and data_root["inode"] > 0
        and type(service_root) is dict
        and set(service_root) == {"device", "inode"}
        and type(service_root["device"]) is int
        and service_root["device"] == data_root["device"]
        and type(service_root["inode"]) is int
        and service_root["inode"] > 0
        and service_root["inode"] != data_root["inode"]
    )


def epoch_document(
    *,
    release_id: str,
    anchor_close_ns: int,
    policy_sha256: str,
    selector_sha256: str,
    population_sha256: str,
    storage: Mapping[str, Any],
    prior_usage_path: str,
    prior_usage_sha256: str,
    prior_usage_census: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile the explicit new sampling epoch; cursor reset is part of its identity."""
    if (
        not _safe_release_id(release_id)
        or type(anchor_close_ns) is not int
        or anchor_close_ns <= 0
        or anchor_close_ns % policy_v6.BUCKET_NS
        or not _sha(policy_sha256)
        or not _sha(selector_sha256)
        or population_sha256 != policy_v6.POPULATION_SHA256
        or not _storage_shape(dict(storage))
        or not prior_usage_path.startswith("control/gecko-v6-prior-usage/")
        or not _sha(prior_usage_sha256)
        or type(prior_usage_census) is not dict
        or set(prior_usage_census)
        != {
            "receipt_path",
            "receipt_sha256",
            "root_inventory_sha256",
            "attempt_count",
            "observed_ns",
        }
        or type(prior_usage_census.get("receipt_path")) is not str
        or not prior_usage_census["receipt_path"].startswith("docs/evidence/")
        or not _sha(prior_usage_census.get("receipt_sha256"))
        or not _sha(prior_usage_census.get("root_inventory_sha256"))
        or type(prior_usage_census.get("attempt_count")) is not int
        or prior_usage_census["attempt_count"] < 0
        or type(prior_usage_census.get("observed_ns")) is not int
        or prior_usage_census["observed_ns"] <= 0
        or prior_usage_census["observed_ns"] >= anchor_close_ns
    ):
        raise CaptureV6Error("v6_epoch_input_invalid")
    core = {
        "schema": EPOCH_SCHEMA,
        "schema_version": 6,
        "release_id": release_id,
        "root": f"{ROOT_PREFIX}/{release_id}",
        "first_bucket_close_ns": anchor_close_ns,
        "policy_id": policy_v6.POLICY_ID,
        "policy_sha256": policy_sha256,
        "selector_policy_id": scout.POLICY_ID,
        "selector_sha256": selector_sha256,
        "source_pin": policy_v6.SOURCE_PIN,
        "population_sha256": population_sha256,
        "cursor_in": 0,
        "retention_in": [],
        "predecessor_cursor_disposition": "not_carried_new_epoch",
        "reset_reason": "explicit_new_v6_sampling_epoch",
        "prior_usage": {"path": prior_usage_path, "sha256": prior_usage_sha256},
        "prior_usage_census": dict(prior_usage_census),
        "storage": dict(storage),
        "provider_io": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return {**core, "epoch_sha256": sha256(canonical_json(core))}


def verify_epoch(epoch: dict[str, Any], *, now_ns: int | None = None) -> None:
    expected_keys = {
        "schema",
        "schema_version",
        "release_id",
        "root",
        "first_bucket_close_ns",
        "policy_id",
        "policy_sha256",
        "selector_policy_id",
        "selector_sha256",
        "source_pin",
        "population_sha256",
        "cursor_in",
        "retention_in",
        "predecessor_cursor_disposition",
        "reset_reason",
        "prior_usage",
        "prior_usage_census",
        "storage",
        "provider_io",
        "market_source_qualified",
        "training_admission",
        "economic_admission",
        "epoch_sha256",
    }
    if (
        type(epoch) is not dict
        or set(epoch) != expected_keys
        or epoch.get("schema") != EPOCH_SCHEMA
        or epoch.get("schema_version") != 6
        or not _safe_release_id(epoch.get("release_id"))
        or epoch.get("root") != f"{ROOT_PREFIX}/{epoch.get('release_id')}"
        or type(epoch.get("first_bucket_close_ns")) is not int
        or epoch["first_bucket_close_ns"] <= 0
        or epoch["first_bucket_close_ns"] % policy_v6.BUCKET_NS
        or epoch.get("policy_id") != policy_v6.POLICY_ID
        or not _sha(epoch.get("policy_sha256"))
        or epoch.get("selector_policy_id") != scout.POLICY_ID
        or not _sha(epoch.get("selector_sha256"))
        or epoch.get("source_pin") != policy_v6.SOURCE_PIN
        or epoch.get("population_sha256") != policy_v6.POPULATION_SHA256
        or epoch.get("cursor_in") != 0
        or epoch.get("retention_in") != []
        or epoch.get("predecessor_cursor_disposition") != "not_carried_new_epoch"
        or epoch.get("reset_reason") != "explicit_new_v6_sampling_epoch"
        or type(epoch.get("prior_usage")) is not dict
        or set(epoch["prior_usage"]) != {"path", "sha256"}
        or type(epoch["prior_usage"].get("path")) is not str
        or not epoch["prior_usage"]["path"].startswith("control/gecko-v6-prior-usage/")
        or any(part in {"", ".", ".."} for part in epoch["prior_usage"]["path"].split("/"))
        or not _sha(epoch["prior_usage"].get("sha256"))
        or type(epoch.get("prior_usage_census")) is not dict
        or set(epoch["prior_usage_census"])
        != {
            "receipt_path",
            "receipt_sha256",
            "root_inventory_sha256",
            "attempt_count",
            "observed_ns",
        }
        or type(epoch["prior_usage_census"].get("receipt_path")) is not str
        or not epoch["prior_usage_census"]["receipt_path"].startswith("docs/evidence/")
        or not _sha(epoch["prior_usage_census"].get("receipt_sha256"))
        or not _sha(epoch["prior_usage_census"].get("root_inventory_sha256"))
        or type(epoch["prior_usage_census"].get("attempt_count")) is not int
        or epoch["prior_usage_census"]["attempt_count"] < 0
        or type(epoch["prior_usage_census"].get("observed_ns")) is not int
        or epoch["prior_usage_census"]["observed_ns"] >= epoch["first_bucket_close_ns"]
        or not _storage_shape(epoch.get("storage"))
        or any(
            epoch.get(key) is not False
            for key in (
                "provider_io",
                "market_source_qualified",
                "training_admission",
                "economic_admission",
            )
        )
    ):
        raise CaptureV6Error("v6_epoch_shape")
    core = dict(epoch)
    claimed = core.pop("epoch_sha256")
    if claimed != sha256(canonical_json(core)):
        raise CaptureV6Error("v6_epoch_hash")
    if now_ns is not None and (type(now_ns) is not int or epoch["first_bucket_close_ns"] <= now_ns):
        raise CaptureV6Error("v6_epoch_anchor_not_future")


def build_plan(
    policy: dict[str, Any],
    *,
    close_ns: int,
    cursor: int,
    prior_retention: Sequence[Mapping[str, Any]],
    prior_evidence: Sequence[Mapping[str, Any]],
    selector_sha256: str,
    expected_selector_sha256: str,
) -> dict[str, Any]:
    """Invoke only the source-pinned pure selector; no response can replace a slot."""
    policy_v6.verify_policy_hash(policy)
    if selector_sha256 != expected_selector_sha256 or not _sha(selector_sha256):
        raise CaptureV6Error("v6_selector_source_pin")
    if type(close_ns) is not int or close_ns <= 0 or close_ns % policy_v6.BUCKET_NS:
        raise CaptureV6Error("v6_plan_close")
    try:
        selected = scout.plan_bucket(
            policy["population"],
            source_pin=policy_v6.SOURCE_PIN,
            bucket_close_ns=close_ns,
            cursor=cursor,
            prior_retention=prior_retention,
            prior_evidence=prior_evidence,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CaptureV6Error("v6_selector_rejected") from exc
    pool_ids = selected.get("selected_pool_ids")
    scouts = selected.get("scout_pool_ids")
    retention = selected.get("retention")
    if (
        selected.get("population_sha256") != policy_v6.POPULATION_SHA256
        or type(pool_ids) is not list
        or len(pool_ids) != 6
        or len(set(pool_ids)) != 6
        or type(scouts) is not list
        or not 4 <= len(scouts) <= 6
        or type(retention) is not list
        or len(retention) > 2
        or set(pool_ids) != set(scouts) | {row.get("pool_id") for row in retention}
        or selected.get("cursor") != cursor
    ):
        raise CaptureV6Error("v6_selector_output_invalid")
    core = {
        "schema": PLAN_SCHEMA,
        "schema_version": 6,
        "policy_id": policy_v6.POLICY_ID,
        "policy_sha256": policy["policy_sha256"],
        "selector_policy_id": scout.POLICY_ID,
        "selector_sha256": selector_sha256,
        "population_sha256": policy_v6.POPULATION_SHA256,
        "bucket_close_ns": close_ns,
        "cursor_in": cursor,
        "cursor_out": selected["next_cursor"],
        "retention": retention,
        "scout_pool_ids": scouts,
        "selected_pool_ids": pool_ids,
        "provider_io": False,
        "retry": False,
        "backfill": False,
    }
    return {**core, "plan_sha256": sha256(canonical_json(core))}


def request_for_pool(policy: Mapping[str, Any], pool_id: str, close_ns: int) -> dict[str, Any]:
    """Construct the frozen v6 request from a selected pool and bucket close."""
    policy_v6.verify_policy_hash(dict(policy))
    row = next((item for item in policy["population"] if item.get("pool_id") == pool_id), None)
    if row is None or type(close_ns) is not int or close_ns <= 0 or close_ns % policy_v6.BUCKET_NS:
        raise CaptureV6Error("v6_request_binding")
    close_s = close_ns // NS
    path = f"/api/v2/networks/solana/pools/{pool_id}/ohlcv/minute"
    query = {
        "aggregate": "5",
        "currency": "usd",
        "token": "base",
        "include_empty_intervals": "false",
        "limit": "6",
        "before_timestamp": str(close_s),
    }
    url = "https://api.geckoterminal.com" + path + "?" + urllib.parse.urlencode(query)
    headers = dict(policy["protocol"]["headers"])
    request_semantics = {"method": "GET", "url": url, "headers": headers}
    request_id = sha256(
        canonical_json(
            {
                "policy_sha256": policy["policy_sha256"],
                "pool_id": pool_id,
                "bucket_close_ns": close_ns,
            }
        )
    )
    return {
        "schema": "brain-senses-gecko-request-v6",
        "request_id": request_id,
        "pool_id": pool_id,
        "base_mint": row["base_mint"],
        "quote_mint": row["quote_mint"],
        "bucket_close_ns": close_ns,
        "bucket_id": str(close_s),
        "path": path,
        "query": query,
        "url": url,
        "headers": headers,
        "semantic_request_sha256": sha256(canonical_json(request_semantics)),
        "max_response_bytes": policy_v6.MAX_RESPONSE_BYTES,
    }


def _prior_selector_inputs(
    writer: DescriptorWriter, events: Sequence[Mapping[str, Any]], *, close_ns: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive retention inputs only from confirmed, timely, raw-replayed prior-v6 data."""
    previous_close = close_ns - policy_v6.BUCKET_NS
    prior_plan = next(
        (
            _object(
                writer.read_bytes(event["fields"]["plan_path"], max_bytes=256 * 1024),
                "v6_prior_plan",
            )
            for event in reversed(events)
            if event["kind"] == "plan" and event["fields"].get("close_ns") == previous_close
        ),
        None,
    )
    if prior_plan is None:
        return [], []
    retention = [
        dict(row)
        for row in prior_plan["retention"]
        if row.get("expires_after_close_ns", 0) >= close_ns
    ]
    by_request: dict[str, dict[str, Any]] = {}
    for event in events:
        if (
            event["kind"] in {"outcome", "confirmation"}
            and event["fields"].get("close_ns", previous_close) == previous_close
        ):
            by_request.setdefault(event["fields"]["request_id"], {})[event["kind"]] = dict(
                event["fields"]
            )
        elif event["kind"] == "confirmation":
            request_id = event["fields"].get("request_id")
            if request_id:
                by_request.setdefault(request_id, {})["confirmation"] = dict(event["fields"])
    evidence: list[dict[str, Any]] = []
    for event in events:
        if event["kind"] != "intent" or event["fields"].get("close_ns") != previous_close:
            continue
        fields = event["fields"]
        request_id = fields["request_id"]
        linked = by_request.get(request_id, {})
        outcome = linked.get("outcome")
        confirmation = linked.get("confirmation")
        if (
            outcome is None
            or confirmation is None
            or outcome.get("status") not in {"received", "empty_response"}
        ):
            continue
        if (
            not outcome.get("timely")
            or type(outcome.get("availability_ns")) is not int
            or type(confirmation.get("availability_ns")) is not int
            or confirmation["availability_ns"] < outcome["availability_ns"]
            or confirmation["availability_ns"]
            >= previous_close + scout.AVAILABILITY_END_EXCLUSIVE_NS
        ):
            continue
        raw_path = outcome.get("raw_path")
        if type(raw_path) is not str:
            continue
        raw = writer.read_bytes(raw_path, max_bytes=policy_v6.MAX_RESPONSE_BYTES)
        if sha256(raw) != outcome.get("raw_sha256"):
            raise CaptureV6Error("v6_prior_raw_hash")
        try:
            bars = parse_response(raw, previous_close, outcome["receipt_ns"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CaptureV6Error("v6_prior_raw_parse") from exc
        has_new_open = any(bar["disposition"] == "eligible_new_open" for bar in bars)
        evidence.append(
            {
                "pool_id": fields["pool_id"],
                "evidence_bucket_close_ns": previous_close,
                "request_started_ns": next(
                    row["fields"]["request_started_ns"]
                    for row in events
                    if row["kind"] == "dispatch" and row["fields"].get("request_id") == request_id
                ),
                "receipt_ns": outcome["receipt_ns"],
                "availability_ns": confirmation["availability_ns"],
                "received": True,
                "durable": True,
                "eligible_new_open": has_new_open,
            }
        )
    return retention, evidence


def _event(
    *, seq: int, kind: str, at_ns: int, previous_sha256: str | None, fields: dict[str, Any]
) -> dict[str, Any]:
    if type(at_ns) is not int or at_ns <= 0 or (seq == 0) != (previous_sha256 is None):
        raise CaptureV6Error("v6_event_time_or_sequence")
    core = {
        "schema": EVENT_SCHEMA,
        "schema_version": 6,
        "seq": seq,
        "kind": kind,
        "at_ns": at_ns,
        "previous_sha256": previous_sha256,
        "fields": fields,
    }
    return {**core, "event_sha256": sha256(canonical_json(core))}


def replay_events(raw: bytes) -> list[dict[str, Any]]:
    if type(raw) is not bytes:
        raise CaptureV6Error("v6_event_bytes")
    events: list[dict[str, Any]] = []
    previous: str | None = None
    previous_at_ns = 0
    for line in raw.splitlines():
        event = _object(line + b"\n", "v6_event_json")
        if set(event) != {
            "schema",
            "schema_version",
            "seq",
            "kind",
            "at_ns",
            "previous_sha256",
            "fields",
            "event_sha256",
        }:
            raise CaptureV6Error("v6_event_shape")
        core = dict(event)
        claimed = core.pop("event_sha256")
        if (
            event["schema"] != EVENT_SCHEMA
            or event["schema_version"] != 6
            or event["seq"] != len(events)
            or event["previous_sha256"] != previous
            or type(event["kind"]) is not str
            or type(event["fields"]) is not dict
            or type(event["at_ns"]) is not int
            or event["at_ns"] <= 0
            or event["at_ns"] < previous_at_ns
            or claimed != sha256(canonical_json(core))
        ):
            raise CaptureV6Error("v6_event_chain_invalid")
        previous = claimed
        previous_at_ns = event["at_ns"]
        events.append(event)
    if raw and not raw.endswith(b"\n"):
        raise CaptureV6Error("v6_event_partial_line")
    return events


def usage(events: Sequence[Mapping[str, Any]], *, now_ns: int) -> dict[str, Any]:
    """Conservative local Gecko accounting; unresolved intents retain max reservation."""
    if type(now_ns) is not int or now_ns <= 0:
        raise CaptureV6Error("v6_usage_now")
    intents: dict[str, dict[str, Any]] = {}
    dispatches: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    outcome_event_sha: dict[str, str] = {}
    confirms: dict[str, dict[str, Any]] = {}
    plans: set[tuple[int, str]] = set()
    slot_states: dict[tuple[int, str], str] = {}
    abandoned_slots: set[tuple[int, str]] = set()
    for event in events:
        fields = event["fields"]
        kind = event["kind"]
        request_id = fields.get("request_id")
        if kind == "plan":
            close = fields.get("close_ns")
            pool_ids = fields.get("selected_pool_ids")
            if type(close) is not int or type(pool_ids) is not list or len(pool_ids) != 6:
                raise CaptureV6Error("v6_plan_event_invalid")
            for pool_id in pool_ids:
                key = (close, pool_id)
                if type(pool_id) is not str or key in slot_states:
                    raise CaptureV6Error("v6_plan_slot_duplicate")
                slot_states[key] = "unspent"
            plans.add((close, cast(str, fields.get("plan_sha256"))))
        elif kind in {"gap", "missed"}:
            close, pool_id = fields.get("close_ns"), fields.get("pool_id")
            if kind == "gap":
                if type(close) is not int or fields.get("selected_pool_ids") is not None:
                    raise CaptureV6Error("v6_gap_shape")
                continue
            key = (close, pool_id)
            if key not in slot_states or slot_states[key] != "unspent":
                raise CaptureV6Error("v6_missed_slot_invalid")
            slot_states[key] = "missed"
        elif kind == "request_abandoned":
            key = (fields.get("close_ns"), fields.get("pool_id"))
            if (
                set(fields)
                != {
                    "request_id",
                    "close_ns",
                    "pool_id",
                    "request_path",
                    "request_sha256",
                    "reason",
                }
                or type(request_id) is not str
                or type(fields.get("close_ns")) is not int
                or type(fields.get("pool_id")) is not str
                or fields.get("request_path") != f"{REQUEST_DIRECTORY}/{request_id}.json"
                or not _sha(fields.get("request_sha256"))
                or fields.get("reason") != "expired_before_intent"
                or key not in slot_states
                or slot_states[key] != "unspent"
                or key in abandoned_slots
                or request_id in intents
            ):
                raise CaptureV6Error("v6_request_abandonment_invalid")
            abandoned_slots.add(key)
        elif kind == "intent":
            if type(request_id) is not str or request_id in intents:
                raise CaptureV6Error("v6_duplicate_or_bad_intent")
            key = (fields.get("close_ns"), fields.get("pool_id"))
            if key not in slot_states or slot_states[key] != "unspent" or key in abandoned_slots:
                raise CaptureV6Error("v6_intent_slot_invalid")
            if (fields.get("close_ns"), fields.get("plan_sha256")) not in plans:
                raise CaptureV6Error("v6_intent_plan_invalid")
            slot_states[key] = "intent"
            intents[request_id] = dict(fields)
        elif kind == "dispatch":
            if request_id not in intents or request_id in dispatches or request_id in outcomes:
                raise CaptureV6Error("v6_dispatch_without_unique_intent")
            dispatches[request_id] = dict(fields)
        elif kind == "outcome":
            if request_id not in intents or request_id in outcomes:
                raise CaptureV6Error("v6_outcome_without_unique_intent")
            outcomes[request_id] = dict(fields)
            outcome_event_sha[cast(str, request_id)] = cast(str, event.get("event_sha256"))
        elif kind == "confirmation":
            if request_id not in outcomes or request_id in confirms:
                raise CaptureV6Error("v6_confirmation_without_outcome")
            confirms[request_id] = dict(fields)
        else:
            raise CaptureV6Error("v6_event_kind_unknown")
    if any(slot_states.get(key) != "missed" for key in abandoned_slots):
        raise CaptureV6Error("v6_request_abandonment_without_missed")
    if (
        set(dispatches) - set(intents)
        or set(outcomes) - set(intents)
        or set(confirms) - set(outcomes)
    ):
        raise CaptureV6Error("v6_event_lineage")
    rows: list[dict[str, Any]] = []
    for request_id, intent in intents.items():
        intent_ns = intent.get("intent_ns")
        max_raw = intent.get("maximum_response_bytes")
        reserve = intent.get("physical_reserve_bytes")
        if (
            type(intent_ns) is not int
            or intent_ns <= 0
            or max_raw != policy_v6.MAX_RESPONSE_BYTES
            or reserve != policy_v6.PHYSICAL_RESERVE_PER_INTENT
        ):
            raise CaptureV6Error("v6_intent_reservation_invalid")
        dispatched = dispatches.get(request_id)
        outcome = outcomes.get(request_id)
        confirm = confirms.get(request_id)
        dispatch_ns = dispatched.get("request_started_ns") if dispatched else None
        if dispatched and (type(dispatch_ns) is not int or dispatch_ns < intent_ns):
            raise CaptureV6Error("v6_dispatch_time_invalid")
        if outcome is None:
            raw_bytes = max_raw
            physical_bytes = reserve
            terminal_status = "possibly_spent_unresolved"
            effective_ns = intent_ns
        else:
            raw_bytes = outcome.get("charged_raw_bytes")
            physical_bytes = outcome.get("physical_bytes")
            terminal_status = outcome.get("status")
            effective_ns = (
                outcome.get("recovery_ns")
                if terminal_status == "possibly_spent_unresolved"
                else outcome.get("receipt_ns")
            )
            if (
                type(raw_bytes) is not int
                or not 0 <= raw_bytes <= max_raw
                or physical_bytes != reserve
                or type(effective_ns) is not int
                or effective_ns < intent_ns
            ):
                raise CaptureV6Error("v6_outcome_usage_invalid")
            if (
                confirm is not None
                and confirm.get("outcome_sha256") != outcome_event_sha[request_id]
            ):
                raise CaptureV6Error("v6_confirmation_outcome_hash")
            if (
                outcome.get("status") in {"http_200", "empty_response"}
                and request_id not in dispatches
            ):
                raise CaptureV6Error("v6_success_without_dispatch")
        rows.append(
            {
                "request_id": request_id,
                "intent_ns": intent_ns,
                "dispatch_ns": dispatch_ns,
                "effective_ns": effective_ns,
                "raw_bytes": raw_bytes,
                "physical_bytes": physical_bytes,
                "status": terminal_status,
                "confirmed": confirm is not None,
                "ambiguous": outcome is None
                or terminal_status
                in {"transport_uncertain_possibly_spent", "possibly_spent_unresolved"},
            }
        )
    horizon = now_ns - ROLLING_WINDOW_NS
    recent = [row for row in rows if row["intent_ns"] >= horizon]
    day_start = now_ns - (now_ns % DAY_NS)
    today = [row for row in rows if row["intent_ns"] >= day_start]
    return {
        "intents_rolling_30d": len(recent),
        "raw_bytes_rolling_30d": sum(row["raw_bytes"] for row in recent),
        "physical_reserved_rolling_30d": sum(row["physical_bytes"] for row in recent),
        "intents_utc_day": len(today),
        "raw_bytes_utc_day": sum(row["raw_bytes"] for row in today),
        "rows_by_request": {row["request_id"]: row for row in rows},
        "rows": rows,
    }


class LocalJournalV6:
    """Use an existing v6 root descriptor to append source-pinned local evidence."""

    def __init__(
        self,
        writer: DescriptorWriter,
        *,
        expected_storage: Mapping[str, Any],
        observed_storage: Callable[[], Mapping[str, Any]],
        revalidate_storage: Callable[[], Any],
        resource_snapshot: Callable[[], Mapping[str, Any]],
        exclusive_lane_lease: CrossReleaseLaneLeaseV6,
    ) -> None:
        if not _storage_shape(dict(expected_storage)):
            raise CaptureV6Error("v6_storage_binding_invalid")
        self.writer = writer
        self.expected_storage = dict(expected_storage)
        self.observed_storage = observed_storage
        self.revalidate_storage = revalidate_storage
        self.resource_snapshot = resource_snapshot
        self.exclusive_lane_lease = exclusive_lane_lease
        if (
            os.fstat(writer.root_fd).st_dev,
            os.fstat(writer.root_fd).st_ino,
        ) == (
            os.fstat(exclusive_lane_lease.writer.root_fd).st_dev,
            os.fstat(exclusive_lane_lease.writer.root_fd).st_ino,
        ):
            raise CaptureV6Error("v6_global_lane_must_be_data_root_descriptor")
        self._check_storage()

    @contextlib.contextmanager
    def hold_exclusive_lane(self) -> Iterator[None]:
        """Hold the data-root lane lock across reserve, transport, and settlement."""
        with self.exclusive_lane_lease.hold():
            yield

    def _require_exclusive_lane(self) -> None:
        if not self.exclusive_lane_lease.held_by_current_thread():
            raise CaptureV6Error("v6_global_lane_lease_required")

    def _check_storage(self) -> None:
        self.revalidate_storage()
        root_stat = os.fstat(self.writer.root_fd)
        if (root_stat.st_dev, root_stat.st_ino) != (
            self.expected_storage["service_root"]["device"],
            self.expected_storage["service_root"]["inode"],
        ) or dict(self.observed_storage()) != self.expected_storage:
            raise CaptureV6Error("v6_storage_identity_mismatch")

    def _check_capacity(self) -> None:
        snapshot = dict(self.resource_snapshot())
        if (
            set(snapshot) != {"free_bytes", "resident_bytes"}
            or type(snapshot["free_bytes"]) is not int
            or type(snapshot["resident_bytes"]) is not int
            or snapshot["free_bytes"] < policy_v6.MIN_FREE_BYTES
            or snapshot["resident_bytes"] > policy_v6.MAX_RESIDENT_BYTES
        ):
            raise CaptureV6Error("v6_resource_cap")

    def _events(self) -> list[dict[str, Any]]:
        try:
            raw = self.writer.read_bytes(EVENTS_PATH, max_bytes=64 * 1024 * 1024)
        except Exception as exc:
            raise CaptureV6Error("v6_event_log_missing") from exc
        return replay_events(raw)

    def _append(
        self,
        kind: str,
        at_ns: int,
        fields: dict[str, Any],
        *,
        precondition: Callable[[Sequence[Mapping[str, Any]]], None] | None = None,
    ) -> dict[str, Any]:
        with self.writer.lock(".lane.lock", blocking=False):
            self._check_storage()
            events = self._events()
            if precondition is not None:
                precondition(events)
            previous = events[-1]["event_sha256"] if events else None
            event = _event(
                seq=len(events), kind=kind, at_ns=at_ns, previous_sha256=previous, fields=fields
            )
            self.revalidate_storage()
            self.writer.append(EVENTS_PATH, canonical_json(event))
            self._check_storage()
            return event

    def plan_bucket(
        self,
        *,
        policy: dict[str, Any],
        epoch: dict[str, Any],
        close_ns: int,
        now_ns: int,
        selector_sha256: str,
        prior_retention: Sequence[Mapping[str, Any]] = (),
        prior_evidence: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Persist exactly one pre-I/O deterministic plan; restart returns identical bytes."""
        self._require_exclusive_lane()
        verify_epoch(epoch)
        if epoch.get("storage") != self.expected_storage or epoch.get(
            "policy_sha256"
        ) != policy.get("policy_sha256"):
            raise CaptureV6Error("v6_epoch_storage_or_policy_binding")
        if type(now_ns) is not int or now_ns < close_ns + scout.DISPATCH_EARLIEST_NS:
            raise CaptureV6Error("v6_plan_not_due")
        expired = now_ns >= close_ns + scout.DISPATCH_END_EXCLUSIVE_NS
        events = self._events()
        known_plans = [event["fields"]["close_ns"] for event in events if event["kind"] == "plan"]
        known_gaps = [event["fields"]["close_ns"] for event in events if event["kind"] == "gap"]
        known = sorted(set(known_plans) | set(known_gaps))
        path = f"{PLAN_DIRECTORY}/{close_ns}.json"
        prior_plan_events = [
            event
            for event in events
            if event["kind"] == "plan" and event["fields"].get("close_ns", 0) < close_ns
        ]
        cursor = prior_plan_events[-1]["fields"]["cursor_out"] if prior_plan_events else 0
        prior_retention, prior_evidence = _prior_selector_inputs(
            self.writer, events, close_ns=close_ns
        )
        plan = build_plan(
            policy,
            close_ns=close_ns,
            cursor=cursor,
            prior_retention=prior_retention,
            prior_evidence=prior_evidence,
            selector_sha256=selector_sha256,
            expected_selector_sha256=epoch["selector_sha256"],
        )
        raw = canonical_json(plan)
        try:
            existing = self.writer.read_bytes(path, max_bytes=256 * 1024)
        except FileNotFoundError:
            if expired:
                raise CaptureV6Error("v6_plan_window_expired_without_durable_plan") from None
            if close_ns < epoch["first_bucket_close_ns"]:
                raise CaptureV6Error("v6_plan_before_epoch_anchor") from None
            previous_close = max(
                known, default=epoch["first_bucket_close_ns"] - policy_v6.BUCKET_NS
            )
            if close_ns != previous_close + policy_v6.BUCKET_NS:
                raise CaptureV6Error("v6_plan_close_gap_requires_gap_event") from None
            self._check_storage()
            self.writer.atomic_write(path, raw, create_only=True)
            self._check_storage()
        else:
            if existing != raw:
                raise CaptureV6Error("v6_plan_immutable_mismatch")
        if close_ns not in known_plans:
            self._append(
                "plan",
                now_ns,
                {
                    "close_ns": close_ns,
                    "plan_path": path,
                    "plan_sha256": plan["plan_sha256"],
                    "plan_file_sha256": sha256(raw),
                    "cursor_in": plan["cursor_in"],
                    "cursor_out": plan["cursor_out"],
                    "selected_pool_ids": plan["selected_pool_ids"],
                },
            )
        if expired:
            # A plan file may have reached disk before its plan event. Recover its
            # exact selection/cursor, then terminalize every unspent slot as missed;
            # never dispatch from an expired plan.
            current = self._events()
            spent = {
                event["fields"].get("pool_id")
                for event in current
                if event["kind"] in {"intent", "missed"}
                and event["fields"].get("close_ns") == close_ns
            }
            for pool_id in plan["selected_pool_ids"]:
                if pool_id not in spent:
                    self._abandon_orphan_request(plan=plan, pool_id=pool_id, at_ns=now_ns)
                    self.record_missed(plan=plan, pool_id=pool_id, at_ns=now_ns)
        return plan

    def record_gap(
        self, *, epoch: Mapping[str, Any], close_ns: int, at_ns: int, reason: str
    ) -> dict[str, Any]:
        """Seal an unplanned missed close without selecting pools or advancing cursor."""
        self._require_exclusive_lane()
        if (
            type(close_ns) is not int
            or close_ns <= 0
            or close_ns % policy_v6.BUCKET_NS
            or type(at_ns) is not int
            or at_ns < close_ns + scout.DISPATCH_END_EXCLUSIVE_NS
            or reason not in {"unobserved_no_plan", "cooldown_or_capacity_pause"}
        ):
            raise CaptureV6Error("v6_gap_invalid")
        verify_epoch(dict(epoch))
        events = self._events()
        existing = [e["fields"]["close_ns"] for e in events if e["kind"] in {"gap", "plan"}]
        previous_close = max(existing, default=epoch["first_bucket_close_ns"] - policy_v6.BUCKET_NS)
        expected = previous_close + policy_v6.BUCKET_NS
        # The first close is pinned in epoch.json; later gaps are strictly sequential.
        if close_ns != expected:
            raise CaptureV6Error("v6_gap_sequence")
        cursor = (
            0
            if not existing
            else next(
                event["fields"]["cursor_out"]
                for event in reversed(events)
                if event["kind"] == "plan"
            )
            if any(event["kind"] == "plan" for event in events)
            else 0
        )
        return self._append(
            "gap",
            at_ns,
            {
                "close_ns": close_ns,
                "selected_pool_ids": None,
                "reason": reason,
                "cursor_in": cursor,
                "cursor_out": cursor,
                "cursor_unchanged": True,
            },
        )

    def record_missed(self, *, plan: Mapping[str, Any], pool_id: str, at_ns: int) -> dict[str, Any]:
        """Seal one planned slot after dispatch closes; never issues or advances a request."""
        self._require_exclusive_lane()
        close_ns = plan.get("bucket_close_ns")
        if (
            type(close_ns) is not int
            or pool_id not in plan.get("selected_pool_ids", [])
            or type(at_ns) is not int
            or at_ns < close_ns + scout.DISPATCH_END_EXCLUSIVE_NS
        ):
            raise CaptureV6Error("v6_missed_slot_deadline")
        events = self._events()
        if any(
            event["kind"] in {"intent", "missed"}
            and event["fields"].get("close_ns") == close_ns
            and event["fields"].get("pool_id") == pool_id
            for event in events
        ):
            raise CaptureV6Error("v6_missed_slot_already_spent")

        def check_unspent(current: Sequence[Mapping[str, Any]]) -> None:
            if any(
                event["kind"] in {"intent", "missed"}
                and event["fields"].get("close_ns") == close_ns
                and event["fields"].get("pool_id") == pool_id
                for event in current
            ):
                raise CaptureV6Error("v6_missed_slot_already_spent")

        return self._append(
            "missed",
            at_ns,
            {
                "close_ns": close_ns,
                "pool_id": pool_id,
                "plan_sha256": plan["plan_sha256"],
                "reason": "dispatch_window_elapsed_no_intent",
            },
            precondition=check_unspent,
        )

    def _abandon_orphan_request(self, *, plan: Mapping[str, Any], pool_id: str, at_ns: int) -> None:
        """Bind an expired request-file-only prefix before its slot is marked missed."""
        close_ns = plan.get("bucket_close_ns")
        request = request_for_pool(
            _object(self.writer.read_bytes(POLICY_PATH, max_bytes=512 * 1024), "v6_policy_file"),
            pool_id,
            cast(int, close_ns),
        )
        request_path = f"{REQUEST_DIRECTORY}/{request['request_id']}.json"
        try:
            request_raw = self.writer.read_bytes(request_path, max_bytes=64 * 1024)
        except FileNotFoundError:
            return
        if request_raw != canonical_json(request):
            raise CaptureV6Error("v6_orphan_request_bytes_invalid")
        existing = [
            event
            for event in self._events()
            if event["kind"] == "request_abandoned"
            and event["fields"].get("request_id") == request["request_id"]
        ]
        fields = {
            "request_id": request["request_id"],
            "close_ns": close_ns,
            "pool_id": pool_id,
            "request_path": request_path,
            "request_sha256": sha256(request_raw),
            "reason": "expired_before_intent",
        }
        if existing:
            if len(existing) != 1 or existing[0]["fields"] != fields:
                raise CaptureV6Error("v6_orphan_request_abandonment_conflict")
            return
        if at_ns < close_ns + scout.DISPATCH_END_EXCLUSIVE_NS:
            raise CaptureV6Error("v6_orphan_request_abandonment_early")

        def check_unspent(current: Sequence[Mapping[str, Any]]) -> None:
            if self.writer.read_bytes(request_path, max_bytes=64 * 1024) != request_raw:
                raise CaptureV6Error("v6_orphan_request_bytes_changed")
            if any(
                event["kind"] in {"intent", "missed", "request_abandoned"}
                and event["fields"].get("close_ns") == close_ns
                and event["fields"].get("pool_id") == pool_id
                for event in current
            ):
                raise CaptureV6Error("v6_orphan_request_slot_spent")

        self._append("request_abandoned", at_ns, fields, precondition=check_unspent)

    def reserve_intent(
        self,
        *,
        policy: dict[str, Any],
        epoch: dict[str, Any],
        plan: Mapping[str, Any],
        pool_id: str,
        intent_ns: int,
        inherited_usage_rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._require_exclusive_lane()
        policy_v6.verify_policy_hash(policy)
        if pool_id not in plan.get("selected_pool_ids", []) or type(intent_ns) is not int:
            raise CaptureV6Error("v6_intent_not_in_plan")
        self._check_capacity()
        pinned_prior_raw, inherited = _load_pinned_prior_usage(self.writer, epoch)
        if canonical_json(list(inherited_usage_rows)) != pinned_prior_raw:
            raise CaptureV6Error("v6_prior_usage_caller_mismatch")
        plan_path = f"{PLAN_DIRECTORY}/{plan.get('bucket_close_ns')}.json"
        if _object(self.writer.read_bytes(plan_path, max_bytes=256 * 1024), "v6_plan_file") != dict(
            plan
        ):
            raise CaptureV6Error("v6_intent_plan_not_persisted")
        verify_local_journal(
            self.writer,
            epoch=epoch,
            policy=policy,
            selector=scout.plan_bucket,
            selector_sha256=epoch["selector_sha256"],
            expected_storage=self.expected_storage,
            observed_storage=self.observed_storage,
            now_ns=intent_ns,
            inherited_usage_rows=inherited,
            resource_snapshot=self.resource_snapshot(),
        )
        request = request_for_pool(policy, pool_id, cast(int, plan["bucket_close_ns"]))
        request_id = request["request_id"]
        request_raw = canonical_json(request)
        request_path = f"{REQUEST_DIRECTORY}/{request_id}.json"
        events = self._events()
        if not any(
            event["kind"] == "plan"
            and event["fields"].get("close_ns") == plan["bucket_close_ns"]
            and event["fields"].get("plan_sha256") == plan.get("plan_sha256")
            for event in events
        ):
            raise CaptureV6Error("v6_intent_plan_not_durable")
        if any(
            event["kind"] in {"intent", "missed"}
            and event["fields"].get("close_ns") == plan["bucket_close_ns"]
            and event["fields"].get("pool_id") == pool_id
            for event in events
        ):
            raise CaptureV6Error("v6_pool_bucket_already_spent")
        local_usage = usage(events, now_ns=intent_ns)
        if any(
            type(row.get("intent_ns")) is not int
            or type(row.get("raw_bytes_upper_bound")) is not int
            or row["raw_bytes_upper_bound"] < 0
            or type(row.get("physical_bytes_upper_bound")) is not int
            or row["physical_bytes_upper_bound"] < 0
            for row in inherited
        ):
            raise CaptureV6Error("v6_prior_usage_row_invalid")
        horizon = intent_ns - ROLLING_WINDOW_NS
        prior_recent = [row for row in inherited if row["intent_ns"] >= horizon]
        day_start = intent_ns - intent_ns % DAY_NS
        prior_today = [row for row in inherited if row["intent_ns"] >= day_start]
        if (
            local_usage["intents_rolling_30d"] + len(prior_recent) + 1
            > policy_v6.MAX_INTENTS_PER_ROLLING_30D
            or local_usage["intents_utc_day"] + len(prior_today) + 1
            > policy_v6.MAX_INTENTS_PER_UTC_DAY
            or local_usage["raw_bytes_rolling_30d"]
            + sum(row["raw_bytes_upper_bound"] for row in prior_recent)
            + policy_v6.MAX_RESPONSE_BYTES
            > policy_v6.MAX_RAW_BYTES_PER_ROLLING_30D
            or local_usage["raw_bytes_utc_day"]
            + sum(row["raw_bytes_upper_bound"] for row in prior_today)
            + policy_v6.MAX_RESPONSE_BYTES
            > policy_v6.MAX_RAW_BYTES_PER_UTC_DAY
            or local_usage["physical_reserved_rolling_30d"]
            + sum(row["physical_bytes_upper_bound"] for row in prior_recent)
            + policy_v6.PHYSICAL_RESERVE_PER_INTENT
            > policy_v6.MAX_PHYSICAL_RESERVE_PER_ROLLING_30D
        ):
            raise CaptureV6Error("v6_local_budget_exhausted")
        for event in events:
            fields = event["fields"]
            if (
                event["kind"] == "intent"
                and fields.get("pool_id") == pool_id
                and fields.get("close_ns") == plan["bucket_close_ns"]
            ):
                raise CaptureV6Error("v6_pool_bucket_already_spent")
        window_start = plan["bucket_close_ns"] + scout.DISPATCH_EARLIEST_NS
        request_deadline = plan["bucket_close_ns"] + scout.REQUEST_START_END_EXCLUSIVE_NS
        if not window_start <= intent_ns < request_deadline:
            raise CaptureV6Error("v6_intent_start_window")
        dispatches = [
            event["fields"]["request_started_ns"] for event in events if event["kind"] == "dispatch"
        ]
        if dispatches:
            prior = max(dispatches)
            if intent_ns < prior + scout.MINIMUM_DISPATCH_SPACING_NS:
                raise CaptureV6Error("v6_minimum_spacing")
            if (
                sum(intent_ns - at < 60 * NS for at in dispatches)
                >= scout.ROLLING_MINUTE_DISPATCH_CAP
            ):
                raise CaptureV6Error("v6_rolling_minute_cap")
        fields = {
            "request_id": request_id,
            "request_path": request_path,
            "request_sha256": sha256(request_raw),
            "pool_id": pool_id,
            "close_ns": plan["bucket_close_ns"],
            "plan_sha256": plan["plan_sha256"],
            "intent_ns": intent_ns,
            "maximum_response_bytes": policy_v6.MAX_RESPONSE_BYTES,
            "physical_reserve_bytes": policy_v6.PHYSICAL_RESERVE_PER_INTENT,
        }
        self._check_capacity()

        # A deterministic request file may exist before its intent only as a
        # no-network crash prefix. Replay can adopt these exact bytes safely.
        self._check_storage()
        try:
            self.writer.create(request_path, request_raw)
        except FileExistsError:
            if self.writer.read_bytes(request_path, max_bytes=64 * 1024) != request_raw:
                raise CaptureV6Error("v6_request_immutable_mismatch") from None
        self._check_storage()

        def check_under_lane_lock(current: Sequence[Mapping[str, Any]]) -> None:
            current_usage = usage(current, now_ns=intent_ns)
            _current_prior_raw, current_prior_rows = _load_pinned_prior_usage(self.writer, epoch)
            if canonical_json(list(inherited_usage_rows)) != _current_prior_raw:
                raise CaptureV6Error("v6_prior_usage_caller_mismatch")
            current_recent = [row for row in current_prior_rows if row["intent_ns"] >= horizon]
            current_today = [row for row in current_prior_rows if row["intent_ns"] >= day_start]
            plan_path = f"{PLAN_DIRECTORY}/{plan.get('bucket_close_ns')}.json"
            if _object(
                self.writer.read_bytes(plan_path, max_bytes=256 * 1024), "v6_plan_file"
            ) != dict(plan):
                raise CaptureV6Error("v6_intent_plan_not_persisted")
            verify_local_journal(
                self.writer,
                epoch=epoch,
                policy=policy,
                selector=scout.plan_bucket,
                selector_sha256=epoch["selector_sha256"],
                expected_storage=self.expected_storage,
                observed_storage=self.observed_storage,
                now_ns=intent_ns,
                inherited_usage_rows=current_prior_rows,
                resource_snapshot=self.resource_snapshot(),
            )
            if (
                current_usage["intents_rolling_30d"] + len(current_recent) + 1
                > policy_v6.MAX_INTENTS_PER_ROLLING_30D
                or current_usage["intents_utc_day"] + len(current_today) + 1
                > policy_v6.MAX_INTENTS_PER_UTC_DAY
                or current_usage["raw_bytes_rolling_30d"]
                + sum(row["raw_bytes_upper_bound"] for row in current_recent)
                + policy_v6.MAX_RESPONSE_BYTES
                > policy_v6.MAX_RAW_BYTES_PER_ROLLING_30D
                or current_usage["raw_bytes_utc_day"]
                + sum(row["raw_bytes_upper_bound"] for row in current_today)
                + policy_v6.MAX_RESPONSE_BYTES
                > policy_v6.MAX_RAW_BYTES_PER_UTC_DAY
                or current_usage["physical_reserved_rolling_30d"]
                + sum(row["physical_bytes_upper_bound"] for row in current_recent)
                + policy_v6.PHYSICAL_RESERVE_PER_INTENT
                > policy_v6.MAX_PHYSICAL_RESERVE_PER_ROLLING_30D
            ):
                raise CaptureV6Error("v6_local_budget_exhausted")
            if any(
                event["kind"] in {"intent", "missed"}
                and event["fields"].get("pool_id") == pool_id
                and event["fields"].get("close_ns") == plan["bucket_close_ns"]
                for event in current
            ):
                raise CaptureV6Error("v6_pool_bucket_already_spent")
            previous_dispatches = [
                event["fields"]["request_started_ns"]
                for event in current
                if event["kind"] == "dispatch"
            ]
            if previous_dispatches and (
                intent_ns < max(previous_dispatches) + scout.MINIMUM_DISPATCH_SPACING_NS
                or sum(intent_ns - value < 60 * NS for value in previous_dispatches)
                >= scout.ROLLING_MINUTE_DISPATCH_CAP
            ):
                raise CaptureV6Error("v6_dispatch_rate_limit")
            self._check_capacity()

        return self._append("intent", intent_ns, fields, precondition=check_under_lane_lock)

    def append_dispatch(
        self,
        *,
        request_id: str,
        request_started_ns: int,
        monotonic_started_ns: int,
        worker_identity: Mapping[str, Any],
        close_ns: int,
        policy: dict[str, Any],
        epoch: dict[str, Any],
        selector_sha256: str,
        inherited_usage_rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._require_exclusive_lane()
        if (
            type(request_started_ns) is not int
            or type(monotonic_started_ns) is not int
            or monotonic_started_ns <= 0
            or not close_ns + scout.DISPATCH_EARLIEST_NS
            <= request_started_ns
            < close_ns + scout.REQUEST_START_END_EXCLUSIVE_NS
        ):
            raise CaptureV6Error("v6_dispatch_window")
        events = self._events()
        intent = next(
            (
                event["fields"]
                for event in events
                if event["kind"] == "intent" and event["fields"].get("request_id") == request_id
            ),
            None,
        )
        if intent is None or intent.get("close_ns") != close_ns:
            raise CaptureV6Error("v6_dispatch_missing_intent")
        if (
            set(worker_identity)
            != {
                "pid",
                "pgid",
                "birth_id",
                "executable_path",
                "uid",
                "attempt_nonce",
                "worker_source_sha256",
            }
            or type(worker_identity.get("pid")) is not int
            or worker_identity["pid"] <= 0
            or worker_identity.get("pgid") != worker_identity.get("pid")
            or type(worker_identity.get("birth_id")) is not str
            or re.fullmatch(r"[0-9]+\.[0-9]{6}", worker_identity["birth_id"]) is None
            or type(worker_identity.get("executable_path")) is not str
            or not os.path.isabs(worker_identity["executable_path"])
            or ".." in worker_identity["executable_path"].split("/")
            or type(worker_identity.get("uid")) is not int
            or worker_identity["uid"] < 0
            or type(worker_identity.get("attempt_nonce")) is not str
            or re.fullmatch(r"[0-9a-f]{32}", worker_identity["attempt_nonce"]) is None
            or not _sha(worker_identity.get("worker_source_sha256"))
        ):
            raise CaptureV6Error("v6_dispatch_worker_identity")
        prior_dispatches = [
            event["fields"]["request_started_ns"] for event in events if event["kind"] == "dispatch"
        ]
        if prior_dispatches and (
            request_started_ns < max(prior_dispatches) + scout.MINIMUM_DISPATCH_SPACING_NS
            or sum(request_started_ns - at < 60 * NS for at in prior_dispatches)
            >= scout.ROLLING_MINUTE_DISPATCH_CAP
        ):
            raise CaptureV6Error("v6_dispatch_rate_cap")
        if request_started_ns < intent["intent_ns"]:
            raise CaptureV6Error("v6_dispatch_before_intent")
        # Every dispatch requires a full replay of this journal and its current
        # source, resource, request, outcome, and exact-file closure.
        verify_local_journal(
            self.writer,
            epoch=epoch,
            policy=policy,
            selector=scout.plan_bucket,
            selector_sha256=selector_sha256,
            expected_storage=self.expected_storage,
            observed_storage=self.observed_storage,
            now_ns=request_started_ns,
            inherited_usage_rows=inherited_usage_rows,
            resource_snapshot=self.resource_snapshot(),
        )
        fields = {
            "request_id": request_id,
            "request_started_ns": request_started_ns,
            "monotonic_started_ns": monotonic_started_ns,
            "worker_pid": worker_identity["pid"],
            "worker_pgid": worker_identity["pgid"],
            "worker_birth_id": worker_identity["birth_id"],
            "worker_executable_path": worker_identity["executable_path"],
            "worker_uid": worker_identity["uid"],
            "attempt_nonce": worker_identity["attempt_nonce"],
            "worker_source_sha256": worker_identity["worker_source_sha256"],
        }

        def check_dispatch(current: Sequence[Mapping[str, Any]]) -> None:
            verify_local_journal(
                self.writer,
                epoch=epoch,
                policy=policy,
                selector=scout.plan_bucket,
                selector_sha256=selector_sha256,
                expected_storage=self.expected_storage,
                observed_storage=self.observed_storage,
                now_ns=request_started_ns,
                inherited_usage_rows=inherited_usage_rows,
                resource_snapshot=self.resource_snapshot(),
            )
            intents = [
                event["fields"]
                for event in current
                if event["kind"] == "intent" and event["fields"].get("request_id") == request_id
            ]
            if len(intents) != 1 or intents[0].get("close_ns") != close_ns:
                raise CaptureV6Error("v6_dispatch_missing_intent")
            if any(
                event["kind"] == "dispatch" and event["fields"].get("request_id") == request_id
                for event in current
            ):
                raise CaptureV6Error("v6_dispatch_duplicate")
            times = [
                event["fields"]["request_started_ns"]
                for event in current
                if event["kind"] == "dispatch"
            ]
            if times and (
                request_started_ns < max(times) + scout.MINIMUM_DISPATCH_SPACING_NS
                or sum(request_started_ns - value < 60 * NS for value in times)
                >= scout.ROLLING_MINUTE_DISPATCH_CAP
            ):
                raise CaptureV6Error("v6_dispatch_rate_cap")

        return self._append("dispatch", request_started_ns, fields, precondition=check_dispatch)

    def persist_outcome(
        self,
        *,
        request_id: str,
        status: str,
        raw_body: bytes | None,
        receipt_ns: int,
        monotonic_receipt_ns: int,
        availability_ns: int,
        close_ns: int,
        eligible_new_open_count: int | None = None,
    ) -> dict[str, Any]:
        self._require_exclusive_lane()
        if raw_body is not None and (
            type(raw_body) is not bytes or len(raw_body) > policy_v6.MAX_RESPONSE_BYTES
        ):
            status = "oversize"
            raw_body = None
        if status not in {
            "received",
            "empty_response",
            "unsupported_at_receipt",
            "rate_limited",
            "client_or_access_error",
            "provider_error",
            "transport_uncertain_possibly_spent",
            "malformed_response",
            "response_over_budget",
            "possibly_spent_unresolved",
        }:
            raise CaptureV6Error("v6_outcome_status")
        if (
            type(receipt_ns) is not int
            or type(monotonic_receipt_ns) is not int
            or monotonic_receipt_ns <= 0
            or type(availability_ns) is not int
            or availability_ns < receipt_ns
        ):
            raise CaptureV6Error("v6_outcome_time_order")
        before_write_events = self._events()
        matching_intents = [
            event["fields"]
            for event in before_write_events
            if event["kind"] == "intent" and event["fields"].get("request_id") == request_id
        ]
        matching_dispatches = [
            event["fields"]
            for event in before_write_events
            if event["kind"] == "dispatch" and event["fields"].get("request_id") == request_id
        ]
        if (
            len(matching_intents) != 1
            or matching_intents[0].get("close_ns") != close_ns
            or len(matching_dispatches) != 1
            or any(
                event["kind"] == "outcome" and event["fields"].get("request_id") == request_id
                for event in before_write_events
            )
            or receipt_ns < matching_dispatches[0]["request_started_ns"]
        ):
            raise CaptureV6Error("v6_outcome_without_dispatch")
        parsed_bars: list[dict[str, Any]] = []
        if raw_body is not None and status in {"received", "empty_response"}:
            try:
                parsed_bars = parse_response(raw_body, close_ns, receipt_ns)
            except (TypeError, ValueError, OverflowError) as exc:
                raise CaptureV6Error("v6_outcome_raw_parse") from exc
            expected_status = "received" if parsed_bars else "empty_response"
            if status != expected_status:
                raise CaptureV6Error("v6_outcome_status_raw_mismatch")
        if status in {"received", "empty_response"} and raw_body is None:
            raise CaptureV6Error("v6_success_raw_missing")
        derived_eligible = sum(bar["disposition"] == "eligible_new_open" for bar in parsed_bars)
        if eligible_new_open_count is not None and eligible_new_open_count != derived_eligible:
            raise CaptureV6Error("v6_eligible_count_claim_mismatch")
        path: str | None = None
        raw_digest: str | None = None
        raw_bytes = 0
        if raw_body is not None:
            raw_bytes = len(raw_body)
            raw_digest = sha256(raw_body)
            path = f"{RAW_DIRECTORY}/{request_id}.bin"
            self._check_storage()
            try:
                self.writer.create(path, raw_body)
            except FileExistsError:
                if self.writer.read_bytes(path, max_bytes=policy_v6.MAX_RESPONSE_BYTES) != raw_body:
                    raise CaptureV6Error("v6_raw_immutable_mismatch") from None
            self._check_storage()
        within = availability_ns < close_ns + scout.AVAILABILITY_END_EXCLUSIVE_NS
        fields = {
            "request_id": request_id,
            "status": status,
            "receipt_ns": receipt_ns,
            "monotonic_receipt_ns": monotonic_receipt_ns,
            "recovery_ns": None,
            "availability_ns": availability_ns,
            "raw_path": path,
            "raw_sha256": raw_digest,
            "raw_bytes": raw_bytes,
            "charged_raw_bytes": raw_bytes
            if raw_body is not None
            else policy_v6.MAX_RESPONSE_BYTES,
            "physical_bytes": policy_v6.PHYSICAL_RESERVE_PER_INTENT,
            "eligible_new_open_count": derived_eligible,
            "timely": within,
        }

        def check_unique_outcome(current: Sequence[Mapping[str, Any]]) -> None:
            epoch = _object(
                self.writer.read_bytes(EPOCH_PATH, max_bytes=128 * 1024), "v6_epoch_file"
            )
            policy = _object(
                self.writer.read_bytes(POLICY_PATH, max_bytes=512 * 1024), "v6_policy_file"
            )
            _prior_raw, prior_rows = _load_pinned_prior_usage(self.writer, epoch)
            verify_local_journal(
                self.writer,
                epoch=epoch,
                policy=policy,
                selector=scout.plan_bucket,
                selector_sha256=epoch["selector_sha256"],
                expected_storage=self.expected_storage,
                observed_storage=self.observed_storage,
                now_ns=availability_ns,
                inherited_usage_rows=prior_rows,
                resource_snapshot=self.resource_snapshot(),
            )
            if any(
                event["kind"] == "outcome" and event["fields"].get("request_id") == request_id
                for event in current
            ):
                raise CaptureV6Error("v6_outcome_duplicate")

        return self._append("outcome", availability_ns, fields, precondition=check_unique_outcome)

    def confirm(self, *, request_id: str, at_ns: int, outcome_sha256: str) -> dict[str, Any]:
        self._require_exclusive_lane()
        if not _sha(outcome_sha256):
            raise CaptureV6Error("v6_confirmation_hash")
        events = self._events()
        outcome = next(
            (
                event
                for event in events
                if event["kind"] == "outcome" and event["fields"].get("request_id") == request_id
            ),
            None,
        )
        if (
            outcome is None
            or outcome["event_sha256"] != outcome_sha256
            or at_ns < outcome["fields"]["availability_ns"]
        ):
            raise CaptureV6Error("v6_confirmation_outcome_mismatch")

        def check_unique_confirmation(current: Sequence[Mapping[str, Any]]) -> None:
            epoch = _object(
                self.writer.read_bytes(EPOCH_PATH, max_bytes=128 * 1024), "v6_epoch_file"
            )
            policy = _object(
                self.writer.read_bytes(POLICY_PATH, max_bytes=512 * 1024), "v6_policy_file"
            )
            _prior_raw, prior_rows = _load_pinned_prior_usage(self.writer, epoch)
            verify_local_journal(
                self.writer,
                epoch=epoch,
                policy=policy,
                selector=scout.plan_bucket,
                selector_sha256=epoch["selector_sha256"],
                expected_storage=self.expected_storage,
                observed_storage=self.observed_storage,
                now_ns=at_ns,
                inherited_usage_rows=prior_rows,
                resource_snapshot=self.resource_snapshot(),
            )
            matching_outcomes = [
                event
                for event in current
                if event["kind"] == "outcome" and event["fields"].get("request_id") == request_id
            ]
            if (
                len(matching_outcomes) != 1
                or matching_outcomes[0]["event_sha256"] != outcome_sha256
                or any(
                    event["kind"] == "confirmation"
                    and event["fields"].get("request_id") == request_id
                    for event in current
                )
            ):
                raise CaptureV6Error("v6_confirmation_duplicate_or_changed")

        return self._append(
            "confirmation",
            at_ns,
            {"request_id": request_id, "availability_ns": at_ns, "outcome_sha256": outcome_sha256},
            precondition=check_unique_confirmation,
        )

    def recover_unresolved(
        self,
        *,
        now_ns: int,
        quiescence_proofs: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[str]:
        """Terminalize spent intent prefixes only after the transport is quiescent."""
        self._require_exclusive_lane()
        proofs = dict(quiescence_proofs or {})
        epoch = _object(self.writer.read_bytes(EPOCH_PATH, max_bytes=128 * 1024), "v6_epoch_file")
        policy = _object(
            self.writer.read_bytes(POLICY_PATH, max_bytes=512 * 1024), "v6_policy_file"
        )
        current_events = self._events()
        event_plan_closes = {
            event["fields"].get("close_ns") for event in current_events if event["kind"] == "plan"
        }
        for name in self.writer.list_names(PLAN_DIRECTORY):
            if not name.endswith(".json") or not name[:-5].isdigit():
                raise CaptureV6Error("v6_plan_file_name")
            close_ns = int(name[:-5])
            if close_ns in event_plan_closes:
                continue
            self.plan_bucket(
                policy=policy,
                epoch=epoch,
                close_ns=close_ns,
                now_ns=now_ns,
                selector_sha256=epoch["selector_sha256"],
            )
        events = self._events()
        intents = {
            event["fields"]["request_id"]: event["fields"]
            for event in events
            if event["kind"] == "intent"
        }
        outcome_events = {
            event["fields"]["request_id"]: event for event in events if event["kind"] == "outcome"
        }
        outcomes = set(outcome_events)
        unresolved = sorted(set(intents) - outcomes)
        dispatches = {
            event["fields"]["request_id"]: event["fields"]
            for event in events
            if event["kind"] == "dispatch"
        }
        expected_proofs = set(unresolved) & set(dispatches)
        if set(proofs) != expected_proofs:
            raise CaptureV6Error("v6_recovery_quiescence_set")
        for request_id in unresolved:
            intent = intents[request_id]
            proof = None
            if request_id in dispatches:
                proof = _verify_quiescence_proof(
                    proofs[request_id], request_id=request_id, dispatch=dispatches[request_id]
                )
                if now_ns < proof["quiesced_ns"]:
                    raise CaptureV6Error("v6_recovery_before_transport_quiescence")
            recovery_hash = sha256(canonical_json(proof)) if proof is not None else None
            orphan_raw_path = f"{RAW_DIRECTORY}/{request_id}.bin"
            try:
                orphan_raw = self.writer.read_bytes(
                    orphan_raw_path, max_bytes=policy_v6.MAX_RESPONSE_BYTES
                )
            except FileNotFoundError:
                orphan_raw = None
            self._append(
                "outcome",
                now_ns,
                {
                    "request_id": request_id,
                    "status": "possibly_spent_unresolved",
                    "receipt_ns": None,
                    "monotonic_receipt_ns": None,
                    "recovery_ns": now_ns,
                    "quiescence_proof": proof,
                    "quiescence_proof_sha256": recovery_hash,
                    "availability_ns": now_ns,
                    "raw_path": orphan_raw_path if orphan_raw is not None else None,
                    "raw_sha256": sha256(orphan_raw) if orphan_raw is not None else None,
                    "raw_bytes": len(orphan_raw) if orphan_raw is not None else 0,
                    "charged_raw_bytes": intent["maximum_response_bytes"],
                    "physical_bytes": intent["physical_reserve_bytes"],
                    "eligible_new_open_count": 0,
                    "timely": False,
                },
            )
        for plan_event in [event for event in events if event["kind"] == "plan"]:
            close_ns = plan_event["fields"]["close_ns"]
            if now_ns < close_ns + scout.DISPATCH_END_EXCLUSIVE_NS:
                continue
            plan = _object(
                self.writer.read_bytes(plan_event["fields"]["plan_path"], max_bytes=256 * 1024),
                "v6_recovery_plan",
            )
            refreshed = self._events()
            spent = {
                event["fields"].get("pool_id")
                for event in refreshed
                if event["kind"] in {"intent", "missed"}
                and event["fields"].get("close_ns") == close_ns
            }
            for pool_id in plan["selected_pool_ids"]:
                if pool_id not in spent:
                    self._abandon_orphan_request(plan=plan, pool_id=pool_id, at_ns=now_ns)
                    self.record_missed(plan=plan, pool_id=pool_id, at_ns=now_ns)
        # Full replay rejects expired slots without a terminal attempt/missed row.
        # Seal those opportunities before calling confirm(), whose precondition
        # independently replays the entire journal.
        refreshed_outcomes = {
            event["fields"]["request_id"]: event
            for event in self._events()
            if event["kind"] == "outcome"
        }
        refreshed_confirmations = {
            event["fields"]["request_id"]
            for event in self._events()
            if event["kind"] == "confirmation"
        }
        for request_id, outcome in refreshed_outcomes.items():
            if request_id not in refreshed_confirmations:
                self.confirm(
                    request_id=request_id,
                    at_ns=max(now_ns, outcome["fields"]["availability_ns"]),
                    outcome_sha256=outcome["event_sha256"],
                )
        return unresolved


def initialize_existing_root_v6(
    writer: DescriptorWriter,
    *,
    epoch: dict[str, Any],
    policy: dict[str, Any],
    selector_sha256: str,
    prior_usage_bytes: bytes,
    expected_storage: Mapping[str, Any],
    observed_storage: Callable[[], Mapping[str, Any]],
    revalidate_storage: Callable[[], Any],
) -> None:
    """Create-only initialize inside an already-created, verified service descriptor.

    This helper never creates the service root or selects a release ID. The owner host must
    establish those boundaries and supply a descriptor for the exact empty service directory.
    """
    verify_epoch(epoch)
    policy_v6.verify_policy_hash(policy)
    if (
        epoch["storage"] != dict(expected_storage)
        or epoch["policy_sha256"] != policy["policy_sha256"]
        or selector_sha256 != epoch["selector_sha256"]
        or not _sha(selector_sha256)
        or type(prior_usage_bytes) is not bytes
        or sha256(prior_usage_bytes) != epoch["prior_usage"]["sha256"]
    ):
        raise CaptureV6Error("v6_initialize_binding")
    _prior_usage_rows(prior_usage_bytes)
    if (os.fstat(writer.root_fd).st_dev, os.fstat(writer.root_fd).st_ino) != (
        expected_storage["service_root"]["device"],
        expected_storage["service_root"]["inode"],
    ) or dict(observed_storage()) != dict(expected_storage):
        raise CaptureV6Error("v6_initialize_storage_identity")

    def checked() -> None:
        revalidate_storage()
        stat = os.fstat(writer.root_fd)
        if (stat.st_dev, stat.st_ino) != (
            expected_storage["service_root"]["device"],
            expected_storage["service_root"]["inode"],
        ):
            raise CaptureV6Error("v6_initialize_detached")
        if dict(observed_storage()) != dict(expected_storage):
            raise CaptureV6Error("v6_initialize_identity_changed")

    checked()
    prior_parent = "/".join(epoch["prior_usage"]["path"].split("/")[:-1])
    writer.mkdir(prior_parent)
    checked()
    for directory in (
        PLAN_DIRECTORY,
        REQUEST_DIRECTORY,
        INTENT_DIRECTORY,
        RAW_DIRECTORY,
        CONFIRMATION_DIRECTORY,
    ):
        names = writer.list_names("")
        if directory not in names:
            checked()
            writer.mkdir(directory)
            checked()
    rows = {
        EPOCH_PATH: canonical_json(epoch),
        POLICY_PATH: canonical_json(policy),
        EVENTS_PATH: b"",
        epoch["prior_usage"]["path"]: prior_usage_bytes,
    }
    genesis_core = {
        "schema": GENESIS_SCHEMA,
        "schema_version": 6,
        "epoch_sha256": epoch["epoch_sha256"],
        "policy_sha256": policy["policy_sha256"],
        "selector_sha256": selector_sha256,
        "storage": dict(expected_storage),
        "cursor_in": 0,
        "reset_reason": "explicit_new_v6_sampling_epoch",
    }
    genesis = {**genesis_core, "genesis_sha256": sha256(canonical_json(genesis_core))}
    rows[GENESIS_PATH] = canonical_json(genesis)
    for path, raw in rows.items():
        checked()
        try:
            writer.create(path, raw)
        except FileExistsError:
            if writer.read_bytes(path, max_bytes=max(len(raw), 1)) != raw:
                raise CaptureV6Error("v6_initialize_conflicting_existing_file") from None
        checked()


def verify_local_journal(
    writer: DescriptorWriter,
    *,
    epoch: dict[str, Any],
    policy: dict[str, Any],
    selector: Callable[..., dict[str, Any]],
    selector_sha256: str,
    expected_storage: Mapping[str, Any],
    observed_storage: Callable[[], Mapping[str, Any]],
    now_ns: int,
    inherited_usage_rows: Sequence[Mapping[str, Any]],
    resource_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Independently replay epoch, plans, request wires, outcomes and budget state."""
    verify_epoch(epoch)
    policy_v6.verify_policy_hash(policy)
    pinned_prior_raw, pinned_prior_rows = _load_pinned_prior_usage(writer, epoch)
    if (
        epoch["policy_sha256"] != policy["policy_sha256"]
        or epoch["storage"] != dict(expected_storage)
        or selector_sha256 != epoch["selector_sha256"]
        or canonical_json(list(inherited_usage_rows)) != pinned_prior_raw
    ):
        raise CaptureV6Error("v6_replay_epoch_binding")
    resources = dict(resource_snapshot)
    if (
        set(resources) != {"free_bytes", "resident_bytes"}
        or type(resources["free_bytes"]) is not int
        or type(resources["resident_bytes"]) is not int
        or resources["free_bytes"] < policy_v6.MIN_FREE_BYTES
        or resources["resident_bytes"] > policy_v6.MAX_RESIDENT_BYTES
    ):
        raise CaptureV6Error("v6_replay_resource_cap")
    writer_stat = os.fstat(writer.root_fd)
    if (writer_stat.st_dev, writer_stat.st_ino) != (
        expected_storage["service_root"]["device"],
        expected_storage["service_root"]["inode"],
    ) or dict(observed_storage()) != dict(expected_storage):
        raise CaptureV6Error("v6_replay_storage_identity")
    if (
        _object(writer.read_bytes(EPOCH_PATH, max_bytes=128 * 1024), "v6_epoch_file") != epoch
        or _object(writer.read_bytes(POLICY_PATH, max_bytes=512 * 1024), "v6_policy_file") != policy
    ):
        raise CaptureV6Error("v6_replay_source_file_mismatch")
    genesis = _object(writer.read_bytes(GENESIS_PATH, max_bytes=128 * 1024), "v6_genesis_invalid")
    expected_genesis = {
        "schema": GENESIS_SCHEMA,
        "schema_version": 6,
        "epoch_sha256": epoch["epoch_sha256"],
        "policy_sha256": policy["policy_sha256"],
        "selector_sha256": selector_sha256,
        "storage": dict(expected_storage),
        "cursor_in": 0,
        "reset_reason": "explicit_new_v6_sampling_epoch",
    }
    if set(genesis) != set(expected_genesis) | {"genesis_sha256"}:
        raise CaptureV6Error("v6_genesis_shape")
    genesis_hash = genesis.pop("genesis_sha256")
    if genesis != expected_genesis or genesis_hash != sha256(canonical_json(expected_genesis)):
        raise CaptureV6Error("v6_genesis_binding")

    events = replay_events(writer.read_bytes(EVENTS_PATH, max_bytes=64 * 1024 * 1024))
    states = usage(events, now_ns=now_ns)
    plans: dict[int, dict[str, Any]] = {}
    slots: dict[tuple[int, str], str] = {}
    cursor = 0
    last_close = epoch["first_bucket_close_ns"] - policy_v6.BUCKET_NS
    for event in events:
        fields = event["fields"]
        if event["kind"] == "gap":
            close = fields.get("close_ns")
            if (
                type(close) is not int
                or close != last_close + policy_v6.BUCKET_NS
                or fields.get("selected_pool_ids") is not None
                or fields.get("cursor_in") != cursor
                or fields.get("cursor_out") != cursor
                or fields.get("cursor_unchanged") is not True
            ):
                raise CaptureV6Error("v6_gap_replay_mismatch")
            last_close = close
        elif event["kind"] == "plan":
            close = fields.get("close_ns")
            if type(close) is not int or close != last_close + policy_v6.BUCKET_NS:
                raise CaptureV6Error("v6_plan_sequence")
            path = f"{PLAN_DIRECTORY}/{close}.json"
            raw = writer.read_bytes(path, max_bytes=256 * 1024)
            plan = _object(raw, "v6_plan_json")
            prior_retention, prior_evidence = _prior_selector_inputs(
                writer, events[: event["seq"]], close_ns=close
            )
            selected = selector(
                policy["population"],
                source_pin=policy_v6.SOURCE_PIN,
                bucket_close_ns=close,
                cursor=cursor,
                prior_retention=prior_retention,
                prior_evidence=prior_evidence,
            )
            if selected != scout.plan_bucket(
                policy["population"],
                source_pin=policy_v6.SOURCE_PIN,
                bucket_close_ns=close,
                cursor=cursor,
                prior_retention=prior_retention,
                prior_evidence=prior_evidence,
            ):
                raise CaptureV6Error("v6_selector_implementation_mismatch")
            rebuilt = build_plan(
                policy,
                close_ns=close,
                cursor=cursor,
                prior_retention=prior_retention,
                prior_evidence=prior_evidence,
                selector_sha256=selector_sha256,
                expected_selector_sha256=selector_sha256,
            )
            if (
                plan != rebuilt
                or fields.get("plan_path") != path
                or fields.get("plan_sha256") != rebuilt["plan_sha256"]
                or fields.get("plan_file_sha256") != sha256(raw)
                or fields.get("cursor_in") != cursor
                or fields.get("cursor_out") != rebuilt["cursor_out"]
                or fields.get("selected_pool_ids") != rebuilt["selected_pool_ids"]
            ):
                raise CaptureV6Error("v6_plan_replay_mismatch")
            plans[close] = plan
            for pool_id in plan["selected_pool_ids"]:
                slots[(close, pool_id)] = "unspent"
            cursor = plan["cursor_out"]
            last_close = close

    expected_request_paths: set[str] = set()
    expected_requests_by_id: dict[str, dict[str, Any]] = {}
    for close, plan in plans.items():
        for pool_id in plan["selected_pool_ids"]:
            request = request_for_pool(policy, pool_id, close)
            expected_requests_by_id[request["request_id"]] = request
    intent_fields_by_id = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "intent"
    }
    abandonment_fields_by_id = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "request_abandoned"
    }
    for event in events:
        if event["kind"] != "request_abandoned":
            continue
        fields = event["fields"]
        request_id = fields["request_id"]
        expected_request = expected_requests_by_id.get(request_id)
        if (
            expected_request is None
            or fields.get("close_ns") != expected_request["bucket_close_ns"]
            or fields.get("pool_id") != expected_request["pool_id"]
            or fields.get("request_path") != f"{REQUEST_DIRECTORY}/{request_id}.json"
            or fields.get("request_sha256") != sha256(canonical_json(expected_request))
            or event["at_ns"] < fields["close_ns"] + scout.DISPATCH_END_EXCLUSIVE_NS
        ):
            raise CaptureV6Error("v6_request_abandonment_binding")
        request_path = fields["request_path"]
        try:
            request_raw = writer.read_bytes(request_path, max_bytes=64 * 1024)
        except Exception as exc:
            raise CaptureV6Error("v6_request_abandonment_file_missing") from exc
        if (
            request_raw != canonical_json(expected_request)
            or sha256(request_raw) != fields["request_sha256"]
        ):
            raise CaptureV6Error("v6_request_abandonment_file_binding")
        expected_request_paths.add(request_path)
    for request_id, _row in states["rows_by_request"].items():
        intent = intent_fields_by_id[request_id]
        plan = plans.get(intent["close_ns"])
        if plan is None or intent["pool_id"] not in plan["selected_pool_ids"]:
            raise CaptureV6Error("v6_intent_plan_join")
        request_path = f"{REQUEST_DIRECTORY}/{request_id}.json"
        request_raw = writer.read_bytes(request_path, max_bytes=64 * 1024)
        expected_request = expected_requests_by_id.get(request_id)
        if (
            expected_request is None
            or _object(request_raw, "v6_request_file") != expected_request
            or intent.get("request_path") != request_path
            or intent.get("request_sha256") != sha256(request_raw)
            or intent.get("plan_sha256") != plan["plan_sha256"]
        ):
            raise CaptureV6Error("v6_intent_request_binding")
        expected_request_paths.add(request_path)

    # The writer publishes the deterministic request file just before the intent.
    # A crash in that interval is safely recoverable because no dispatch can precede
    # the durable intent; accept only exact requests for a planned unspent slot.
    orphan_request_ids: set[str] = set()
    for name in writer.list_names(REQUEST_DIRECTORY):
        if not name.endswith(".json"):
            raise CaptureV6Error("v6_request_file_name")
        request_id = name[:-5]
        request_path = f"{REQUEST_DIRECTORY}/{name}"
        expected_request = expected_requests_by_id.get(request_id)
        request_raw = writer.read_bytes(request_path, max_bytes=64 * 1024)
        if expected_request is None or request_raw != canonical_json(expected_request):
            raise CaptureV6Error("v6_orphan_request_invalid")
        if request_id not in intent_fields_by_id:
            orphan_request_ids.add(request_id)
        expected_request_paths.add(request_path)

    response_paths: set[str] = set()
    dispatch_times: list[int] = []
    for event in events:
        fields, kind = event["fields"], event["kind"]
        if kind in {"intent", "missed"}:
            key = (fields.get("close_ns"), fields.get("pool_id"))
            if key not in slots or slots[key] != "unspent":
                raise CaptureV6Error("v6_slot_duplicate_or_unselected")
            slots[key] = kind
        if kind == "dispatch":
            request_id = fields.get("request_id")
            row = states["rows_by_request"].get(request_id)
            intent = next(
                (
                    e["fields"]
                    for e in events
                    if e["kind"] == "intent" and e["fields"].get("request_id") == request_id
                ),
                None,
            )
            started = fields.get("request_started_ns")
            if (
                row is None
                or intent is None
                or type(started) is not int
                or started < intent["intent_ns"]
                or not intent["close_ns"] + scout.DISPATCH_EARLIEST_NS
                <= started
                < intent["close_ns"] + scout.REQUEST_START_END_EXCLUSIVE_NS
                or set(fields)
                != {
                    "request_id",
                    "request_started_ns",
                    "monotonic_started_ns",
                    "worker_pid",
                    "worker_pgid",
                    "worker_birth_id",
                    "worker_executable_path",
                    "worker_uid",
                    "attempt_nonce",
                    "worker_source_sha256",
                }
                or type(fields.get("worker_pid")) is not int
                or fields["worker_pid"] <= 0
                or fields.get("worker_pgid") != fields.get("worker_pid")
                or type(fields.get("worker_birth_id")) is not str
                or re.fullmatch(r"[0-9]+\.[0-9]{6}", fields["worker_birth_id"]) is None
                or type(fields.get("worker_executable_path")) is not str
                or not os.path.isabs(fields["worker_executable_path"])
                or ".." in fields["worker_executable_path"].split("/")
                or type(fields.get("worker_uid")) is not int
                or fields["worker_uid"] < 0
                or type(fields.get("attempt_nonce")) is not str
                or re.fullmatch(r"[0-9a-f]{32}", fields["attempt_nonce"]) is None
                or not _sha(fields.get("worker_source_sha256"))
            ):
                raise CaptureV6Error("v6_dispatch_intent_join")
            dispatch_times.append(started)
        elif kind == "outcome":
            request_id = fields.get("request_id")
            intent = next(
                (
                    e["fields"]
                    for e in events
                    if e["kind"] == "intent" and e["fields"].get("request_id") == request_id
                ),
                None,
            )
            dispatch = next(
                (
                    e["fields"]
                    for e in events
                    if e["kind"] == "dispatch" and e["fields"].get("request_id") == request_id
                ),
                None,
            )
            if intent is None or (
                dispatch is None
                and (
                    fields.get("status") != "possibly_spent_unresolved"
                    or fields.get("raw_path") is not None
                )
            ):
                raise CaptureV6Error("v6_outcome_dispatch_join")
            if fields.get("status") == "possibly_spent_unresolved":
                if (
                    fields.get("receipt_ns") is not None
                    or fields.get("monotonic_receipt_ns") is not None
                    or type(fields.get("recovery_ns")) is not int
                    or fields.get("availability_ns") != fields.get("recovery_ns")
                ):
                    raise CaptureV6Error("v6_recovery_time_shape")
                if dispatch is None:
                    if (
                        fields.get("quiescence_proof") is not None
                        or fields.get("quiescence_proof_sha256") is not None
                    ):
                        raise CaptureV6Error("v6_unstarted_recovery_proof")
                else:
                    proof = _verify_quiescence_proof(
                        fields.get("quiescence_proof"),
                        request_id=request_id,
                        dispatch=dispatch,
                    )
                    if (
                        sha256(canonical_json(proof)) != fields.get("quiescence_proof_sha256")
                        or fields["recovery_ns"] < proof["quiesced_ns"]
                    ):
                        raise CaptureV6Error("v6_recovery_proof_hash_or_time")
            elif (
                fields.get("recovery_ns") is not None
                or fields.get("quiescence_proof") is not None
                or fields.get("quiescence_proof_sha256") is not None
            ):
                raise CaptureV6Error("v6_nonrecovery_outcome_shape")
            if (
                fields.get("availability_ns", 0)
                >= intent["close_ns"] + scout.AVAILABILITY_END_EXCLUSIVE_NS
            ):
                if fields.get("timely") is not False:
                    raise CaptureV6Error("v6_late_availability_claim")
            raw_path = fields.get("raw_path")
            if raw_path is None:
                if (
                    fields.get("raw_sha256") is not None
                    or fields.get("raw_bytes") != 0
                    or fields.get("status") in {"received", "empty_response"}
                ):
                    raise CaptureV6Error("v6_outcome_empty_raw_shape")
            else:
                expected_path = f"{RAW_DIRECTORY}/{request_id}.bin"
                raw = writer.read_bytes(expected_path, max_bytes=policy_v6.MAX_RESPONSE_BYTES)
                if (
                    raw_path != expected_path
                    or fields.get("raw_sha256") != sha256(raw)
                    or fields.get("raw_bytes") != len(raw)
                ):
                    raise CaptureV6Error("v6_raw_capture_binding")
                if fields.get("status") == "possibly_spent_unresolved":
                    if (
                        fields.get("receipt_ns") is not None
                        or fields.get("monotonic_receipt_ns") is not None
                        or fields.get("eligible_new_open_count") != 0
                        or fields.get("charged_raw_bytes") != policy_v6.MAX_RESPONSE_BYTES
                    ):
                        raise CaptureV6Error("v6_unresolved_raw_claim")
                else:
                    close = intent["close_ns"]
                    try:
                        bars = parse_response(raw, close, fields["receipt_ns"])
                    except (KeyError, TypeError, ValueError, OverflowError) as exc:
                        if fields.get("status") != "malformed_response":
                            raise CaptureV6Error("v6_raw_response_parse") from exc
                        bars = []
                    expected_status = "received" if bars else "empty_response"
                    if (
                        fields.get("status") in {"received", "empty_response"}
                        and fields.get("status") != expected_status
                    ):
                        raise CaptureV6Error("v6_raw_response_status")
                    eligible = sum(row["disposition"] == "eligible_new_open" for row in bars)
                    if fields.get("eligible_new_open_count") != eligible:
                        raise CaptureV6Error("v6_raw_eligible_count")
                response_paths.add(expected_path)
            if fields.get("physical_bytes") != policy_v6.PHYSICAL_RESERVE_PER_INTENT:
                raise CaptureV6Error("v6_physical_reserve_accounting")
        elif kind == "confirmation":
            outcome = next(
                (
                    e
                    for e in events
                    if e["kind"] == "outcome"
                    and e["fields"].get("request_id") == fields.get("request_id")
                ),
                None,
            )
            if (
                outcome is None
                or fields.get("outcome_sha256") != outcome["event_sha256"]
                or fields.get("availability_ns", 0) < outcome["fields"].get("availability_ns", 0)
            ):
                raise CaptureV6Error("v6_confirmation_replay")

    # A raw file can be durable before its outcome event. Preserve it as untrusted
    # orphan evidence only for an already-dispatched request with no durable outcome;
    # recovery charges the maximum response reservation and never parses it as success.
    outcome_by_id = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "outcome"
    }
    dispatch_ids = {
        event["fields"]["request_id"] for event in events if event["kind"] == "dispatch"
    }
    for name in writer.list_names(RAW_DIRECTORY):
        if not name.endswith(".bin"):
            raise CaptureV6Error("v6_raw_file_name")
        request_id = name[:-4]
        raw_path = f"{RAW_DIRECTORY}/{name}"
        if raw_path in response_paths:
            continue
        outcome = outcome_by_id.get(request_id)
        if (
            request_id not in intent_fields_by_id
            or request_id not in dispatch_ids
            or (outcome is not None and outcome.get("status") != "possibly_spent_unresolved")
            or (outcome is not None and outcome.get("raw_path") != raw_path)
        ):
            raise CaptureV6Error("v6_orphan_raw_invalid")
        if writer.stat(raw_path).st_size > policy_v6.MAX_RESPONSE_BYTES:
            raise CaptureV6Error("v6_orphan_raw_oversize")
        response_paths.add(raw_path)

    for index, dispatch_ns in enumerate(dispatch_times):
        previous = dispatch_times[:index]
        if previous and (
            dispatch_ns < max(previous) + scout.MINIMUM_DISPATCH_SPACING_NS
            or sum(dispatch_ns - item < 60 * NS for item in previous)
            >= scout.ROLLING_MINUTE_DISPATCH_CAP
        ):
            raise CaptureV6Error("v6_dispatch_rate_replay")
    for request_id in orphan_request_ids:
        request = expected_requests_by_id[request_id]
        key = (request["bucket_close_ns"], request["pool_id"])
        tombstone = abandonment_fields_by_id.get(request_id)
        if slots.get(key) == "unspent":
            if tombstone is not None:
                raise CaptureV6Error("v6_request_abandonment_without_missed")
        elif slots.get(key) == "missed":
            if tombstone is None:
                raise CaptureV6Error("v6_orphan_request_slot_spent")
        else:
            raise CaptureV6Error("v6_orphan_request_slot_spent")
    for (close, _pool_id), slot_state in slots.items():
        if now_ns >= close + scout.DISPATCH_END_EXCLUSIVE_NS and slot_state == "unspent":
            raise CaptureV6Error("v6_expired_slot_unaccounted")
    inherited = pinned_prior_rows
    inherited_recent = [row for row in inherited if row["intent_ns"] >= now_ns - ROLLING_WINDOW_NS]
    inherited_today = [row for row in inherited if row["intent_ns"] >= now_ns - now_ns % DAY_NS]
    if (
        states["intents_rolling_30d"] + len(inherited_recent)
        > policy_v6.MAX_INTENTS_PER_ROLLING_30D
        or states["raw_bytes_rolling_30d"]
        + sum(row["raw_bytes_upper_bound"] for row in inherited_recent)
        > policy_v6.MAX_RAW_BYTES_PER_ROLLING_30D
        or states["physical_reserved_rolling_30d"]
        + sum(row["physical_bytes_upper_bound"] for row in inherited_recent)
        > policy_v6.MAX_PHYSICAL_RESERVE_PER_ROLLING_30D
        or states["intents_utc_day"] + len(inherited_today) > policy_v6.MAX_INTENTS_PER_UTC_DAY
        or states["raw_bytes_utc_day"]
        + sum(row["raw_bytes_upper_bound"] for row in inherited_today)
        > policy_v6.MAX_RAW_BYTES_PER_UTC_DAY
    ):
        raise CaptureV6Error("v6_local_usage_limit")
    expected_files = {
        EPOCH_PATH,
        POLICY_PATH,
        GENESIS_PATH,
        EVENTS_PATH,
        ".lane.lock",
        epoch["prior_usage"]["path"],
        *(f"{PLAN_DIRECTORY}/{close}.json" for close in plans),
        *expected_request_paths,
        *response_paths,
    }
    if set(writer.list_files()) != expected_files:
        raise CaptureV6Error("v6_exact_file_closure")
    return {
        "schema": "brain-senses-gecko-capture-verify-v6",
        "epoch_sha256": epoch["epoch_sha256"],
        "policy_sha256": policy["policy_sha256"],
        "selector_sha256": selector_sha256,
        "event_count": len(events),
        "event_tail_sha256": events[-1]["event_sha256"] if events else None,
        "plan_count": len(plans),
        "cursor_out": cursor,
        "slots": {f"{close}:{pool}": state for (close, pool), state in slots.items()},
        "usage": states,
        "provider_io": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }


__all__ = [
    "CaptureV6Error",
    "EVENT_SCHEMA",
    "LocalJournalV6",
    "ROOT_PREFIX",
    "build_plan",
    "epoch_document",
    "initialize_existing_root_v6",
    "replay_events",
    "request_for_pool",
    "usage",
    "verify_epoch",
    "verify_local_journal",
]
