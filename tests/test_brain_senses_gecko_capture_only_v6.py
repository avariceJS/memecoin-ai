from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
for _path in (REPO / "src", REPO / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tools import brain_senses_gecko_capture_only_v6 as capture_v6  # noqa: E402
from tools import brain_senses_gecko_lane_policy_v3 as frozen_v3  # noqa: E402
from tools import brain_senses_gecko_lane_policy_v6 as policy_v6  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json  # noqa: E402

NS = 1_000_000_000
CLOSE = 1_800_000_000 * NS
CLOSE -= CLOSE % policy_v6.BUCKET_NS
SELECTOR_SHA = hashlib.sha256(b"pinned selector source test binding").hexdigest()


def _policy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    rows = []
    for index in range(170):
        pool_id = f"pool-{index:03d}"
        rows.append(
            {
                "pool_id": pool_id,
                "base_mint": f"base-{index:03d}",
                "quote_mint": "wsol",
                "rank_sha256": hashlib.sha256(
                    f"{policy_v6.SOURCE_PIN}\0{pool_id}".encode()
                ).hexdigest(),
            }
        )
    rows = scout.pool_order(rows, policy_v6.SOURCE_PIN)
    digest = scout.population_digest(rows)
    monkeypatch.setattr(policy_v6, "POPULATION_SHA256", digest)
    core = {
        "schema": policy_v6.SCHEMA,
        "schema_version": policy_v6.SCHEMA_VERSION,
        "policy_id": policy_v6.POLICY_ID,
        "source": {
            "role": "train",
            "source_pin": policy_v6.SOURCE_PIN,
            "source_sha256": {
                "train_enrollment": frozen_v3.TRAIN_ENROLLMENT_SHA256,
                "train_role_manifest": frozen_v3.TRAIN_MANIFEST_SHA256,
                "train_plan": frozen_v3.TRAIN_PLAN_SHA256,
                "population_selection": frozen_v3.TRAIN_SELECTION_SHA256,
            },
            "enrolled_train_count": 170,
            "population_sha256": digest,
            "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
            "protected_screen_fields": ["pool_id", "base_mint"],
            "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
            "quote_token_features": False,
        },
        "population": rows,
        "sampling_epoch": policy_v6.sampling_epoch_contract(),
        "protocol": policy_v6._protocol(),
        "capacity": {
            "scope": "v6_local_gecko_only;no_global_provider_census_claim",
            "maximum_intents_per_utc_day": policy_v6.MAX_INTENTS_PER_UTC_DAY,
            "maximum_intents_per_rolling_30d": policy_v6.MAX_INTENTS_PER_ROLLING_30D,
            "maximum_raw_bytes_per_utc_day": policy_v6.MAX_RAW_BYTES_PER_UTC_DAY,
            "maximum_raw_bytes_per_rolling_30d": policy_v6.MAX_RAW_BYTES_PER_ROLLING_30D,
            "maximum_physical_reserve_per_rolling_30d": (
                policy_v6.MAX_PHYSICAL_RESERVE_PER_ROLLING_30D
            ),
            "physical_reserve_per_intent": policy_v6.PHYSICAL_RESERVE_PER_INTENT,
            "resident_allocation_ceiling_bytes": policy_v6.MAX_RESIDENT_BYTES,
            "minimum_free_bytes": policy_v6.MIN_FREE_BYTES,
            "conservative_prior_gecko_usage_offset_required": True,
            "prior_usage_claim": "known_gecko_attempts_only;not_global_account_usage",
        },
        "activation_authorized": False,
        "provider_io": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return {**core, "policy_sha256": policy_v6.sha256(policy_v6.canonical(core))}


class Harness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        prior_usage_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.chmod(self.tmp.name, 0o700)
        self.fd = os.open(self.tmp.name, os.O_RDONLY | os.O_DIRECTORY)
        self.service_path = os.path.join(self.tmp.name, "service")
        os.mkdir(self.service_path, 0o700)
        self.service_fd = os.open(self.service_path, os.O_RDONLY | os.O_DIRECTORY)
        self.policy = _policy(monkeypatch)
        data_stat = os.fstat(self.fd)
        service_stat = os.fstat(self.service_fd)
        self.storage = {
            "root_id": "a" * 32,
            "volume_uuid": "12345678-1234-1234-1234-123456789abc",
            "data_root": {"device": data_stat.st_dev, "inode": data_stat.st_ino},
            "service_root": {"device": service_stat.st_dev, "inode": service_stat.st_ino},
        }
        self.prior_usage_bytes = canonical_json(prior_usage_rows or [])
        self.prior_usage_census = {
            "receipt_path": "docs/evidence/test-prior-usage-census.json",
            "receipt_sha256": hashlib.sha256(b"test-receipt").hexdigest(),
            "root_inventory_sha256": hashlib.sha256(b"test-inventory").hexdigest(),
            "attempt_count": len(prior_usage_rows or []),
            "observed_ns": CLOSE - policy_v6.BUCKET_NS,
        }
        self.epoch = capture_v6.epoch_document(
            release_id="b" * 32,
            anchor_close_ns=CLOSE,
            policy_sha256=self.policy["policy_sha256"],
            selector_sha256=SELECTOR_SHA,
            population_sha256=self.policy["source"]["population_sha256"],
            storage=self.storage,
            prior_usage_path="control/gecko-v6-prior-usage/test.json",
            prior_usage_sha256=hashlib.sha256(self.prior_usage_bytes).hexdigest(),
            prior_usage_census=self.prior_usage_census,
        )
        self.writer = DescriptorWriter(self.service_fd)
        self.lane_tmp = tempfile.TemporaryDirectory()
        os.chmod(self.lane_tmp.name, 0o700)
        self.lane_fd = os.open(self.lane_tmp.name, os.O_RDONLY | os.O_DIRECTORY)
        self.lane_writer = DescriptorWriter(self.lane_fd)
        self.lane_writer.mkdir("control")
        self.lane_lease = capture_v6.CrossReleaseLaneLeaseV6(self.lane_writer)
        self.journal = capture_v6.LocalJournalV6(
            self.writer,
            expected_storage=self.storage,
            observed_storage=lambda: dict(self.storage),
            revalidate_storage=lambda: None,
            resource_snapshot=lambda: {
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
            exclusive_lane_lease=self.lane_lease,
        )
        self._held_lane = self.journal.hold_exclusive_lane()
        self._held_lane.__enter__()
        capture_v6.initialize_existing_root_v6(
            self.writer,
            epoch=self.epoch,
            policy=self.policy,
            selector_sha256=SELECTOR_SHA,
            prior_usage_bytes=self.prior_usage_bytes,
            expected_storage=self.storage,
            observed_storage=lambda: dict(self.storage),
            revalidate_storage=lambda: None,
        )

    def close(self) -> None:
        self._held_lane.__exit__(None, None, None)
        self.writer.close()
        os.close(self.service_fd)
        os.close(self.fd)
        self.lane_writer.close()
        os.close(self.lane_fd)
        self.lane_tmp.cleanup()
        self.tmp.cleanup()


def _run_one(
    monkeypatch: pytest.MonkeyPatch, *, ambiguous: bool = False
) -> tuple[Harness, dict[str, Any]]:
    harness = Harness(monkeypatch)
    close = harness.epoch["first_bucket_close_ns"]
    plan = harness.journal.plan_bucket(
        policy=harness.policy,
        epoch=harness.epoch,
        close_ns=close,
        now_ns=close + 60 * NS,
        selector_sha256=SELECTOR_SHA,
    )
    pool = plan["selected_pool_ids"][0]
    intent = harness.journal.reserve_intent(
        policy=harness.policy,
        epoch=harness.epoch,
        plan=plan,
        pool_id=pool,
        intent_ns=close + 60 * NS,
        inherited_usage_rows=[],
    )
    request_id = intent["fields"]["request_id"]
    if ambiguous:
        harness.journal.recover_unresolved(now_ns=close + 301 * NS)
    else:
        harness.journal.append_dispatch(
            request_id=request_id,
            request_started_ns=close + 60 * NS,
            monotonic_started_ns=50_000_000,
            close_ns=close,
            policy=harness.policy,
            epoch=harness.epoch,
            selector_sha256=SELECTOR_SHA,
            worker_identity=_synthetic_worker_identity(),
            inherited_usage_rows=[],
        )
        open_s = close // NS - 300
        raw = canonical_json(
            {"data": {"attributes": {"ohlcv_list": [[open_s, 1, 2, 0.5, 1.5, 100]]}}}
        )
        outcome = harness.journal.persist_outcome(
            request_id=request_id,
            status="received",
            raw_body=raw,
            receipt_ns=close + 61 * NS,
            monotonic_receipt_ns=1_050_000_000,
            availability_ns=close + 62 * NS,
            close_ns=close,
        )
        harness.journal.confirm(
            request_id=request_id,
            at_ns=close + 63 * NS,
            outcome_sha256=outcome["event_sha256"],
        )
    result = capture_v6.verify_local_journal(
        harness.writer,
        epoch=harness.epoch,
        policy=harness.policy,
        selector=scout.plan_bucket,
        selector_sha256=SELECTOR_SHA,
        expected_storage=harness.storage,
        observed_storage=lambda: dict(harness.storage),
        now_ns=close + (301 if ambiguous else 64) * NS,
        inherited_usage_rows=[],
        resource_snapshot={
            "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
            "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
        },
    )
    return harness, result


def _synthetic_worker_identity() -> dict[str, Any]:
    return {
        "pid": 1234,
        "pgid": 1234,
        "birth_id": "1790000000.000001",
        "executable_path": "/usr/bin/python3",
        "uid": 501,
        "attempt_nonce": "a" * 32,
        "worker_source_sha256": "b" * 64,
    }


def _quiescence_proof(request_id: str, close_ns: int) -> dict[str, Any]:
    return {
        "schema": capture_v6.QUIESCENCE_SCHEMA,
        "request_id": request_id,
        "child_pid": 1234,
        "process_group_id": 1234,
        "child_birth_id": "1790000000.000001",
        "child_executable_path": "/usr/bin/python3",
        "child_uid": 501,
        "attempt_nonce": "a" * 32,
        "worker_source_sha256": "b" * 64,
        "absence_verified": True,
        "pid_reuse_observed": False,
        "observed_birth_id": None,
        "process_group_empty": True,
        "descendant_count": 0,
        "request_started_ns": close_ns + 60 * NS,
        "request_started_monotonic_ns": 50_000_000,
        "quiesced_ns": close_ns + 61 * NS,
        "quiesced_monotonic_ns": 1_050_000_000,
        "clock_pair_verified": True,
    }


def test_v6_explicit_epoch_and_durable_one_shot_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    harness, result = _run_one(monkeypatch)
    try:
        assert harness.epoch["cursor_in"] == 0
        assert harness.epoch["retention_in"] == []
        assert result["cursor_out"] == 6
        assert result["usage"]["intents_rolling_30d"] == 1
        assert result["usage"]["rows"][0]["confirmed"] is True
        assert result["provider_io"] is False
        assert result["training_admission"] is False
    finally:
        harness.close()


def test_v6_restart_terminalizes_intent_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    harness, result = _run_one(monkeypatch, ambiguous=True)
    try:
        row = result["usage"]["rows"][0]
        assert row["status"] == "possibly_spent_unresolved"
        assert row["ambiguous"] is True
        assert row["raw_bytes"] == policy_v6.MAX_RESPONSE_BYTES
        assert row["confirmed"] is True
    finally:
        harness.close()


def test_unaccounted_expired_slot_blocks_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        with pytest.raises(capture_v6.CaptureV6Error, match="expired_slot"):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 300 * NS,
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
                },
            )
    finally:
        harness.close()


def test_gap_does_not_advance_cursor_and_next_plan_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    try:
        first = harness.epoch["first_bucket_close_ns"]
        harness.journal.record_gap(
            epoch=harness.epoch, close_ns=first, at_ns=first + 300 * NS, reason="unobserved_no_plan"
        )
        next_close = first + policy_v6.BUCKET_NS
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=next_close,
            now_ns=next_close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        assert plan["cursor_in"] == 0
        result = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=SELECTOR_SHA,
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=next_close + 61 * NS,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
        )
        assert result["cursor_out"] == 6
    finally:
        harness.close()


def test_plan_hash_tampering_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        path = f"{capture_v6.PLAN_DIRECTORY}/{close}.json"
        changed = dict(plan)
        changed["cursor_out"] = 7
        harness.writer.atomic_write(path, canonical_json(changed))
        with pytest.raises(capture_v6.CaptureV6Error):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 61 * NS,
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
                },
            )
    finally:
        harness.close()


def test_dispatch_replays_full_journal_before_recording_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=plan,
            pool_id=plan["selected_pool_ids"][0],
            intent_ns=close + 60 * NS,
            inherited_usage_rows=[],
        )
        changed = dict(plan)
        changed["cursor_out"] = (plan["cursor_out"] + 1) % 170
        harness.writer.atomic_write(
            f"{capture_v6.PLAN_DIRECTORY}/{close}.json", canonical_json(changed)
        )
        with pytest.raises(capture_v6.CaptureV6Error):
            harness.journal.append_dispatch(
                request_id=intent["fields"]["request_id"],
                request_started_ns=close + 60 * NS,
                monotonic_started_ns=50_000_000,
                close_ns=close,
                policy=harness.policy,
                epoch=harness.epoch,
                selector_sha256=SELECTOR_SHA,
                worker_identity=_synthetic_worker_identity(),
                inherited_usage_rows=[],
            )
        assert not any(event["kind"] == "dispatch" for event in harness.journal._events())
    finally:
        harness.close()


def test_prior_usage_pin_and_resource_floor_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        with pytest.raises(capture_v6.CaptureV6Error, match="epoch_binding"):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 1,
                inherited_usage_rows=[
                    {
                        "intent_ns": close,
                        "raw_bytes_upper_bound": 0,
                        "physical_bytes_upper_bound": 0,
                    }
                ],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": 0,
                },
            )
        with pytest.raises(capture_v6.CaptureV6Error, match="resource_cap"):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 1,
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES - 1,
                    "resident_bytes": 0,
                },
            )
    finally:
        harness.close()


def test_prior_usage_file_is_loaded_and_physical_bytes_are_summed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close = CLOSE
    prior_rows = [
        {
            "intent_ns": close,
            "raw_bytes_upper_bound": 0,
            "physical_bytes_upper_bound": 1,
        }
    ]
    harness = Harness(monkeypatch, prior_usage_rows=prior_rows)
    try:
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        with pytest.raises(capture_v6.CaptureV6Error, match="caller_mismatch"):
            harness.journal.reserve_intent(
                policy=harness.policy,
                epoch=harness.epoch,
                plan=plan,
                pool_id=plan["selected_pool_ids"][0],
                intent_ns=close + 60 * NS,
                inherited_usage_rows=[],
            )
        intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=plan,
            pool_id=plan["selected_pool_ids"][0],
            intent_ns=close + 60 * NS,
            inherited_usage_rows=prior_rows,
        )
        assert intent["fields"]["physical_reserve_bytes"] == policy_v6.PHYSICAL_RESERVE_PER_INTENT
        pinned_path = harness.epoch["prior_usage"]["path"]
        harness.writer.atomic_write(pinned_path, canonical_json([]))
        with pytest.raises(capture_v6.CaptureV6Error, match="prior_usage_hash"):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 61 * NS,
                inherited_usage_rows=prior_rows,
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
                },
            )
    finally:
        harness.close()

    high_physical_rows = [
        {
            "intent_ns": close,
            "raw_bytes_upper_bound": 0,
            "physical_bytes_upper_bound": (
                policy_v6.MAX_PHYSICAL_RESERVE_PER_ROLLING_30D
                - policy_v6.PHYSICAL_RESERVE_PER_INTENT
                + 1
            ),
        }
    ]
    high_harness = Harness(monkeypatch, prior_usage_rows=high_physical_rows)
    try:
        plan = high_harness.journal.plan_bucket(
            policy=high_harness.policy,
            epoch=high_harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        with pytest.raises(capture_v6.CaptureV6Error, match="local_budget_exhausted"):
            high_harness.journal.reserve_intent(
                policy=high_harness.policy,
                epoch=high_harness.epoch,
                plan=plan,
                pool_id=plan["selected_pool_ids"][0],
                intent_ns=close + 60 * NS,
                inherited_usage_rows=high_physical_rows,
            )
    finally:
        high_harness.close()


def test_unreferenced_file_breaks_exact_root_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    harness, _result = _run_one(monkeypatch)
    try:
        harness.writer.create("unexpected.json", b"{}\n")
        close = harness.epoch["first_bucket_close_ns"]
        with pytest.raises(capture_v6.CaptureV6Error, match="exact_file_closure"):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 64 * NS,
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
                },
            )
    finally:
        harness.close()


def test_request_file_before_intent_is_adopted_without_network_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        pool_id = plan["selected_pool_ids"][0]
        request = capture_v6.request_for_pool(harness.policy, pool_id, close)
        request_path = f"{capture_v6.REQUEST_DIRECTORY}/{request['request_id']}.json"
        harness.writer.create(request_path, canonical_json(request))
        before_intent = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=SELECTOR_SHA,
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=close + 61 * NS,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
        )
        assert before_intent["usage"]["intents_rolling_30d"] == 0
        intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=plan,
            pool_id=pool_id,
            intent_ns=close + 62 * NS,
            inherited_usage_rows=[],
        )
        assert intent["fields"]["request_sha256"] == policy_v6.sha256(canonical_json(request))
        recovered = harness.journal.recover_unresolved(now_ns=close + 301 * NS)
        assert recovered == [request["request_id"]]
        result = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=SELECTOR_SHA,
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=close + 302 * NS,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
        )
        row = result["usage"]["rows_by_request"][request["request_id"]]
        assert row["dispatch_ns"] is None
        assert row["ambiguous"] is True
        assert row["raw_bytes"] == policy_v6.MAX_RESPONSE_BYTES
    finally:
        harness.close()


def test_request_orphan_for_missed_slot_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        pool_id = plan["selected_pool_ids"][0]
        request = capture_v6.request_for_pool(harness.policy, pool_id, close)
        harness.writer.create(
            f"{capture_v6.REQUEST_DIRECTORY}/{request['request_id']}.json",
            canonical_json(request),
        )
        harness.journal.record_missed(plan=plan, pool_id=pool_id, at_ns=close + 301 * NS)
        with pytest.raises(capture_v6.CaptureV6Error, match="orphan_request_slot_spent"):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 302 * NS,
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
                },
            )
    finally:
        harness.close()


def test_late_request_orphan_recovery_tombstones_before_next_plan_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        orphan_pool = plan["selected_pool_ids"][0]
        orphan_request = capture_v6.request_for_pool(harness.policy, orphan_pool, close)
        orphan_path = f"{capture_v6.REQUEST_DIRECTORY}/{orphan_request['request_id']}.json"
        harness.writer.create(orphan_path, canonical_json(orphan_request))

        record_missed = harness.journal.record_missed

        def crash_after_tombstone(*, plan: Any, pool_id: str, at_ns: int) -> Any:
            if pool_id == orphan_pool:
                raise RuntimeError("simulated_crash_after_request_tombstone")
            return record_missed(plan=plan, pool_id=pool_id, at_ns=at_ns)

        monkeypatch.setattr(harness.journal, "record_missed", crash_after_tombstone)
        with pytest.raises(RuntimeError, match="simulated_crash_after_request_tombstone"):
            harness.journal.recover_unresolved(now_ns=close + 301 * NS)
        partial_events = harness.journal._events()
        assert any(event["kind"] == "request_abandoned" for event in partial_events)
        assert not any(
            event["kind"] == "missed" and event["fields"]["pool_id"] == orphan_pool
            for event in partial_events
        )
        monkeypatch.setattr(harness.journal, "record_missed", record_missed)
        assert harness.journal.recover_unresolved(now_ns=close + 301 * NS) == []
        tombstone = next(
            event for event in harness.journal._events() if event["kind"] == "request_abandoned"
        )
        assert tombstone["fields"] == {
            "request_id": orphan_request["request_id"],
            "close_ns": close,
            "pool_id": orphan_pool,
            "request_path": orphan_path,
            "request_sha256": policy_v6.sha256(canonical_json(orphan_request)),
            "reason": "expired_before_intent",
        }

        def verify_current() -> dict[str, Any]:
            return capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 302 * NS,
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
                },
            )

        first_replay = verify_current()
        assert first_replay["usage"]["intents_rolling_30d"] == 0
        assert sum(event["kind"] == "missed" for event in harness.journal._events()) == 6

        harness.writer.atomic_write(orphan_path, b"replaced-request-bytes")
        with pytest.raises(capture_v6.CaptureV6Error, match="abandonment_file_binding"):
            verify_current()
        harness.writer.atomic_write(orphan_path, canonical_json(orphan_request))
        harness.writer.unlink(orphan_path)
        with pytest.raises(capture_v6.CaptureV6Error, match="abandonment_file_missing"):
            verify_current()
        harness.writer.create(orphan_path, canonical_json(orphan_request))
        assert verify_current()["usage"]["intents_rolling_30d"] == 0

        next_close = close + policy_v6.BUCKET_NS
        next_plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=next_close,
            now_ns=next_close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        selected_pool = next_plan["selected_pool_ids"][0]
        next_intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=next_plan,
            pool_id=selected_pool,
            intent_ns=next_close + 60 * NS,
            inherited_usage_rows=[],
        )
        assert next_intent["fields"]["request_id"] != orphan_request["request_id"]
        harness.journal.append_dispatch(
            request_id=next_intent["fields"]["request_id"],
            request_started_ns=next_close + 60 * NS,
            monotonic_started_ns=50_000_000,
            close_ns=next_close,
            policy=harness.policy,
            epoch=harness.epoch,
            selector_sha256=SELECTOR_SHA,
            worker_identity=_synthetic_worker_identity(),
            inherited_usage_rows=[],
        )
        assert all(
            event["fields"].get("request_id") != orphan_request["request_id"]
            for event in harness.journal._events()
            if event["kind"] in {"intent", "dispatch"}
        )
    finally:
        harness.close()


def test_orphan_plan_file_is_reconciled_as_expired_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        append = harness.journal._append

        def fail_plan_append(kind: str, at_ns: int, fields: dict[str, Any], **kwargs: Any) -> Any:
            if kind == "plan":
                raise RuntimeError("simulated_crash_after_plan_file")
            return append(kind, at_ns, fields, **kwargs)

        monkeypatch.setattr(harness.journal, "_append", fail_plan_append)
        with pytest.raises(RuntimeError, match="simulated_crash_after_plan_file"):
            harness.journal.plan_bucket(
                policy=harness.policy,
                epoch=harness.epoch,
                close_ns=close,
                now_ns=close + 60 * NS,
                selector_sha256=SELECTOR_SHA,
            )
        monkeypatch.setattr(harness.journal, "_append", append)
        plan_path = f"{capture_v6.PLAN_DIRECTORY}/{close}.json"
        orphan_plan = capture_v6._object(
            harness.writer.read_bytes(plan_path, max_bytes=256 * 1024), "test_plan"
        )
        assert not any(event["kind"] == "plan" for event in harness.journal._events())

        assert harness.journal.recover_unresolved(now_ns=close + 301 * NS) == []
        events = harness.journal._events()
        plan_event = next(event for event in events if event["kind"] == "plan")
        missed = [event for event in events if event["kind"] == "missed"]
        assert plan_event["fields"]["selected_pool_ids"] == orphan_plan["selected_pool_ids"]
        assert plan_event["fields"]["cursor_out"] == orphan_plan["cursor_out"]
        assert {event["fields"]["pool_id"] for event in missed} == set(
            orphan_plan["selected_pool_ids"]
        )
        assert len(missed) == len(orphan_plan["selected_pool_ids"])
        with pytest.raises(capture_v6.CaptureV6Error, match="pool_bucket_already_spent"):
            harness.journal.reserve_intent(
                policy=harness.policy,
                epoch=harness.epoch,
                plan=orphan_plan,
                pool_id=orphan_plan["selected_pool_ids"][0],
                intent_ns=close + 62 * NS,
                inherited_usage_rows=[],
            )
    finally:
        harness.close()


def test_raw_file_before_outcome_recovers_as_max_reserve_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        pool_id = plan["selected_pool_ids"][0]
        intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=plan,
            pool_id=pool_id,
            intent_ns=close + 60 * NS,
            inherited_usage_rows=[],
        )
        request_id = intent["fields"]["request_id"]
        harness.journal.append_dispatch(
            request_id=request_id,
            request_started_ns=close + 60 * NS,
            monotonic_started_ns=50_000_000,
            close_ns=close,
            policy=harness.policy,
            epoch=harness.epoch,
            selector_sha256=SELECTOR_SHA,
            worker_identity=_synthetic_worker_identity(),
            inherited_usage_rows=[],
        )
        plausible_success = canonical_json(
            {"data": {"attributes": {"ohlcv_list": [[close // NS - 300, 1, 2, 0.5, 1.5, 100]]}}}
        )
        harness.writer.create(f"{capture_v6.RAW_DIRECTORY}/{request_id}.bin", plausible_success)
        prefix = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=SELECTOR_SHA,
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=close + 64 * NS,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
        )
        assert prefix["usage"]["rows_by_request"][request_id]["ambiguous"] is True
        with pytest.raises(capture_v6.CaptureV6Error, match="quiescence_set"):
            harness.journal.recover_unresolved(now_ns=close + 301 * NS)
        proof = _quiescence_proof(request_id, close)
        harness.journal.recover_unresolved(
            now_ns=close + 301 * NS, quiescence_proofs={request_id: proof}
        )
        recovered = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=SELECTOR_SHA,
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=close + 302 * NS,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
        )
        row = recovered["usage"]["rows_by_request"][request_id]
        assert row["status"] == "possibly_spent_unresolved"
        assert row["raw_bytes"] == policy_v6.MAX_RESPONSE_BYTES
        assert row["ambiguous"] is True
        unresolved = next(
            event
            for event in harness.journal._events()
            if event["kind"] == "outcome" and event["fields"]["request_id"] == request_id
        )
        assert unresolved["fields"]["receipt_ns"] is None
        assert unresolved["fields"]["monotonic_receipt_ns"] is None
        assert unresolved["fields"]["recovery_ns"] == close + 301 * NS
        assert unresolved["fields"]["raw_path"] == (f"{capture_v6.RAW_DIRECTORY}/{request_id}.bin")
        assert unresolved["fields"]["raw_sha256"] == policy_v6.sha256(plausible_success)
        assert unresolved["fields"]["raw_bytes"] == len(plausible_success)
        harness.writer.atomic_write(unresolved["fields"]["raw_path"], b"changed-after-recovery")
        with pytest.raises(capture_v6.CaptureV6Error, match="raw_capture_binding"):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 303 * NS,
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
                },
            )
    finally:
        harness.close()


def test_raw_added_after_empty_ambiguous_recovery_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=plan,
            pool_id=plan["selected_pool_ids"][0],
            intent_ns=close + 60 * NS,
            inherited_usage_rows=[],
        )
        request_id = intent["fields"]["request_id"]
        harness.journal.append_dispatch(
            request_id=request_id,
            request_started_ns=close + 60 * NS,
            monotonic_started_ns=50_000_000,
            close_ns=close,
            policy=harness.policy,
            epoch=harness.epoch,
            selector_sha256=SELECTOR_SHA,
            worker_identity=_synthetic_worker_identity(),
            inherited_usage_rows=[],
        )
        proof = _quiescence_proof(request_id, close)
        harness.journal.recover_unresolved(
            now_ns=close + 301 * NS, quiescence_proofs={request_id: proof}
        )
        harness.writer.create(
            f"{capture_v6.RAW_DIRECTORY}/{request_id}.bin", b"arrived-after-recovery"
        )
        with pytest.raises(capture_v6.CaptureV6Error, match="orphan_raw_invalid"):
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=SELECTOR_SHA,
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=close + 302 * NS,
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                    "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
                },
            )
    finally:
        harness.close()


def test_outcome_without_confirmation_is_recovered_by_outcome_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=plan,
            pool_id=plan["selected_pool_ids"][0],
            intent_ns=close + 60 * NS,
            inherited_usage_rows=[],
        )
        request_id = intent["fields"]["request_id"]
        harness.journal.append_dispatch(
            request_id=request_id,
            request_started_ns=close + 60 * NS,
            monotonic_started_ns=50_000_000,
            close_ns=close,
            policy=harness.policy,
            epoch=harness.epoch,
            selector_sha256=SELECTOR_SHA,
            worker_identity=_synthetic_worker_identity(),
            inherited_usage_rows=[],
        )
        raw = canonical_json(
            {"data": {"attributes": {"ohlcv_list": [[close // NS - 300, 1, 2, 0.5, 1.5, 100]]}}}
        )
        outcome = harness.journal.persist_outcome(
            request_id=request_id,
            status="received",
            raw_body=raw,
            receipt_ns=close + 61 * NS,
            monotonic_receipt_ns=1_050_000_000,
            availability_ns=close + 62 * NS,
            close_ns=close,
        )
        prefix = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=SELECTOR_SHA,
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=close + 63 * NS,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
        )
        assert prefix["usage"]["rows_by_request"][request_id]["confirmed"] is False
        harness.journal.recover_unresolved(now_ns=close + 301 * NS)
        recovered = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=SELECTOR_SHA,
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=close + 302 * NS,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
        )
        row = recovered["usage"]["rows_by_request"][request_id]
        assert row["status"] == "received"
        assert row["confirmed"] is True
        assert row["raw_bytes"] == len(raw)
        confirmation = next(
            event
            for event in harness.journal._events()
            if event["kind"] == "confirmation" and event["fields"]["request_id"] == request_id
        )
        assert confirmation["fields"]["outcome_sha256"] == outcome["event_sha256"]
    finally:
        harness.close()


def test_invalid_outcome_dispatch_is_rejected_before_raw_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        close = harness.epoch["first_bucket_close_ns"]
        plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=close,
            now_ns=close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=plan,
            pool_id=plan["selected_pool_ids"][0],
            intent_ns=close + 60 * NS,
            inherited_usage_rows=[],
        )
        request_id = intent["fields"]["request_id"]
        raw = b"raw bytes must not be written before dispatch validation"
        with pytest.raises(capture_v6.CaptureV6Error, match="outcome_without_dispatch"):
            harness.journal.persist_outcome(
                request_id=request_id,
                status="provider_error",
                raw_body=raw,
                receipt_ns=close + 61 * NS,
                monotonic_receipt_ns=1_050_000_000,
                availability_ns=close + 62 * NS,
                close_ns=close,
            )
        with pytest.raises(FileNotFoundError):
            harness.writer.read_bytes(
                f"{capture_v6.RAW_DIRECTORY}/{request_id}.bin",
                max_bytes=policy_v6.MAX_RESPONSE_BYTES,
            )
    finally:
        harness.close()


def test_prior_raw_replay_can_seed_retention_without_cursor_rewind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        first_close = harness.epoch["first_bucket_close_ns"]
        first_plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=first_close,
            now_ns=first_close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        retained_id = first_plan["scout_pool_ids"][0]
        intent = harness.journal.reserve_intent(
            policy=harness.policy,
            epoch=harness.epoch,
            plan=first_plan,
            pool_id=retained_id,
            intent_ns=first_close + 60 * NS,
            inherited_usage_rows=[],
        )
        request_id = intent["fields"]["request_id"]
        harness.journal.append_dispatch(
            request_id=request_id,
            request_started_ns=first_close + 60 * NS,
            monotonic_started_ns=50_000_000,
            close_ns=first_close,
            policy=harness.policy,
            epoch=harness.epoch,
            selector_sha256=SELECTOR_SHA,
            worker_identity=_synthetic_worker_identity(),
            inherited_usage_rows=[],
        )
        raw = canonical_json(
            {
                "data": {
                    "attributes": {"ohlcv_list": [[first_close // NS - 300, 1, 2, 0.5, 1.5, 100]]}
                }
            }
        )
        outcome = harness.journal.persist_outcome(
            request_id=request_id,
            status="received",
            raw_body=raw,
            receipt_ns=first_close + 61 * NS,
            monotonic_receipt_ns=1_050_000_000,
            availability_ns=first_close + 62 * NS,
            close_ns=first_close,
        )
        harness.journal.confirm(
            request_id=request_id,
            at_ns=first_close + 63 * NS,
            outcome_sha256=outcome["event_sha256"],
        )
        harness.journal.recover_unresolved(now_ns=first_close + 301 * NS)
        second_close = first_close + policy_v6.BUCKET_NS
        _retention, evidence = capture_v6._prior_selector_inputs(
            harness.writer, harness.journal._events(), close_ns=second_close
        )
        assert evidence and evidence[0]["eligible_new_open"] is True
        second_plan = harness.journal.plan_bucket(
            policy=harness.policy,
            epoch=harness.epoch,
            close_ns=second_close,
            now_ns=second_close + 60 * NS,
            selector_sha256=SELECTOR_SHA,
        )
        assert second_plan["cursor_in"] == 6
        assert retained_id in {row["pool_id"] for row in second_plan["retention"]}
        result = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=SELECTOR_SHA,
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=second_close + 61 * NS,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": policy_v6.MIN_FREE_BYTES + 1,
                "resident_bytes": policy_v6.MAX_RESIDENT_BYTES - 1,
            },
        )
        assert result["cursor_out"] == second_plan["cursor_out"]
        assert result["cursor_out"] > second_plan["cursor_in"]
    finally:
        harness.close()


def test_forced_detach_during_event_append_never_writes_underlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    mount = Path(harness.tmp.name)
    detached = mount.with_name(mount.name + "-detached")
    state = {"detached": False}
    close = harness.epoch["first_bucket_close_ns"]
    plan = harness.journal.plan_bucket(
        policy=harness.policy,
        epoch=harness.epoch,
        close_ns=close,
        now_ns=close + 60 * NS,
        selector_sha256=SELECTOR_SHA,
    )
    intent = harness.journal.reserve_intent(
        policy=harness.policy,
        epoch=harness.epoch,
        plan=plan,
        pool_id=plan["selected_pool_ids"][0],
        intent_ns=close + 60 * NS,
        inherited_usage_rows=[],
    )

    def detach_at_append(operation: str) -> None:
        if operation == "append_write" and not state["detached"]:
            state["detached"] = True
            os.rename(mount, detached)
            mount.mkdir(mode=0o700)
            (mount / "underlay-sentinel").write_bytes(b"unchanged")

    def revalidate_mount() -> None:
        current = os.stat(mount, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (
            harness.storage["data_root"]["device"],
            harness.storage["data_root"]["inode"],
        ):
            raise OSError("mount detached")

    harness.writer._fault_hook = detach_at_append
    harness.journal.revalidate_storage = revalidate_mount
    try:
        with pytest.raises(OSError, match="mount detached"):
            harness.journal.append_dispatch(
                request_id=intent["fields"]["request_id"],
                request_started_ns=close + 60 * NS,
                monotonic_started_ns=50_000_000,
                close_ns=close,
                policy=harness.policy,
                epoch=harness.epoch,
                selector_sha256=SELECTOR_SHA,
                worker_identity=_synthetic_worker_identity(),
                inherited_usage_rows=[],
            )
        assert state["detached"] is True
        assert (mount / "underlay-sentinel").read_bytes() == b"unchanged"
        assert sorted(path.name for path in mount.iterdir()) == ["underlay-sentinel"]
        event_file = next(detached.rglob("events.jsonl"))
        assert event_file.exists()
        assert b'"kind":"dispatch"' in event_file.read_bytes()
    finally:
        if mount.exists():
            (mount / "underlay-sentinel").unlink(missing_ok=True)
            mount.rmdir()
        if detached.exists():
            os.rename(detached, mount)
        harness.close()


def test_data_root_lane_lease_is_exclusive_across_release_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    second_writer = DescriptorWriter(harness.lane_fd)
    second_lease = capture_v6.CrossReleaseLaneLeaseV6(second_writer)
    try:
        with pytest.raises(Exception, match="external_writer_already_active"):
            with second_lease.hold():
                pytest.fail("a second release acquired the shared Gecko lane")
    finally:
        second_writer.close()
        harness.close()


def test_service_root_identity_change_is_rejected_before_event_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    try:
        events_before = harness.writer.read_bytes(
            capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024
        )
        replacement = dict(harness.storage)
        replacement["service_root"] = {
            **harness.storage["service_root"],
            "inode": harness.storage["service_root"]["inode"] + 1,
        }
        harness.journal.observed_storage = lambda: replacement
        with pytest.raises(capture_v6.CaptureV6Error, match="storage_identity_mismatch"):
            harness.journal._append("dispatch", CLOSE + NS, {"unreachable": True})
        assert (
            harness.writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024)
            == events_before
        )
    finally:
        harness.close()
