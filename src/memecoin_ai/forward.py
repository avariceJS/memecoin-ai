"""Bounded current finalized snapshot corpus; live/offline share the same causal book."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memecoin_ai.causal import (
    CausalBook,
    observation_targets,
    observed_checkpoint,
    publish_features,
)
from memecoin_ai.datasets import MODULES
from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.sources import (
    Capture,
    HTTPSourceError,
    ReadOnlyRPC,
    SourceError,
    TransportSourceError,
)
from memecoin_ai.tokens import decode_supported_checkpoint


def _source() -> dict[str, str]:
    return {
        name: Path(__file__).with_name(name).read_text()
        for name in (*MODULES, "causal.py", "forward.py")
    }


def _book(plan: dict[str, Any]) -> CausalBook:
    return CausalBook(
        [r["pool_id"] for r in plan["cohort"]],
        max_receipt_age_ns=plan["max_receipt_age_ns"],
        max_chain_age_ns=plan["max_chain_age_ns"],
        max_gap_ns=plan["max_gap_ns"],
    )


def _append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a") as stream:
        stream.write(canonical(row) + "\n")


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _report(
    plan: dict[str, Any],
    attempts: list[dict[str, Any]],
    features: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    return dict(
        schema_version=1,
        kind="causal_forward_corpus",
        source_alias=plan["source_alias"],
        requests=len(attempts),
        planned_requests=plan["max_requests"],
        complete=len(attempts) == plan["max_requests"]
        and all(a["status"] != "error" for a in attempts),
        response_bytes=sum(a.get("response_bytes", 0) for a in attempts),
        attempt_statuses=dict(Counter(a["status"] for a in attempts)),
        feature_rows=len(features),
        usable_feature_rows=sum(r["usable"] for r in features),
        masked_reasons=dict(Counter(x for r in features for x in r["reasons"])),
        target_partitions=dict(Counter(t["partition"] for t in targets)),
        censor_reasons=dict(Counter(t["censor_reason"] for t in targets if t["censor_reason"])),
        temporal_checks_passed=True,
        training_ready=False,
        market_dataset_qualified=False,
        execution_qualified=False,
        edge_established=False,
        blockers=[
            "short fixed cohort engineering sample; no regime/universe coverage",
            "observed price targets are not executable returns",
            "snapshot polling has unknown intervening activity",
            "calibration/evaluation split is engineering-only; no final holdout",
        ],
    )


def collect_forward(
    client: ReadOnlyRPC,
    references: list[Capture],
    output: Path,
    *,
    rounds: int = 12,
    interval_seconds: int = 10,
    clock: Callable[[], int] = time.time_ns,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[dict[str, Any]], None] = lambda row: None,
) -> dict[str, Any]:
    integer(rounds, "rounds", 4)
    integer(interval_seconds, "interval seconds", 3)
    if rounds > 30 or interval_seconds > 30 or not 1 <= len(references) <= 4:
        raise ValidationError("forward corpus bounds exceeded")
    cohort = []
    for cap in references:
        req = json.loads(cap.request_json)
        pool = req["params"][0][0]
        if decode_supported_checkpoint(cap, pool)["schema_version"] != 2:
            raise ValidationError("forward references require Clock checkpoints")
        cohort.append(
            dict(
                pool_id=pool,
                method="getMultipleAccounts",
                params=req["params"],
                reference_sha256=digest(cap.response),
            )
        )
    if len({r["pool_id"] for r in cohort}) != len(cohort):
        raise ValidationError("duplicate forward pool")
    start, mono_start = clock(), monotonic()
    plan: dict[str, Any] = dict(
        schema_version=1,
        kind="causal_forward_corpus",
        started_ns=start,
        source_alias=client.source_alias,
        cohort=cohort,
        rounds=rounds,
        interval_ns=interval_seconds * 10**9,
        max_requests=rounds * len(cohort),
        timeout_seconds=client.timeout,
        max_response_bytes=client.max_bytes,
        spacing_seconds=1,
        automatic_retries=0,
        stop_on_first_error=True,
        max_wall_seconds=rounds * interval_seconds + 2 * client.timeout,
        max_receipt_age_ns=25 * 10**9,
        max_chain_age_ns=45 * 10**9,
        max_gap_ns=25 * 10**9,
        horizon_ns=20 * 10**9,
        target_schema=2,
        target_tolerance_ns=15 * 10**9,
        calibration_end_ns=start + (rounds // 2) * interval_seconds * 10**9,
        seed=None,
        execution_model="none; observed log-price diagnostic targets",
        universe_selection="existing fixed cohort before collection; no survivor claims",
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "plan.json").write_text(canonical(plan) + "\n")
    (output / "source.json").write_text(canonical(_source()) + "\n")
    for i, cap in enumerate(references):
        cap.save(output / "references" / f"{i:03d}")
    for name in ("attempts.jsonl", "frames.jsonl"):
        (output / name).touch(exist_ok=False)
    book = _book(plan)
    attempts: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    stopped = False
    for round_index in range(rounds):
        remaining = mono_start + round_index * interval_seconds - monotonic()
        if remaining > 0:
            sleep(remaining)
        round_attempts = 0
        for item in cohort:
            if monotonic() - mono_start + client.timeout > plan["max_wall_seconds"]:
                stopped = True
                break
            if attempts:
                sleep(1)
            index = len(attempts)
            row = dict(
                index=index,
                round=round_index,
                pool_id=item["pool_id"],
                method=item["method"],
                params=item["params"],
                http_status=None,
                rpc_code=None,
            )
            before = monotonic()
            try:
                cap = client.fetch(item["method"], item["params"])
                row.update(
                    http_status=200,
                    response_bytes=len(cap.response),
                    response_ns=int((monotonic() - before) * 10**9),
                )
                cap.save(output / "captures" / f"{index:03d}")
                body = json.loads(cap.response)
                if "error" in body:
                    row.update(rpc_code=body["error"].get("code"), category="rpc_error")
                obs = observed_checkpoint(cap, item["pool_id"], cap.received_ns)
                obs["available_ns"] = clock()
                if cap.request_started_ns < start:
                    raise ValidationError("capture predates forward plan")
                row.update(available_ns=obs["available_ns"], status=book.ingest(obs))
                if row["status"] in ("conflicting_same_bank", "regressed_slot"):
                    row.update(status="error", category=row["status"])
            except (SourceError, ValidationError, KeyError, TypeError, ValueError) as exc:
                row.update(
                    status="error",
                    error_type=type(exc).__name__,
                    available_ns=clock(),
                    category=exc.reason
                    if isinstance(exc, TransportSourceError)
                    else "http_error"
                    if isinstance(exc, HTTPSourceError)
                    else row.get(
                        "category",
                        "source_error" if isinstance(exc, SourceError) else "checkpoint_validation",
                    ),
                )
                if isinstance(exc, HTTPSourceError):
                    row["http_status"] = exc.status
                book.gap(item["pool_id"], row["available_ns"], row["category"])
            row.setdefault("response_ns", int((monotonic() - before) * 10**9))
            attempts.append(row)
            round_attempts += 1
            _append(output / "attempts.jsonl", row)
            if row["status"] == "error":
                emit(row)
                stopped = True
                break
        if round_attempts:
            as_of = clock()
            rows = publish_features(book.features(as_of), clock())
            features.extend(rows)
            frame = dict(
                round=round_index,
                attempt_count=len(attempts),
                as_of_ns=as_of,
                feature_available_ns=rows[0]["feature_available_ns"],
                rows=rows,
            )
            _append(output / "frames.jsonl", frame)
            emit(
                dict(
                    round=round_index,
                    requests=len(attempts),
                    usable=sum(r["usable"] for r in rows),
                    masks=[r["reasons"] for r in rows],
                )
            )
        if stopped:
            break
    targets = observation_targets(
        features,
        horizon_ns=plan["horizon_ns"],
        tolerance_ns=plan["target_tolerance_ns"],
        calibration_end_ns=plan["calibration_end_ns"],
        target_schema=plan.get("target_schema", 1),
    )
    for name, rows in (("features", features), ("targets", targets)):
        (output / (name + ".jsonl")).write_text("".join(canonical(r) + "\n" for r in rows))
    report = _report(plan, attempts, features, targets)
    (output / "report.json").write_text(canonical(report) + "\n")
    files = {
        str(p.relative_to(output)): digest(p.read_bytes())
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    (output / "manifest.json").write_text(canonical(dict(schema_version=1, files=files)) + "\n")
    return report


def verify_forward(output: Path, *, reproduce_compatible_version: bool = False) -> dict[str, Any]:
    manifest = json.loads((output / "manifest.json").read_text())
    paths = {
        str(p.relative_to(output))
        for p in output.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    if manifest.get("schema_version") != 1 or set(manifest["files"]) != paths:
        raise ValidationError("invalid forward manifest")
    for name, sha in manifest["files"].items():
        if digest((output / name).read_bytes()) != sha:
            raise ValidationError("forward artifact checksum mismatch")
    if (
        not reproduce_compatible_version
        and json.loads((output / "source.json").read_text()) != _source()
    ):
        raise ValidationError("forward transformation version differs")
    plan = json.loads((output / "plan.json").read_text())
    if plan.get("kind") != "causal_forward_corpus" or plan.get("schema_version") != 1:
        raise ValidationError("invalid forward plan")
    for i, item in enumerate(plan["cohort"]):
        cap = Capture.load(output / "references" / f"{i:03d}")
        if (
            digest(cap.response) != item["reference_sha256"]
            or json.loads(cap.request_json)["params"] != item["params"]
        ):
            raise ValidationError("forward cohort provenance differs")
        decode_supported_checkpoint(cap, item["pool_id"])
    attempts = _read(output / "attempts.jsonl")
    frames = _read(output / "frames.jsonl")
    if len(attempts) > plan["max_requests"]:
        raise ValidationError("forward request budget exceeded")
    book = _book(plan)
    features = []
    cursor = 0
    for frame_index, frame in enumerate(frames):
        if frame["round"] != frame_index or not cursor < frame["attempt_count"] <= len(attempts):
            raise ValidationError("invalid forward frame order")
        while cursor < frame["attempt_count"]:
            row = attempts[cursor]
            item = plan["cohort"][cursor % len(plan["cohort"])]
            if (
                row["index"] != cursor
                or row["round"] != frame_index
                or row["pool_id"] != item["pool_id"]
                or (row["method"], row["params"]) != (item["method"], item["params"])
            ):
                raise ValidationError("forward attempt differs from frozen plan")
            path = output / "captures" / f"{cursor:03d}"
            if row["status"] == "error":
                if cursor != len(attempts) - 1:
                    raise ValidationError("forward continued after error")
                if path.exists():
                    cap = Capture.load(path)
                    if json.loads(cap.request_json)["params"] != item["params"]:
                        raise ValidationError("failed forward capture request differs")
                book.gap(item["pool_id"], row["available_ns"], row["category"])
            else:
                cap = Capture.load(path)
                if (
                    cap.source_alias != plan["source_alias"]
                    or cap.request_started_ns < plan["started_ns"]
                    or json.loads(cap.request_json)["params"] != item["params"]
                ):
                    raise ValidationError("forward capture provenance mismatch")
                obs = observed_checkpoint(cap, item["pool_id"], row["available_ns"])
                if book.ingest(obs) != row["status"]:
                    raise ValidationError("forward observation status differs")
            cursor += 1
        actual = publish_features(book.features(frame["as_of_ns"]), frame["feature_available_ns"])
        if actual != frame["rows"]:
            raise ValidationError("forward online/offline features differ")
        features.extend(actual)
    if cursor != len(attempts) or features != _read(output / "features.jsonl"):
        raise ValidationError("forward feature coverage differs")
    targets = observation_targets(
        features,
        horizon_ns=plan["horizon_ns"],
        tolerance_ns=plan["target_tolerance_ns"],
        calibration_end_ns=plan["calibration_end_ns"],
        target_schema=plan.get("target_schema", 1),
    )
    if targets != _read(output / "targets.jsonl"):
        raise ValidationError("forward target reproduction differs")
    report = _report(plan, attempts, features, targets)
    if report != json.loads((output / "report.json").read_text()):
        raise ValidationError("forward report reproduction differs")
    return report


def _fresh_target_result(parent: Path) -> dict[str, Any]:
    # Explicit compatibility replay: every legacy feature/target/report must still match
    # raw evidence. Source-version differences do not waive any value comparison.
    prior = verify_forward(parent, reproduce_compatible_version=True)
    plan = json.loads((parent / "plan.json").read_text())
    features = _read(parent / "features.jsonl")
    targets = observation_targets(
        features,
        horizon_ns=plan["horizon_ns"],
        tolerance_ns=plan["target_tolerance_ns"],
        calibration_end_ns=plan["calibration_end_ns"],
        target_schema=2,
    )
    return dict(
        targets=targets,
        report=dict(
            schema_version=2,
            kind="fresh_snapshot_targets",
            parent_manifest_sha256=digest((parent / "manifest.json").read_bytes()),
            parent_reproduced_without_value_changes=True,
            parent_collection_complete=prior["complete"],
            feature_rows=len(features),
            usable_feature_rows=prior["usable_feature_rows"],
            target_partitions=dict(Counter(t["partition"] for t in targets)),
            censor_reasons=dict(Counter(t["censor_reason"] for t in targets if t["censor_reason"])),
            original_target_partitions=prior["target_partitions"],
            training_ready=False,
            execution_qualified=False,
            market_dataset_qualified=False,
        ),
    )


def refine_forward_targets(parent: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    (output / "spec.json").write_text(
        canonical(
            dict(
                schema_version=2,
                kind="fresh_snapshot_targets",
                parent_manifest_sha256=digest((parent / "manifest.json").read_bytes()),
                rule="endpoint snapshot available at/after horizon end and slot newer than start",
                network_requests=0,
            )
        )
        + "\n"
    )
    (output / "source.json").write_text(canonical(_source()) + "\n")
    result = _fresh_target_result(parent)
    (output / "targets.jsonl").write_text("".join(canonical(t) + "\n" for t in result["targets"]))
    (output / "report.json").write_text(canonical(result["report"]) + "\n")
    (output / "manifest.json").write_text(
        canonical(
            dict(
                schema_version=2,
                files={
                    name: digest((output / name).read_bytes())
                    for name in ("spec.json", "source.json", "targets.jsonl", "report.json")
                },
            )
        )
        + "\n"
    )
    report: dict[str, Any] = result["report"]
    return report


def verify_forward_targets(parent: Path, output: Path) -> dict[str, Any]:
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("schema_version") != 2 or set(manifest["files"]) != {
        "spec.json",
        "source.json",
        "targets.jsonl",
        "report.json",
    }:
        raise ValidationError("invalid fresh target manifest")
    for name, sha in manifest["files"].items():
        if digest((output / name).read_bytes()) != sha:
            raise ValidationError("fresh target checksum mismatch")
    spec = json.loads((output / "spec.json").read_text())
    if (
        spec.get("schema_version") != 2
        or spec.get("kind") != "fresh_snapshot_targets"
        or spec["parent_manifest_sha256"] != digest((parent / "manifest.json").read_bytes())
        or json.loads((output / "source.json").read_text()) != _source()
    ):
        raise ValidationError("fresh target provenance/version mismatch")
    result = _fresh_target_result(parent)
    if (
        _read(output / "targets.jsonl") != result["targets"]
        or json.loads((output / "report.json").read_text()) != result["report"]
    ):
        raise ValidationError("fresh target reproduction mismatch")
    report: dict[str, Any] = result["report"]
    return report
