import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from memecoin_experience import block_inventory, block_range_capture
from memecoin_experience.storage import canonical


class _Response:
    status = 200
    headers = {"content-type": "application/json", "x-ratelimit-remaining": "99"}

    def __init__(self, body):
        self.body = body

    def read(self, limit):
        return self.body[:limit]

    def close(self):
        return None


class _Opener:
    def __init__(
        self,
        *,
        fail_method=None,
        malformed_method=None,
        interrupt=False,
        fail_index=None,
        enumeration_result=None,
    ):
        self.requests = []
        self.fail_method = fail_method
        self.malformed_method = malformed_method
        self.interrupt = interrupt
        self.fail_index = fail_index
        self.enumeration_result = enumeration_result

    def open(self, request, timeout):
        payload = json.loads(request.data)
        self.requests.append(payload)
        if self.interrupt:
            raise KeyboardInterrupt("simulated process interruption")
        if payload["method"] == self.fail_method or len(self.requests) - 1 == self.fail_index:
            raise TimeoutError("secret endpoint should not appear in output")
        if payload["method"] == self.malformed_method:
            return _Response(b"<not-json-provider-response>")
        method, params = payload["method"], payload["params"]
        if method == "getBlocks":
            result = self.enumeration_result if self.enumeration_result is not None else [params[0]]
        else:
            slot = params[0]
            parent = slot - 1
            result = dict(
                blockhash=f"hash-{slot}",
                previousBlockhash=f"hash-{parent}",
                parentSlot=parent,
                transactions=[],
            )
        return _Response(canonical(dict(jsonrpc="2.0", id=payload["id"], result=result)))


def _install_opener(monkeypatch, opener):
    monkeypatch.setattr(block_range_capture, "_endpoint_matches", lambda *_: True)
    monkeypatch.setattr(
        block_range_capture.urllib.request,
        "build_opener",
        lambda *_: opener,
    )


def test_fixed_range_capture_publishes_and_replicates_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener()
    _install_opener(monkeypatch, opener)
    output = Path("work/block-range")
    receipt = block_range_capture.capture_once(
        output, "https://redacted.example.invalid/v2/not-a-real-secret"
    )
    assert len(opener.requests) == block_range_capture.MAX_REQUESTS == 4
    assert [request["method"] for request in opener.requests] == [
        "getBlocks",
        "getBlock",
        "getBlock",
        "getBlock",
    ]
    assert receipt["interval"] == [454_538_107, 454_538_108]
    assert receipt["attempts"] == 4
    assert receipt["retries"] == 0
    assert receipt["free_plan_independently_verified"] is False

    source, target = tmp_path / "source", tmp_path / "replica"
    pin = block_inventory.publish_finalized_block_inventory(source, output)
    inventory = block_inventory.load_finalized_block_inventory(source, pin)
    assert inventory["source_kind"] == "alchemy_free_http_capture_v1"
    assert inventory["source_account_alias"] == "alchemy-free-local"
    assert inventory["ledger_counts"]["complete"] == 1
    block_inventory.replicate_finalized_block_inventory(source, target, pin)
    import shutil

    shutil.rmtree(output)
    shutil.rmtree(source)
    assert block_inventory.load_finalized_block_inventory(target, pin) == inventory


def test_timeout_is_sealed_as_one_failed_attempt_without_retry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener(fail_method="getBlocks")
    _install_opener(monkeypatch, opener)
    output = Path("work/block-range-timeout")
    receipt = block_range_capture.capture_once(
        output, "https://redacted.example.invalid/v2/not-a-real-secret"
    )
    assert len(opener.requests) == 4
    assert receipt["attempts"] == 4
    assert receipt["retries"] == 0
    assert receipt["outcomes"][0]["transport_error_class"] == "TimeoutError"
    assert "secret endpoint" not in json.dumps(receipt)
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, output)
    result = block_inventory.load_finalized_block_inventory(store, pin)
    assert result["enumeration_status"] == "failed"
    assert result["ledger_counts"]["complete"] == 1


def test_malformed_http_200_is_retained_as_failed_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener(malformed_method="getBlocks")
    _install_opener(monkeypatch, opener)
    output = Path("work/block-range-malformed")
    block_range_capture.capture_once(
        output, "https://redacted.example.invalid/v2/not-a-real-secret"
    )
    assert (output / "enumeration/response.json").read_bytes() == b"<not-json-provider-response>"
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, output)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["enumeration_status"] == "failed"
    assert receipt["ledger_counts"]["complete"] == 1


def test_dispatch_interruption_leaves_durable_reservation_and_blocks_retry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener(interrupt=True)
    _install_opener(monkeypatch, opener)
    output = Path("work/block-range-interrupted")
    with pytest.raises(KeyboardInterrupt, match="interruption"):
        block_range_capture.capture_once(
            output, "https://redacted.example.invalid/v2/not-a-real-secret"
        )
    ledger = json.loads((output / "attempt-ledger.json").read_text())
    assert len(opener.requests) == 1
    assert len(ledger["attempts"]) == 1
    assert ledger["attempts"][0]["status"] == "reserved"
    assert ledger["attempts"][0]["retry_count"] == 0
    assert ledger["planned_requests"][0]["status"] == "reserved"
    assert all(row["status"] == "planned" for row in ledger["planned_requests"][1:])
    assert json.loads(ledger["attempts"][0]["request_json"]) == opener.requests[0]
    assert (
        ledger["attempts"][0]["request_sha256"]
        == hashlib.sha256(ledger["attempts"][0]["request_json"].encode()).hexdigest()
    )
    with pytest.raises(FileExistsError):
        block_range_capture.capture_once(
            output, "https://redacted.example.invalid/v2/not-a-real-secret"
        )
    assert len(opener.requests) == 1


def _two_slot_plan(**changes):
    plan = replace(
        block_range_capture.FROZEN_ALCHEMY_FREE_PLAN,
        start_slot=50,
        end_slot=52,
        left_witness_slot=49,
        right_witness_slot=52,
        request_budget=5,
    )
    return replace(plan, **changes).checked()


def test_plan_bounds_and_frozen_one_slot_wrapper():
    plan = block_range_capture.FROZEN_ALCHEMY_FREE_PLAN
    assert (plan.start_slot, plan.end_slot) == (454_538_107, 454_538_108)
    assert (plan.left_witness_slot, plan.right_witness_slot) == (454_538_106, 454_538_108)
    assert plan.request_budget == 4
    with pytest.raises(ValueError, match="plan_bounds"):
        replace(plan, end_slot=plan.start_slot).checked()
    with pytest.raises(ValueError, match="plan_bounds"):
        replace(plan, left_witness_slot=plan.start_slot).checked()
    with pytest.raises(ValueError, match="plan_bounds"):
        replace(plan, request_budget=5).checked()
    with pytest.raises(ValueError, match="plan_bounds"):
        replace(plan, max_bundle_bytes=1_000).checked()


def test_partial_enumeration_keeps_full_blocks_without_complete_lineage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener(enumeration_result=[50])
    _install_opener(monkeypatch, opener)
    output = Path("work/block-range-partial-enumeration")
    plan = _two_slot_plan()
    block_range_capture.capture_once(output, "https://redacted.example.invalid/v2/fake", plan=plan)
    assert len(opener.requests) == 5
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, output)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    rows = [
        row
        for ref in receipt["ledger_pages"]
        for row in json.loads(
            (store / "qualification-block-inventory-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    assert [row["status"] for row in rows] == ["complete", "complete"]
    assert rows[1]["enumeration_relation"] == "not_listed"
    assert rows[1]["lineage_status"] == "conflict"


def test_failed_witness_is_separate_from_requested_slot_capture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener(fail_index=1)
    _install_opener(monkeypatch, opener)
    output = Path("work/block-range-failed-witness")
    block_range_capture.capture_once(
        output,
        "https://redacted.example.invalid/v2/fake",
        plan=block_range_capture.FROZEN_ALCHEMY_FREE_PLAN,
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, output)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    rows = [
        row
        for ref in receipt["ledger_pages"]
        for row in json.loads(
            (store / "qualification-block-inventory-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    assert rows[0]["status"] == "complete"
    assert rows[0]["lineage_status"] == "unresolved"


def test_request_budget_stop_marks_unattempted_slots_stopped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener()
    _install_opener(monkeypatch, opener)
    output = Path("work/block-range-budget-stop")
    plan = _two_slot_plan(request_budget=2)
    run = block_range_capture.capture_once(
        output, "https://redacted.example.invalid/v2/fake", plan=plan
    )
    assert len(opener.requests) == 2
    assert run["stop_reason"] == "request_budget_stop"
    assert run["stopped_slots"] == [50, 51]
    assert len(run["stopped_requests"]) == 3
    assert all(row["status"] == "stopped_budget" for row in run["stopped_requests"])
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, output)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["ledger_counts"]["stopped"] == 2


def test_expired_plan_stops_before_any_dispatch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener()
    _install_opener(monkeypatch, opener)
    output = Path("work/block-range-expired")
    plan = replace(block_range_capture.FROZEN_ALCHEMY_FREE_PLAN, expires_at_ns=1)
    result = block_range_capture.capture_once(
        output, "https://redacted.example.invalid/v2/fake", plan=plan
    )
    assert opener.requests == []
    assert result["stop_reason"] == "plan_expired"
    assert result["fixture_written"] is False
    ledger = json.loads((output / "attempt-ledger.json").read_text())
    assert ledger["attempts"] == []
    assert all(row["status"] == "stopped_expired" for row in ledger["planned_requests"])


def test_plan_provenance_is_provider_neutral_and_never_records_endpoint_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opener = _Opener()
    _install_opener(monkeypatch, opener)
    plan = replace(
        block_range_capture.FROZEN_ALCHEMY_FREE_PLAN,
        provider_alias="public_rpc",
        endpoint_origin="https://rpc.example.invalid",
        endpoint_env="MEMECOIN_PUBLIC_RPC_URL",
        account_alias="public_primary",
        source_alias="public_rpc_range_v1",
    ).checked()
    output = Path("work/block-range-provider-neutral")
    receipt = block_range_capture.capture_once(
        output,
        "https://rpc.example.invalid/credential-path-must-not-be-recorded",
        plan=plan,
    )
    assert receipt["provider_alias"] == "public_rpc"
    assert receipt["endpoint_origin"] == "https://rpc.example.invalid"
    serialized = "".join(
        path.read_text(errors="ignore") for path in output.rglob("*") if path.is_file()
    )
    assert "credential-path-must-not-be-recorded" not in serialized
