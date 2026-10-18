"""Admitted history enters real AUTO snapshots without changing the live growth namespace."""

import json
import time

import pytest

from memecoin_brain.data import load_dataset
from memecoin_curriculum.admission import compile_corpus
from memecoin_ml.data import read, sha
from memecoin_operator import adapters, deployment
from memecoin_operator.learning import partitions
from memecoin_operator.readmodel import coverage
from memecoin_registry.contracts import digest, seal
from memecoin_runner.ledger import publish

from .test_adapters import bind_source, index_source, operator_config


def historical(tmp_path, end):
    birth = dict(
        pool="A" * 32,
        base_mint="B" * 32,
        quote_mint="C" * 32,
        event_s=end - 10 * 86400,
        month="synthetic",
    )
    capture = tmp_path / "historical-capture"
    capture.mkdir()
    bars = [[birth["event_s"] + i * 300, 1, 1.1, 0.9, 1.0, 100] for i in range(100)]
    (capture / "response.bin").write_text(
        json.dumps(
            dict(
                data=dict(attributes=dict(ohlcv_list=bars)),
                meta=dict(
                    base=dict(address=birth["base_mint"]), quote=dict(address=birth["quote_mint"])
                ),
            )
        )
    )
    now = time.time_ns()
    publish(
        capture / "request.json",
        dict(
            started_ns=now - 2,
            lane="historical",
            url=(
                f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{birth['pool']}"
                "/ohlcv/minute?aggregate=5&currency=usd&token=base&include_empty_intervals=false"
                f"&before_timestamp={birth['event_s'] + 3 * 86400}"
            ),
        ),
    )
    publish(
        capture / "receipt.json",
        dict(received_ns=now - 1, available_ns=now, response_sha256=sha(capture / "response.bin")),
    )
    seal(capture)
    corpus = tmp_path / "corpus"
    compile_corpus([(capture, birth)], corpus)
    return dict(path=str(corpus), sha=sha(corpus / "manifest.json"))


def test_snapshot_adds_new_pools_preserves_evaluation_and_next_growth(tmp_path, monkeypatch):
    now = time.time_ns()
    end = now // 10**9 // 300 * 300
    source = bind_source(tmp_path / "source", now + 86400 * 10**9)
    index_source(source, end)
    root = tmp_path / "operator"
    operator_config(root, source, now + 86400 * 10**9)
    ref = historical(tmp_path, end)
    monkeypatch.setattr(adapters.time, "time_ns", lambda: now + 10**9)
    control = root / "control"
    control.mkdir()
    old = adapters.snapshot(root, control)
    deployment.stage(root, tmp_path / "runtime", historical_training_corpora=[ref])
    broad = root / "broad"
    broad.mkdir()
    result = adapters.snapshot(root, broad)
    assert result["retained"]["train"]["pools"] == old["retained"]["train"]["pools"] + 1
    assert result["matured_samples"] == coverage(source, now + 10**9, ttl_s=0)["matured_samples"]
    assert result["training_matured_samples"] == old["matured_samples"] + 90
    assert result["matured_namespace"] == "continuous_source_only"
    assert result["historical_training_corpora"] == [ref]
    a, _ = partitions(load_dataset(control / "dataset"), now + 10**9)
    b, _ = partitions(load_dataset(broad / "dataset"), now + 10**9)
    assert digest(a["validation"]) == digest(b["validation"])
    assert digest(a["test"]) == digest(b["test"])
    assert read(tmp_path / "runtime/binding.json")["historical_training_corpora"] == [ref]
    # Replacing the reference or source bytes cannot silently alter a later refit.
    (tmp_path / "corpus/rows.jsonl").write_text("{}\n")
    rejected = root / "rejected"
    rejected.mkdir()
    with pytest.raises(ValueError, match="inventory"):
        adapters.snapshot(root, rejected)
