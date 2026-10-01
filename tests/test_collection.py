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


def setup_store(path):
    cap, pool = checkpoint()
    cap = replace(cap, response=cap.response.replace(b'"slot":101', b'"slot":100'))
    end = mutate(
        mutate(cap, 2, 64, (10090).to_bytes(8, "little")), 3, 64, (9960).to_bytes(8, "little")
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
            signatures=[sig], message=dict(accountKeys=[dict(pubkey=v) for v in vaults])
        ),
        meta=dict(err=None, preTokenBalances=balances(before), postTokenBalances=balances(after)),
    )


def fixture_client(info, fail_once=False):
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
            result = dict(signatures=["z-first", "a-second"], blockhash="fixture")
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
    store, _ = setup_store(tmp_path / "s.sqlite")
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
