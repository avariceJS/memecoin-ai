"""B05 read-only comparison of pinned raw and bar snapshots; no market requests."""

from collections import Counter
from pathlib import Path

from memecoin_ai.sources import Capture
from memecoin_brain.source import bars
from memecoin_brain_sources.replay import reproduce
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]


def audit(output: Path) -> dict:
    raw, snapshot = BASE / "work/brain-b05/raw01", BASE / "work/brain-b05/bar-snapshot01"
    # External pin recorded immediately after acquisition, before this offline analysis.
    pin = read(BASE / "work/brain-b05/raw-reproduction-source.json")["manifest_sha"]
    replay = reproduce(raw, pin)
    verify(snapshot)
    result, provenance = read(raw / "result.json"), read(snapshot / "provenance.json")
    first_request = Capture.load(raw / "attempts/000000/capture").request_started_ns
    comparisons = []
    for tx in result["transactions"]:
        pool, ev = tx["pool"], tx["evidence"]
        candidates = []
        for ref in provenance["captures"]:
            if ref["pool"] != pool:
                continue
            p = Path(ref["path"])
            verify(p)
            if sha(p / "manifest.json") != ref["manifest_sha"]:
                raise ValueError("bar_external_pin_changed")
            available = read(p / "receipt.json")["available_ns"]
            if available <= first_request:
                candidates.append((available, p))
        available, last = max(candidates)
        last_bar = max(bars(last, pool), key=lambda r: r["open_s"])
        page = result["pages"][pool]
        block_times = [r["blockTime"] for r in page["rows"] if r["blockTime"] is not None]
        raw_tx = Capture.load(Path(ev["raw_path"]) / "capture").result()
        comparisons.append(
            dict(
                pool=pool,
                signature_rows=len(page["rows"]),
                page_sizes=[p["returned"] for p in page["captures"]],
                failed_signatures=sum(r["err"] is not None for r in page["rows"]),
                oldest_reference_block_s=min(block_times),
                newest_reference_block_s=max(block_times),
                newest_reference_age_seconds=first_request / 1e9 - max(block_times),
                selected_transaction_block_s=ev["event_time_s"],
                selected_transaction_version=raw_tx["version"],
                raw_instruction_labels=[
                    s for s in raw_tx["meta"].get("logMessages", []) if "Instruction:" in s
                ],
                labels_are_not_verified_swap_decoding=True,
                gt_capture=str(last),
                gt_capture_manifest_sha=sha(last / "manifest.json"),
                gt_capture_available_ns=available,
                gt_latest_open_s=last_bar["open_s"],
                gt_bar_close_age_seconds=first_request / 1e9 - (last_bar["open_s"] + 300),
                gt_capture_age_seconds=(first_request - available) / 1e9,
                last_reference_inside_last_gt_bar=(
                    last_bar["open_s"] <= ev["event_time_s"] < last_bar["open_s"] + 300
                ),
                history_complete=False,
                inactivity_proven=False,
            )
        )
    coverage = read(snapshot / "coverage.json")
    report = dict(
        raw_manifest_sha=pin,
        snapshot_manifest_sha=sha(snapshot / "manifest.json"),
        raw_source_commit="0c9af4b",
        launcher_commit="3f3dbdb",
        plan_commit="fba806a",
        raw_reproduction=replay,
        comparisons=comparisons,
        account_owner_counts=dict(Counter(r["owner"] for r in result["inventory"])),
        raw_artifact_bytes=sum(p.stat().st_size for p in raw.rglob("*") if p.is_file()),
        raw_response_bytes=sum(
            p.stat().st_size for p in raw.glob("attempts/*/capture/response.json")
        ),
        source_response_count=13,
        sample_not_bandwidth_forecast=True,
        corpus=dict(
            bars=coverage["bars"],
            pools=coverage["pools"],
            revision_observations=coverage["revision_observations"],
            revised_unique_keys=coverage["revised_unique_keys"],
            receipt_UTC_hours=coverage["receipt_UTC_hours"],
            close_revision_observations=coverage["close_revision_observations"],
            sampling_bias=coverage["sampling_bias"],
            quarantined_captures=len(provenance["quarantined"]),
        ),
        shadow=read(snapshot / "shadow/report.json"),
        new_fits=0,
        champion_changed=False,
        execution_model="none",
    )
    output.mkdir(exist_ok=False)
    publish(output / "report.json", report)
    seal(output)
    return report


if __name__ == "__main__":
    import sys

    audit(Path(sys.argv[1]))
