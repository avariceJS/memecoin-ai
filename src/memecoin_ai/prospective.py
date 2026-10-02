"""Resumable campaigns of immutable bounded prospective segments (no backfill)."""

from __future__ import annotations

import fcntl
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.forward import _read, _source, collect_forward, verify_forward
from memecoin_ai.sources import Capture, ReadOnlyRPC


def source() -> dict[str, str]:
    return {**_source(), "prospective.py": Path(__file__).read_text()}


def hashes(path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(path)): digest(p.read_bytes())
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def write(path: Path, value: Any) -> None:
    with path.open("x") as stream:
        stream.write(canonical(value) + "\n")


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def create_campaign(
    output: Path,
    references: list[Capture],
    *,
    source_alias: str,
    endpoint_origin: str,
    segments: int = 4,
    rounds: int = 30,
    interval_seconds: int = 5,
    clock: Callable[[], int] = time.time_ns,
) -> dict[str, Any]:
    for value, name, low, high in (
        (segments, "segments", 2, 8),
        (rounds, "rounds", 4, 30),
        (interval_seconds, "interval", 3, 30),
    ):
        integer(value, name, low)
        if value > high:
            raise ValidationError("campaign bound exceeded")
    if not 1 <= len(references) <= 4:
        raise ValidationError("campaign cohort bound exceeded")
    from memecoin_ai.tokens import decode_supported_checkpoint

    pools = [json.loads(c.request_json)["params"][0][0] for c in references]
    if len(set(pools)) != len(pools):
        raise ValidationError("duplicate campaign pool")
    for cap, pool in zip(references, pools, strict=True):
        if decode_supported_checkpoint(cap, pool)["schema_version"] != 2:
            raise ValidationError("campaign requires Clock")
    # Reuse URL validation; origin must not carry a path, query or credentials.
    identity = ReadOnlyRPC(endpoint_origin, source_alias).endpoint_origin
    if identity != endpoint_origin:
        raise ValidationError("campaign endpoint must be a canonical HTTPS origin only")
    start = clock()
    plan = dict(
        schema_version=2,
        kind="prospective_campaign",
        endpoint_origin=identity,
        created_ns=start,
        source_alias=source_alias,
        pools=pools,
        max_segments=segments,
        rounds=rounds,
        interval_seconds=interval_seconds,
        max_requests=segments * rounds * len(pools),
        timeout_seconds=15,
        max_response_bytes=1_000_000,
        cooldown_ns=30 * 10**9,
        max_consecutive_failed_segments=2,
        expiry_ns=start + 3600 * 10**9,
        calibration_end_ns=start + segments * rounds * interval_seconds * 10**9 // 2,
        boundary_policy="reset history; censor every segment tail; never bridge gaps",
        resume_policy="one new segment per invocation; only timeout/connection may resume",
        interrupted_policy="seal existing bytes, reserve full budget, start new segment",
        seed=None,
        training_ready=False,
    )
    output.mkdir(parents=True, exist_ok=False)
    write(output / "plan.json", plan)
    write(output / "source.json", source())
    for i, cap in enumerate(references):
        cap.save(output / "references" / f"{i:03d}")
    write(output / "plan-manifest.json", hashes(output))
    (output / "segments").mkdir()
    return plan


def _plan(root: Path) -> dict[str, Any]:
    manifest = read(root / "plan-manifest.json")
    for name, sha in manifest.items():
        if digest((root / name).read_bytes()) != sha:
            raise ValidationError("campaign plan checksum mismatch")
    if read(root / "source.json") != source():
        raise ValidationError("campaign requires its pinned source version")
    plan: dict[str, Any] = read(root / "plan.json")
    return plan


def _seal(
    directory: Path, previous: str | None, finished: int, interrupted: bool = False
) -> dict[str, Any]:
    report = None if interrupted else verify_forward(directory / "corpus")
    result = dict(
        index=int(directory.name),
        previous_seal_sha256=previous,
        finished_ns=finished,
        interrupted=interrupted,
        files=hashes(directory),
        report=report,
    )
    write(directory / "seal.json", result)
    return result


def verify_campaign(root: Path) -> dict[str, Any]:
    plan = _plan(root)
    directories = sorted((root / "segments").iterdir())
    previous = None
    reports = []
    for i, directory in enumerate(directories):
        if directory.name != f"{i:03d}" or i >= plan["max_segments"]:
            raise ValidationError("campaign segment sequence/budget invalid")
        seal = read(directory / "seal.json")
        actual = hashes(directory)
        actual.pop("seal.json")
        reservation = read(directory / "reservation.json")
        if (
            seal["files"] != actual
            or seal["previous_seal_sha256"] != previous
            or seal["index"] != i
        ):
            raise ValidationError("campaign seal mismatch")
        if (
            reservation["plan_sha256"] != digest((root / "plan.json").read_bytes())
            or reservation["index"] != i
        ):
            raise ValidationError("campaign reservation mismatch")
        if not seal["interrupted"]:
            cp = read(directory / "corpus/plan.json")
            if (
                cp["source_alias"] != plan["source_alias"]
                or cp["rounds"] != plan["rounds"]
                or cp["interval_ns"] != plan["interval_seconds"] * 10**9
                or cp["timeout_seconds"] != plan["timeout_seconds"]
                or cp["max_response_bytes"] != plan["max_response_bytes"]
                or cp["started_ns"] < reservation["started_ns"]
                or [r["pool_id"] for r in cp["cohort"]] != plan["pools"]
            ):
                raise ValidationError("segment differs from campaign plan")
            for j in range(len(plan["pools"])):
                if Capture.load(root / "references" / f"{j:03d}") != Capture.load(
                    directory / "corpus/references" / f"{j:03d}"
                ):
                    raise ValidationError("segment cohort reference differs")
            if verify_forward(directory / "corpus") != seal["report"]:
                raise ValidationError("campaign segment reproduction differs")
        reports.append(seal)
        previous = digest((directory / "seal.json").read_bytes())
    return dict(
        plan=plan,
        segments=reports,
        reserved_requests=len(reports) * plan["rounds"] * len(plan["pools"]),
        successful_snapshots=sum(
            s["report"]["requests"] - s["report"]["attempt_statuses"].get("error", 0)
            for s in reports
            if s["report"]
        ),
        complete=len(reports) == plan["max_segments"]
        and all(s["report"] and s["report"]["complete"] for s in reports),
        training_ready=False,
    )


def resume_campaign(
    root: Path,
    client: ReadOnlyRPC,
    *,
    clock: Callable[[], int] = time.time_ns,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[dict[str, Any]], None] = lambda row: None,
) -> dict[str, Any]:
    # OS lock is released on process death; no stale pid lock to guess away.
    with (root / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError("campaign already collecting") from exc
        plan = _plan(root)
        if (root / "closure.json").exists():
            raise ValidationError("campaign explicitly closed; new plan required")
        if client.endpoint_origin != plan["endpoint_origin"]:
            raise ValidationError("endpoint origin differs from frozen campaign")
        if (client.source_alias, client.timeout, client.max_bytes) != (
            plan["source_alias"],
            plan["timeout_seconds"],
            plan["max_response_bytes"],
        ):
            raise ValidationError("client differs from campaign plan")
        dirs = sorted((root / "segments").iterdir())
        if dirs and not (dirs[-1] / "seal.json").exists():
            prev = digest((dirs[-2] / "seal.json").read_bytes()) if len(dirs) > 1 else None
            _seal(dirs[-1], prev, clock(), interrupted=True)
        state = verify_campaign(root)
        seals = state["segments"]
        if len(seals) >= plan["max_segments"]:
            return state  # Exhausted resume sends no requests.
        now = clock()
        if now >= plan["expiry_ns"]:
            raise ValidationError("campaign expired; new explicit plan required")
        failures = 0
        for seal in reversed(seals):
            if seal["report"] and seal["report"]["complete"]:
                break
            failures += 1
        if failures >= plan["max_consecutive_failed_segments"]:
            raise ValidationError("consecutive failed segment budget exhausted")
        if seals and not (seals[-1]["report"] and seals[-1]["report"]["complete"]):
            if not seals[-1]["interrupted"]:
                attempts = _read(dirs[-1] / "corpus/attempts.jsonl")
                if attempts and attempts[-1].get("category") not in (None, "timeout", "connection"):
                    raise ValidationError("non-transient segment error; campaign stopped")
            if now < seals[-1]["finished_ns"] + plan["cooldown_ns"]:
                raise ValidationError("failed segment cooldown not elapsed")
        index = len(seals)
        directory = root / "segments" / f"{index:03d}"
        directory.mkdir(exist_ok=False)
        write(
            directory / "reservation.json",
            dict(
                index=index, started_ns=now, plan_sha256=digest((root / "plan.json").read_bytes())
            ),
        )
        references = [
            Capture.load(root / "references" / f"{i:03d}") for i in range(len(plan["pools"]))
        ]
        collect_forward(
            client,
            references,
            directory / "corpus",
            rounds=plan["rounds"],
            interval_seconds=plan["interval_seconds"],
            clock=clock,
            monotonic=monotonic,
            sleep=sleep,
            emit=emit,
        )
        prev = digest((dirs[-1] / "seal.json").read_bytes()) if dirs else None
        _seal(directory, prev, clock())
        return verify_campaign(root)
