"""Offline reproduction of the single D51 attempt; no F01 registry or protected inputs."""

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

from memecoin_ai.causal import observed_checkpoint
from memecoin_ai.execution_targets import verify_execution_targets
from memecoin_ai.prospective import verify_campaign
from memecoin_ai.sources import Capture
from memecoin_collector.scheduler import durable
from memecoin_ml.data import read, sha
from memecoin_operational.contract import check_bundle, no_symlink
from memecoin_registry.contracts import seal, verify_inventory

CORE = Path(__file__).resolve().parents[1]
ROOT = CORE / "work/collector-d51"
SOURCE = ROOT / "attempts/live-01"
PIN = "a06f1d69ff8fb1c0f8647fc5622c3e2d8c1811e697d40eb8be15deb1663da880"


def summary(values):
    values = sorted(values)
    if not values:
        return dict(n=0)
    return dict(
        n=len(values),
        min=values[0],
        p50=values[math.ceil(len(values) * 0.5) - 1],
        p95=values[math.ceil(len(values) * 0.95) - 1],
        max=values[-1],
        quantile="nearest_rank",
    )


def report(output):
    no_symlink(output)
    if output.resolve().parent != ROOT.resolve() or not output.name.startswith("report-"):
        raise ValueError("D51_report_sibling_only")
    if output.exists():
        raise FileExistsError("immutable_report_exists")
    check_bundle(CORE / "work/collector-d50/runtime-candidate-final", PIN)
    before = sha(SOURCE / "seal.json")
    sealed = read(SOURCE / "seal.json")
    verify_inventory(SOURCE, sealed["files"], exclude="seal.json")
    campaign = verify_campaign(SOURCE / "campaign")  # Exact raw -> online features/targets.
    execution = (
        verify_execution_targets(SOURCE / "campaign", SOURCE / "campaign-targets")
        if (SOURCE / "campaign-targets").exists()
        else None
    )
    statuses, masks, per_pool = Counter(), Counter(), Counter()
    latency, chain_age, publication, feature_age, receipt_age = [], [], [], [], []
    features, observations, response_bytes = [], [], 0
    pool_times = defaultdict(list)
    for segment in campaign["segments"]:
        if segment["interrupted"]:
            continue
        corpus = SOURCE / "campaign/segments" / f"{segment['index']:03}/corpus"
        attempts = [json.loads(s) for s in (corpus / "attempts.jsonl").read_text().splitlines()]
        rows = [json.loads(s) for s in (corpus / "features.jsonl").read_text().splitlines()]
        features.extend(rows)
        for row in rows:
            masks.update(row["reasons"])
            for key, dest in (
                ("chain_clock_age_seconds", feature_age),
                ("receipt_age_seconds", receipt_age),
            ):
                value = row["values"][key]
                if value is not None:
                    dest.append(value)
        for a in attempts:
            statuses[a["status"]] += 1
            if a["status"] == "error":
                continue
            cap = Capture.load(corpus / "captures" / f"{a['index']:03}")
            obs = observed_checkpoint(cap, a["pool_id"], a["available_ns"], config_layout_version=2)
            observations.append(obs)
            per_pool[a["pool_id"]] += 1
            pool_times[a["pool_id"]].append(a["available_ns"])
            latency.append((cap.received_ns - cap.request_started_ns) / 1e9)
            chain_age.append(cap.received_ns / 1e9 - obs["chain_timestamp_seconds"])
            publication.append((a["available_ns"] - cap.received_ns) / 1e9)
            response_bytes += len(cap.response)
    intervals = [
        (b - a) / 1e9
        for values in pool_times.values()
        for a, b in zip(values[:-1], values[1:], strict=True)
    ]
    supervision = read(SOURCE / "supervision.json")
    phases_ok = len(supervision["phases"]) == 4 and all(
        p["category"] == "completed" for p in supervision["phases"]
    )
    passed = (
        sealed["outcome"]["status"] == "completed"
        and phases_ok
        and campaign["complete"]
        and campaign["successful_snapshots"] == 180
        and statuses == {"advanced": 180}
        and len(features) == 180
        and set(per_pool) == set(campaign["plan"]["pools"])
        and all(n == 60 for n in per_pool.values())
        and execution is not None
    )
    verify_inventory(SOURCE, sealed["files"], exclude="seal.json")
    if sha(SOURCE / "seal.json") != before:
        raise ValueError("source_changed_during_reproduction")
    result = dict(
        stage="D51",
        verdict="BOUNDED_LIVE_PASS" if passed else "BOUNDED_LIVE_FAIL",
        source=str(SOURCE),
        source_seal_sha256=before,
        bundle_sha256=PIN,
        raw_features_targets_reproduced=True,
        execution_targets_reproduced=execution is not None,
        raw_unchanged=True,
        supervision=supervision,
        outcome=sealed["outcome"],
        reserved_rpc=read(SOURCE / "reservation.json")["reserved_rpc"],
        attempted_rpc=sum(statuses.values()),
        successful_snapshots=campaign["successful_snapshots"],
        attempt_statuses=dict(statuses),
        coherent_checkpoints=len(observations),
        snapshots_per_pool=dict(per_pool),
        features=len(features),
        usable_features=sum(f["usable"] for f in features),
        masked_reasons=dict(masks),
        rpc_latency_seconds=summary(latency),
        chain_age_at_receipt_seconds=summary(chain_age),
        observation_publication_seconds=summary(publication),
        chain_age_at_feature_seconds=summary(feature_age),
        receipt_age_at_feature_seconds=summary(receipt_age),
        same_pool_interval_seconds=summary(intervals),
        first_available_ns=min((o["available_ns"] for o in observations), default=None),
        last_available_ns=max((o["available_ns"] for o in observations), default=None),
        raw_response_bytes=response_bytes,
        total_attempt_bytes=sum(p.stat().st_size for p in SOURCE.rglob("*") if p.is_file()),
        execution_report=execution,
        seed=None,
        time_split="existing campaign engineering calibration/evaluation, outside F01",
        execution_model="unchanged conditional snapshot reset, pre-network costs",
        analysis_rpc=0,
        actual_F01_admission=False,
        holdout_opened=False,
        training_ready=False,
        calendar_activation_authorized=False,
    )
    output.mkdir()
    durable(output / "report.json", result)
    seal(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    def audit(event, args):
        if event == "socket.connect":
            raise PermissionError("offline_network_forbidden")

    sys.addaudithook(audit)
    result = report(args.output.absolute())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
