"""Independent one-bucket qualification watchdog for a Gecko v4 live canary."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import stat
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for _entry in (REPO, REPO / "src", REPO / "tools"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from memecoin_research_storage import _open_relative_directory, writer_guard  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_host as v2  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_verify as capture_verify  # noqa: E402
from tools import brain_senses_gecko_lane_runtime_v4 as lane_runtime  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v1 as scout_chain  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v2 as usage_inventory  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json  # noqa: E402
from tools.brain_senses_gecko_capture_only_v4_host import (  # noqa: E402
    CONFIG_PATH,
    LAUNCHD_PREFIX,
    first_bucket_close,
    verify_prepared_release,
)

LABEL_PREFIX = "ai.memecoin.brain.senses.gecko-canary-watchdog-v2-"
RECEIPT_ROOT = "services/gecko-market-v1/canary-watchdog-v1"
NS = 1_000_000_000
WATCHDOG_INTERVAL_SECONDS = 15
POST_WINDOW_GRACE_NS = 30 * NS


class WatchdogError(ValueError):
    """Invalid canary configuration or failed independent qualification."""


def label_for(release_id: str) -> str:
    if len(release_id) != 32 or any(ch not in "0123456789abcdef" for ch in release_id):
        raise WatchdogError("release_id_invalid")
    return f"{LABEL_PREFIX}{release_id}"


def deadline_for(bucket_close_ns: int) -> int:
    if type(bucket_close_ns) is not int or bucket_close_ns <= 0:
        raise WatchdogError("bucket_close_invalid")
    return bucket_close_ns + capture_verify.REQUEST_START_END_NS + POST_WINDOW_GRACE_NS


def evaluate_bucket(
    *,
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
    verified: Mapping[str, Any],
    bucket_close_ns: int,
    health: Mapping[str, Any],
) -> dict[str, Any]:
    """Accept six timely, durable attempts with no missing or ambiguous slot."""
    close = bucket_close_ns
    selected = plan.get("selection", {}).get("selected_pool_ids")
    if type(selected) is not list or len(selected) != 6:
        raise WatchdogError("canary_plan_slot_count_invalid")
    sequence = plan.get("sequence")
    intents = [
        row
        for row in state.get("intents", [])
        if isinstance(row.get("request"), Mapping)
        and row["request"].get("plan_sequence") == sequence
    ]
    ids = {row.get("request_id") for row in intents}
    dispatches = [row for row in state.get("dispatches", []) if row.get("request_id") in ids]
    results = [row for row in state.get("results", []) if row.get("request_id") in ids]
    confirmations = [row for row in state.get("confirmations", []) if row.get("request_id") in ids]
    skips = [row for row in state.get("skips", []) if row.get("plan_sequence") == sequence]
    dispatch_ns = sorted(row.get("dispatch_ns") for row in dispatches)
    if (
        len(intents) != 6
        or len(dispatches) != 6
        or len(results) != 6
        or len(confirmations) != 6
        or skips
        or len({row.get("request_id") for row in dispatches}) != 6
        or verified.get("status") != "verified_prefix"
        or verified.get("unresolved_count") != 0
        or verified.get("provider_io") is not False
        or len(dispatch_ns) != 6
        or any(type(at) is not int for at in dispatch_ns)
        or any(at < close + capture_verify.WINDOW_START_NS for at in dispatch_ns)
        or any(at >= close + capture_verify.REQUEST_START_END_NS for at in dispatch_ns)
        or any(
            right - left < capture_verify.MINIMUM_DISPATCH_SPACING_NS
            for left, right in zip(dispatch_ns, dispatch_ns[1:], strict=False)
        )
        or health.get("stop_latched") is not False
    ):
        raise WatchdogError("canary_bucket_qualification_failed")
    return {
        "bucket_close_ns": close,
        "dispatch_count": len(dispatches),
        "dispatch_ns": dispatch_ns,
        "intent_count": len(intents),
        "result_count": len(results),
        "confirmation_count": len(confirmations),
        "missed_dispatch_count": len(skips),
        "eligible_new_open_count": sum(
            1
            for row in results
            if row.get("status") == "received"
            and any(
                isinstance(bar, Mapping) and bar.get("region") == "new_open"
                for bar in row.get("bars", [])
            )
        ),
        "timing_gate": "passed",
        "provider_io": False,
        "economic_admission": False,
        "market_source_qualified": False,
        "training_admission": False,
    }


def render_launchd_plist(release_id: str, bucket_close_ns: int) -> bytes:
    label = label_for(release_id)
    deadline = deadline_for(bucket_close_ns)
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": [
                str(REPO / ".venv/bin/python"),
                "-B",
                str(Path(__file__).resolve()),
                "--scheduled-tick",
                "--release-id",
                release_id,
                "--bucket-close-ns",
                str(bucket_close_ns),
            ],
            "WorkingDirectory": str(REPO),
            "StartInterval": WATCHDOG_INTERVAL_SECONDS,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ProcessType": "Background",
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": "/dev/null",
            "EnvironmentVariables": {"SENSES_CANARY_DEADLINE_NS": str(deadline)},
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def _bootout(label: str) -> None:
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _write_receipt(writer: DescriptorWriter, release_id: str, receipt: Mapping[str, Any]) -> None:
    root = f"{RECEIPT_ROOT}/{release_id}"
    writer.mkdir(root)
    core = dict(receipt)
    core.pop("record_sha256", None)
    payload = canonical_json(
        {**core, "record_sha256": hashlib.sha256(canonical_json(core)).hexdigest()}
    )
    writer.atomic_write(f"{root}/{receipt['bucket_close_ns']}.json", payload, create_only=True)


def _writer_for_guard(guard: Any) -> DescriptorWriter:
    root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)

    def before_write() -> None:
        guard.revalidate()

    try:
        return DescriptorWriter(root_fd, before_write=before_write)
    finally:
        os.close(root_fd)


def run_scheduled_tick(
    release_id: str, bucket_close_ns: int, *, now_ns: int | None = None
) -> dict[str, Any]:
    deadline = deadline_for(bucket_close_ns)
    observed = time.time_ns() if now_ns is None else now_ns
    if observed < deadline:
        collector_label = f"ai.memecoin.brain.senses.gecko-capture-only-v4-{release_id}"
        try:
            labels = v2.loaded_launchd_labels()
            with writer_guard(CONFIG_PATH) as guard:
                guard.revalidate()
        except Exception:
            _bootout(collector_label)
            raise
        return {
            "status": "monitoring" if collector_label in labels else "waiting_for_collector",
            "provider_io": False,
            "deadline_ns": deadline,
        }

    receipt: dict[str, Any] = {
        "schema": "brain-senses-gecko-canary-watchdog-v2",
        "schema_version": 2,
        "release_id": release_id,
        "bucket_close_ns": bucket_close_ns,
        "deadline_ns": deadline,
        "observed_ns": observed,
        "provider_io": False,
        "activation_authorized": False,
    }
    collector_label = f"ai.memecoin.brain.senses.gecko-capture-only-v4-{release_id}"
    try:
        with writer_guard(CONFIG_PATH) as guard:
            writer = _writer_for_guard(guard)
            try:
                receipt_path = f"{RECEIPT_ROOT}/{release_id}/{bucket_close_ns}.json"
                if writer.exists(receipt_path):
                    prior_raw = writer.read_bytes(receipt_path, max_bytes=128 * 1024)
                    prior = json.loads(prior_raw)
                    prior_core = dict(prior) if type(prior) is dict else {}
                    prior_hash = prior_core.pop("record_sha256", None)
                    if (
                        type(prior) is dict
                        and canonical_json(prior) == prior_raw
                        and type(prior_hash) is str
                        and prior_hash == hashlib.sha256(canonical_json(prior_core)).hexdigest()
                        and prior.get("schema")
                        in {
                            "brain-senses-gecko-canary-watchdog-v1",
                            "brain-senses-gecko-canary-watchdog-v2",
                        }
                        and prior.get("release_id") == release_id
                        and prior.get("bucket_close_ns") == bucket_close_ns
                        and prior.get("status") in {"passed", "failed_closed"}
                    ):
                        if prior["status"] == "failed_closed":
                            _bootout(collector_label)
                        return prior
                release = verify_prepared_release(writer, guard, release_id)
                state = v2.read_capture_state(writer, release["root"])
                plan = next(
                    row
                    for row in reversed(release["journal"].records())
                    if row.get("schema") == scout_chain.PLAN_SCHEMA
                    and row.get("bucket_close_ns") == bucket_close_ns
                )
                coordinator = lane_runtime.make_capacity_coordinator(
                    writer, guard, now_ns=lambda: observed
                )
                _ledger_raw, coordinator_events = coordinator._load_ledger()
                terminals = state["results"]
                raw_responses = {
                    row["request_id"]: base64.b64decode(row["raw_body_b64"], validate=True)
                    for row in terminals
                    if isinstance(row.get("raw_body_b64"), str)
                }
                verified = capture_verify.verify_capture_prefix_v2(
                    root=release["root"],
                    binding=release["binding"],
                    policy=release["policy"],
                    chain_records=release["journal"].records(),
                    intents=state["intents"],
                    dispatch_records=state["dispatches"],
                    terminals=terminals,
                    raw_responses=raw_responses,
                    confirmations=state["confirmations"],
                    coordinator_events=coordinator_events,
                    skips=state["skips"],
                )
                proof = evaluate_bucket(
                    plan=plan,
                    state=state,
                    verified=verified,
                    bucket_close_ns=bucket_close_ns,
                    health=release["health"],
                )
                inventory = usage_inventory.build_usage_inventory(writer)
                roots = inventory["root_inventory"]["root_paths"]
                cap = shared_usage.assess_usage(
                    inventory["snapshots"],
                    expected_roots=roots,
                    checked_ns=observed,
                    prospective_request_id=f"watchdog-{release_id}-{bucket_close_ns}",
                )
                if inventory.get("provider_io") is not False or not cap.get("admitted"):
                    raise WatchdogError("canary_shared_usage_replay_failed")
                census_observed_ns = time.time_ns()
                receipt["observed_ns"] = census_observed_ns
                proof["shared_usage_admitted"] = True
                proof["pre_publication_census"] = {
                    "root_inventory_sha256": inventory["root_inventory_sha256"],
                    "observed_ns": census_observed_ns,
                    "includes_this_receipt": False,
                }
                receipt.update({"status": "passed", "proof": proof})
                _write_receipt(writer, release_id, receipt)
            finally:
                writer.close()
    except Exception as exc:
        receipt.update(
            {
                "status": "failed_closed",
                "reason": (
                    exc.args[0] if exc.args and isinstance(exc.args[0], str) else type(exc).__name__
                ),
            }
        )
        _bootout(collector_label)
        try:
            with writer_guard(CONFIG_PATH) as guard:
                writer = _writer_for_guard(guard)
                try:
                    _write_receipt(writer, release_id, receipt)
                finally:
                    writer.close()
        except Exception:
            pass
        raise
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduled-tick", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--bucket-close-ns", type=int, required=True)
    args = parser.parse_args(argv)
    if args.scheduled_tick == args.install:
        parser.error("choose exactly one of --scheduled-tick or --install")
    try:
        if args.install:
            with writer_guard(CONFIG_PATH) as guard:
                writer = _writer_for_guard(guard)
                try:
                    release = verify_prepared_release(writer, guard, args.release_id)
                    if first_bucket_close(release) != args.bucket_close_ns:
                        raise WatchdogError("watchdog_bucket_binding_mismatch")
                    state = v2.read_capture_state(writer, release["root"])
                    if any(
                        state.get(key)
                        for key in ("intents", "dispatches", "results", "confirmations", "skips")
                    ):
                        raise WatchdogError("watchdog_candidate_not_empty")
                    inventory = usage_inventory.build_usage_inventory(writer)
                    cap = shared_usage.assess_usage(
                        inventory["snapshots"],
                        expected_roots=inventory["root_inventory"]["root_paths"],
                        checked_ns=time.time_ns(),
                        prospective_request_id=f"canary-preflight-{args.release_id}-{args.bucket_close_ns}",
                    )
                    if inventory.get("provider_io") is not False or not cap.get("admitted"):
                        raise WatchdogError("watchdog_candidate_usage_preflight_failed")
                finally:
                    writer.close()
            if any(label.startswith(LAUNCHD_PREFIX) for label in v2.loaded_launchd_labels()):
                raise WatchdogError("watchdog_refuses_competing_gecko_agent")
            label = label_for(args.release_id)
            path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
            raw = render_launchd_plist(args.release_id, args.bucket_close_ns)
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            if path.exists():
                raise WatchdogError("watchdog_plist_already_exists")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                view = memoryview(raw)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise WatchdogError("watchdog_plist_write_failed")
                    view = view[count:]
                os.fsync(fd)
            finally:
                os.close(fd)
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            launch_result = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if launch_result.returncode != 0 and label not in v2.loaded_launchd_labels():
                raise WatchdogError("watchdog_bootstrap_failed")
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or path.read_bytes() != raw
                or label not in v2.loaded_launchd_labels()
            ):
                raise WatchdogError("watchdog_install_verification_failed")
            return 0
        tick_result = run_scheduled_tick(args.release_id, args.bucket_close_ns)
    except Exception as exc:
        reason = exc.args[0] if exc.args and isinstance(exc.args[0], str) else type(exc).__name__
        print(json.dumps({"status": "failed_closed", "reason": reason}, sort_keys=True))
        return 2
    print(json.dumps(tick_result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
