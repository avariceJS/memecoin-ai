"""Synthetic contract failures and receipt causality, with no provider calls."""

import copy
import json

import pytest

from memecoin_brain.source import EXCLUDED
from memecoin_curriculum.admission import (
    compile_corpus,
    disjoint,
    load_corpus,
    merge_history,
    qualify,
)
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish


def capture(tmp_path, *, gap=False, wrong=False, currency="usd", empty=False):
    root = tmp_path / "capture"
    root.mkdir()
    birth = dict(
        pool="A" * 32, base_mint="B" * 32, quote_mint="C" * 32, event_s=300000, month="synthetic"
    )
    available = 10**18
    rows = [
        [birth["event_s"] + i * 300, 1.0, 1.1, 0.9, 1.0 + i * 0.0001, 100]
        for i in range(60)
        if not gap or i % 6 != 0
    ]
    body = dict(
        data=dict(attributes=dict(ohlcv_list=[] if empty else rows)),
        meta=dict(
            base=dict(address="wrong" if wrong else birth["base_mint"]),
            quote=dict(address=birth["quote_mint"]),
        ),
    )
    (root / "response.bin").write_text(json.dumps(body))
    publish(
        root / "request.json",
        dict(
            started_ns=available - 2,
            lane="historical",
            url=(
                f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{birth['pool']}"
                f"/ohlcv/minute?aggregate=5&currency={currency}&token=base"
                f"&include_empty_intervals=false&before_timestamp={birth['event_s'] + 3 * 86400}"
            ),
        ),
    )
    publish(
        root / "receipt.json",
        dict(
            available_ns=available,
            received_ns=available - 1,
            response_sha256=sha(root / "response.bin"),
        ),
    )
    seal(root)
    return root, birth


def test_admission_never_fills_gaps_and_reports_no_support(tmp_path):
    c, birth = capture(tmp_path, gap=True)
    rows, report = qualify(c, birth)
    assert len(rows) == 50
    assert report["missing_intervals"] > 0
    assert report["eligible"] == 0
    assert report["status"] == "no_contiguous_targets"
    assert report["historical_online_availability_known"] is False
    assert {r["available_ns"] for r in rows} == {10**18}


@pytest.mark.parametrize(
    "kwargs,reason",
    [({"wrong": True}, "mint_mismatch"), ({"currency": "token"}, "price_namespace")],
)
def test_foreign_price_or_wrong_base_cannot_enter_training(tmp_path, kwargs, reason):
    c, birth = capture(tmp_path, **kwargs)
    with pytest.raises(ValueError, match=reason):
        qualify(c, birth)


def test_birth_range_and_protected_market_rejected(tmp_path):
    c, birth = capture(tmp_path)
    later = dict(birth, event_s=birth["event_s"] + 300)
    request = read(c / "request.json")
    request["url"] = request["url"].replace(
        str(birth["event_s"] + 3 * 86400), str(later["event_s"] + 3 * 86400)
    )
    (c / "request.json").write_text(json.dumps(request))
    (c / "manifest.json").unlink()
    seal(c)
    with pytest.raises(ValueError, match="birth_range"):
        qualify(c, later)
    with pytest.raises(ValueError, match="protected_pool"):
        qualify(c, dict(birth, pool=next(iter(EXCLUDED))))


def test_sealed_admission_cutoff_pin_and_first_receipt_merge(tmp_path):
    c, birth = capture(tmp_path)
    root = tmp_path / "corpus"
    r = compile_corpus([(c, birth)], root)
    assert r["eligible_pools"] == 1
    assert r["eligible_decisions"] == 50
    ref = dict(path=str(root), sha=sha(root / "manifest.json"))
    rows = load_corpus(ref, 10**18)
    with pytest.raises(ValueError, match="availability"):
        load_corpus(ref, 10**18 - 1)
    with pytest.raises(ValueError, match="pin"):
        load_corpus(dict(ref, sha="0" * 64), 10**18)
    old = copy.deepcopy(rows[:1])
    old[0]["available_ns"] -= 1
    old[0]["ohlcv"][3] = 0.98
    result, prov = merge_history(old, dict(as_of_ns=10**18), [ref])
    assert result[0] == old[0]
    assert len(result) == len(rows)
    assert len(prov["historical_merge_revisions"]) == 1
    assert len(prov["merged_rows_sha"]) == 64
    (root / "rows.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="inventory"):
        load_corpus(ref, 10**18)


def test_quarantine_is_counted_and_duplicate_pool_not_double_counted(tmp_path):
    c, b = capture(tmp_path, wrong=True)
    r = compile_corpus([(c, b)], tmp_path / "bad")
    assert r["bars"] == r["eligible_pools"] == r["eligible_decisions"] == 0
    assert len(r["rejected"]) == 1
    with pytest.raises(ValueError, match="duplicate_pool"):
        compile_corpus([(c, b), (c, b)], tmp_path / "duplicate")


def test_disjoint_accounts_for_features_before_first_context_frame():
    # Feature dependencies span15 input bars plus3 future bars, not only8+3.
    samples = [
        dict(pool="a", event_close_s=i * 300, maturity_s=(i + 3) * 300) for i in range(20, 56)
    ]
    assert disjoint(samples) == 2
