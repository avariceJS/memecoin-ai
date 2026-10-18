from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from memecoin_ai.causal import FEATURE_NAMES
from memecoin_ml.data import read, save_dataset, sha, write
from memecoin_registry import registry as legacy
from memecoin_registry.contracts import inventory
from memecoin_sampling.contract import NS, freeze
from memecoin_sampling.registry import initialize

ANCHOR = 1791331200 * NS  # 2026-10-07 UTC; synthetic test clock, not actual captures.


def fixture_window(root: Path, source: Path, index: int) -> Path:
    binding = read(root / "contract/binding.json")
    source = Path(binding["source_root"]) / "windows" / f"{index:03d}"
    due = binding["windows"][index]["due_ns"]
    frames: dict[str, Any] = {}
    samples = []
    source.mkdir(parents=True)
    for segment in range(2):
        raw = source / f"campaign/segments/{segment:03d}/corpus"
        raw.mkdir(parents=True)
        rows = []
        for pool in binding["source_semantics"]["campaign"]["pools"]:
            history = []
            group = f"{segment}:{pool}:0"
            for n in range(30):
                decision = due + (segment * 150 + n * 5) * NS
                rows.append(dict(usable=n > 0, reasons=[] if n else ["history_warmup"]))
                if not n:
                    continue
                key = f"{segment}:{pool}:{n}"
                frames[key] = dict(
                    group=group,
                    decision_ns=decision,
                    available_ns=decision - 1000,
                    values=[math.sin(n + j) for j in range(len(FEATURE_NAMES))],
                )
                history.append(key)
                targets = []
                for direction in (0, 1):
                    for size in (0.0001, 0.001):
                        for delay in (0, 5):
                            status = ("entry_unfilled", "exit_unfilled", "conditional_roundtrip")[
                                (n + direction + delay) % 3
                            ]
                            if n >= 25:
                                status = "censored"
                            targets.append(
                                dict(
                                    action=[direction, size, delay, 20],
                                    status=status,
                                    available_ns=decision + (25 * NS if n < 25 else 0),
                                    reason="synthetic_fixture",
                                    **{
                                        "return": 0.001 * math.sin(n)
                                        if status == "conditional_roundtrip"
                                        else None
                                    },
                                )
                            )
                samples.append(
                    dict(
                        id=key,
                        group=group,
                        pool_id=pool,
                        decision_ns=decision,
                        maturity_ns=max(t["available_ns"] for t in targets),
                        history=history[-8:],
                        targets=targets,
                    )
                )
        import json

        (raw / "features.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    bundle = dict(
        schema=1,
        qualification="engineering_only",
        feature_names=list(FEATURE_NAMES),
        history_policy=binding["history_policy"],
        frames=frames,
        samples=samples,
        source={"kind": "synthetic_D48_boundary_fixture"},
    )
    save_dataset(source / "fixture-dataset", bundle)
    write(
        source / "campaign/plan.json",
        dict(
            binding["source_semantics"]["campaign"],
            max_segments=2,
            rounds=30,
            max_requests=180,
            timeout_seconds=15,
            max_response_bytes=1000000,
        ),
    )
    (source / "campaign-targets").mkdir()
    write(source / "campaign-targets/spec.json", binding["source_semantics"]["targets"])
    write(
        source / "reservation.json",
        dict(
            index=index,
            due_ns=due,
            started_ns=due,
            reserved_rpc=180,
            plan_sha256=sha(root / "contract/binding.json"),
        ),
    )
    outcome = dict(
        index=index,
        status="completed",
        data_kind="synthetic_fixture",
        finished_ns=due + 330 * NS,
        corpus_exit_code=0,
        execution_or_collection_exit_code=0,
        qa_exit_code=0,
    )
    write(source / "seal.json", dict(outcome=outcome, files=inventory(source)))
    return source


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    spec = read(Path("configs/research-f01-v1.json"))
    freeze(
        spec,
        tmp_path / "proposal",
        anchor_ns=ANCHOR,
        source_root=tmp_path / "future-source",
        now_ns=ANCHOR - 10**15,
    )
    root = tmp_path / "registry"
    initialize(tmp_path / "proposal", root)

    def export(window: Path, output: Path, **kwargs: Any) -> None:
        bundle = read(window / "fixture-dataset/dataset.json")
        bundle["source"]["seal_sha256"] = sha(window / "seal.json")
        save_dataset(output, bundle)

    # Only the previously qualified raw->dataset converter is mocked here.
    # Production _admit still checks seals, semantics, identities, times/grid and counts.
    monkeypatch.setattr(legacy, "export_window", export)
    import memecoin_sampling.registry as sampling_registry

    monkeypatch.setattr(
        sampling_registry,
        "verify_campaign",
        lambda path: dict(complete=True, successful_snapshots=180, reserved_requests=180),
    )
    return root
