"""Frozen prospective sampling proposal. No activation, RPC or training permission."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import digest, seal, verify
from memecoin_research.framework import DAY, materialize, validate_spec
from memecoin_runner.contracts import sources as runner_sources

Row = dict[str, Any]
NS = 10**9


def policy() -> Row:
    return dict(
        schema=1,
        id="F01-sampling-v1",
        days=84,
        development_days=56,
        utc_offsets_seconds=[1800, 23400, 45000, 66600],
        segments=2,
        rounds=30,
        interval_seconds=5,
        pools=3,
        rpc_per_window=180,
        total_max_rpc=60480,
        window_wall_seconds=600,
        segment_wall_seconds=210,
        lateness_seconds=900,
        max_failed_windows=8,
        timeout_seconds=15,
        max_response_bytes=1000000,
        wss_enabled=False,
        execution_rpc=0,
        retries=0,
        catch_up=False,
        fail_policy="first_error_closes_window; eight_failed_windows_stop_schedule",
        missing_policy="missed remains missing; interrupted reserves full budget; no backfill",
        activation_authorized=False,
        research_launch_authorized=False,
    )


def sources() -> Row:
    result = runner_sources()
    result.update(
        {f"memecoin_sampling/{p.name}": sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    )
    return result


def make_binding(spec: Row, *, anchor_ns: int, created_ns: int, source_root: Path) -> Row:
    validate_spec(spec)
    if not source_root.is_absolute() or ".." in source_root.parts:
        raise ValueError("absolute_canonical_source_root_required")
    design = materialize(spec, anchor_ns=anchor_ns, created_ns=created_ns)
    rules = policy()
    windows = [
        dict(
            index=d * 4 + j,
            day=d,
            stratum=j,
            due_ns=anchor_ns + d * DAY + offset * NS,
            role="development" if d < 56 else "reserved_holdout",
        )
        for d in range(84)
        for j, offset in enumerate(rules["utc_offsets_seconds"])
    ]
    return dict(
        schema=1,
        stage="D48",
        spec_sha256=digest(spec),
        created_ns=created_ns,
        anchor_ns=anchor_ns,
        policy=rules,
        design=design,
        windows=windows,
        source_semantics=spec["source_semantics"],
        sources=sources(),
        history_policy=dict(context=8, max_gap_ns=15 * NS),
        source_root=str(source_root),
        status="proposal_pending_activation_review",
        collection_authorized=False,
        research_launch_authorized=False,
        holdout_release_authorized=False,
    )


def validate_binding(binding: Row, spec: Row) -> None:
    if binding != make_binding(
        spec,
        anchor_ns=binding["anchor_ns"],
        created_ns=binding["created_ns"],
        source_root=Path(binding["source_root"]),
    ):
        raise ValueError("sampling_contract_or_sources_changed")


def freeze(
    spec: Row, output: Path, *, anchor_ns: int, source_root: Path, now_ns: int | None = None
) -> Row:
    source_root = source_root.absolute()
    paths_disjoint(output, source_root)
    if source_root.exists():
        raise ValueError("future_source_root_must_not_exist_at_freeze")
    binding = make_binding(
        spec,
        anchor_ns=anchor_ns,
        created_ns=time.time_ns() if now_ns is None else now_ns,
        source_root=source_root,
    )
    output.mkdir(parents=True, exist_ok=False)
    write(output / "binding.json", binding)
    write(output / "spec.json", spec)
    seal(output)
    return binding


def load(root: Path) -> tuple[Row, Row]:
    verify(root)
    binding, spec = read(root / "binding.json"), read(root / "spec.json")
    validate_binding(binding, spec)
    return binding, spec


def development_slot(binding: Row, index: int) -> Row:
    # Guard must precede ANY source path inspection, even exists/hash/listdir.
    if type(index) is not int or not 0 <= index < 224:
        raise ValueError("protected_or_unknown_window_no_source_read")
    slot: Row = binding["windows"][index]
    if slot["role"] != "development":
        raise ValueError("protected_partition")
    return slot


def paths_disjoint(source: Path, destination: Path) -> None:
    a, b = source.resolve(), destination.resolve()
    if a.is_relative_to(b) or b.is_relative_to(a):
        raise ValueError("source_and_output_must_be_disjoint")
