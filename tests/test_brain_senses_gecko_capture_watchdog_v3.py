from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO / "src", REPO / "tools"):
    sys.path.insert(0, str(entry))

from tools import brain_senses_gecko_capture_handoff_supervisor_v1 as handoff  # noqa: E402
from tools import brain_senses_gecko_capture_watchdog_v3 as watchdog  # noqa: E402
from tools.brain_senses_external_writer_v1 import canonical_json  # noqa: E402


def _h(value: str) -> str:
    return value * 64


def _audit(close_ns: int, *, statuses: list[str] | None = None) -> dict[str, Any]:
    statuses = statuses or ["received"] * 6
    ids = [f"{index + 1:064x}" for index in range(6)]
    pools = [f"pool-{index}" for index in range(6)]
    intents = []
    dispatches = []
    results = []
    confirmations = []
    for index, (request_id, pool_id, status) in enumerate(zip(ids, pools, statuses, strict=True)):
        intent_sha = _h(chr(ord("a") + index))
        result = {
            "request_id": request_id,
            "intent_sha256": intent_sha,
            "status": status,
            "http_status": 200,
            "request_started_ns": close_ns + (70 + index * 22) * watchdog.NS,
            "receipt_ns": close_ns + (71 + index * 22) * watchdog.NS,
        }
        intents.append({"request_id": request_id, "pool_id": pool_id, "intent_sha256": intent_sha})
        dispatches.append(
            {
                "request_id": request_id,
                "intent_sha256": intent_sha,
                "dispatch_ns": result["request_started_ns"],
            }
        )
        results.append(result)
        confirmations.append(
            {
                "request_id": request_id,
                "outcome_sha256": watchdog.sha256(canonical_json(result)),
                "availability_ns": close_ns + (72 + index * 22) * watchdog.NS,
            }
        )
    return {
        "capture_status": "verified_prefix",
        "inventory_status": "verified",
        "watchdog_provider_io": False,
        "pending_coordinator_reservations": 0,
        "unresolved_count": 0,
        "stop_reason": None,
        "root_inventory_sha256": _h("1"),
        "plan_sha256": _h("2"),
        "capture_prefix_sha256": _h("3"),
        "capture_verifier_sha256": _h("4"),
        "source_manifest_sha256": _h("5"),
        "release_sha256": _h("6"),
        "plan": {
            "bucket_close_ns": close_ns,
            "selection": {"selected_pool_ids": pools},
        },
        "intents": intents,
        "dispatches": dispatches,
        "results": results,
        "confirmations": confirmations,
        "skips": [],
    }


def test_watchdog_passes_only_exact_six_confirmed_capture_outcomes() -> None:
    close_ns = 1_800_000_000_000_000_000
    receipt = watchdog.evaluate_final_audit(
        bucket_close_ns=close_ns,
        observed_now_ns=close_ns + 331 * watchdog.NS,
        collector_absent=True,
        process_quiescent=True,
        audit=_audit(
            close_ns,
            statuses=["received", "empty_response", "received", "received", "received", "received"],
        ),
    )
    assert receipt["intent_count"] == receipt["confirmation_count"] == 6
    assert receipt["provider_io"] is False
    assert receipt["market_source_qualified"] is False
    assert receipt["receipt_sha256"] == watchdog.sha256(
        canonical_json({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    )


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda audit: audit["results"].pop(), "watchdog_capture_identity_set_invalid"),
        (lambda audit: audit["skips"].append({"slot_index": 5}), "watchdog_capture_has_skips"),
        (
            lambda audit: audit["results"][0].update(status="provider_error", http_status=503),
            "watchdog_capture_attempt_not_timely_or_durable",
        ),
        (
            lambda audit: audit["confirmations"][0].update(outcome_sha256=_h("f")),
            "watchdog_capture_attempt_not_timely_or_durable",
        ),
        (lambda audit: audit.update(unresolved_count=1), "watchdog_independent_replay_failed"),
    ],
)
def test_watchdog_rejects_incomplete_or_mutated_prefix(mutate, reason: str) -> None:
    close_ns = 1_800_000_000_000_000_000
    audit = _audit(close_ns)
    mutate(audit)
    with pytest.raises(watchdog.WatchdogError, match=reason):
        watchdog.evaluate_final_audit(
            bucket_close_ns=close_ns,
            observed_now_ns=close_ns + 331 * watchdog.NS,
            collector_absent=True,
            process_quiescent=True,
            audit=audit,
        )


def test_watchdog_calls_bootout_before_wait_and_replay_and_is_read_only_early() -> None:
    close_ns = 1_800_000_000_000_000_000
    order: list[str] = []
    kwargs = {
        "bucket_close_ns": close_ns,
        "bootout_collector": lambda: order.append("bootout"),
        "await_process_quiescence": lambda: order.append("quiescent") or True,
        "replay_under_locks": lambda: order.append("replay") or _audit(close_ns),
        "publisher": lambda receipt: order.append("publish") or "diagnostics/canary.json",
    }
    early = watchdog.run_watchdog_once(now_ns=close_ns + 329 * watchdog.NS, **kwargs)
    assert early["status"] == "waiting_for_deadline"
    assert order == []
    result = watchdog.run_watchdog_once(now_ns=close_ns + 330 * watchdog.NS, **kwargs)
    assert result["receipt_path"] == "diagnostics/canary.json"
    assert order == ["bootout", "quiescent", "replay", "publish"]


class _FakeWriter:
    def __init__(self) -> None:
        self.rows: dict[str, bytes] = {}

    def list_names(self, root: str) -> list[str]:
        return [path.rsplit("/", 1)[-1] for path in self.rows if path.startswith(root + "/")]

    def atomic_write(self, path: str, payload: bytes, *, create_only: bool) -> None:
        assert create_only is True
        if path in self.rows:
            raise FileExistsError(path)
        self.rows[path] = payload

    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        value = self.rows[path]
        assert len(value) <= max_bytes
        return value


def test_diagnostic_write_is_create_only_idempotent_and_revalidated() -> None:
    close_ns = 1_800_000_000_000_000_000
    receipt = watchdog.evaluate_final_audit(
        bucket_close_ns=close_ns,
        observed_now_ns=close_ns + 330 * watchdog.NS,
        collector_absent=True,
        process_quiescent=True,
        audit=_audit(close_ns),
    )
    writer = _FakeWriter()
    calls = 0

    def revalidate() -> None:
        nonlocal calls
        calls += 1

    path = watchdog.publish_diagnostic_create_only(
        writer, diagnostics_root="diagnostics/canary", receipt=receipt, revalidate=revalidate
    )
    assert path.endswith(f"{close_ns}.json")
    assert (
        watchdog.publish_diagnostic_create_only(
            writer, diagnostics_root="diagnostics/canary", receipt=receipt, revalidate=revalidate
        )
        == path
    )
    assert calls == 4
    assert len(writer.rows) == 1
    writer.rows[path] += b"tamper"
    with pytest.raises(watchdog.WatchdogError, match="watchdog_diagnostic_collision"):
        watchdog.publish_diagnostic_create_only(
            writer, diagnostics_root="diagnostics/canary", receipt=receipt, revalidate=revalidate
        )


def _certificate() -> dict[str, Any]:
    core = {
        "schema": handoff.SCHEMA,
        "schema_version": handoff.VERSION,
        "canary_receipt_sha256": _h("1"),
        "source_manifest_sha256": _h("2"),
        "continuous_release_sha256": _h("3"),
        "continuous_policy_sha256": _h("4"),
        "collector_plist_sha256": _h("5"),
        "watchdog_plist_sha256": _h("6"),
        "storage": {
            "root_id": "a" * 32,
            "volume_uuid": "12345678-1234-5678-1234-567812345678",
            "device": 1,
            "inode": 2,
        },
        "predecessor_root": "services/gecko/capture-only-v2/" + "b" * 32,
        "successor_root": "services/gecko/capture-only-v2/" + "c" * 32,
        "predecessor_cursor": 24,
        "predecessor_tail_sha256": _h("7"),
        "preinit_census_sha256": _h("8"),
        "coordinator_prefix_sha256": _h("9"),
        "continuous_label": "ai.memecoin.brain.senses.gecko-capture-only-v5",
        "continuous_program_arguments": [
            "/python",
            "-B",
            "/repo/runtime_v5.py",
            "--scheduled-tick",
        ],
        "first_bucket_close_ns": 1_800_000_000_000_000_000,
        "created_ns": 1_799_999_000_000_000_000,
    }
    return {**core, "certificate_sha256": handoff.sha256(canonical_json(core))}


def _snapshot(certificate: dict[str, Any], loaded: bool) -> dict[str, Any]:
    return {
        "loaded": loaded,
        "label": certificate["continuous_label"] if loaded else None,
        "program_arguments": certificate["continuous_program_arguments"] if loaded else None,
        "plist_sha256": certificate["collector_plist_sha256"] if loaded else None,
        "source_manifest_sha256": certificate["source_manifest_sha256"] if loaded else None,
        "uid": os.getuid(),
        "pid": 987 if loaded else None,
        "competing_gecko_labels": [],
    }


def test_handoff_bootstrap_is_intent_first_and_restart_idempotent() -> None:
    certificate = _certificate()
    events: list[dict[str, Any]] = []
    order: list[str] = []
    loaded = False

    class _Locks:
        def __enter__(self):
            order.append("locks_enter")

        def __exit__(self, *_args):
            order.append("locks_exit")

    def bootstrap(_label, _arguments):
        nonlocal loaded
        order.append("bootstrap")
        loaded = True

    def run() -> dict[str, Any]:
        return handoff.run_handoff(
            certificate=certificate,
            now_ns=1_799_999_100_000_000_000,
            read_events=lambda: list(events),
            append_event_create_only=lambda event: events.append(dict(event)),
            with_supervisor_lock=lambda: _NoopContext(),
            with_ordered_locks=_Locks,
            reverify_predecessor_and_global_census=lambda _cert: {
                "verified": True,
                "canary_receipt_sha256": certificate["canary_receipt_sha256"],
                "predecessor_tail_sha256": certificate["predecessor_tail_sha256"],
                "preinit_census_sha256": certificate["preinit_census_sha256"],
                "coordinator_prefix_sha256": certificate["coordinator_prefix_sha256"],
                "old_collector_absent": True,
                "process_quiescent": True,
            },
            create_successor_or_verify=lambda cert: {
                "successor_root": cert["successor_root"],
                "successor_verified": True,
                "certificate_sha256": cert["certificate_sha256"],
                "empty_attempt_prefix_verified": not events,
                "attempt_count": 0,
                "root_identity": cert["storage"],
                "global_census_stable": True,
                "provider_io": False,
            },
            launch_snapshot=lambda: _snapshot(certificate, loaded),
            bootstrap_exact=bootstrap,
            prove_absent_means_no_callback=lambda: True,
        )

    first = run()
    assert first["status"] == "loaded_verified"
    assert [row["kind"] for row in events] == ["bootstrap_intent", "loaded_verified"]
    assert order.index("locks_exit") < order.index("bootstrap")
    assert run()["status"] == "already_loaded_verified"
    assert order.count("bootstrap") == 1


def test_handoff_recovers_intent_only_after_proving_launch_absent() -> None:
    certificate = _certificate()
    intent = handoff.make_event(
        kind="bootstrap_intent",
        seq=0,
        at_ns=1_799_999_100_000_000_000,
        certificate_sha256=certificate["certificate_sha256"],
        previous_event_sha256="0" * 64,
    )
    events = [intent]
    loaded = False
    bootstraps = 0

    def bootstrap(_label, _arguments):
        nonlocal loaded, bootstraps
        bootstraps += 1
        loaded = True

    kwargs = {
        "certificate": certificate,
        "now_ns": 1_799_999_200_000_000_000,
        "read_events": lambda: list(events),
        "append_event_create_only": lambda event: events.append(dict(event)),
        "with_supervisor_lock": lambda: _NoopContext(),
        "with_ordered_locks": lambda: _NoopContext(),
        "reverify_predecessor_and_global_census": lambda _cert: {
            "verified": True,
            "canary_receipt_sha256": certificate["canary_receipt_sha256"],
            "predecessor_tail_sha256": certificate["predecessor_tail_sha256"],
            "preinit_census_sha256": certificate["preinit_census_sha256"],
            "coordinator_prefix_sha256": certificate["coordinator_prefix_sha256"],
            "old_collector_absent": True,
            "process_quiescent": True,
        },
        "create_successor_or_verify": lambda cert: {
            "successor_root": cert["successor_root"],
            "successor_verified": True,
            "certificate_sha256": cert["certificate_sha256"],
            "empty_attempt_prefix_verified": False,
            "attempt_count": 0,
            "root_identity": cert["storage"],
            "global_census_stable": True,
            "provider_io": False,
        },
        "launch_snapshot": lambda: _snapshot(certificate, loaded),
        "bootstrap_exact": bootstrap,
        "prove_absent_means_no_callback": lambda: True,
    }
    assert handoff.run_handoff(**kwargs)["status"] == "loaded_verified"
    assert bootstraps == 1
    assert len(events) == 2


def test_handoff_recovers_empty_exact_root_created_before_intent() -> None:
    certificate = _certificate()
    events: list[dict[str, Any]] = []
    loaded = False
    bootstraps = 0

    def bootstrap(_label, _arguments):
        nonlocal loaded, bootstraps
        bootstraps += 1
        loaded = True

    result = handoff.run_handoff(
        certificate=certificate,
        now_ns=1_799_999_100_000_000_000,
        read_events=lambda: list(events),
        append_event_create_only=lambda event: events.append(dict(event)),
        with_supervisor_lock=lambda: _NoopContext(),
        with_ordered_locks=lambda: _NoopContext(),
        reverify_predecessor_and_global_census=lambda _cert: {
            "verified": True,
            "canary_receipt_sha256": certificate["canary_receipt_sha256"],
            "predecessor_tail_sha256": certificate["predecessor_tail_sha256"],
            "preinit_census_sha256": certificate["preinit_census_sha256"],
            "coordinator_prefix_sha256": certificate["coordinator_prefix_sha256"],
            "old_collector_absent": True,
            "process_quiescent": True,
        },
        # This represents an exact, empty root left by a crash before intent.
        create_successor_or_verify=lambda cert: {
            "successor_root": cert["successor_root"],
            "successor_verified": True,
            "certificate_sha256": cert["certificate_sha256"],
            "empty_attempt_prefix_verified": True,
            "attempt_count": 0,
            "root_identity": cert["storage"],
            "global_census_stable": True,
            "provider_io": False,
        },
        launch_snapshot=lambda: _snapshot(certificate, loaded),
        bootstrap_exact=bootstrap,
        prove_absent_means_no_callback=lambda: True,
    )
    assert result["status"] == "loaded_verified"
    assert bootstraps == 1
    assert [row["kind"] for row in events] == ["bootstrap_intent", "loaded_verified"]


def test_handoff_recovers_when_bootstrap_raises_after_job_became_loaded() -> None:
    certificate = _certificate()
    events: list[dict[str, Any]] = []
    loaded = False
    attempts = 0

    def bootstrap_then_raise(_label, _arguments):
        nonlocal loaded, attempts
        attempts += 1
        loaded = True
        raise RuntimeError("synthetic interrupted caller")

    kwargs = {
        "certificate": certificate,
        "now_ns": 1_799_999_100_000_000_000,
        "read_events": lambda: list(events),
        "append_event_create_only": lambda event: events.append(dict(event)),
        "with_supervisor_lock": lambda: _NoopContext(),
        "with_ordered_locks": lambda: _NoopContext(),
        "reverify_predecessor_and_global_census": lambda _cert: {
            "verified": True,
            "canary_receipt_sha256": certificate["canary_receipt_sha256"],
            "predecessor_tail_sha256": certificate["predecessor_tail_sha256"],
            "preinit_census_sha256": certificate["preinit_census_sha256"],
            "coordinator_prefix_sha256": certificate["coordinator_prefix_sha256"],
            "old_collector_absent": True,
            "process_quiescent": True,
        },
        "create_successor_or_verify": lambda cert: {
            "successor_root": cert["successor_root"],
            "successor_verified": True,
            "certificate_sha256": cert["certificate_sha256"],
            "empty_attempt_prefix_verified": not events,
            "root_identity": cert["storage"],
            "global_census_stable": True,
            "provider_io": False,
            "attempt_count": 0,
        },
        "launch_snapshot": lambda: _snapshot(certificate, loaded),
        "bootstrap_exact": bootstrap_then_raise,
        "prove_absent_means_no_callback": lambda: True,
    }
    with pytest.raises(RuntimeError, match="synthetic interrupted caller"):
        handoff.run_handoff(**kwargs)
    kwargs["now_ns"] = 1_799_999_200_000_000_000
    kwargs["bootstrap_exact"] = lambda _label, _args: pytest.fail("must not bootstrap twice")
    assert handoff.run_handoff(**kwargs)["status"] == "loaded_verified"
    assert attempts == 1
    assert len(events) == 2


class _NoopContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_handoff_rejects_bad_census_mismatch_and_future_bucket_violation() -> None:
    certificate = _certificate()
    with pytest.raises(handoff.HandoffError, match="handoff_certificate_invalid"):
        handoff.verify_handoff_certificate(
            {**certificate, "storage": {**certificate["storage"], "root_id": "d" * 64}}
        )
    with pytest.raises(handoff.HandoffError, match="handoff_first_bucket_not_future"):
        handoff.run_handoff(
            certificate=certificate,
            now_ns=certificate["first_bucket_close_ns"],
            read_events=lambda: [],
            append_event_create_only=lambda _event: None,
            with_supervisor_lock=lambda: _NoopContext(),
            with_ordered_locks=lambda: _NoopContext(),
            reverify_predecessor_and_global_census=lambda _cert: pytest.fail(
                "stale immutable certificate must stop before host reads"
            ),
            create_successor_or_verify=lambda _cert: pytest.fail("must not create root"),
            launch_snapshot=lambda: pytest.fail("must not query or bootstrap"),
            bootstrap_exact=lambda _label, _args: pytest.fail("must not bootstrap"),
            prove_absent_means_no_callback=lambda: pytest.fail("must not probe callback"),
        )
