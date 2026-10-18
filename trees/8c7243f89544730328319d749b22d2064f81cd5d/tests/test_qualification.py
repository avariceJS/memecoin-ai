import copy
import json
import shutil
from pathlib import Path

import pytest

from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.qualification import qualify_cpmm, reconcile_vault_transfers, verify_qualification
from memecoin_ai.raydium import decode_checkpoint, swap_candidates
from memecoin_ai.sources import Capture, ReadOnlyRPC, SourceError

FIXTURE = Path(__file__).parent / "fixtures/cpmm_control"


def test_real_capture_reproduces_checkpoint_and_transaction_flows():
    result = verify_qualification(FIXTURE)
    assert result["checkpoints_reproduced"] == 1
    assert result["reconciliations_reproduced"] == 2
    report = json.loads((FIXTURE / "report.json").read_text())
    assert all(item["all_match"] for item in report["reconciliations"])
    versions = {Capture.load(p).result()["version"] for p in FIXTURE.glob("transaction-*")}
    assert 1 in versions and 0 in versions
    for p in FIXTURE.glob("transaction-*"):
        swap_candidates(Capture.load(p).result())


def test_real_v1_complete_offline_pipeline(tmp_path):
    captures = {
        Capture.load(p).request_json: Capture.load(p) for p in FIXTURE.iterdir() if p.is_dir()
    }

    def transport(endpoint, request, timeout, limit):
        return captures[request.decode()].response

    clock = iter(range(1000, 10000))
    rpc = ReadOnlyRPC(
        "https://example.invalid", "fixture", transport=transport, clock=lambda: next(clock)
    )
    original = json.loads((FIXTURE / "report.json").read_text())
    report = qualify_cpmm(
        rpc,
        tmp_path / "audit",
        limit=5,
        discovery_address=original["spec"]["discovery_address"],
        request_interval_s=0,
    )
    assert report["transactions_received"] == 5
    assert report["pools_discovered"] == 2
    assert len(report["checkpoints"]) == 1
    assert len(report["failures"]) == 1
    assert report["market_dataset_qualified"] is False
    verified = verify_qualification(tmp_path / "audit")
    assert verified["checkpoints_reproduced"] == 1
    assert verified["decoder_matches_recorded"] is True
    with pytest.raises(FileExistsError):
        qualify_cpmm(rpc, tmp_path / "audit")


def test_token2022_sample_is_preserved_but_rejected():
    capture = Capture.load(FIXTURE / "pool-000-checkpoint")
    pool_id = json.loads(capture.request_json)["params"][0][0]
    with pytest.raises(ValidationError, match="Token-2022"):
        decode_checkpoint(capture, pool_id)


def test_reconciliation_detects_tampered_flow_and_missing_metadata():
    report = json.loads((FIXTURE / "report.json").read_text())
    item = report["reconciliations"][0]
    tx = Capture.load(FIXTURE / item["transaction"]).result()
    vaults = list(item["vaults"])
    damaged = copy.deepcopy(tx)
    keys = [k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
    index = keys.index(vaults[0])
    balance = next(b for b in damaged["meta"]["postTokenBalances"] if b["accountIndex"] == index)
    balance["uiTokenAmount"]["amount"] = str(int(balance["uiTokenAmount"]["amount"]) + 1)
    assert reconcile_vault_transfers(damaged, vaults)["all_match"] is False
    damaged["meta"]["preTokenBalances"] = []
    with pytest.raises(ValidationError, match="balance metadata"):
        reconcile_vault_transfers(damaged, vaults)
    with pytest.raises(ValidationError, match="distinct"):
        reconcile_vault_transfers(tx, [])


@pytest.mark.parametrize("change", ["receipt", "claim", "path", "derived"])
def test_audit_integrity_guards(tmp_path, change):
    shutil.copytree(FIXTURE, tmp_path / "audit")
    path = tmp_path / "audit"
    report = json.loads((path / "report.json").read_text())
    if change == "receipt":
        p = path / "discovery/receipt.json"
        obj = json.loads(p.read_text())
        obj["received_ns"] += 1
        p.write_text(canonical(obj))
    elif change == "claim":
        report["market_dataset_qualified"] = True
    elif change == "path":
        report["captures"]["../elsewhere"] = {}
    else:
        report["checkpoints"][0]["effective_reserves"][0] += 1
    (path / "report.json").write_text(canonical(report))
    with pytest.raises((ValidationError, SourceError)):
        verify_qualification(path)


def test_source_failure_is_not_empty_success(tmp_path):
    def broken(*args):
        raise SourceError("RPC transport failed; endpoint details redacted")

    rpc = ReadOnlyRPC("https://example.invalid", "fixture", transport=broken)
    report = qualify_cpmm(rpc, tmp_path / "audit", request_interval_s=0)
    assert len(report["failures"]) == 1
    assert report["checkpoints"] == []
    assert report["market_dataset_qualified"] is False


def test_real_token2022_inventory_does_not_imply_execution_support():
    from memecoin_ai.raydium import TOKEN_2022, checkpoint_inventory

    capture = Capture.load(FIXTURE / "pool-000-checkpoint")
    pool_id = json.loads(capture.request_json)["params"][0][0]
    inventory = checkpoint_inventory(capture, pool_id)
    assert inventory["execution_qualified"] is False
    extended = [mint for mint in inventory["mint_inventory"] if mint["owner"] == TOKEN_2022]
    assert extended and extended[0]["extensions"]
