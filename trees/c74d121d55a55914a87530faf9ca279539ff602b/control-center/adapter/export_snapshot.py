"""Offline reviewed projection; no core imports, RPC, subprocesses, recursive scans."""

import hashlib
import json
import sys
from pathlib import Path

from safe_io import Refused, json_bytes, read_bytes

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]


SOURCE_PATHS = {
    "d43": "docs/evidence/calendar-execution-2026-10-03.json",
    "d44": "docs/evidence/ml-foundation-2026-10-03.json",
    "d45": "docs/evidence/corpus-registry-2026-10-03.json",
    "d46": "docs/evidence/research-readiness-2026-10-03.json",
    "d47": "docs/evidence/f01-runner-integrity-2026-10-03.json",
    "d48": "docs/evidence/f01-sampling-2026-10-03.json",
    "d49": "docs/evidence/f01-collector-qualification-2026-10-03.json",
    "f01": "configs/research-f01-v1.json",
    "registry_protocol": "configs/corpus-registry-v1.json",
}


def validate_pins(pins):
    if (
        pins.get("schema") != 1
        or pins.get("core_baseline_commit") != "6a7e079"
        or {key: row.get("path") for key, row in pins.get("sources", {}).items()} != SOURCE_PATHS
    ):
        raise Refused("source_allowlist_changed")
    for row in pins["sources"].values():
        value = row.get("sha256", "")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise Refused("invalid_source_pin")


def build():
    pins = json_bytes(read_bytes(APP, "contracts/source-pins.json"))
    validate_pins(pins)
    raw = {
        key: json_bytes(read_bytes(REPO, pin["path"], pin["sha256"]))
        for key, pin in pins["sources"].items()
    }
    a, m, c, r, t, s, q, spec, protocol = [
        raw[k]
        for k in ("d43", "d44", "d45", "d46", "d47", "d48", "d49", "f01", "registry_protocol")
    ]
    windows = []
    for row in a["windows"]:
        # Fixed reviewed D43 partition; never copy entire row or inspect protected metrics.
        item = dict(
            id=f"d43:{row['index']}",
            index=row["index"],
            plannedAt=row["due_utc"],
            access="PROTECTED" if row["index"] >= 4 else "DEVELOPMENT",
        )
        if item["access"] == "DEVELOPMENT":
            item.update(
                recordedStatus=row["status"],
                actualStartNs=None,
                finishedNs=str(row["finished_ns"]) if "finished_ns" in row else None,
                firstReceiptAt=row.get("first_receipt_utc"),
                lastReceiptAt=row.get("last_receipt_utc"),
                snapshots=row.get("snapshots"),
                plannedSnapshots=180,
                usable=row.get("quality", {}).get("usable_features"),
                matureDecisions=row.get("targets", {}).get("mature_decisions"),
                scenarios=row.get("targets", {}).get("targets"),
                corpusExitCode=row.get("corpus_exit_code"),
                qaExitCode=row.get("qa_exit_code"),
                executionOrCollectionExitCode=row.get("execution_or_collection_exit_code"),
                recordedSealSha256=row.get("seal_sha256"),
            )
        windows.append(item)
    benchmarks = []
    for i, row in enumerate(m["benchmark"]["results"]):
        timings = row.get("timings", {})
        benchmarks.append(
            dict(
                id=f"d44:benchmark:{i}",
                modelKind=row["model"]["kind"],
                width=row["model"]["width"],
                context=row["model"]["context"],
                device=row["device"],
                batchSize=row["batch_size"],
                parameters=row.get("parameters"),
                status=row["status"],
                medianInferenceMs=timings.get("inference_ms", {}).get("median"),
                medianForwardBackwardMs=timings.get("forward_backward_ms", {}).get("median"),
                scope="ENGINEERING_BENCHMARK",
                inputKind="SYNTHETIC",
            )
        )
    runs = [
        dict(
            id=f"d44:run:{row['case']}",
            datasetKind=row["dataset"].upper(),
            scope="ENGINEERING_SMOKE",
            modelKind=row["training"]["model"]["kind"],
            seed=row["training"]["seed"],
            epochs=row["training"]["epochs"],
            parameters=row["parameters"],
            selectedEpoch=row["selected_epoch"],
        )
        for row in m["runs"]
    ]
    development = c["qualification_report"]["development"]
    sources = {
        key: dict(
            artifactId=key,
            sha256=pin["sha256"],
            coreBaselineCommit=pins["core_baseline_commit"],
            evidenceDate=raw[key].get("date", pins["review_date"]),
            recordedAtNs=str(raw[key]["recorded_ns"]) if "recorded_ns" in raw[key] else None,
            integrity="PINNED_BYTES",
            temporalScope="RECORDED_SNAPSHOT",
        )
        for key, pin in pins["sources"].items()
    }

    def pane(ids, data, qualification):
        return dict(
            availability="AVAILABLE",
            provenance="REAL_EVIDENCE",
            qualification=qualification,
            sourceIds=ids,
            data=data,
            reasonCode=None,
        )

    def unavailable(reason):
        return dict(
            availability="UNAVAILABLE",
            provenance=None,
            qualification=None,
            sourceIds=[],
            data=None,
            reasonCode=reason,
        )

    capabilities = [
        dict(id=key, version=1, availability="AVAILABLE", mode="READ_ONLY_RECORDED")
        for key in ("calendar", "corpus", "research", "models", "sourceQualification")
    ]
    for key in (
        "marketFeed",
        "predictions",
        "trainingControl",
        "collectorControl",
        "portfolio",
        "shadow",
        "paper",
        "live",
        "emergencyStop",
        "hardwareLive",
        "rawLogs",
        "replay",
    ):
        capabilities.append(
            dict(
                id=key,
                version=1,
                availability="UNAVAILABLE",
                reasonCode="NO_APPROVED_CORE_READ_OR_CONTROL_CONTRACT",
            )
        )
    data = dict(
        apiVersion="1.0",
        mode="OBSERVATORY",
        coreBaselineCommit=pins["core_baseline_commit"],
        sourcePolicy="REVIEWED_PROJECTION_ONLY",
        sources=sources,
        capabilities=capabilities,
        calendar=pane(
            ["d43", "registry_protocol"],
            dict(
                windows=windows,
                holdoutStartNs=str(protocol["holdout"]["start_ns"]),
                holdoutEndNs=str(protocol["holdout"]["end_ns"]),
            ),
            "ENGINEERING_CORPUS",
        ),
        corpus=pane(
            ["d45"],
            dict(
                revision=c["revision"],
                developmentWindows=c["qualification_report"]["development_windows"],
                usableDecisions=development["decisions"],
                scenarios=development["variants"],
                censoredScenarios=development["statuses"]["censored"],
                allScenariosObservedDecisions=development["all_scenarios_observed_decisions"],
                greedyNonoverlapDecisions=development["greedy_nonoverlap_decisions"],
                nonoverlapIsIndependent=False,
                researchPermission="NOT_GRANTED_BY_UI",
            ),
            "ENGINEERING_ONLY",
        ),
        research=pane(
            ["d46", "d47", "d48", "f01"],
            dict(
                specId=spec["id"],
                recordedProtocolFrozen=r["decision"]["protocol_frozen"],
                recordedResearchLaunchAuthorized=r["decision"]["research_launch_authorized"],
                recordedHoldoutReleaseAuthorized=r["decision"]["holdout_release_authorized"],
                recordedResearchFits=t["real_F01_fits"],
                fitBudget=spec["training"]["max_trainable_fits"],
                recordedHoldoutReleases=t["actual_holdout_releases"],
                globallyUnseenHoldout="NOT_CERTIFIED",
                engineeringFits=t["engineering_run_history"]["total"],
                numericChecks=s["proposal"]["numeric_checks"],
                numericBlockers=s["proposal"]["blockers"],
                missingReviews=spec["required_reviews"],
                admission="LOCKED",
                holdout="PROTECTED",
                sampling=dict(
                    anchorNs=str(s["proposal"]["anchor_ns"]),
                    developmentEndNs=str(s["proposal"]["development_end_ns"]),
                    holdoutEndNs=str(s["proposal"]["holdout_end_ns"]),
                    plannedWindows=s["sampling"]["planned_windows"],
                    developmentWindows=s["sampling"]["development_windows"],
                    reservedWindows=s["sampling"]["holdout_windows"],
                    admittedWindows=s["proposal"]["development_windows_admitted"],
                    recordedCollectionAuthorized=s["proposal"]["collection_authorized"],
                    utcOffsets=s["sampling"]["UTC_hours"],
                ),
            ),
            "FROZEN_DESIGN_NOT_ADMISSION",
        ),
        models=pane(
            ["d44"],
            dict(
                activeModel=None,
                marketBackboneSelected=m["decision"]["market_backbone_selected"],
                runs=runs,
                benchmarks=benchmarks,
            ),
            "ENGINEERING_ONLY",
        ),
        sourceQualification=pane(
            ["d49"],
            dict(
                recordedVerdict=q["verdict"],
                sourceName="Alchemy HTTP",
                commitment=q["source"]["commitment"],
                requests=q["source"]["rpc"],
                accepted=q["source"]["accepted"],
                latencySeconds=q["source"]["latency_seconds"],
                clockAgeRangeSeconds=q["source"]["chain_age_seconds"],
                pools=q["source"]["pools"],
                blockers=q["blockers"],
                recordedBaseTestsPassed=q["tests"]["base_passed"],
                recordedCombinedTestsPassed=q["tests"]["combined_passed"],
            ),
            "BOUNDED_PROBE_ONLY",
        ),
        system=unavailable("NO_CURRENT_SERVICE_OR_HARDWARE_FEED"),
        market=unavailable("NO_APPROVED_PRICE_RESERVES_VOLUME_EXPORT"),
        predictions=unavailable("NO_PUBLISHED_PREDICTION_STREAM"),
        trading=unavailable("NO_APPROVED_TRADING_CONTRACT"),
    )
    return (json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def main():
    data = build()
    target = APP / "published/observatory-v1.json"
    manifest = APP / "published/manifest.json"
    if sys.argv[1:] == ["--check"]:
        assert data == read_bytes(APP, "published/observatory-v1.json")
        assert (
            json_bytes(read_bytes(APP, "published/manifest.json"))["sha256"]
            == hashlib.sha256(data).hexdigest()
        )
        print("Exact projection reproduction; no runtime artifacts opened.")
    elif sys.argv[1:] == ["--write-new"]:
        # Never overwrite an existing projection/manifest; changes use a reviewed new version.
        with target.open("xb") as f:
            f.write(data)
        with manifest.open("x") as f:
            json.dump(dict(schema=1, file=target.name, sha256=hashlib.sha256(data).hexdigest()), f)
            f.write("\n")
    else:
        raise SystemExit("Use --check or --write-new; no arbitrary source/output arguments.")


if __name__ == "__main__":
    main()
