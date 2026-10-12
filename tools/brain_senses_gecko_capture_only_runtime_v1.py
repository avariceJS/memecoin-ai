"""Capture-only Gecko host path with a separate plan and activation identity.

This module never delegates authority to the market-qualified v4 release gate.
It replays the frozen four-request pilot as a technical storage predecessor,
then uses the v4 one-shot transport primitive only under a capture-only plan.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_research_storage import _open_relative_directory, writer_guard  # noqa: E402
from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_external_clock_v1 as clock  # noqa: E402
from tools import brain_senses_external_launch_v2 as launch  # noqa: E402
from tools import brain_senses_gecko_capture_only_monitor_v1 as capture_monitor  # noqa: E402
from tools import brain_senses_gecko_capture_only_verify_v1 as capture_verify  # noqa: E402
from tools import brain_senses_gecko_interpreter_handoff_v1 as interpreter_handoff  # noqa: E402
from tools import brain_senses_gecko_lane_engine_v4 as engine  # noqa: E402
from tools import brain_senses_gecko_lane_policy_v4 as offline  # noqa: E402
from tools import brain_senses_gecko_lane_runtime_v4 as v4  # noqa: E402
from tools.brain_senses_external_writer_v1 import (  # noqa: E402
    DescriptorWriter,
    canonical_json,
    sha256,
)

CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
CAPTURE_ID = "9d7d89fc934c46f3a8a9a5b6d7c4e201"
CAPTURE_ROOT_PREFIX = "services/gecko-market-v1/capture-only-v1"
PREDECESSOR_PILOT_ID = "11f28c4965aa427d9658cd3adeadc746"
LABEL = "ai.memecoin.brain.senses.gecko-capture-only-v3"
PLIST_PATH = REPO / "ops/launchd/ai.memecoin.brain.senses.gecko-capture-only-v3.plist"
HANDOFF_PATH = Path.home() / ".config/brain-research/gecko-capture-only-v3-interpreter-handoff.json"
HANDOFF_SOURCE_KEY = "host/gecko-capture-only-v3-interpreter-handoff.json"
PLAN_SCHEMA = "brain-senses-gecko-capture-only-v1-plan"
CALLBACK_SCHEMA = "brain-senses-gecko-capture-only-v1-callback"
RELEASE_SCHEMA = "brain-senses-gecko-capture-only-v1-release"
REVIEW_SCHEMA = "brain-senses-gecko-capture-only-v1-review"
ACTIVATION_SCHEMA = "brain-senses-gecko-capture-only-v1-activation"
PREFLIGHT_SCHEMA = "brain-senses-gecko-capture-only-v1-preflight"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SUBDIRS = ("intents", "captures", "results", "skips", "clocks", "capture-health-events")


class CaptureRuntimeError(RuntimeError):
    """Fail-closed capture-only host gate."""


def source_bindings() -> dict[str, str]:
    names = (
        "tools/brain_senses_gecko_capture_only_runtime_v1.py",
        "tools/brain_senses_gecko_capture_only_verify_v1.py",
        "tools/brain_senses_gecko_capture_only_monitor_v1.py",
        "tools/brain_senses_gecko_interpreter_handoff_v1.py",
        "tests/test_brain_senses_gecko_capture_only_v1.py",
        "ops/launchd/ai.memecoin.brain.senses.gecko-capture-only-v3.plist",
    )
    return {
        **v4.source_bindings(),
        **{name: sha256((REPO / name).read_bytes()) for name in names},
    }


def _service_root(capture_id: str) -> str:
    if not _ID.fullmatch(capture_id):
        raise CaptureRuntimeError("capture_identity_invalid")
    return f"{CAPTURE_ROOT_PREFIX}/{capture_id}"


def _source_map_for_store(writer: DescriptorWriter, guard: Any) -> dict[str, str]:
    guard.revalidate()
    # The v4 source binding contains the imported source bundle manifest digest.
    base = v4._source_bindings_for_store(writer, guard)
    handoff, handoff_raw = interpreter_handoff.load_receipt(
        str(HANDOFF_PATH), owner_uid=os.getuid()
    )
    interpreter_handoff.verify_receipt(
        handoff,
        plist_path=str(PLIST_PATH),
        expected_label=LABEL,
        expected_argv=_expected_argv(CAPTURE_ID),
        owner_uid=os.getuid(),
    )
    guard.revalidate()
    return {**base, **source_bindings(), HANDOFF_SOURCE_KEY: sha256(handoff_raw)}


def compile_capture_plan(
    *,
    capture_id: str,
    storage: Mapping[str, str],
    policy: dict[str, Any],
    sources: Mapping[str, str],
    predecessor: Mapping[str, Any],
    first_bucket_close_ns: int,
) -> dict[str, Any]:
    offline.verify_policy_v4(policy)
    if not _ID.fullmatch(capture_id):
        raise CaptureRuntimeError("capture_identity_invalid")
    if set(storage) != {"root_id", "volume_uuid"}:
        raise CaptureRuntimeError("storage_binding_invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", str(storage["root_id"])):
        raise CaptureRuntimeError("storage_binding_invalid")
    if (
        not isinstance(sources, Mapping)
        or not sources
        or any(
            not isinstance(path, str) or not _SHA.fullmatch(str(digest))
            for path, digest in sources.items()
        )
    ):
        raise CaptureRuntimeError("source_map_invalid")
    expected_predecessor = {
        "kind",
        "pilot_id",
        "service_root",
        "plan_sha256",
        "pilot_receipt_sha256",
        "pilot_prefix_sha256",
        "pilot_bucket_close_ns",
        "technical_status",
        "technical_reason",
        "source_sha256",
    }
    if set(predecessor) != expected_predecessor or predecessor.get("kind") != "sealed_v4_pilot":
        raise CaptureRuntimeError("predecessor_binding_invalid")
    if predecessor.get("technical_status") not in {"passed", "diagnostic_only"} or (
        predecessor.get("technical_status") == "diagnostic_only"
        and predecessor.get("technical_reason") != "pilot_no_eligible_new_open"
    ):
        raise CaptureRuntimeError("predecessor_not_technically_clean")
    if (
        type(first_bucket_close_ns) is not int
        or first_bucket_close_ns <= predecessor["pilot_bucket_close_ns"]
        or first_bucket_close_ns % (900 * offline.NS)
    ):
        raise CaptureRuntimeError("capture_schedule_not_future")
    value: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "schema_version": 1,
        "capture_id": capture_id,
        "service_root": _service_root(capture_id),
        "storage": dict(storage),
        "policy": policy,
        "policy_sha256": policy["policy_sha256"],
        "source_sha256": dict(sorted(sources.items())),
        "predecessor": dict(predecessor),
        "schedule": {
            "first_bucket_close_ns": first_bucket_close_ns,
            "bucket_seconds": 900,
            "window_start_seconds": 60,
            "request_start_deadline_seconds_exclusive": 215,
            "window_end_seconds_exclusive": 240,
            "forward_only": True,
            "no_backfill": True,
            "no_retry": True,
            "calendar_expiry": "none",
        },
        "authority": {
            "capture_authorized": False,
            "market_source_qualified": False,
            "training_admission": False,
            "economic_admission": False,
            "provider_io": False,
        },
        "plan_sha256": "",
    }
    value["plan_sha256"] = sha256(
        canonical_json({k: v for k, v in value.items() if k != "plan_sha256"})
    )
    verify_capture_plan(value)
    return value


def verify_capture_plan(plan: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "schema_version",
        "capture_id",
        "service_root",
        "storage",
        "policy",
        "policy_sha256",
        "source_sha256",
        "predecessor",
        "schedule",
        "authority",
        "plan_sha256",
    }
    if not isinstance(plan, Mapping) or set(plan) != required:
        raise CaptureRuntimeError("capture_plan_shape")
    core = dict(plan)
    claimed = core.pop("plan_sha256")
    if claimed != sha256(canonical_json(core)):
        raise CaptureRuntimeError("capture_plan_hash")
    capture_id = plan["capture_id"]
    storage = plan["storage"]
    if (
        plan["schema"] != PLAN_SCHEMA
        or plan["schema_version"] != 1
        or not _ID.fullmatch(str(capture_id))
        or plan["service_root"] != _service_root(str(capture_id))
        or not isinstance(storage, Mapping)
        or set(storage) != {"root_id", "volume_uuid"}
        or not re.fullmatch(r"[0-9a-f]{32}", str(storage["root_id"]))
        or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(storage["volume_uuid"])
        )
        or not isinstance(plan["source_sha256"], Mapping)
        or dict(plan["source_sha256"]) != dict(sorted(plan["source_sha256"].items()))
        or plan["source_sha256"] != source_bindings_with_source_bundle(plan["source_sha256"])
        or not isinstance(plan["policy"], dict)
        or plan["policy_sha256"] != plan["policy"].get("policy_sha256")
        or plan["authority"]
        != {
            "capture_authorized": False,
            "market_source_qualified": False,
            "training_admission": False,
            "economic_admission": False,
            "provider_io": False,
        }
    ):
        raise CaptureRuntimeError("capture_plan_binding")
    offline.verify_policy_v4(plan["policy"])
    predecessor = plan["predecessor"]
    if (
        not isinstance(predecessor, Mapping)
        or predecessor.get("kind") != "sealed_v4_pilot"
        or predecessor.get("technical_status") not in {"passed", "diagnostic_only"}
        or (
            predecessor.get("technical_status") == "diagnostic_only"
            and predecessor.get("technical_reason") != "pilot_no_eligible_new_open"
        )
        or predecessor.get("pilot_id") != PREDECESSOR_PILOT_ID
        or predecessor.get("service_root") != v4._pilot_root(PREDECESSOR_PILOT_ID)
        or any(
            not _SHA.fullmatch(str(predecessor.get(key)))
            for key in ("plan_sha256", "pilot_receipt_sha256", "pilot_prefix_sha256")
        )
        or not isinstance(predecessor.get("source_sha256"), Mapping)
        or type(predecessor.get("pilot_bucket_close_ns")) is not int
    ):
        raise CaptureRuntimeError("capture_predecessor_binding")
    expected_schedule = {
        "first_bucket_close_ns": predecessor["pilot_bucket_close_ns"] + 900 * offline.NS,
        "bucket_seconds": 900,
        "window_start_seconds": 60,
        "request_start_deadline_seconds_exclusive": 215,
        "window_end_seconds_exclusive": 240,
        "forward_only": True,
        "no_backfill": True,
        "no_retry": True,
        "calendar_expiry": "none",
    }
    schedule = plan["schedule"]
    if (
        not isinstance(schedule, Mapping)
        or set(schedule) != set(expected_schedule)
        or schedule["first_bucket_close_ns"] < expected_schedule["first_bucket_close_ns"]
        or schedule["first_bucket_close_ns"] % (900 * offline.NS)
        or any(
            schedule[key] != value
            for key, value in expected_schedule.items()
            if key != "first_bucket_close_ns"
        )
    ):
        raise CaptureRuntimeError("capture_schedule_binding")


def source_bindings_with_source_bundle(plan_sources: Mapping[str, Any]) -> dict[str, str]:
    """Compare executable pins while retaining the imported manifest pin."""
    expected = source_bindings()
    binding_key = v4.SOURCE_BINDING_KEY
    if set(plan_sources) != {*expected, binding_key, HANDOFF_SOURCE_KEY}:
        raise CaptureRuntimeError("capture_source_map_shape")
    if any(plan_sources.get(path) != digest for path, digest in expected.items()):
        raise CaptureRuntimeError("capture_source_pin_changed")
    manifest = plan_sources.get(binding_key)
    if not _SHA.fullmatch(str(manifest)):
        raise CaptureRuntimeError("capture_source_manifest_pin")
    handoff_digest = plan_sources.get(HANDOFF_SOURCE_KEY)
    if not _SHA.fullmatch(str(handoff_digest)):
        raise CaptureRuntimeError("capture_interpreter_handoff_pin")
    return {**expected, binding_key: str(manifest), HANDOFF_SOURCE_KEY: str(handoff_digest)}


def verify_predecessor(
    store: Any, *, expected_source_sha256: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    plan = store.read_json("plan.json")
    v4.verify_live_plan(plan)
    store.bind_verified_plan(plan)
    store._verify_guard(plan)
    if plan["source_sha256"] != dict(expected_source_sha256):
        raise CaptureRuntimeError("predecessor_source_changed")
    receipt_raw = store.writer.read_bytes(
        store._path("pilot-receipt.json"), max_bytes=8 * 1024 * 1024
    )
    receipt = store.read_json("pilot-receipt.json")
    v4.verify_pilot_receipt(plan, receipt, sha256(receipt_raw))
    live_summary = v4.verify_live_store(store, plan, require_clock=True)
    summary, prefix = v4.verify_pilot_bucket(
        store, plan, receipt["bucket_close_ns"], require_clock=True
    )
    allowed_reason = summary.get("reason") == "pilot_no_eligible_new_open"
    exact_four = (
        summary.get("intents") == 4
        and summary.get("dispatches") == 4
        and summary.get("http_200_captures") == 4
        and summary.get("durable_confirmations") == 4
        and summary.get("skips") == 0
        and summary.get("clock_verified") is True
        and summary.get("verified_store") == "verified_prefix"
    )
    if not exact_four or summary.get("status") != "passed" and not allowed_reason:
        raise CaptureRuntimeError("predecessor_not_capture_clean")
    if live_summary.get("possibly_spent") != 0:
        raise CaptureRuntimeError("predecessor_possibly_spent")
    if receipt["summary"].get("pilot_qualification") != summary:
        raise CaptureRuntimeError("predecessor_qualification_changed")
    return plan, summary, receipt_raw


def predecessor_certificate(
    plan: Mapping[str, Any], summary: Mapping[str, Any], receipt_raw: bytes
) -> dict[str, Any]:
    return {
        "kind": "sealed_v4_pilot",
        "pilot_id": plan["pilot_id"],
        "service_root": plan["service_root"],
        "plan_sha256": plan["plan_sha256"],
        "pilot_receipt_sha256": sha256(receipt_raw),
        "pilot_prefix_sha256": sha256(canonical_json(dict(summary))),
        "pilot_bucket_close_ns": summary["bucket_close_ns"],
        "technical_status": summary["status"],
        "technical_reason": summary["reason"],
        "source_sha256": plan["source_sha256"],
    }


def verify_release(plan: Mapping[str, Any], release: Mapping[str, Any]) -> None:
    verify_capture_plan(plan)
    expected = {
        "schema",
        "schema_version",
        "capture_id",
        "plan_sha256",
        "source_sha256",
        "predecessor_sha256",
        "preflight_sha256",
        "callback_sha256",
        "review_sha256",
        "storage",
        "capture_authorized",
        "market_source_qualified",
        "training_admission",
        "economic_admission",
        "status",
    }
    if not isinstance(release, Mapping) or set(release) != expected:
        raise CaptureRuntimeError("capture_release_shape")
    if (
        release["schema"] != RELEASE_SCHEMA
        or release["schema_version"] != 1
        or release["capture_id"] != plan["capture_id"]
        or release["plan_sha256"] != plan["plan_sha256"]
        or release["source_sha256"] != plan["source_sha256"]
        or release["predecessor_sha256"] != sha256(canonical_json(plan["predecessor"]))
        or not _SHA.fullmatch(str(release["preflight_sha256"]))
        or not _SHA.fullmatch(str(release["callback_sha256"]))
        or not _SHA.fullmatch(str(release["review_sha256"]))
        or release["storage"] != plan["storage"]
        or release["capture_authorized"] is not True
        or release["market_source_qualified"] is not False
        or release["training_admission"] is not False
        or release["economic_admission"] is not False
        or release["status"] != "reviewed_capture_only"
    ):
        raise CaptureRuntimeError("capture_release_binding")


def verify_review(
    plan: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    preflight_sha256: str,
    callback_sha256: str,
) -> None:
    expected = {
        "schema",
        "schema_version",
        "capture_id",
        "plan_sha256",
        "source_sha256",
        "predecessor_sha256",
        "preflight_sha256",
        "callback_sha256",
        "storage",
        "decision_reference",
        "integrator_verification",
        "reviewed_ns",
        "status",
        "capture_authorized",
        "market_source_qualified",
        "training_admission",
        "economic_admission",
        "provider_io",
    }
    if not isinstance(review, Mapping) or set(review) != expected:
        raise CaptureRuntimeError("capture_review_shape")
    if (
        review["schema"] != REVIEW_SCHEMA
        or review["schema_version"] != 1
        or review["capture_id"] != plan["capture_id"]
        or review["plan_sha256"] != plan["plan_sha256"]
        or review["source_sha256"] != plan["source_sha256"]
        or review["predecessor_sha256"] != sha256(canonical_json(plan["predecessor"]))
        or review["preflight_sha256"] != preflight_sha256
        or review["callback_sha256"] != callback_sha256
        or review["storage"] != plan["storage"]
        or review["decision_reference"] != "sol-capture-only-continuous-release-v1"
        or review["integrator_verification"] != "passed"
        or type(review["reviewed_ns"]) is not int
        or review["reviewed_ns"] <= 0
        or review["status"] != "approved_capture_only"
        or review["capture_authorized"] is not True
        or review["market_source_qualified"] is not False
        or review["training_admission"] is not False
        or review["economic_admission"] is not False
        or review["provider_io"] is not False
    ):
        raise CaptureRuntimeError("capture_review_binding")


def _create_release(store: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Seal a release only from a separately supplied, exact review receipt."""
    if store.writer.exists(store._path("activation.json")):
        raise CaptureRuntimeError("capture_release_after_activation")
    preflight_raw = store.writer.read_bytes(
        store._path("capture-preflight.json"), max_bytes=1024 * 1024
    )
    preflight = store.read_json("capture-preflight.json")
    _verify_preflight(plan, preflight)
    callback_raw = store.writer.read_bytes(
        store._path("launchd-callback.json"), max_bytes=1024 * 1024
    )
    callback = store.read_json("launchd-callback.json")
    callback_core = dict(callback)
    callback_sha = callback_core.pop("callback_sha256", None)
    if (
        callback_sha != sha256(canonical_json(callback_core))
        or callback.get("plan_sha256") != plan["plan_sha256"]
        or callback.get("provider_io") is not False
        or callback.get("status") != "no_provider_callback_verified"
        or callback.get("preflight_sha256") != sha256(preflight_raw)
    ):
        raise CaptureRuntimeError("capture_callback_invalid")
    review_raw = store.writer.read_bytes(store._path("capture-review.json"), max_bytes=1024 * 1024)
    review = store.read_json("capture-review.json")
    if canonical_json(review) != review_raw:
        raise CaptureRuntimeError("capture_review_not_canonical")
    verify_review(
        plan,
        review,
        preflight_sha256=sha256(preflight_raw),
        callback_sha256=sha256(callback_raw),
    )
    release: dict[str, Any] = {
        "schema": RELEASE_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "predecessor_sha256": sha256(canonical_json(plan["predecessor"])),
        "preflight_sha256": sha256(preflight_raw),
        "callback_sha256": sha256(callback_raw),
        "review_sha256": sha256(review_raw),
        "storage": plan["storage"],
        "capture_authorized": True,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "status": "reviewed_capture_only",
    }
    verify_release(plan, release)
    store._create_exact("capture-release.json", release)
    return {
        "status": "capture_release_sealed",
        "provider_io": False,
        "release_sha256": sha256(canonical_json(release)),
    }


def _verify_release_review_file(
    store: Any,
    plan: Mapping[str, Any],
    release: Mapping[str, Any],
    *,
    preflight_sha256: str,
    callback_sha256: str,
) -> bytes:
    review_raw = store.writer.read_bytes(store._path("capture-review.json"), max_bytes=1024 * 1024)
    review = store.read_json("capture-review.json")
    if canonical_json(review) != review_raw:
        raise CaptureRuntimeError("capture_review_not_canonical")
    verify_review(
        plan,
        review,
        preflight_sha256=preflight_sha256,
        callback_sha256=callback_sha256,
    )
    if release.get("review_sha256") != sha256(review_raw):
        raise CaptureRuntimeError("capture_review_release_mismatch")
    return cast(bytes, review_raw)


def verify_activation(plan: Mapping[str, Any], activation: Mapping[str, Any]) -> None:
    verify_capture_plan(plan)
    expected = {
        "schema",
        "schema_version",
        "capture_id",
        "plan_sha256",
        "source_sha256",
        "release_sha256",
        "callback_sha256",
        "preflight_sha256",
        "capture_authorized",
        "market_source_qualified",
        "training_admission",
        "economic_admission",
        "provider_io",
    }
    if not isinstance(activation, Mapping) or set(activation) != expected:
        raise CaptureRuntimeError("capture_activation_shape")
    if (
        activation["schema"] != ACTIVATION_SCHEMA
        or activation["schema_version"] != 1
        or activation["capture_id"] != plan["capture_id"]
        or activation["plan_sha256"] != plan["plan_sha256"]
        or activation["source_sha256"] != plan["source_sha256"]
        or not _SHA.fullmatch(str(activation["release_sha256"]))
        or not _SHA.fullmatch(str(activation["callback_sha256"]))
        or not _SHA.fullmatch(str(activation["preflight_sha256"]))
        or activation["capture_authorized"] is not True
        or activation["market_source_qualified"] is not False
        or activation["training_admission"] is not False
        or activation["economic_admission"] is not False
        or activation["provider_io"] is not True
    ):
        raise CaptureRuntimeError("capture_activation_binding")


def _parse_launchctl_print(text: str, *, uid: int) -> tuple[str, list[str]]:
    header = f"gui/{uid}/{LABEL} = {{"
    lines = text.splitlines()
    if not lines or lines[0].strip() != header:
        raise CaptureRuntimeError("capture_launchd_print_label_mismatch")
    path_value: str | None = None
    args: list[str] = []
    in_args = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("path = "):
            path_value = stripped.removeprefix("path = ")
        elif stripped == "arguments = {":
            if in_args:
                raise CaptureRuntimeError("capture_launchd_print_arguments")
            in_args = True
        elif in_args and stripped == "}":
            in_args = False
            break
        elif in_args:
            if not stripped or "\x00" in stripped:
                raise CaptureRuntimeError("capture_launchd_print_arguments")
            args.append(stripped)
    if in_args or path_value is None or not args:
        raise CaptureRuntimeError("capture_launchd_print_incomplete")
    return path_value, args


def launchd_identity(
    capture_id: str, *, expected_handoff_sha256: str | None = None
) -> tuple[int, list[str]]:
    expected = _expected_argv(capture_id)
    uid = os.getuid()
    if os.environ.get("XPC_SERVICE_NAME") != LABEL:
        raise CaptureRuntimeError("capture_launchd_service_missing")
    try:
        receipt, receipt_raw = interpreter_handoff.load_receipt(str(HANDOFF_PATH), owner_uid=uid)
        receipt_digest = sha256(receipt_raw)
        if expected_handoff_sha256 is not None and receipt_digest != expected_handoff_sha256:
            raise CaptureRuntimeError("capture_interpreter_handoff_changed")
        interpreter_handoff.verify_receipt(
            receipt,
            plist_path=str(PLIST_PATH),
            expected_label=LABEL,
            expected_argv=expected,
            owner_uid=uid,
        )
        before_files = interpreter_handoff._file_snapshot(receipt["paths"], owner_uid=uid)
        listing = subprocess.run(
            ["launchctl", "list"], check=False, capture_output=True, text=True, timeout=5
        )
        matches = [
            row.split()
            for row in listing.stdout.splitlines()
            if row.split() and row.split()[-1] == LABEL
        ]
        pid = os.getpid()
        if listing.returncode or len(matches) != 1 or int(matches[0][0]) != pid:
            raise CaptureRuntimeError("capture_launchd_list_mismatch")
        service = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{LABEL}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if service.returncode:
            raise CaptureRuntimeError("capture_launchd_print_failed")
        loaded_path, loaded_argv = _parse_launchctl_print(service.stdout, uid=uid)
        if loaded_path != str(PLIST_PATH) or loaded_argv != expected:
            raise CaptureRuntimeError("capture_launchd_program_binding")
        process = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        process_uid = subprocess.run(
            ["ps", "-p", str(pid), "-o", "uid="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        import shlex

        actual = shlex.split(process.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise CaptureRuntimeError("capture_launchd_identity_unavailable") from exc
    if (
        process.returncode
        or process_uid.returncode
        or process_uid.stdout.strip() != str(uid)
        or len(actual) != len(expected)
        or actual[1:] != expected[1:]
        or sys.argv != expected[2:]
        or sys.executable != receipt["probe"]["sys_executable"]
        or sys.prefix != receipt["probe"]["sys_prefix"]
        or sys.base_prefix != receipt["probe"]["sys_base_prefix"]
        or list(sys.version_info[:3]) != receipt["probe"]["python_version"]
    ):
        raise CaptureRuntimeError("capture_launchd_argv_mismatch")
    proc_path = interpreter_handoff._proc_pidpath(pid)
    if actual[0] != proc_path:
        raise CaptureRuntimeError("capture_launchd_proc_path_mismatch")
    interpreter_handoff.verify_receipt(
        receipt,
        plist_path=str(PLIST_PATH),
        expected_label=LABEL,
        expected_argv=expected,
        owner_uid=uid,
        proc_path=proc_path,
    )
    after_files = interpreter_handoff._file_snapshot(receipt["paths"], owner_uid=uid)
    if before_files != after_files:
        raise CaptureRuntimeError("capture_interpreter_changed_during_callback")
    receipt_after, raw_after = interpreter_handoff.load_receipt(str(HANDOFF_PATH), owner_uid=uid)
    if raw_after != receipt_raw or receipt_after != receipt:
        raise CaptureRuntimeError("capture_interpreter_receipt_changed")
    return pid, expected


def _expected_argv(capture_id: str) -> list[str]:
    return [
        str(REPO / ".venv/bin/python"),
        "-B",
        str(REPO / "tools/brain_senses_gecko_capture_only_runtime_v1.py"),
        "--scheduled-tick",
        "--capture-id",
        capture_id,
    ]


def _fresh_bucket(now_ns: int, first_close_ns: int) -> int | None:
    close = (now_ns // (900 * offline.NS)) * 900 * offline.NS
    if close < first_close_ns:
        close = first_close_ns
    start, end = engine.dispatch_window(close)
    return close if start <= now_ns < end else None


def _wait_until(target_ns: int, now_ns: Callable[[], int]) -> None:
    """Wait in bounded sleeps, then let the caller recheck its dispatch window."""
    while True:
        remaining = target_ns - now_ns()
        if remaining <= 0:
            return
        time.sleep(min(remaining / offline.NS, 1.0))


def _bind_clock_intent(
    intent: dict[str, Any],
    *,
    clock_path: str,
    clock_sha256: str,
    monotonic_ns: Callable[[], int],
) -> dict[str, Any]:
    """Add the independently verified v4 clock-bound intent contract and hash."""
    intent["schema"] = v4.independent_v4.INTENT_SCHEMA_V4
    intent["clock_path"] = clock_path
    intent["clock_sha256"] = clock_sha256
    intent["decision_monotonic_ns"] = monotonic_ns()
    core = dict(intent)
    core.pop("intent_sha256", None)
    intent["intent_sha256"] = sha256(canonical_json(core))
    return intent


def _tech_callback(
    store: Any,
    plan: Mapping[str, Any],
    now_ns: Callable[[], int],
    identity: Callable[[str], tuple[int, list[str]]],
    *,
    identity_result: tuple[int, list[str]] | None = None,
    callback_started_ns: int | None = None,
    callback_monotonic_ns: int | None = None,
) -> dict[str, Any]:
    if store.writer.exists(store._path("activation.json")):
        raise CaptureRuntimeError("callback_after_activation")
    callback_started_ns = now_ns() if callback_started_ns is None else callback_started_ns
    callback_monotonic_ns = (
        time.monotonic_ns() if callback_monotonic_ns is None else callback_monotonic_ns
    )
    pid, argv = identity_result or identity(str(plan["capture_id"]))
    preflight_raw = store.writer.read_bytes(
        store._path("capture-preflight.json"), max_bytes=1024 * 1024
    )
    preflight = store.read_json("capture-preflight.json")
    _verify_preflight(plan, preflight)
    bucket = _fresh_bucket(now_ns(), plan["schedule"]["first_bucket_close_ns"])
    record = {
        "schema": CALLBACK_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "storage": plan["storage"],
        "predecessor_sha256": sha256(canonical_json(plan["predecessor"])),
        "preflight_sha256": sha256(preflight_raw),
        "label": LABEL,
        "pid": pid,
        "argv": argv,
        "callback_started_ns": callback_started_ns,
        "callback_monotonic_ns": callback_monotonic_ns,
        "callback_ns": now_ns(),
        "bucket_close_ns": bucket,
        "provider_io": False,
        "capture_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "status": "no_provider_callback_verified",
    }
    record["callback_sha256"] = sha256(canonical_json(record))
    if store.writer.exists(store._path("launchd-callback.json")):
        existing = store.read_json("launchd-callback.json")
        existing_core = dict(existing)
        existing_sha = existing_core.pop("callback_sha256", None)
        if (
            existing_sha != sha256(canonical_json(existing_core))
            or existing.get("plan_sha256") != plan["plan_sha256"]
        ):
            raise CaptureRuntimeError("capture_callback_conflict")
        return {"status": existing["status"], "provider_io": False, "callback_sha256": existing_sha}
    store._create_exact("launchd-callback.json", record)
    return {
        "status": record["status"],
        "provider_io": False,
        "callback_sha256": record["callback_sha256"],
    }


def _create_activation(store: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    callback_raw = store.writer.read_bytes(
        store._path("launchd-callback.json"), max_bytes=1024 * 1024
    )
    callback = store.read_json("launchd-callback.json")
    callback_core = dict(callback)
    callback_sha = callback_core.pop("callback_sha256", None)
    if (
        callback_sha != sha256(canonical_json(callback_core))
        or callback.get("provider_io") is not False
        or callback.get("capture_id") != plan["capture_id"]
        or callback.get("plan_sha256") != plan["plan_sha256"]
        or callback.get("source_sha256") != plan["source_sha256"]
        or callback.get("status") != "no_provider_callback_verified"
        or callback.get("label") != LABEL
        or callback.get("argv") != _expected_argv(str(plan["capture_id"]))
        or callback.get("capture_authorized") is not False
        or callback.get("market_source_qualified") is not False
        or callback.get("training_admission") is not False
        or callback.get("economic_admission") is not False
    ):
        raise CaptureRuntimeError("capture_callback_invalid")
    preflight_raw = store.writer.read_bytes(
        store._path("capture-preflight.json"), max_bytes=1024 * 1024
    )
    preflight = store.read_json("capture-preflight.json")
    _verify_preflight(plan, preflight)
    release_raw = store.writer.read_bytes(
        store._path("capture-release.json"), max_bytes=1024 * 1024
    )
    release = store.read_json("capture-release.json")
    verify_release(plan, release)
    if (
        callback.get("preflight_sha256") != sha256(preflight_raw)
        or release.get("callback_sha256") != sha256(callback_raw)
        or release.get("preflight_sha256") != sha256(preflight_raw)
    ):
        raise CaptureRuntimeError("capture_release_preflight_binding")
    _verify_release_review_file(
        store,
        plan,
        release,
        preflight_sha256=sha256(preflight_raw),
        callback_sha256=sha256(callback_raw),
    )
    activation = {
        "schema": ACTIVATION_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "release_sha256": sha256(release_raw),
        "callback_sha256": sha256(callback_raw),
        "preflight_sha256": sha256(preflight_raw),
        "capture_authorized": True,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "provider_io": True,
    }
    verify_activation(plan, activation)
    store._create_exact("activation.json", activation)
    return {
        "status": "capture_activation_created",
        "provider_io": False,
        "activation_sha256": sha256(canonical_json(activation)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--initialize", action="store_true")
    modes.add_argument("--scheduled-tick", action="store_true")
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--seal-release", action="store_true")
    modes.add_argument("--activate", action="store_true")
    modes.add_argument("--probe-interpreter-handoff", action="store_true")
    parser.add_argument("--capture-id", default=CAPTURE_ID)
    args = parser.parse_args(argv)
    try:
        if args.probe_interpreter_handoff:
            receipt = interpreter_handoff.build_receipt(
                plist_path=str(PLIST_PATH),
                expected_label=LABEL,
                expected_argv=_expected_argv(CAPTURE_ID),
            )
            print(canonical_json(receipt).decode(), end="")
            return 0
        if not _ID.fullmatch(args.capture_id):
            raise CaptureRuntimeError("capture_identity_invalid")
        result = run_host_mode(
            "initialize"
            if args.initialize
            else "scheduled-tick"
            if args.scheduled_tick
            else "preflight"
            if args.preflight
            else "seal-release"
            if args.seal_release
            else "activate",
            capture_id=args.capture_id,
        )
    except Exception as exc:
        reason = (
            exc.args[0]
            if isinstance(exc, CaptureRuntimeError) and exc.args and isinstance(exc.args[0], str)
            else "capture_host_gate_failure"
        )
        result = {"status": "failed_closed", "reason": reason, "provider_io": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") not in {"failed_closed"} else 2


def run_host_mode(
    mode: str,
    *,
    capture_id: str,
    now_ns: Callable[[], int] = time.time_ns,
    callback_identity: Callable[[str], tuple[int, list[str]]] = launchd_identity,
) -> dict[str, Any]:
    """Locked descriptor-bound entrypoint; test callers inject only clock/identity."""
    if mode not in {
        "initialize",
        "preflight",
        "scheduled-tick",
        "seal-release",
        "activate",
    } or not _ID.fullmatch(capture_id):
        raise CaptureRuntimeError("capture_host_arguments")
    with launch.host_lock(), writer_guard(CONFIG_PATH) as guard:
        data_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
        try:
            writer = DescriptorWriter(data_fd, before_write=guard.revalidate)
        finally:
            os.close(data_fd)
        try:
            root = _service_root(capture_id)
            store = v4.DescriptorLaneStore(writer, guard=guard, root=root)
            if mode == "initialize":
                return _initialize_capture(store, writer, guard, capture_id, now_ns)
            plan = store.read_json("plan.json")
            verify_capture_plan(plan)
            store._verify_guard(plan)
            if plan["source_sha256"] != _source_map_for_store(writer, guard):
                raise CaptureRuntimeError("capture_source_changed")
            predecessor_root = v4._pilot_root(PREDECESSOR_PILOT_ID)
            predecessor = v4.DescriptorLaneStore(writer, guard=guard, root=predecessor_root)
            predecessor_fd = _open_relative_directory(writer.root_fd, predecessor_root)
            try:
                with _shared_read_lock(predecessor_fd):
                    predecessor_plan, summary, receipt_raw = verify_predecessor(
                        predecessor, expected_source_sha256=plan["predecessor"]["source_sha256"]
                    )
                    actual_cert = predecessor_certificate(predecessor_plan, summary, receipt_raw)
                    if actual_cert != plan["predecessor"]:
                        raise CaptureRuntimeError("capture_predecessor_changed")
                    expected_root_id = guard.root_id
                    if plan["storage"] != {
                        "root_id": expected_root_id,
                        "volume_uuid": guard.volume_uuid.lower(),
                    }:
                        raise CaptureRuntimeError("capture_storage_changed")
                    callback_started_ns: int | None = None
                    callback_monotonic_ns: int | None = None
                    verified_identity: tuple[int, list[str]] | None = None
                    if mode == "scheduled-tick":
                        # Prove the actual launchd invocation before opening any
                        # service writer lock or mutating callback/health/journal state.
                        # Keep these samples at invocation entry; identity may take time.
                        callback_started_ns = now_ns()
                        callback_monotonic_ns = time.monotonic_ns()
                        pinned_handoff = plan["source_sha256"][HANDOFF_SOURCE_KEY]
                        if callback_identity is launchd_identity:
                            verified_identity = launchd_identity(
                                capture_id, expected_handoff_sha256=pinned_handoff
                            )
                        else:
                            verified_identity = callback_identity(capture_id)
                    with writer.lock(f"{root}/.runner.lock", blocking=False):
                        _require_no_pending_gecko_reservation(writer, guard, now_ns)
                        if mode == "preflight":
                            return _create_preflight(store, plan, guard, now_ns)
                        if mode == "seal-release":
                            return _create_release(store, plan)
                        if mode == "activate":
                            return _create_activation(store, plan)
                        if not writer.exists(store._path("activation.json")):
                            result = _tech_callback(
                                store,
                                plan,
                                now_ns,
                                callback_identity,
                                identity_result=verified_identity,
                                callback_started_ns=callback_started_ns,
                                callback_monotonic_ns=callback_monotonic_ns,
                            )
                            _publish_health(
                                store,
                                plan,
                                guard,
                                now_ns,
                                status="healthy",
                                reason="none",
                            )
                            return result
                        activation = store.read_json("activation.json")
                        verify_activation(plan, activation)
                        preflight_raw = writer.read_bytes(
                            store._path("capture-preflight.json"), max_bytes=1024 * 1024
                        )
                        callback_raw = writer.read_bytes(
                            store._path("launchd-callback.json"), max_bytes=1024 * 1024
                        )
                        release_raw = writer.read_bytes(
                            store._path("capture-release.json"), max_bytes=1024 * 1024
                        )
                        preflight = store.read_json("capture-preflight.json")
                        _verify_preflight(plan, preflight)
                        release = store.read_json("capture-release.json")
                        verify_release(plan, release)
                        if (
                            activation.get("release_sha256") != sha256(release_raw)
                            or activation.get("callback_sha256") != sha256(callback_raw)
                            or activation.get("preflight_sha256") != sha256(preflight_raw)
                            or release.get("preflight_sha256") != sha256(preflight_raw)
                            or release.get("callback_sha256") != sha256(callback_raw)
                        ):
                            raise CaptureRuntimeError("capture_activation_receipt_mismatch")
                        _verify_release_review_file(
                            store,
                            plan,
                            release,
                            preflight_sha256=sha256(preflight_raw),
                            callback_sha256=sha256(callback_raw),
                        )
                        if verified_identity is None:
                            raise CaptureRuntimeError("capture_launchd_identity_missing")
                        # Dispatch implementation is intentionally isolated from the v4
                        # release gate. The common engine receives only this plan's
                        # frozen policy and descriptor-backed callback surface.
                        try:
                            result = run_capture_bucket(
                                store, plan, activation, guard=guard, now_ns=now_ns
                            )
                        except Exception:
                            _best_effort_integrity_alarm(store, plan, guard, now_ns)
                            raise
                        terminal_statuses = set(result.get("terminal_statuses", []))
                        stopped = result.get("status") in {
                            "spent_unresolved_stop",
                            "manual_review_stop",
                            "cooldown_stop",
                            "capture_bucket_stopped",
                        } or bool(
                            terminal_statuses
                            & (v4.IMMEDIATE_STOP_STATUSES | v4.TRANSIENT_TERMINAL_STATUSES)
                        )
                        if (
                            terminal_statuses
                            & {
                                engine.STATUS_RATE_LIMITED,
                                engine.STATUS_PROVIDER_ERROR,
                                engine.STATUS_TRANSPORT,
                            }
                            or result.get("status") == "cooldown_stop"
                        ):
                            health_reason = "cooldown"
                        elif terminal_statuses & v4.IMMEDIATE_STOP_STATUSES:
                            health_reason = "manual_review"
                        elif stopped:
                            health_reason = "integrity"
                        else:
                            health_reason = "none"
                        _publish_health(
                            store,
                            plan,
                            guard,
                            now_ns,
                            status="stopped" if stopped else "healthy",
                            reason=health_reason,
                            completed_operation=not stopped,
                            recovery_proof_sha256=(
                                sha256(canonical_json(result))
                                if result.get("status") == "spent_unresolved_stop"
                                else None
                            ),
                        )
                        return result
            finally:
                os.close(predecessor_fd)
        finally:
            writer.close()


def run_capture_bucket(
    store: Any,
    plan: Mapping[str, Any],
    activation: Mapping[str, Any],
    *,
    guard: Any,
    now_ns: Callable[[], int],
    fetch_once: Callable[[dict[str, Any], int, int], engine.FetchResult] | None = None,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    wait_until: Callable[[int], None] | None = None,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Run one forward-only bucket under capture activation; never invoke v4 gate."""
    verify_activation(plan, activation)
    store._policy = dict(plan["policy"])
    coordinator = coordinator or v4.make_capacity_coordinator(store.writer, guard, now_ns=now_ns)
    return _run_capture_bucket_with_coordinator(
        store,
        plan,
        activation,
        guard=guard,
        coordinator=coordinator,
        now_ns=now_ns,
        fetch_once=fetch_once,
        monotonic_ns=monotonic_ns,
        wait_until=wait_until,
    )


def _run_capture_bucket_with_coordinator(
    store: Any,
    plan: Mapping[str, Any],
    activation: Mapping[str, Any],
    *,
    guard: Any,
    coordinator: Any,
    now_ns: Callable[[], int],
    fetch_once: Callable[[dict[str, Any], int, int], engine.FetchResult] | None,
    monotonic_ns: Callable[[], int],
    wait_until: Callable[[int], None] | None,
) -> dict[str, Any]:
    verify_activation(plan, activation)
    store._policy = dict(plan["policy"])
    coordinator.recover_service("gecko", intent_exists=lambda writer, path: writer.exists(path))
    store.reconcile_result_confirmations(now_ns)
    capture_verify.verify_capture_prefix(store, plan)
    unresolved = store.intent_request_ids() - {row["request_id"] for row in store.result_rows()}
    if unresolved:
        return {
            "status": "spent_unresolved_stop",
            "provider_io": False,
            "unresolved": sorted(unresolved),
        }
    dispatch_state = store.dispatch_state()
    if dispatch_state is not None and dispatch_state["manual_review_required"]:
        return {"status": "manual_review_stop", "provider_io": False}
    if (
        dispatch_state is not None
        and type(dispatch_state.get("cooldown_until_ns")) is int
        and now_ns() < dispatch_state["cooldown_until_ns"]
    ):
        return {"status": "cooldown_stop", "provider_io": False}
    close_ns = _fresh_bucket(now_ns(), plan["schedule"]["first_bucket_close_ns"])
    if close_ns is None:
        return {"status": "outside_capture_window", "provider_io": False}
    clock_path, clock_sha, clock_record = store.clock_binding(close_ns, now_ns=now_ns)
    clock.validate_clock(clock_record, now_ns=now_ns())
    policy = dict(plan["policy"])
    order = v4.rotated_pool_order(policy, close_ns)
    prior_confirmation = store.latest_confirmation_ns()
    attempted: list[str] = []
    dispatched: list[str] = []
    statuses: list[str] = []
    stop_reason: str | None = None
    for pool_id in order:
        request = engine.request_for_bucket(policy, pool_id, close_ns)
        if store.seen_request(request["request_id"]):
            continue
        now = now_ns()
        start, end = engine.dispatch_window(close_ns)
        due_by = min(end, close_ns + v4.MAX_REQUEST_START_OFFSET_NS)
        if now < start:
            return {"status": "not_due", "provider_io": False, "bucket_close_ns": close_ns}
        if prior_confirmation is not None:
            ready = prior_confirmation + v4.MINIMUM_DURABLE_RESULT_SPACING_NS
            if now < ready:
                _wait_until(ready, now_ns) if wait_until is None else wait_until(ready)
                now = now_ns()
        if now >= due_by or end - now < 25 * offline.NS:
            store.append_skip(engine.make_skip_record(pool_id, close_ns, now, "missed_dispatch"))
            continue
        holder: dict[str, Any] = {}

        def reserve(
            intent: dict[str, Any],
            amount: int,
            _end: int,
            _holder: dict[str, Any] = holder,
            _clock_path: str = clock_path,
            _clock_sha: str = clock_sha,
            _monotonic_ns: Callable[[], int] = monotonic_ns,
        ) -> engine.ReservationResult:
            _bind_clock_intent(
                intent,
                clock_path=_clock_path,
                clock_sha256=_clock_sha,
                monotonic_ns=_monotonic_ns,
            )
            reservation = coordinator.reserve_and_persist_intent(
                service="gecko",
                reservation_id=intent["request_id"],
                request_id=intent["request_id"],
                intent_path=f"{store.root}/intents/{intent['request_id']}.json",
                outcome_path=f"{store.root}/results/{intent['request_id']}.json",
                intent_bytes=canonical_json(intent),
                intent_sha256=sha256(canonical_json(intent)),
                reserve_bytes=amount,
                lane_lease=getattr(coordinator, "_gecko_runtime_lease", None),
            )
            _holder[intent["request_id"]] = reservation
            return engine.ReservationResult("reserved", now_ns())

        def state_gate(
            _request: dict[str, Any],
            at: int,
            _due_by: int = due_by,
            _end: int = end,
            _request_id: str = request["request_id"],
            _clock_record: Mapping[str, Any] = clock_record,
            _monotonic_ns: Callable[[], int] = monotonic_ns,
            _store: Any = store,
        ) -> bool:
            try:
                clock.validate_progress(_clock_record, wall_ns=at, monotonic_ns=_monotonic_ns())
            except clock.ClockError:
                return False
            if at >= _due_by or _end - at < 25 * offline.NS:
                return False
            return cast(bool, v4._within_lane_budget(_store, at, _request_id))

        def persist(
            terminal: dict[str, Any],
            raw: bytes | None,
            _holder: dict[str, Any] = holder,
            _store: Any = store,
            _coordinator: Any = coordinator,
            _now_ns: Callable[[], int] = now_ns,
        ) -> None:
            reservation = _holder.get(terminal["request_id"])
            if reservation is None:
                raise CaptureRuntimeError("terminal_without_reservation")
            terminal["bars"] = v4._runtime_bars(terminal.get("bars", []))
            payload = _store.persist_response_and_result(terminal, raw, _now_ns)
            _coordinator.settle(
                reservation,
                outcome=terminal["status"],
                outcome_bytes=payload,
                outcome_path=reservation.outcome_path,
                ambiguous=terminal["status"] == engine.STATUS_TRANSPORT,
                lane_lease=_coordinator._gecko_runtime_lease,
            )
            _store.append_result_confirmation(terminal["request_id"], sha256(payload), _now_ns())

        from contextlib import contextmanager

        @contextmanager
        def lane() -> Iterator[Any]:
            with coordinator.lane_lock("gecko") as lease:
                old = getattr(coordinator, "_gecko_runtime_lease", None)
                coordinator._gecko_runtime_lease = lease
                try:
                    yield lease
                finally:
                    coordinator._gecko_runtime_lease = old

        fetch = fetch_once or v4._public_fetch_once
        result = engine.execute_one_shot(
            policy,
            request,
            decision_ns=now,
            clock_ns=now_ns,
            lane_lock=lane,
            read_dispatch_state=store.dispatch_state,
            append_dispatch_record=store.append_dispatch_record,
            validate_dispatch_state=state_gate,
            reserve_intent=reserve,
            fetch_once=fetch,
            persist_terminal=persist,
            persist_skip=store.append_skip,
        )
        attempted.append(pool_id)
        statuses.append(str(result.get("status")))
        if result.get("provider_io") is True:
            dispatched.append(pool_id)
        prior_confirmation = store.latest_confirmation_ns()
        state_after = store.dispatch_state()
        if (
            result.get("provider_io") is not True
            or result.get("status") in v4.IMMEDIATE_STOP_STATUSES
            or result.get("status") in v4.TRANSIENT_TERMINAL_STATUSES
            or state_after is not None
            and state_after["manual_review_required"]
        ):
            if result.get("status") in v4.IMMEDIATE_STOP_STATUSES:
                stop_reason = str(result.get("status"))
            elif result.get("status") in v4.TRANSIENT_TERMINAL_STATUSES:
                stop_reason = "transient_provider_failure"
            elif result.get("provider_io") is not True:
                stop_reason = "request_not_dispatched"
            elif state_after is not None and state_after["manual_review_required"]:
                stop_reason = "manual_review"
            break
    capture_verify.verify_capture_prefix(store, plan)
    bucket_intents = sum(
        engine.request_for_bucket(policy, pool_id, close_ns)["request_id"]
        in store.intent_request_ids()
        for pool_id in (row["pool_id"] for row in policy["pools"])
    )
    return {
        "status": "capture_bucket_stopped"
        if stop_reason is not None
        else "capture_bucket_processed",
        "stop_reason": stop_reason,
        "bucket_close_ns": close_ns,
        "attempted_pool_ids": attempted,
        "dispatched_pool_ids": dispatched,
        "terminal_statuses": statuses,
        "missing_pool_count": max(0, 4 - bucket_intents),
        "missing_pool_buckets_unknown": 4 - bucket_intents,
        "provider_io": bool(dispatched),
        "capture_authorized": True,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }


def _initialize_capture(
    store: Any,
    writer: DescriptorWriter,
    guard: Any,
    capture_id: str,
    now_ns: Callable[[], int],
) -> dict[str, Any]:
    """Create a fresh future-only capture root from the fixed sealed pilot."""
    root = _service_root(capture_id)
    try:
        writer.stat(root)
    except FileNotFoundError:
        pass
    else:
        raise CaptureRuntimeError("capture_root_already_exists")
    predecessor = v4.DescriptorLaneStore(
        writer, guard=guard, root=v4._pilot_root(PREDECESSOR_PILOT_ID)
    )
    predecessor_fd = _open_relative_directory(writer.root_fd, predecessor.root)
    try:
        with _shared_read_lock(predecessor_fd):
            predecessor_plan, summary, receipt_raw = verify_predecessor(
                predecessor,
                expected_source_sha256=predecessor.read_json("plan.json")["source_sha256"],
            )
            # Require no pending Gecko capacity reservation before creating a new lane root.
            coordinator = v4.make_capacity_coordinator(writer, guard, now_ns=now_ns)
            with writer.lock(capacity.LOCK_PATH, blocking=True):
                _, ledger = coordinator._load_ledger()
                reservations, terminal = capacity._reservation_state(ledger)
                if _has_pending_gecko(reservations, terminal):
                    raise CaptureRuntimeError("gecko_capacity_reservation_pending")
            policy = v4._compile_pinned_live_policy(writer, guard)
            source_map = _source_map_for_store(writer, guard)
            first_close = ((now_ns() // (900 * offline.NS)) + 1) * 900 * offline.NS
            first_close = max(first_close, summary["bucket_close_ns"] + 900 * offline.NS)
            certificate = predecessor_certificate(predecessor_plan, summary, receipt_raw)
            plan = compile_capture_plan(
                capture_id=capture_id,
                storage={"root_id": guard.root_id, "volume_uuid": guard.volume_uuid.lower()},
                policy=policy,
                sources=source_map,
                predecessor=certificate,
                first_bucket_close_ns=first_close,
            )
            guard.revalidate()
            for directory in (
                CAPTURE_ROOT_PREFIX,
                root,
                *(f"{root}/{name}" for name in _SUBDIRS),
            ):
                writer.mkdir(directory)
                entry = writer.stat(directory)
                if (
                    not stat.S_ISDIR(entry.st_mode)
                    or entry.st_dev != writer.device
                    or entry.st_uid != os.getuid()
                    or stat.S_IMODE(entry.st_mode) != 0o700
                ):
                    raise CaptureRuntimeError("capture_directory_unsafe")
            store.guard.revalidate()
            writer.create(f"{root}/.runner.lock", b"gecko-capture-only-v1-runner\n")
            store._create_exact("plan.json", plan)
            store._policy = dict(policy)
            guard.revalidate()
            return {
                "status": "capture_plan_initialized",
                "provider_io": False,
                "capture_authorized": False,
                "market_source_qualified": False,
                "training_admission": False,
                "economic_admission": False,
                "plan_sha256": plan["plan_sha256"],
                "first_bucket_close_ns": first_close,
            }
    finally:
        os.close(predecessor_fd)


@contextlib.contextmanager
def _shared_read_lock(service_fd: int) -> Iterator[int]:
    """Lock an existing private service runner inode shared, without creating it."""
    try:
        fd = os.open(".runner.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=service_fd)
    except FileNotFoundError as exc:
        raise CaptureRuntimeError("predecessor_lock_missing") from exc
    try:
        entry = os.fstat(fd)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_dev != os.fstat(service_fd).st_dev
            or entry.st_uid != os.getuid()
            or entry.st_nlink != 1
            or stat.S_IMODE(entry.st_mode) != 0o600
        ):
            raise CaptureRuntimeError("predecessor_lock_unsafe")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CaptureRuntimeError("predecessor_writer_active") from exc
        yield fd
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _verify_preflight(plan: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    expected = {
        "schema",
        "schema_version",
        "capture_id",
        "plan_sha256",
        "source_sha256",
        "predecessor_sha256",
        "storage",
        "observed_ns",
        "free_bytes",
        "minimum_free_bytes",
        "provider_io",
        "capture_authorized",
        "status",
        "preflight_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise CaptureRuntimeError("capture_preflight_shape")
    core = dict(value)
    claimed = core.pop("preflight_sha256")
    if (
        value["schema"] != PREFLIGHT_SCHEMA
        or value["schema_version"] != 1
        or value["capture_id"] != plan["capture_id"]
        or value["plan_sha256"] != plan["plan_sha256"]
        or value["source_sha256"] != plan["source_sha256"]
        or value["predecessor_sha256"] != sha256(canonical_json(plan["predecessor"]))
        or value["storage"] != plan["storage"]
        or type(value["observed_ns"]) is not int
        or type(value["free_bytes"]) is not int
        or type(value["minimum_free_bytes"]) is not int
        or value["minimum_free_bytes"] != 58 * 1024**3
        or value["free_bytes"] < value["minimum_free_bytes"]
        or value["provider_io"] is not False
        or value["capture_authorized"] is not False
        or value["status"] != "preflight_passed"
        or claimed != sha256(canonical_json(core))
    ):
        raise CaptureRuntimeError("capture_preflight_binding")


def _create_preflight(
    store: Any, plan: Mapping[str, Any], guard: Any, now_ns: Callable[[], int]
) -> dict[str, Any]:
    path = store._path("capture-preflight.json")
    if store.writer.exists(path):
        existing = store.read_json("capture-preflight.json")
        _verify_preflight(plan, existing)
        return {
            "status": "capture_preflight_already_passed",
            "provider_io": False,
            "preflight_sha256": existing["preflight_sha256"],
        }
    snapshot = guard.revalidate()
    required = 58 * 1024**3
    if snapshot.available_bytes < required:
        raise CaptureRuntimeError("capture_preflight_storage_floor")
    value: dict[str, Any] = {
        "schema": PREFLIGHT_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "predecessor_sha256": sha256(canonical_json(plan["predecessor"])),
        "storage": plan["storage"],
        "observed_ns": now_ns(),
        "free_bytes": snapshot.available_bytes,
        "minimum_free_bytes": required,
        "provider_io": False,
        "capture_authorized": False,
        "status": "preflight_passed",
    }
    value["preflight_sha256"] = sha256(canonical_json(value))
    _verify_preflight(plan, value)
    guard.revalidate()
    store._create_exact("capture-preflight.json", value)
    guard.revalidate()
    return {
        "status": "capture_preflight_created",
        "provider_io": False,
        "preflight_sha256": value["preflight_sha256"],
    }


def _publish_health(
    store: Any,
    plan: Mapping[str, Any],
    guard: Any,
    now_ns: Callable[[], int],
    *,
    status: str,
    reason: str,
    completed_operation: bool = False,
    recovery_proof_sha256: str | None = None,
) -> dict[str, Any]:
    summary = capture_verify.verify_capture_prefix(store, plan)
    coordinator = v4.make_capacity_coordinator(store.writer, guard, now_ns=now_ns)
    with store.writer.lock(capacity.LOCK_PATH, blocking=True):
        ledger_raw, _ = coordinator._load_ledger()
    snapshot = guard.revalidate()
    return cast(
        dict[str, Any],
        capture_monitor.publish(
            store.writer,
            store,
            now_ns=now_ns(),
            root_id=guard.root_id,
            volume_uuid=guard.volume_uuid,
            request_count=summary["intents"],
            result_count=summary["durable_results"],
            request_tail_sha256=summary["request_tail_sha256"],
            result_tail_sha256=summary["result_tail_sha256"],
            coordinator_tail_sha256=sha256(ledger_raw),
            free_bytes=snapshot.available_bytes,
            source_sha256=sha256(canonical_json(plan["source_sha256"])),
            plan_sha256=plan["plan_sha256"],
            stopped_reason=reason if status == "stopped" else None,
            completed_operation=completed_operation,
            recovery_proof_sha256=recovery_proof_sha256,
        ),
    )


def _best_effort_integrity_alarm(
    store: Any,
    plan: Mapping[str, Any],
    guard: Any,
    now_ns: Callable[[], int],
) -> bool:
    """Latch a diagnostic alarm against the last verified tails without advancing them."""
    try:
        prior = capture_monitor.read_snapshot(store)
        if prior is None:
            return False
        guard.revalidate()
        capture_monitor.publish(
            store.writer,
            store,
            now_ns=now_ns(),
            root_id=guard.root_id,
            volume_uuid=guard.volume_uuid,
            request_count=prior["request_count"],
            result_count=prior["result_count"],
            request_tail_sha256=prior["request_tail_sha256"],
            result_tail_sha256=prior["result_tail_sha256"],
            coordinator_tail_sha256=prior["coordinator_tail_sha256"],
            free_bytes=prior["free_bytes"],
            source_sha256=sha256(canonical_json(plan["source_sha256"])),
            plan_sha256=plan["plan_sha256"],
            stopped_reason="integrity",
        )
        return True
    except Exception:
        # A failed guard, corrupt monitor, or detached volume makes the old heartbeat stale.
        return False


def _has_pending_gecko(reservations: Mapping[str, Mapping[str, Any]], terminal: set[str]) -> bool:
    return any(
        row.get("service") == "gecko" and reservation_id not in terminal
        for reservation_id, row in reservations.items()
    )


def _require_no_pending_gecko_reservation(
    writer: DescriptorWriter, guard: Any, now_ns: Callable[[], int]
) -> None:
    coordinator = v4.make_capacity_coordinator(writer, guard, now_ns=now_ns)
    with writer.lock(capacity.LOCK_PATH, blocking=True):
        _, rows = coordinator._load_ledger()
        reservations, terminal = capacity._reservation_state(rows)
        if _has_pending_gecko(reservations, terminal):
            raise CaptureRuntimeError("gecko_capacity_reservation_pending")


if __name__ == "__main__":
    raise SystemExit(main())
