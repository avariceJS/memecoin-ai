"""Read-only provenance audit of B03 tails, capture conflicts and freshness."""

import json
from collections import defaultdict
from pathlib import Path

from memecoin_brain.data import load_dataset, scaler
from memecoin_brain_scale.curves import folds
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal, verify


def run(snapshot: Path, curves: Path, source: Path, output: Path):
    verify(snapshot)
    verify(curves)
    bundle = load_dataset(snapshot / "dataset")
    rows = read(snapshot / "rows.json")
    lookup = {(r["pool"], r["open_s"]): r for r in rows}
    captures = {c["manifest_sha"]: Path(c["path"]) for c in bundle["provenance"]["captures"]}
    for c in bundle["provenance"]["captures"]:
        path = Path(c["path"])
        verify(path)
        if sha(path / "manifest.json") != c["manifest_sha"]:
            raise ValueError("capture_binding")
    tails = []
    for fold in folds(bundle):
        if fold["fraction"] != 100:
            continue
        fitted = scaler(bundle, fold["parts"]["train"])
        points = []
        keys = {k for s in fold["parts"]["evaluation"] for k in s["history"]}
        for key in keys:
            f = bundle["frames"][key]
            for j, value in enumerate(f["values"]):
                if value is not None:
                    points.append((abs((value - fitted["mean"][j]) / fitted["scale"][j]), key, j))
        for z, key, j in sorted(points, reverse=True)[:12]:
            f = bundle["frames"][key]
            r = lookup[(f["pool"], f["event_close_s"] - 300)]
            cap = captures[r["capture_sha"]]
            verify(cap)
            matches = []
            for c in bundle["provenance"]["captures"]:
                if c["pool"] != r["pool"]:
                    continue
                path = Path(c["path"])
                raw = json.loads((path / "response.bin").read_bytes())["data"]["attributes"][
                    "ohlcv_list"
                ]
                versions = [v[1:] for v in raw if v[0] == r["open_s"]]
                if versions:
                    matches.append(
                        dict(
                            capture=c["path"],
                            sha=c["manifest_sha"],
                            available_ns=read(path / "receipt.json")["available_ns"],
                            ohlcv=versions,
                        )
                    )
            tails.append(
                dict(
                    fold=fold["name"],
                    key=key,
                    channel=bundle["feature_names"][j],
                    abs_z=z,
                    value=f["values"][j],
                    raw_row=r,
                    raw_versions=matches,
                    independent_onchain_verification=False,
                )
            )
    conflicts = []
    for q in read(snapshot / "coverage.json")["quarantine"]:
        cap = Path(q["path"])
        verify(cap)
        raw = json.loads((cap / "response.bin").read_bytes())["data"]["attributes"]["ohlcv_list"]
        grouped = defaultdict(list)
        for row in raw:
            grouped[row[0]].append(row[1:])
        duplicates = []
        for t, values in grouped.items():
            if len(values) > 1:
                changed = [
                    name
                    for j, name in enumerate(["open", "high", "low", "close", "volume"])
                    if len({v[j] for v in values}) > 1
                ]
                duplicates.append(dict(open_s=t, versions=values, changed_fields=changed))
        conflicts.append(dict(**q, duplicates=duplicates))
    cycles = []
    for p in sorted((source / "cycles").iterdir()):
        verify(p)
        c = read(p / "collection.json")
        cycles.append(
            dict(
                cycle=p.name, denominator=c["denominator"], fresh=c["fresh"], statuses=c["statuses"]
            )
        )
    result = dict(
        snapshot_sha=sha(snapshot / "manifest.json"),
        curves_sha=sha(curves / "manifest.json"),
        tail_examples=tails,
        conflicts=conflicts,
        cycles=cycles,
        rows_removed=0,
        raw_modified=False,
        inactivity_proven=False,
        execution_model="none",
        network_requests=0,
        fits=0,
    )
    output.mkdir(exist_ok=False)
    write(output / "result.json", result)
    seal(output)
    return result
