"""Synthetic journal/index around real regression transactions; not new RPC evidence."""

import copy
import json
from dataclasses import replace

import pytest
from test_protocol import FIXTURE, cases
from test_raydium import mutate

from memecoin_ai.collection import PilotCollector, PilotStore
from memecoin_ai.datasets import (
    conditional_quotes,
    dataset_spec,
    export_economic_dataset,
    reconstruct_dataset,
    verify_economic_dataset,
)
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.protocol import audit_protocol, checkpoint, economic_observation
from memecoin_ai.sources import Capture, ReadOnlyRPC


def journal(path, *, bad_endpoint=False, late_ns=0):
    case = cases()[1]
    alias = "synthetic-index-around-regression"
    start = replace(Capture.load(FIXTURE / str(case["start"])), source_alias=alias)
    end = replace(Capture.load(FIXTURE / str(case["end"])), source_alias=alias)
    if bad_endpoint:
        amount = checkpoint(end, case["pool"]).state.vaults[0] + 1
        end = mutate(end, 2, 64, amount.to_bytes(8, "little"))
    txs = [Capture.load(FIXTURE / str(i)).result() for i in case["transactions"]]
    by_signature = {tx["transaction"]["signatures"][0]: tx for tx in txs}
    first_slot = start.result()["context"]["slot"]

    def transport(endpoint, body, timeout, limit):
        req = json.loads(body)
        if req["method"] == "getSignaturesForAddress":
            relevant = [
                tx
                for tx in reversed(txs)
                if req["params"][0]
                in {k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]}
            ]
            result = [
                dict(
                    signature=t["transaction"]["signatures"][0],
                    slot=t["slot"],
                    confirmationStatus="finalized",
                )
                for t in relevant
            ]
            result.append(
                dict(
                    signature="synthetic-boundary", slot=first_slot, confirmationStatus="finalized"
                )
            )
        elif req["method"] == "getTransaction":
            result = by_signature[req["params"][0]]
        else:
            assert req["method"] == "getBlock"
            result = dict(
                signatures=[
                    t["transaction"]["signatures"][0] for t in txs if t["slot"] == req["params"][0]
                ]
            )
        return canonical(dict(jsonrpc="2.0", id=1, result=result)).encode()

    store = PilotStore(path)
    store.configure([case["pool"]], alias, 100)
    store.checkpoint(store.save_capture(start), case["pool"])
    store.checkpoint(store.save_capture(end), case["pool"])
    clock = iter(range(end.received_ns + 1 + late_ns, end.received_ns + 1000 + late_ns))
    client = ReadOnlyRPC(
        "https://example.invalid", alias, transport=transport, clock=lambda: next(clock)
    )
    PilotCollector(store, client, delay=0).recover()
    return store


def test_real_economic_path_preserves_transactions_and_unknown_availability(tmp_path):
    store = journal(tmp_path / "input.sqlite")
    try:
        before = audit_protocol(store)
        result = reconstruct_dataset(store, dataset_spec([10, 100]))
        assert result["report"]["protocol_audit"] == before
        assert before["all_intervals_consistent"]
        rows = result["states"]
        txs = [r for r in rows if r["phase"] == "transaction"]
        assert len(txs) == len(cases()[1]["transactions"])
        assert rows[-1]["state"] == rows[-2]["state"]
        assert [r["signature"] for r in txs] == [
            s["signature"] for s in before["intervals"][0]["steps"]
        ]
        assert all(r["historical_available_ns"] is None for r in rows)
        assert all(r["evidence_ready_ns"] >= r["source_received_ns"] for r in rows)
        for prior, row in zip(rows, rows[1:], strict=False):
            if row.get("failed"):
                assert row["state"] == prior["state"]
        assert not result["report"]["training_ready"]
        assert all(
            q["immediate_roundtrip_loss"] >= 0 for q in result["quotes"] if q["status"] == "quoted"
        )
    finally:
        store.close()


def test_late_receipts_cannot_be_promoted_to_historical_availability(tmp_path):
    a = journal(tmp_path / "a.sqlite")
    b = journal(tmp_path / "b.sqlite", late_ns=10**12)
    try:
        x = reconstruct_dataset(a, dataset_spec([100]))
        y = reconstruct_dataset(b, dataset_spec([100]))
        assert x["quotes"] == y["quotes"]
        assert [r["state"] for r in x["states"]] == [r["state"] for r in y["states"]]
        assert y["report"]["evidence_ready_ns"] - x["report"]["evidence_ready_ns"] == 10**12
        assert all(r["historical_available_ns"] is None for r in y["states"])
    finally:
        a.close()
        b.close()


def test_endpoint_disagreement_quarantines_whole_segment_without_repair(tmp_path):
    store = journal(tmp_path / "bad.sqlite", bad_endpoint=True)
    try:
        result = reconstruct_dataset(store, dataset_spec([100]))
        assert result["report"]["counts"]["quarantined_segments"] == 1
        assert result["states"] == result["quotes"] == []
        assert result["report"]["protocol_audit"]["intervals"][0]["blockers"]
    finally:
        store.close()


def test_standalone_bundle_reproduces_and_detects_rehashed_derived_tamper(tmp_path):
    store = journal(tmp_path / "input.sqlite")
    try:
        a, b = tmp_path / "one", tmp_path / "two"
        report = export_economic_dataset(store, a, sizes_ppm=[100])
        export_economic_dataset(store, b, sizes_ppm=[100])
        assert verify_economic_dataset(a) == report
        for name in ("states.jsonl", "quotes.jsonl", "report.json"):
            assert (a / name).read_bytes() == (b / name).read_bytes()
        with pytest.raises(FileExistsError):
            export_economic_dataset(store, a, sizes_ppm=[100])
        rows = [json.loads(line) for line in (a / "states.jsonl").read_text().splitlines()]
        rows[0]["historical_available_ns"] = 1
        (a / "states.jsonl").write_text("".join(canonical(r) + "\n" for r in rows))
        manifest = json.loads((a / "manifest.json").read_text())
        manifest["files"]["states.jsonl"] = digest((a / "states.jsonl").read_bytes())
        (a / "manifest.json").write_text(canonical(manifest))
        with pytest.raises(ValidationError, match="reproduction"):
            verify_economic_dataset(a)
    finally:
        store.close()


def test_token2022_quote_surface_is_pure_and_preserves_transfer_deductions():
    path = FIXTURE.parent / "token2022/checkpoint-2"
    cap = Capture.load(path)
    pool = json.loads(cap.request_json)["params"][0][0]
    ctx = checkpoint(cap, pool)
    row = economic_observation(ctx, ctx.state, list(ctx.withheld), cap.result()["context"]["slot"])
    row.update(state_id="synthetic-quote-probe", chain_timestamp_seconds=ctx.pool.open_time + 1)
    before = copy.deepcopy(row)
    quotes = conditional_quotes(row, [100, 1000])
    assert row == before
    assert all(q["status"] == "quoted" for q in quotes)
    assert any(q["input_transfer_fee"] > 0 for q in quotes)
    assert any(q["output_transfer_fee"] > 0 for q in quotes)
    assert all(q["immediate_roundtrip_loss"] > 0 for q in quotes)
    row["chain_timestamp_seconds"] = None
    assert all(q["status"] == "unavailable" for q in conditional_quotes(row, [100]))


@pytest.mark.parametrize("sizes", [[], [0], [True], [10001], [10, 10], [100, 10]])
def test_invalid_sizing_grid_fails_before_export(sizes):
    with pytest.raises(ValidationError):
        dataset_spec(sizes)


def test_parameter_snapshot_uses_slot_epoch_without_mutating_or_looking_at_endpoint():
    from test_history import anchor, fee_update

    cap, history = anchor()
    ctx = checkpoint(cap, history.addresses[0])
    before = economic_observation(ctx, ctx.state, list(ctx.withheld), history.slot, history)
    history.apply(fee_update(history, bps=123), has_children=False)
    activation = (
        history.schedule.first_normal_slot
        + (history.epoch + 2 - history.schedule.first_normal_epoch)
        * history.schedule.slots_per_epoch
    )
    original_slot = history.slot
    later = economic_observation(ctx, ctx.state, list(ctx.withheld), activation, history)
    assert history.slot == original_slot
    assert before["token_fees"][1]["bps"] != later["token_fees"][1]["bps"]
    assert later["token_fees"][1]["bps"] == 123
    assert before["epoch"] + 2 == later["epoch"]
