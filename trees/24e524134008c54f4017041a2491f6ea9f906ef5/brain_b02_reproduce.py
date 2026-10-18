"""Installed-wheel B02 data/folds/checkpoint scoring replay without training/network."""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.installed_path.resolve()))
    import torch

    import memecoin_brain_scale
    from memecoin_brain.data import build, load_dataset
    from memecoin_brain.model import ensemble_predict, scoring
    from memecoin_brain_scale.audit import merge, paired_summary, shadow_snapshot
    from memecoin_brain_scale.curves import folds, records
    from memecoin_ml.data import read, sha, write
    from memecoin_registry.contracts import seal, verify

    assert (
        Path(memecoin_brain_scale.__file__).resolve().is_relative_to(args.installed_path.resolve())
    )
    torch.set_num_threads(1)
    base = Path(__file__).resolve().parents[1] / "work/brain-b02"
    snapshot = base / "snapshot01"
    curves = base / "curves02"
    verify(snapshot)
    verify(curves)
    bundle = load_dataset(snapshot / "dataset")
    provenance = bundle["provenance"]
    captures = [(Path(c["path"]), c["pool"]) for c in provenance["captures"]]
    for c in provenance["captures"]:
        assert sha(Path(c["path"]) / "manifest.json") == c["manifest_sha"]
    rows, replay_prov = merge(captures, provenance["as_of_ns"])
    replay_prov.update(
        universe_sha=provenance["universe_sha"], window_floor_s=provenance["window_floor_s"]
    )
    assert (
        build([r for r in rows if r["open_s"] >= provenance["window_floor_s"]], replay_prov)
        == bundle
    )
    original = read(curves / "result.json")
    for design, expected in zip(folds(bundle), original["experiments"], strict=True):
        assert design["membership"] == expected["membership"]
        root = curves / design["name"]
        saved = read(root / "evaluation-predictions.json")
        samples = design["parts"]["evaluation"]
        values = dict(empirical=saved["predictions"]["empirical"])
        for kind in ("linear", "gru"):
            values[kind] = ensemble_predict(root, kind, bundle, samples)
            assert values[kind] == saved["predictions"][kind]
            assert scoring(samples, values[kind]) == expected["metrics"]["evaluation"][kind]
        assert paired_summary(records(samples, values)) == expected["paired"]["overall"]
    args.output.mkdir(exist_ok=False)
    primary = Path(__file__).resolve().parents[1] / "work/brain-b01/dev01"
    baseline_rows, _ = merge(
        [c for c in captures if c[0].is_relative_to(primary)], provenance["as_of_ns"]
    )
    shadow = shadow_snapshot(primary, args.output / "shadow", baseline_rows, provenance["as_of_ns"])
    assert shadow == read(snapshot / "shadow/report.json")
    result = dict(
        dataset_exact=True,
        fold_membership_exact=True,
        all_six_ensemble_predictions_exact=True,
        metrics_bootstrap_exact=True,
        shadow_report_exact=True,
        fits=0,
        network_requests=0,
        installed_package=str(Path(memecoin_brain_scale.__file__).resolve()),
        snapshot_sha=sha(snapshot / "manifest.json"),
        curves_sha=sha(curves / "manifest.json"),
    )
    write(args.output / "result.json", result)
    seal(args.output)
    print(result)


if __name__ == "__main__":
    main()
