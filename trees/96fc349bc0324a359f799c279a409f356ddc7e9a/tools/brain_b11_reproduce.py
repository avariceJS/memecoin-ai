"""Offline B11 verification. Reads sealed inputs, never imports a network client."""

import argparse
import gc
import hashlib
import json
from pathlib import Path

from memecoin_ai.sources import Capture
from memecoin_brain.data import build, fingerprint, load_dataset
from memecoin_continuous.store import view
from memecoin_curriculum.archive import assemble, path_labels
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE / "docs/evidence/brain-archive-reproduction-2026-10-04.json",
    )
    args = parser.parse_args()
    root = BASE / "work/brain-b11/textbook02"
    verify(root)
    result = read(root / "result.json")
    rows, provenance = view(root, result["cutoff_ns"])
    bundle = load_dataset(root / "dataset-full")
    expected = fingerprint(bundle)
    assert expected == result["full_archive"]["dataset_fingerprint"]
    del bundle
    gc.collect()
    merged = assemble(load_dataset(p) for p in sorted((root / "shards").iterdir()))
    assert fingerprint(merged) == expected
    # Independent legacy build per pool avoids its global 40k capacity without using shards.
    pools = sorted({r["pool"] for r in rows})
    checked = 0
    for pool in pools:
        legacy = build([r for r in rows if r["pool"] == pool], provenance)
        actual = [s for s in merged["samples"] if s["pool"] == pool]
        assert actual == legacy["samples"]
        for sample in actual:
            for key in sample["history"]:
                assert merged["frames"][key] == legacy["frames"][key]
        checked += len(actual)
    h = hashlib.sha256()
    for label in path_labels(rows, result["cutoff_ns"]):
        h.update((json.dumps(label, sort_keys=True, allow_nan=False) + "\n").encode())
    assert h.hexdigest() == sha(root / "auxiliary-targets.jsonl")
    del merged
    gc.collect()

    probe = BASE / "work/brain-b11/archive-probe02"
    verify(probe)
    binding = read(probe / "binding.json")
    pages, signatures, previous, first, body_bytes, cost = [], {}, {}, None, 0, 0
    for path in sorted((probe / "attempts").iterdir()):
        cap = Capture.load(path / "capture")
        reservation = read(path / "reservation.json")
        available = read(path / "availability.json")["available_ns"]
        assert reservation["started_ns"] <= cap.request_started_ns <= cap.received_ns <= available
        assert available <= binding["expires_ns"]
        body_bytes += len(cap.response)
        cost += reservation["cu"]
        request = json.loads(cap.request_json)
        value = cap.result()
        if cap.method == "getTransactionsForAddress":
            pool, config = request["params"]
            assert pool in binding["cohort"]
            assert config["commitment"] == "finalized" and config["sortOrder"] == "asc"
            assert config.get("paginationToken") == previous.get(pool)
            previous[pool] = value.get("paginationToken")
            txs = value["data"]
            times = []
            for tx in txs:
                sig = tx["transaction"]["signatures"][0]
                assert sig not in signatures.setdefault(pool, set())
                signatures[pool].add(sig)
                assert 1735689600 <= tx["blockTime"] < 1767225600
                assert pool in [
                    k["pubkey"] if isinstance(k, dict) else k
                    for k in tx["transaction"]["message"]["accountKeys"]
                ]
                times.append(tx["blockTime"])
                if first is None:
                    first = tx
            assert times == sorted(times)
            pages.append(dict(pool=pool, rows=len(txs), first=min(times), last=max(times)))
        else:
            assert cap.method == "getTransaction"
            assert request["params"][0] == first["transaction"]["signatures"][0]
            assert all(
                value.get(k) == first.get(k)
                for k in ("slot", "blockTime", "transaction", "meta", "version")
            )
    report = dict(
        textbook_manifest_sha=sha(root / "manifest.json"),
        probe_manifest_sha=sha(probe / "manifest.json"),
        dataset_fingerprint=expected,
        legacy_pool_build_exact_decisions=checked,
        auxiliary_labels_exact=True,
        pages=pages,
        core_cross_check=True,
        nominal_cu=cost,
        raw_response_bytes=body_bytes,
        transactions=sum(p["rows"] for p in pages),
        training_eligible=False,
        history_complete=False,
        network_requests=0,
        monetary_cost_independently_verified=False,
    )
    publish(args.output, report)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
