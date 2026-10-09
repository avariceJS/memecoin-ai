from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture
from memecoin_pumpswap.schema import SCHEMA
from tools.brain_senses_cross_provider_verify import _json_differences, run_once


class FakeRPC:
    source_alias = "helius-free"

    def __init__(self, tx: dict[str, Any], *, fail: bool = False) -> None:
        self.tx = tx
        self.fail = fail
        self.calls = 0

    def fetch(self, method: str, params: list[object]) -> Capture:
        self.calls += 1
        if self.fail:
            raise TimeoutError("fixture timeout")
        now = time.time_ns()
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response = canonical({"jsonrpc": "2.0", "id": 1, "result": self.tx}).encode()
        return Capture(self.source_alias, method, request, now, now, response)


def _public_root(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    import platform

    from memecoin_ai.sources import Capture
    from tools import brain_senses_program_watch

    root = tmp_path / "public"
    signature = "fixture-cross-provider-signature"
    signature_hash = hashlib.sha256(signature.encode()).hexdigest()
    tx: dict[str, Any] = {
        "slot": 42,
        "blockTime": 1_800_000_000,
        "version": 0,
        "transaction": {"message": {"instructions": []}},
        "meta": {"err": None},
    }
    params = [signature, {"encoding": "jsonParsed", "commitment": "finalized"}]
    request = canonical({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": params})
    response = canonical({"jsonrpc": "2.0", "id": 1, "result": tx}).encode()
    capture = Capture("solana-public", "getTransaction", request, 100, 200, response)
    capture_rel = f"captures/{signature_hash}"
    capture.save(root / capture_rel)
    status_dir = root / "transactions" / signature_hash
    status_dir.mkdir(parents=True)
    status = {
        "signature_sha256": signature_hash,
        "slot": 42,
        "status": "captured_success",
        "capture_path": capture_rel,
    }
    (status_dir / "status.json").write_text(canonical(status) + "\n")
    binding = {
        "schema_version": 1,
        "source_alias": "solana-public",
        "address": SCHEMA["address"],
        "collector_code_sha256": hashlib.sha256(
            Path(brain_senses_program_watch.__file__).read_bytes()
        ).hexdigest(),
        "python_version": platform.python_version(),
        "polling": {},
        "commitment": "finalized",
        "transaction_encoding": "jsonParsed",
        "transaction_version_max": 1,
        "coverage_claim": "provider_returned_address_signatures_only",
        "retry_policy": "no automatic retry",
    }
    (root / "state.json").write_text(canonical({"binding": binding}) + "\n")
    return root, tx


def test_json_comparison_distinguishes_missing_from_null() -> None:
    assert _json_differences({"x": None}, {}) == ["/x:missing_in_helius"]
    assert _json_differences({"x": None}, {"x": None}) == []
    assert _json_differences({"x": None}, {"x": 0}) == ["/x:value"]


def test_paired_verifier_pins_frame_and_does_not_repeat_completed_request(tmp_path: Path) -> None:
    public_root, tx = _public_root(tmp_path)
    output = tmp_path / "paired"
    rpc = FakeRPC(tx)
    first = run_once(
        public_root,
        output,
        slot_start=40,
        slot_end=50,
        random_count=1,
        target_count=0,
        pace_seconds=0.25,
        rpc_client=rpc,
    )
    assert first["completed"] == 1
    assert first["outcomes"] == {"field_equivalent": 1}
    # A new live public request outside this pinned frame must not invalidate resume.
    second = run_once(
        public_root,
        output,
        slot_start=40,
        slot_end=50,
        random_count=1,
        target_count=0,
        pace_seconds=0.25,
        rpc_client=rpc,
    )
    assert second["completed"] == 1
    assert rpc.calls == 1
    assert len(list((output / "requests").glob("*.json"))) == 1


def test_paired_verifier_records_timeout_once_without_retry(tmp_path: Path) -> None:
    public_root, tx = _public_root(tmp_path)
    output = tmp_path / "paired"
    rpc = FakeRPC(tx, fail=True)
    first = run_once(
        public_root,
        output,
        slot_start=40,
        slot_end=50,
        random_count=1,
        target_count=0,
        pace_seconds=0.25,
        rpc_client=rpc,
    )
    second = run_once(
        public_root,
        output,
        slot_start=40,
        slot_end=50,
        random_count=1,
        target_count=0,
        pace_seconds=0.25,
        rpc_client=rpc,
    )
    assert first["outcomes"] == {"request_failed": 1}
    assert second["completed"] == 1
    assert rpc.calls == 1


def test_paired_verifier_stops_on_ambiguous_reserved_request(tmp_path: Path) -> None:
    public_root, tx = _public_root(tmp_path)
    output = tmp_path / "paired"
    # Establish the immutable plan without making any provider request.
    from tools.brain_senses_cross_provider_verify import _build_plan, _create

    output.mkdir()
    for name in ("requests", "request-results", "captures", "observations", "events"):
        (output / name).mkdir()
    plan = _build_plan(public_root, 40, 50, 20261009, 1, 0)
    _create(output / "plan.json", plan)
    _create(output / "requests" / "000000000001.json", {"sequence": 1})
    rpc = FakeRPC(tx)
    try:
        run_once(
            public_root,
            output,
            slot_start=40,
            slot_end=50,
            random_count=1,
            target_count=0,
            rpc_client=rpc,
        )
    except ValueError as exc:
        assert str(exc) == "ambiguous_reserved_request_requires_manual_reconciliation"
    else:
        raise AssertionError("ambiguous reservation must stop without retry")
    assert rpc.calls == 0
