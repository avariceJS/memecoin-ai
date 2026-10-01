import copy
import json
from dataclasses import replace

import pytest
from test_raydium import checkpoint, mutate

from memecoin_ai.collection import BudgetExhausted, PilotCollector, PilotStore
from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.raydium import TOKEN, decode_checkpoint
from memecoin_ai.reconciliation import audit_pilot, vault_transition, write_audit
from memecoin_ai.sources import ReadOnlyRPC, SourceError


def setup_store(path, ending=(10090, 9960)):
    cap, pool = checkpoint()
    cap = replace(cap, response=cap.response.replace(b'"slot":101', b'"slot":100'))
    end = mutate(
        mutate(cap, 2, 64, ending[0].to_bytes(8, "little")), 3, 64, ending[1].to_bytes(8, "little")
    )
    end = replace(end, received_ns=500, response=end.response.replace(b'"slot":100', b'"slot":110'))
    store = PilotStore(path)
    store.configure([pool], "fixture", 2)
    store.checkpoint(store.save_capture(cap), pool)
    store.checkpoint(store.save_capture(end), pool)
    info = decode_checkpoint(cap, pool)
    return store, info


def transaction(info, sig, before, after):
    vaults = info["pool"]["vaults"]
    mints = info["pool"]["mints"]

    def balances(amounts):
        return [
            dict(
                accountIndex=i,
                mint=mints[i],
                programId=TOKEN,
                uiTokenAmount=dict(amount=str(amounts[i])),
            )
            for i in range(2)
        ]

    return dict(
        slot=110,
        version=1,
        transaction=dict(
            signatures=[sig],
            message=dict(accountKeys=[dict(pubkey=v) for v in [*vaults, info["pool_id"]]]),
        ),
        meta=dict(err=None, preTokenBalances=balances(before), postTokenBalances=balances(after)),
    )


def fixture_client(info, fail_once=False, reverse_block=False):
    calls = []
    clock = iter(range(1000, 10000))
    failed = False

    def transport(endpoint, body, timeout, limit):
        nonlocal failed
        request = json.loads(body)
        method = request["method"]
        params = request["params"]
        calls.append(request)
        if method == "getSignaturesForAddress":
            if params[1].get("before") is None:
                result = [
                    dict(signature="future", slot=111, confirmationStatus="finalized"),
                    dict(signature="a-second", slot=110, confirmationStatus="finalized"),
                ]
            else:
                assert params[1]["before"] == "a-second"
                result = [
                    dict(signature="z-first", slot=110, confirmationStatus="finalized"),
                    dict(signature="boundary", slot=100, confirmationStatus="finalized"),
                ]
        elif method == "getTransaction":
            if fail_once and not failed:
                failed = True
                raise SourceError("injected transport outage")
            result = transaction(
                info,
                params[0],
                (10000, 10000) if params[0] == "z-first" else (10100, 9950),
                (10100, 9950) if params[0] == "z-first" else (10090, 9960),
            )
        elif method == "getBlock":
            result = dict(
                signatures=["a-second", "z-first"] if reverse_block else ["z-first", "a-second"],
                blockhash="fixture",
            )
        else:
            raise AssertionError(method)
        return canonical(dict(jsonrpc="2.0", id=1, result=result)).encode()

    return ReadOnlyRPC(
        "https://example.invalid", "fixture", transport=transport, clock=lambda: next(clock)
    ), calls


def test_durable_pagination_restart_and_ordered_reconstruction(tmp_path):
    path = tmp_path / "journal.sqlite"
    store, info = setup_store(path)
    client, calls = fixture_client(info)
    collector = PilotCollector(store, client, budget=1, delay=0)
    with pytest.raises(BudgetExhausted):
        collector.recover()
    assert not audit_pilot(store)["all_intervals_consistent"]
    assert store.db.execute("SELECT before_sig FROM scans WHERE id=1").fetchone()[0] == "a-second"
    store.close()
    store = PilotStore(path)
    PilotCollector(store, client, budget=30, delay=0).recover()
    result = audit_pilot(store)
    assert result["all_intervals_consistent"]
    interval = result["intervals"][0]
    assert interval["residuals"] == [0, 0]
    assert [s["signature"] for s in interval["steps"]] == ["z-first", "a-second"]
    assert interval["signatures"] == 2  # future and start-bank transactions excluded
    assert result["market_dataset_qualified"] is False
    assert not interval["full_pool_state_reconstructed"]
    count = len(calls)
    PilotCollector(store, client, budget=30, delay=0).recover()
    assert len(calls) == count
    write_audit(store, tmp_path / "report.json")
    with pytest.raises(FileExistsError):
        write_audit(store, tmp_path / "report.json")
    store.close()


def test_transaction_failure_remains_pending_until_recovery(tmp_path):
    store, info = setup_store(tmp_path / "s.sqlite")
    client, _ = fixture_client(info, True)
    PilotCollector(store, client, budget=30, delay=0).recover()
    first = audit_pilot(store)
    assert not first["all_intervals_consistent"]
    assert first["issues"]
    PilotCollector(store, client, budget=30, delay=0).recover()
    assert audit_pilot(store)["all_intervals_consistent"]
    assert store.db.execute("SELECT count(*) FROM issues").fetchone()[0] == 1
    store.close()


def test_equal_terminal_balances_do_not_close_unfetched_scans(tmp_path):
    store, _ = setup_store(tmp_path / "s.sqlite", (10000, 10000))
    result = audit_pilot(store)
    assert not result["all_intervals_consistent"]
    assert not result["intervals"][0]["scans_complete"]
    store.close()


def test_empty_or_wrong_page_cannot_advance_cursor(tmp_path):
    store, info = setup_store(tmp_path / "s.sqlite")
    client, _ = fixture_client(info)
    scan = store.db.execute("SELECT * FROM scans LIMIT 1").fetchone()
    cap = client.fetch(
        "getSignaturesForAddress",
        [scan["address"], dict(commitment="finalized", minContextSlot=110, limit=2)],
    )
    cap = replace(cap, response=canonical(dict(jsonrpc="2.0", id=1, result=[])).encode())
    with pytest.raises(ValidationError, match="empty"):
        store.apply_page(scan["id"], store.save_capture(cap))
    assert store.db.execute("SELECT before_sig FROM scans LIMIT 1").fetchone()[0] is None
    assert store.db.execute("SELECT count(*) FROM signatures").fetchone()[0] == 0
    store.close()


def test_missing_metadata_failed_transaction_and_amount_guards(tmp_path):
    store, info = setup_store(tmp_path / "s.sqlite")
    tx = transaction(info, "s", (10000, 10000), (10000, 10000))
    vault = info["pool"]["vaults"][0]
    mint = info["pool"]["mints"][0]
    failed = copy.deepcopy(tx)
    failed["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    assert vault_transition(failed, vault, mint) == (10000, 10000)
    failed["meta"]["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "1"
    with pytest.raises(ValidationError, match="failed transaction"):
        vault_transition(failed, vault, mint)
    tx["meta"]["postTokenBalances"] = []
    with pytest.raises(ValidationError, match="closure not assumed zero"):
        vault_transition(tx, vault, mint)
    store.close()


def test_spec_and_checkpoint_identity_are_immutable(tmp_path):
    store, info = setup_store(tmp_path / "s.sqlite")
    with pytest.raises(ValidationError, match="immutable"):
        store.configure([info["pool_id"]], "different", 2)
    cap = store.capture(2)
    with pytest.raises(ValidationError, match="conflicting"):
        store.checkpoint(
            store.save_capture(mutate(cap, 2, 64, (20000).to_bytes(8, "little"))), info["pool_id"]
        )
    with pytest.raises(ValidationError, match="regressed"):
        store.checkpoint(store.save_capture(store.capture(1)), info["pool_id"])
    store.close()


def test_raw_and_cache_tampering_detected(tmp_path):
    store, info = setup_store(tmp_path / "s.sqlite")
    client, _ = fixture_client(info)
    PilotCollector(store, client, budget=30, delay=0).recover()
    with store.db:
        store.db.execute("UPDATE scans SET before_sig='invented' WHERE id=1")
    with pytest.raises(ValidationError, match="cache"):
        audit_pilot(store)
    store.close()


def test_single_writer_read_only_audit_and_export(tmp_path):
    from memecoin_ai.reconciliation import export_pilot, verify_pilot

    path = tmp_path / "s.sqlite"
    store, info = setup_store(path)
    with pytest.raises(ValidationError, match="another collector"):
        PilotStore(path)
    reader = PilotStore(path, read_only=True)
    assert not audit_pilot(reader)["all_intervals_consistent"]
    reader.close()
    client, _ = fixture_client(info)
    PilotCollector(store, client, budget=30, delay=0).recover()
    export_pilot(store, tmp_path / "bundle")
    verified = verify_pilot(tmp_path / "bundle")
    assert verified["consistent"] and verified["nonempty_intervals"] == 1
    assert verify_pilot(tmp_path / "bundle") == verified
    (tmp_path / "bundle/report.json").write_text("{}")
    with pytest.raises(ValidationError, match="checksum"):
        verify_pilot(tmp_path / "bundle")
    store.close()


def test_equal_terminal_sum_cannot_hide_wrong_intr_slot_order(tmp_path):
    store, info = setup_store(tmp_path / "s.sqlite")
    client, _ = fixture_client(info, reverse_block=True)
    PilotCollector(store, client, budget=30, delay=0).recover()
    result = audit_pilot(store)["intervals"][0]
    assert result["residuals"] == [0, 0]
    assert not result["consistency_passed"]
    assert any("continuity mismatch" in b for b in result["blockers"])
    store.close()


def test_page_size_can_change_without_rewriting_existing_receipts(tmp_path):
    store, info = setup_store(tmp_path / "s.sqlite")
    client, calls = fixture_client(info)
    collector = PilotCollector(store, client, budget=1, delay=0)
    with pytest.raises(BudgetExhausted):
        collector.recover()
    PilotCollector(store, client, budget=30, delay=0, page_size=100).recover()
    assert calls[0]["params"][1]["limit"] == 2
    assert calls[1]["params"][1]["limit"] == 100
    assert audit_pilot(store)["all_intervals_consistent"]
    store.close()


def test_unobserved_cohort_member_cannot_be_hidden_by_good_interval(tmp_path):
    from memecoin_ai.raydium import b58encode

    store, info = setup_store(tmp_path / "s.sqlite")
    # Emulate a cohort member with failed initial checkpoints, retaining its fixed inclusion.
    spec = store.spec()
    spec["pools"].append(b58encode(bytes([99]) * 32))
    with store.db:
        store.db.execute("UPDATE settings SET body=?", (canonical(spec),))
    client, _ = fixture_client(info)
    PilotCollector(store, client, budget=30, delay=0).recover()
    result = audit_pilot(store)
    assert result["intervals"][0]["consistency_passed"]
    assert not result["all_intervals_consistent"]
    store.close()


def test_cli_recovery_tuning_reaches_actual_requests(tmp_path, monkeypatch):
    import sys

    from memecoin_ai import cli

    path = tmp_path / "s.sqlite"
    store, info = setup_store(path)
    store.close()
    client, calls = fixture_client(info)
    monkeypatch.setattr(cli, "ReadOnlyRPC", lambda endpoint, source: client)
    monkeypatch.setenv("MEMECOIN_RPC_URL", "https://example.invalid")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mcai",
            "recover-pilot",
            "--store",
            str(path),
            "--source-alias",
            "fixture",
            "--rpc-interval",
            "0",
            "--recovery-page-size",
            "100",
        ],
    )
    assert cli.main() == 0
    requests = [c for c in calls if c["method"] == "getSignaturesForAddress"]
    assert requests and all(c["params"][1]["limit"] == 100 for c in requests)


def test_finalized_pages_are_shared_across_adjacent_intervals(tmp_path):
    cap, pool = checkpoint()
    store = PilotStore(tmp_path / "s.sqlite")
    store.configure([pool], "fixture", 2)
    store.checkpoint(store.save_capture(cap), pool)
    middle = replace(cap, response=cap.response.replace(b'"slot":101', b'"slot":105'))
    store.checkpoint(store.save_capture(middle), pool)
    end = mutate(
        mutate(cap, 2, 64, (10090).to_bytes(8, "little")), 3, 64, (9960).to_bytes(8, "little")
    )
    end = replace(end, response=end.response.replace(b'"slot":101', b'"slot":110'))
    store.checkpoint(store.save_capture(end), pool)
    info = decode_checkpoint(cap, pool)
    client, calls = fixture_client(info)
    PilotCollector(store, client, budget=30, delay=0).recover()
    assert audit_pilot(store)["all_intervals_consistent"]
    assert len([c for c in calls if c["method"] == "getSignaturesForAddress"]) == 6
    store.close()


def test_conflicting_page_is_atomic(tmp_path):
    store, info = setup_store(tmp_path / "s.sqlite")
    client, _ = fixture_client(info)
    scan = store.db.execute("SELECT * FROM scans LIMIT 1").fetchone()
    cap = client.fetch(
        "getSignaturesForAddress",
        [scan["address"], dict(commitment="finalized", minContextSlot=110, limit=2)],
    )
    payload = json.loads(cap.response)
    payload["result"] = [payload["result"][1], payload["result"][1]]
    cap = replace(cap, response=canonical(payload).encode())
    with pytest.raises(ValidationError, match="duplicate"):
        store.apply_page(scan["id"], store.save_capture(cap))
    assert store.db.execute("SELECT count(*) FROM signatures").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM pages").fetchone()[0] == 0
    assert store.db.execute("SELECT before_sig FROM scans LIMIT 1").fetchone()[0] is None
    store.close()


def test_pilot_version_requires_explicit_opt_in(tmp_path):
    import json
    from pathlib import Path

    from memecoin_ai.collection import PilotStore
    from memecoin_ai.sources import Capture

    cap = Capture.load(Path(__file__).parent / "fixtures" / "token2022" / "checkpoint-0")
    pool = json.loads(cap.request_json)["params"][0][0]
    store = PilotStore(tmp_path / "pilot.sqlite")
    try:
        store.configure([pool], cap.source_alias, 100)
        with pytest.raises(ValidationError, match="checkpoint version"):
            store.checkpoint(store.save_capture(cap), pool)
    finally:
        store.close()
