"""Qualification harness regression uses synthetic sources only."""

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("torch")

from curriculum.test_admission import capture  # noqa: E402

from memecoin_continuous.store import connect, ingest  # noqa: E402
from memecoin_experience.continuous import export  # noqa: E402
from memecoin_experience.storage import atomic_json, file_hash, seal_files  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "experience_qualify", Path(__file__).parents[1] / "tools/experience_qualify.py"
)
qualify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualify)


def test_fixed_qualification_composition_generations_registration_and_score(tmp_path):
    cap, birth = capture(tmp_path)
    payload = qualify.read(cap / "response.bin")
    payload["data"]["attributes"]["ohlcv_list"] = [
        [300000 + i * 300, 1.0, 1.1, 0.9, 1.0 + (i % 30) * 0.001, 100.0] for i in range(700)
    ]
    (cap / "response.bin").write_text(json.dumps(payload))
    receipt = qualify.read(cap / "receipt.json")
    receipt["response_sha256"] = file_hash(cap / "response.bin")
    (cap / "receipt.json").write_text(json.dumps(receipt))
    (cap / "manifest.json").unlink()
    seal_files(cap)
    index = tmp_path / "index.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    pools = [
        {k: birth[k] for k in ("pool", "base_mint", "quote_mint")},
        dict(pool="missing", base_mint="missing-base", quote_mint="missing-quote"),
    ]
    source = dict(
        role="train",
        cutoff_ns=10**18,
        start_s=300000,
        end_s=480000,
        selection="synthetic qualification",
        pools=pools,
    )
    export(index, tmp_path / "train", source)
    plan = dict(
        owner="synthetic",
        pools=pools,
        source_cutoff_ns=10**18,
        representation=dict(context=8, horizons=[1, 3, 6]),
        first_split=dict(
            train_start_s=300000, train_end_s=330000, validation_end_s=360000, test_end_s=390000
        ),
        cumulative_split=dict(
            train_start_s=300000, train_end_s=420000, validation_end_s=450000, test_end_s=480000
        ),
        evaluation=dict(start_s=480000, end_s=510000, scope="retrospective"),
        config=dict(width=8, epochs=1, batch=64),
    )
    root = qualify.bind(tmp_path / "qualification", plan)
    prepared = qualify.prepare(root, plan, tmp_path / "train")
    assert (
        prepared["base_examples"] + prepared["increment_examples"]
        == prepared["cumulative_examples"]
    )
    assert prepared["exposure"]["cumulative"]["per_pool"]["missing"] == {}
    assert qualify.prepare(root, plan, tmp_path / "train") == prepared
    for case in qualify.CASES[:3]:
        result = qualify.fit(root, plan, case)
        assert result["rows_seen"] > 0
    protocols = qualify.register(root, plan)
    assert qualify.register(root, plan) == protocols
    export(
        index,
        tmp_path / "eval",
        dict(source, role="research_evaluation", start_s=480000, end_s=510000),
    )
    scored = qualify.score(root, plan, tmp_path / "eval")
    assert qualify.score(root, plan, tmp_path / "eval") == scored
    assert all(r["enrollment"] == 2 and r["supported_pools"] == 1 for r in scored.values())
    with pytest.raises(ValueError, match="immutable_conflict"):
        qualify.bind(root, dict(plan, owner="changed"))
    with pytest.raises(ValueError, match="source_binding"):
        qualify.source_reference(tmp_path / "train", dict(plan, pools=pools[:1]), "train")
    atomic_json(root / "V1-protocol.json", dict(protocol="0" * 64), replace=True)
    with pytest.raises(FileNotFoundError):
        qualify.register(root, plan)
