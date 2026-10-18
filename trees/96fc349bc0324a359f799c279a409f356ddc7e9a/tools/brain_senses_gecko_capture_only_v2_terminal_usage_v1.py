"""One-root terminal usage reconciliation for the stopped a078 Gecko capture run.

This is deliberately an exact exception for one immutable root. It never writes to that root,
never authorizes a retry, and returns no cursor or dispatch authority.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from memecoin_research_storage import _open_relative_directory
from tools import brain_senses_capacity_coordinator_v2 as capacity
from tools import brain_senses_gecko_capture_only_v2_health_v1 as health
from tools import brain_senses_gecko_capture_only_v2_verify as verifier
from tools import brain_senses_gecko_lane_runtime_v4 as runtime
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

SCHEMA = "brain-senses-gecko-capture-only-v2-terminal-usage-reconciliation-v2"
ROOT = "services/gecko-market-v1/capture-only-v2/a078981054ade7727b437262674a892c"
RELEASE_ID = "a078981054ade7727b437262674a892c"
HEALTH_ROOT = f"services/gecko-market-v1/host-health-v1/{RELEASE_ID}"
LABEL = f"ai.memecoin.brain.senses.gecko-capture-only-v2-{RELEASE_ID}"
RECEIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/evidence/gecko-capture-only-v2-a078-terminal-usage-2026-10-13.json"
)
RECEIPT_SHA256 = "ed60f93ad730c38ff24a4c1a4cc81a9d9444c1ad4a1f1a8fc27fd46b2d7fab5a"

EXPECTED_STORAGE = {
    "root_id": "9428695af6d4f46c08e1a444cdc66de8",
    "volume_uuid": "8f8f1081-9be4-4914-9111-75f1b90255bf",
    "device": 16777242,
    "inode": 400,
}
EXPECTED_RELEASE_SHA256 = "a078981054ade7727b437262674a892c27d8272b69f044708a7947f603380f34"
EXPECTED_POLICY_SHA256 = "a81d5827a8c4d236188b3c13f4af7a3e97e3bf4a949f0c441848ff27401f054a"
EXPECTED_BINDING_SHA256 = "7498efd6c06b2404ddfbdb5248b38ce113c54512cbce41f3494edc532d6f4727"
EXPECTED_SOURCE_SHA256 = "6605c044a8efc85edca58bc5e8dedc8e662c8c2eccacdfb85ab58ca19a658534"
EXPECTED_POPULATION_SHA256 = "938ee425361c27e03f18a123bcdf3d35edbbfafe719c12324c517b408c1cbc8d"
EXPECTED_PLAN_RECORD_SHA256 = "6a743b1632d049f4c8ab1d0e4f3f9bfd079cd3cd9f4632713c205356eff866ba"
EXPECTED_REQUEST_PREFIX_SHA256 = "a4a5e6e4d4dc6e4c33c43537cade22656a1eadbfe15880f8c7e57304865c8366"
EXPECTED_HEALTH_PREFIX_SHA256 = "b597eaed3abffba59e20243996fa24dc40dd59cdfd3d48c74fc695e82c8ad091"
EXPECTED_HEALTH_SNAPSHOT_SHA256 = "e92bc8d59de0791d5aeb2dd5667bd20faa4d0fe06c81dd4a43fef74c248476b2"
EXPECTED_STOP_EVENT_SHA256 = "9f5bf5026b2eee70d11cd9e0a37882792d7a426604ec4a647f34f1db7c435289"
EXPECTED_STOP_NS = 1_791_851_678_845_027_000
EXPECTED_BUCKET_CLOSE_NS = 1_791_851_400_000_000_000
SOURCE_COMMIT = "e7564f0eac31a9e11588c3fdbd4f2b0a1ee90769"
SOURCE_CODE_PATHS = (
    "tools/brain_senses_gecko_capture_only_v2_host.py",
    "tools/brain_senses_gecko_capture_only_v2_runtime.py",
    "tools/brain_senses_gecko_capture_only_v2_service.py",
    "tools/brain_senses_gecko_capture_only_v2_scheduler.py",
    "tools/brain_senses_gecko_capture_only_v2_health_v1.py",
    "tools/brain_senses_gecko_capture_only_v2_verify.py",
    "tools/brain_senses_gecko_scout_chain_v1.py",
    "tools/brain_senses_gecko_rotating_scout_v1.py",
    "tools/brain_senses_gecko_shared_usage_v1.py",
    "tools/brain_senses_gecko_usage_inventory_v1.py",
    "tools/brain_senses_capacity_coordinator_v2.py",
    "tools/brain_senses_external_clock_v1.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_gecko_lane_engine_v3.py",
    "tools/brain_senses_gecko_lane_live_sources_v2.py",
    "tools/brain_senses_gecko_lane_policy_v3.py",
    "tools/brain_senses_gecko_lane_verify_v4.py",
    "tools/brain_senses_gecko_lane_runtime_v4.py",
    "src/memecoin_research_storage/__init__.py",
    "docs/evidence/gecko-v4-usage-reconciliation-2026-10-12.json",
)
EXPECTED_REQUESTS = (
    "eb0899d7d1e02a5791d7b214d57f50c21f1cea72cba2b927b116747d9dc5880a",
    "da456948df052347f2be6849dc6100074472b81675695605b6bc5f93576410dc",
    "08f32a2cf7ad116a097cd9d9ae71d9c31f0412de00304e2c19fce8b3f573cf94",
    "408054bb43b01adf8fed536ffc6dae9d41128a52eb820dc25555cd684540e6e1",
)
EXPECTED_RAW = {
    EXPECTED_REQUESTS[0]: (518, "db541a5c714aff9c9fd7627b5a41586f8cc1e69acc3db621d784d44570891d74"),
    EXPECTED_REQUESTS[1]: (
        1133,
        "6a857c9e1232d3c0ec6f4dff6aad1797a5f1f71dd38d57b1197928edbc4d85cc",
    ),
    EXPECTED_REQUESTS[2]: (880, "1d861aaebc7bb1f71f184628330f9778b6ec943cc817ffad933e7e74eae84d6c"),
    EXPECTED_REQUESTS[3]: (510, "4a4d21f269c3459fc4a2be635e1c06287ddc2d0a7d5ad0635fcb7641d85731d2"),
}
EXPECTED_SLOTS = (
    "DW24TdFi8cYSM85PCx25TDqkqwf94i9yfeChcBTjCSH2",
    "FBpUN77t8zT88aJ5L7hLQybEHq4zCz31Ph2yUWqmCRZ1",
    "6P6pepGxdf3AykMpMC3qkuogepf7uFEavAeCKpbEsStE",
    "BkgieFGyJFDBfKcskkvrb2krAnLHTFgBQ6tsArxeXy4c",
    "2XSMvFDYMdKbd7oP2mLMVqA4boJXB7ffUh4eqhDkD2QP",
    "DkPH8sYPoHKFGHGCqbcJMszxDjsGgRbQgBWuE9iqergx",
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class TerminalUsageError(ValueError):
    """The exact stopped root or its immutable reconciliation receipt no longer matches."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise TerminalUsageError(reason)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _verify_root_census(
    before_paths: Sequence[str],
    before_sha256: str,
    after_paths: Sequence[str],
    after_sha256: str,
    *,
    expected_sha256: str | None = None,
) -> None:
    if expected_sha256 is not None:
        _require(before_sha256 == expected_sha256, "a078_global_root_census_input")
    _require(
        list(before_paths) == list(after_paths) and before_sha256 == after_sha256,
        "a078_global_root_census_changed",
    )


def _source_map(policy: Mapping[str, Any]) -> dict[str, str]:
    source = policy.get("source")
    _require(
        type(source) is dict and type(source.get("source_sha256")) is dict,
        "a078_source_map_missing",
    )
    result = {str(name): str(digest) for name, digest in source["source_sha256"].items()}
    repo = Path(__file__).resolve().parents[1]
    for relative in SOURCE_CODE_PATHS:
        try:
            raw = subprocess.run(
                ["git", "show", f"{SOURCE_COMMIT}:{relative}"],
                cwd=repo,
                capture_output=True,
                check=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise TerminalUsageError("a078_source_commit_unavailable") from exc
        result[relative] = sha256(raw)
    result = dict(sorted(result.items()))
    _require(sha256(canonical_json(result)) == EXPECTED_SOURCE_SHA256, "a078_source_map_mismatch")
    return result


def _json(raw: bytes, reason: str, *, limit: int = 32 * 1024 * 1024) -> dict[str, Any]:
    _require(len(raw) <= limit, f"{reason}_oversize")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TerminalUsageError(f"{reason}_invalid_json") from exc
    _require(type(value) is dict and canonical_json(value) == raw, f"{reason}_noncanonical")
    return cast(dict[str, Any], value)


def _root_lock(writer: DescriptorWriter, root: str) -> int:
    root_fd = _open_relative_directory(writer.root_fd, root)
    try:
        fd = os.open(".runner.lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
    finally:
        os.close(root_fd)
    entry = os.fstat(fd)
    _require(
        stat.S_ISREG(entry.st_mode)
        and entry.st_dev == writer.device
        and entry.st_uid == os.getuid()
        and entry.st_nlink == 1
        and stat.S_IMODE(entry.st_mode) == 0o600,
        "a078_runner_lock_unsafe",
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise TerminalUsageError("a078_writer_active") from exc
    return fd


def launchd_label_loaded() -> bool:
    """Read only the exact legacy label state; an unavailable query is not absence."""
    try:
        result = subprocess.run(
            ["launchctl", "list", LABEL],
            capture_output=True,
            check=False,
            timeout=5,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TerminalUsageError("a078_launchd_state_unavailable") from exc
    if result.returncode == 0:
        return True
    if "Could not find service" in result.stderr:
        return False
    raise TerminalUsageError("a078_launchd_state_unavailable")


def _tree_manifest(
    writer: DescriptorWriter, root: str
) -> tuple[list[dict[str, Any]], list[str], int]:
    files: list[dict[str, Any]] = []
    directories: list[str] = []
    latest_mtime_ns = 0

    def walk(relative: str) -> None:
        nonlocal latest_mtime_ns
        for name in writer.list_names(relative):
            path = f"{relative}/{name}"
            info = writer.stat(path)
            _require(
                info.st_dev == writer.device and info.st_uid == os.getuid(), "a078_tree_identity"
            )
            latest_mtime_ns = max(latest_mtime_ns, info.st_mtime_ns)
            if stat.S_ISDIR(info.st_mode):
                _require(stat.S_IMODE(info.st_mode) == 0o700, "a078_tree_directory_mode")
                directories.append(path[len(root) + 1 :])
                walk(path)
            else:
                _require(
                    stat.S_ISREG(info.st_mode)
                    and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_nlink == 1,
                    "a078_tree_file_identity",
                )
                raw = writer.read_bytes(path, max_bytes=32 * 1024 * 1024)
                files.append(
                    {
                        "path": path[len(root) + 1 :],
                        "size": len(raw),
                        "sha256": sha256(raw),
                    }
                )

    walk(root)
    files.sort(key=lambda row: row["path"])
    directories.sort()
    return files, directories, latest_mtime_ns


def _canonical_rows(
    writer: DescriptorWriter, root: str, path: str, *, max_bytes: int
) -> list[dict[str, Any]]:
    raw = writer.read_bytes(f"{root}/{path}", max_bytes=max_bytes)
    _require(not raw or raw.endswith(b"\n"), "a078_journal_torn")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines(keepends=True):
        row = _json(line, "a078_journal_row", limit=max_bytes)
        rows.append(row)
    return rows


def _read_root(writer: DescriptorWriter, storage: Mapping[str, Any]) -> dict[str, Any]:
    _require(storage == EXPECTED_STORAGE, "a078_storage_pin_mismatch")
    service_fd = _open_relative_directory(writer.root_fd, ROOT)
    try:
        root_stat = os.fstat(service_fd)
        _require(
            root_stat.st_dev == writer.device and root_stat.st_uid == os.getuid(),
            "a078_service_root_identity",
        )
        _require(stat.S_IMODE(root_stat.st_mode) == 0o700, "a078_service_root_mode")
    finally:
        os.close(service_fd)

    store = runtime.DescriptorLaneStore(writer, guard=None, root=ROOT)
    binding = store.read_json("binding.json")
    policy = store.read_json("policy.json")
    chain_names = tuple(store.files("chain"))
    _require(chain_names == ("00000000.json", "00000001.json"), "a078_chain_file_closure")
    chain_records = [store.read_json(f"chain/{name}") for name in chain_names]
    intents = [store.read_json(f"intents/{name}") for name in store.files("intents")]
    terminals = [store.read_json(f"results/{name}") for name in store.files("results")]
    confirmations = [
        store.read_json(f"confirmations/{name}") for name in store.files("confirmations")
    ]
    skips: list[dict[str, Any]] = []
    _require(store.files("skips") == (), "a078_skip_artifacts_present")
    raw_responses: dict[str, bytes] = {}
    for row in terminals:
        body = row.get("raw_body_b64")
        if body is not None:
            _require(type(body) is str, "a078_raw_body_encoding")
            try:
                decoded = base64.b64decode(body, validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise TerminalUsageError("a078_raw_body_encoding") from exc
            _require(base64.b64encode(decoded).decode("ascii") == body, "a078_raw_body_encoding")
            raw_responses[str(row["request_id"])] = decoded
    dispatch_records = _canonical_rows(writer, ROOT, "dispatch.jsonl", max_bytes=16 * 1024 * 1024)
    with writer.lock(capacity.LOCK_PATH, blocking=False):
        coordinator_policy_raw = writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000)
        coordinator_policy = _json(
            coordinator_policy_raw, "a078_coordinator_policy", limit=1_000_000
        )
        _require(
            coordinator_policy.get("root_binding") == binding.get("storage"),
            "a078_coordinator_storage_binding",
        )
        coordinator_raw = writer.read_bytes(capacity.LEDGER_PATH, max_bytes=64_000_000)
        coordinator_events = capacity._verify_ledger(coordinator_raw)
    summary = verifier.verify_capture_prefix_v2(
        root=ROOT,
        binding=binding,
        policy=policy,
        chain_records=chain_records,
        intents=intents,
        dispatch_records=dispatch_records,
        terminals=terminals,
        raw_responses=raw_responses,
        confirmations=confirmations,
        coordinator_events=coordinator_events,
        skips=skips,
    )
    files, directories, latest_mtime_ns = _tree_manifest(writer, ROOT)
    expected_files = {
        ".runner.lock",
        "binding.json",
        "chain/00000000.json",
        "chain/00000001.json",
        "dispatch.jsonl",
        "policy.json",
        *(f"intents/{rid}.json" for rid in EXPECTED_REQUESTS),
        *(f"results/{rid}.json" for rid in EXPECTED_REQUESTS),
        *(f"confirmations/{rid}.json" for rid in EXPECTED_REQUESTS),
    }
    _require({row["path"] for row in files} == expected_files, "a078_service_file_closure")
    _require(
        directories == ["chain", "confirmations", "intents", "results", "skips"],
        "a078_service_directory_closure",
    )
    _require(
        binding.get("root") == ROOT
        and binding.get("release_sha256") == EXPECTED_RELEASE_SHA256
        and binding.get("policy_sha256") == EXPECTED_POLICY_SHA256
        and binding.get("binding_sha256") == EXPECTED_BINDING_SHA256
        and binding.get("storage") == EXPECTED_STORAGE
        and binding.get("provider_io") is False
        and binding.get("activation_authorized") is False,
        "a078_binding_pin_mismatch",
    )
    _require(
        binding.get("release_sha256")[:32] == RELEASE_ID,
        "a078_release_identity_invalid",
    )
    _require(
        policy.get("policy_sha256") == EXPECTED_POLICY_SHA256
        and policy.get("population_sha256") == EXPECTED_POPULATION_SHA256
        and policy.get("source", {}).get("role") == "train"
        and policy.get("source", {}).get("training_eligible") is False,
        "a078_policy_pin_mismatch",
    )
    _require(
        len(chain_records) == 2
        and chain_records[1].get("record_sha256") == EXPECTED_PLAN_RECORD_SHA256
        and chain_records[1].get("bucket_close_ns") == EXPECTED_BUCKET_CLOSE_NS
        and tuple(chain_records[1].get("selection", {}).get("selected_pool_ids", ()))
        == EXPECTED_SLOTS,
        "a078_plan_pin_mismatch",
    )
    source_map = _source_map(policy)
    _require(
        summary.get("status") == "verified_prefix"
        and summary.get("plan_count") == 1
        and summary.get("intent_count") == 4
        and summary.get("dispatch_count") == 4
        and summary.get("terminal_count") == 4
        and summary.get("confirmation_count") == 4
        and summary.get("unresolved_count") == 0
        and summary.get("confirmed_eligible_request_count") == 0
        and not skips
        and tuple(row.get("request_id") for row in summary.get("usage_attempts", ()))
        == EXPECTED_REQUESTS,
        "a078_attempt_census_mismatch",
    )
    attempts_by_id = {row["request_id"]: row for row in summary["usage_attempts"]}
    intent_by_id = {row["request_id"]: row for row in intents}
    terminal_by_id = {row["request_id"]: row for row in terminals}
    dispatch_by_id = {row["request_id"]: row for row in dispatch_records}
    confirmation_by_id = {row["request_id"]: row for row in confirmations}
    coordinator_by_id: dict[str, list[dict[str, Any]]] = {}
    for event in coordinator_events:
        fields = event.get("fields", {})
        if fields.get("reservation_id") in EXPECTED_REQUESTS and fields.get(
            "outcome_path", fields.get("intent_path", "")
        ).startswith(ROOT + "/"):
            coordinator_by_id.setdefault(str(fields["reservation_id"]), []).append(event)
    root_coordinator_events = [
        event
        for event in coordinator_events
        if event.get("fields", {}).get("intent_path", "").startswith(ROOT + "/")
        or event.get("fields", {}).get("outcome_path", "").startswith(ROOT + "/")
    ]
    _require(
        [event.get("seq") for event in root_coordinator_events] == list(range(127, 135)),
        "a078_coordinator_root_event_closure",
    )
    attempt_rows = []
    for request_id in EXPECTED_REQUESTS:
        intent = intent_by_id[request_id]
        terminal = terminal_by_id[request_id]
        dispatch = dispatch_by_id[request_id]
        confirmation = confirmation_by_id[request_id]
        coordinator_rows = coordinator_by_id.get(request_id, [])
        _require(
            terminal.get("status") == "received"
            and terminal.get("http_status") == 200
            and terminal.get("provider_io") is True
            and terminal.get("response_bytes") == EXPECTED_RAW[request_id][0]
            and terminal.get("response_sha256") == EXPECTED_RAW[request_id][1]
            and sha256(raw_responses[request_id]) == EXPECTED_RAW[request_id][1]
            and len(raw_responses[request_id]) == EXPECTED_RAW[request_id][0],
            "a078_raw_capture_mismatch",
        )
        _require(
            type(confirmation.get("availability_ns")) is int
            and confirmation["availability_ns"] >= terminal["receipt_ns"]
            and confirmation["availability_ns"] < EXPECTED_STOP_NS
            and terminal["receipt_ns"] < EXPECTED_STOP_NS
            and dispatch["dispatch_ns"] < EXPECTED_STOP_NS
            and intent["decision_ns"] < EXPECTED_STOP_NS,
            "a078_attempt_after_stop",
        )
        _require(
            len(coordinator_rows) == 2
            and [event["kind"] for event in coordinator_rows] == ["reserved", "settled"]
            and coordinator_rows[0]["fields"].get("intent_path")
            == f"{ROOT}/intents/{request_id}.json"
            and coordinator_rows[1]["fields"].get("outcome_path")
            == f"{ROOT}/results/{request_id}.json"
            and coordinator_rows[1]["fields"].get("outcome") == "received"
            and coordinator_rows[1]["fields"].get("outcome_sha256")
            == sha256(canonical_json(terminal))
            and confirmation.get("coordinator_event_sha256")
            == coordinator_rows[1].get("event_sha256"),
            "a078_coordinator_settlement_mismatch",
        )
        _require(
            attempts_by_id[request_id].get("response_bytes") == EXPECTED_RAW[request_id][0]
            and attempts_by_id[request_id].get("response_sha256") == EXPECTED_RAW[request_id][1],
            "a078_usage_projection_mismatch",
        )
        attempt_rows.append(
            {
                "request_id": request_id,
                "slot_index": intent["request"]["slot_index"],
                "pool_id": intent["request"]["pool_id"],
                "intent_sha256": intent["intent_sha256"],
                "dispatch_ns": dispatch["dispatch_ns"],
                "dispatch_record_sha256": dispatch["record_sha256"],
                "request_started_ns": terminal["request_started_ns"],
                "provider_receipt_ns": terminal["receipt_ns"],
                "local_durable_availability_ns": confirmation["availability_ns"],
                "decision_ns": intent["decision_ns"],
                "raw_bytes": EXPECTED_RAW[request_id][0],
                "raw_sha256": EXPECTED_RAW[request_id][1],
                "result_sha256": sha256(canonical_json(terminal)),
                "confirmation_sha256": confirmation["confirmation_sha256"],
                "coordinator_reserved_event_sha256": coordinator_rows[0]["event_sha256"],
                "coordinator_settled_event_sha256": coordinator_rows[1]["event_sha256"],
            }
        )
    _require(sum(row["raw_bytes"] for row in attempt_rows) == 3041, "a078_raw_total_mismatch")
    request_prefix = sha256(canonical_json(files))
    _require(request_prefix == EXPECTED_REQUEST_PREFIX_SHA256, "a078_request_prefix_pin_mismatch")
    _require(latest_mtime_ns <= EXPECTED_STOP_NS, "a078_post_stop_request_artifact")
    coordinator_stops = [event for event in coordinator_events if event.get("seq") == 134]
    _require(
        len(coordinator_stops) == 1
        and coordinator_stops[0].get("event_sha256")
        == "f5b628882b9ef3e4efa9a8a42eadef280a5abed2826346ad92ea837e78e162ff",
        "a078_coordinator_tail_mismatch",
    )
    return {
        "binding": binding,
        "policy": policy,
        "chain_records": chain_records,
        "source_map": source_map,
        "intents": intents,
        "terminals": terminals,
        "dispatch_records": dispatch_records,
        "confirmations": confirmations,
        "attempts": attempt_rows,
        "coordinator_sequence": 134,
        "coordinator_tail_sha256": coordinator_stops[0]["event_sha256"],
        "request_prefix_sha256": request_prefix,
        "request_files": files,
        "latest_request_mtime_ns": latest_mtime_ns,
        "summary": summary,
    }


def _health_facts(writer: DescriptorWriter, storage: Mapping[str, Any]) -> dict[str, Any]:
    events = health.read_events(writer, release_id=RELEASE_ID)
    snapshot = health.read_snapshot(writer, release_id=RELEASE_ID, storage=storage)
    _require(
        snapshot.get("status") == "stopped"
        and snapshot.get("stop_latched") is True
        and snapshot.get("reason") == "manual_reconciliation_required"
        and snapshot.get("snapshot_sha256") == EXPECTED_HEALTH_SNAPSHOT_SHA256
        and snapshot.get("source_sha256") == EXPECTED_SOURCE_SHA256
        and snapshot.get("request_prefix_sha256") == EXPECTED_REQUEST_PREFIX_SHA256
        and snapshot.get("plan_record_sha256") == EXPECTED_PLAN_RECORD_SHA256
        and snapshot.get("intent_count") == 4
        and snapshot.get("received_count") == 4
        and snapshot.get("durable_count") == 4
        and snapshot.get("selected_count") == 6
        and snapshot.get("missing_count") == 2,
        "a078_health_snapshot_mismatch",
    )
    _require(
        len(events) == 2
        and events[-1].get("to_status") == "stopped"
        and events[-1].get("reason") == "manual_reconciliation_required"
        and events[-1].get("observed_ns") == EXPECTED_STOP_NS
        and events[-1].get("event_sha256") == EXPECTED_STOP_EVENT_SHA256
        and snapshot.get("transition_tail_sha256") == EXPECTED_STOP_EVENT_SHA256,
        "a078_health_stop_mismatch",
    )
    files, directories, _mtime = _tree_manifest(writer, HEALTH_ROOT)
    _require(
        {row["path"] for row in files}
        == {".runner.lock", "events/000000.json", "events/000001.json", "snapshot.json"}
        and directories == ["events"],
        "a078_health_file_closure",
    )
    prefix = sha256(canonical_json(files))
    _require(prefix == EXPECTED_HEALTH_PREFIX_SHA256, "a078_health_prefix_mismatch")
    _require(
        snapshot.get("coordinator_sequence") == 134
        and snapshot.get("coordinator_tail_sha256")
        == "f5b628882b9ef3e4efa9a8a42eadef280a5abed2826346ad92ea837e78e162ff",
        "a078_health_coordinator_tail_mismatch",
    )
    return {"snapshot": snapshot, "events": events, "health_prefix_sha256": prefix}


def build_receipt(
    writer: DescriptorWriter,
    *,
    storage: Mapping[str, Any],
    label_loaded: bool | None = None,
    root_inventory_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic metadata receipt after a fresh no-write root replay."""
    loaded = launchd_label_loaded() if label_loaded is None else label_loaded
    _require(loaded is False, "a078_launch_agent_loaded")
    from tools import brain_senses_gecko_usage_inventory_v1 as inventory

    service_lock = _root_lock(writer, ROOT)
    try:
        root_paths_before, _root_manifest_before, root_census_before = inventory._discover_roots(
            writer
        )
        if root_inventory_sha256 is not None:
            _verify_root_census(
                root_paths_before,
                root_census_before,
                root_paths_before,
                root_census_before,
                expected_sha256=root_inventory_sha256,
            )
        census = _read_root(writer, storage)
        health_lock = _root_lock(writer, HEALTH_ROOT)
        try:
            health_census = _health_facts(writer, storage)
        finally:
            fcntl.flock(health_lock, fcntl.LOCK_UN)
            os.close(health_lock)
        root_paths_after, _root_manifest_after, root_census_after = inventory._discover_roots(
            writer
        )
        _verify_root_census(
            root_paths_before,
            root_census_before,
            root_paths_after,
            root_census_after,
        )
    finally:
        fcntl.flock(service_lock, fcntl.LOCK_UN)
        os.close(service_lock)
    snapshot = health_census["snapshot"]
    selected_ids = tuple(census["chain_records"][1]["selection"]["selected_pool_ids"])
    attempted_ids = {row["request_id"] for row in census["intents"]}
    attempted_pool_ids = {row["request"]["pool_id"] for row in census["intents"]}
    _require(
        len(selected_ids) == 6
        and len(set(selected_ids)) == 6
        and attempted_pool_ids <= set(selected_ids)
        and len(attempted_pool_ids) == 4
        and len(attempted_ids) == 4,
        "a078_slot_partition_invalid",
    )
    return {
        "schema": SCHEMA,
        "schema_version": 2,
        "verifier": {
            "module": "tools.brain_senses_gecko_capture_only_v2_terminal_usage_v1",
            "schema": SCHEMA,
            "version": 1,
        },
        "release_id": RELEASE_ID,
        "request_root": ROOT,
        "health_root": HEALTH_ROOT,
        "storage": dict(EXPECTED_STORAGE),
        "global_root_census": {
            "paths": root_paths_before,
            "sha256": root_census_before,
        },
        "source": {
            "source_commit": SOURCE_COMMIT,
            "source_sha256": census["source_map"],
            "release_sha256": EXPECTED_RELEASE_SHA256,
            "policy_sha256": EXPECTED_POLICY_SHA256,
            "binding_sha256": EXPECTED_BINDING_SHA256,
            "policy_source_sha256": EXPECTED_SOURCE_SHA256,
            "population_sha256": EXPECTED_POPULATION_SHA256,
            "plan_record_sha256": EXPECTED_PLAN_RECORD_SHA256,
        },
        "health_stop": {
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "source_sha256": snapshot["source_sha256"],
            "status": snapshot["status"],
            "reason": snapshot["reason"],
            "stop_latched": snapshot["stop_latched"],
            "observed_ns": snapshot["observed_ns"],
            "transition_tail_sha256": snapshot["transition_tail_sha256"],
            "request_prefix_sha256": snapshot["request_prefix_sha256"],
            "health_prefix_sha256": health_census["health_prefix_sha256"],
        },
        "census": {
            "scheduled_slots": 6,
            "distinct_selected_pools": list(selected_ids),
            "intents": 4,
            "dispatches": 4,
            "http_200_received_results": 4,
            "durable_confirmations": 4,
            "skips": 0,
            "unresolved": 0,
            "no_intent_no_skip_unknown_slots": 2,
            "raw_response_bytes": 3041,
            "attempts": census["attempts"],
            "request_prefix_sha256": census["request_prefix_sha256"],
            "coordinator_sequence": census["coordinator_sequence"],
            "coordinator_tail_sha256": census["coordinator_tail_sha256"],
            "latest_request_mtime_ns": census["latest_request_mtime_ns"],
            "stop_observed_ns": EXPECTED_STOP_NS,
        },
        "exclusion": {
            "launchd_label": LABEL,
            "launchd_loaded": False,
            "shared_service_lock_held_during_replay": True,
            "old_writer_excluded": True,
            "post_stop_request_artifacts": 0,
            "cursor_carryover": False,
            "provider_io_during_reconciliation": False,
        },
        "authority": {
            "retry_authorized": False,
            "dispatch_authorized": False,
            "market_source_qualified": False,
            "training_admission": False,
            "economic_admission": False,
        },
    }


def validate_receipt(receipt: Mapping[str, Any], *, expected: Mapping[str, Any]) -> None:
    """Require exact canonical receipt equality to a freshly reconstructed census."""
    _require(type(receipt) is dict, "a078_receipt_shape")
    _require(set(receipt) == set(expected), "a078_receipt_fields")
    _require(canonical_json(receipt) == canonical_json(expected), "a078_receipt_census_mismatch")


def load_pinned_receipt() -> dict[str, Any]:
    raw = RECEIPT_PATH.read_bytes()
    _require(bool(RECEIPT_SHA256) and sha256(raw) == RECEIPT_SHA256, "a078_receipt_hash_mismatch")
    receipt = _json(raw, "a078_receipt")
    _require(
        receipt.get("schema") == SCHEMA and receipt.get("schema_version") == 2,
        "a078_receipt_schema",
    )
    return receipt


def reconcile_stopped_health_root(
    writer: DescriptorWriter,
    *,
    root: str,
    root_inventory_sha256: str,
    storage: Mapping[str, Any],
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a verified-none sidecar only when the exact terminal receipt still replays."""
    _require(root == HEALTH_ROOT, "a078_health_root_not_allowlisted")
    loaded = launchd_label_loaded()
    expected = build_receipt(
        writer,
        storage=storage,
        label_loaded=loaded,
        root_inventory_sha256=root_inventory_sha256,
    )
    pinned = load_pinned_receipt() if receipt is None else dict(receipt)
    validate_receipt(pinned, expected=expected)
    return shared_usage.make_verified_root_snapshot(
        root=HEALTH_ROOT,
        status="verified_none",
        root_inventory_sha256=root_inventory_sha256,
        source_prefix_sha256=expected["health_stop"]["health_prefix_sha256"],
        attempts=[],
    )


__all__ = [
    "EXPECTED_REQUESTS",
    "HEALTH_ROOT",
    "LABEL",
    "RECEIPT_PATH",
    "RECEIPT_SHA256",
    "ROOT",
    "SOURCE_COMMIT",
    "SCHEMA",
    "TerminalUsageError",
    "build_receipt",
    "load_pinned_receipt",
    "reconcile_stopped_health_root",
    "validate_receipt",
]
