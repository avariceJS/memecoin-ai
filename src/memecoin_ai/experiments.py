from __future__ import annotations

import json
import math
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from memecoin_ai import __version__
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.replay import ReplayResult


def summarize(result: ReplayResult, initial_cash: int) -> dict[str, object]:
    if not result.equity or initial_cash <= 0:
        raise ValidationError("invalid equity history")
    peak = initial_cash
    drawdown = 0.0
    for mark in result.equity:
        equity = int(mark["liquidation_equity_quote"])
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    terminal = result.equity[-1]
    statuses = ("filled", "failed", "dropped", "rejected")
    return {
        "net_liquidation_pnl_quote": terminal["liquidation_equity_quote"] - initial_cash,
        "net_liquidation_return": terminal["liquidation_equity_quote"] / initial_cash - 1,
        "max_liquidation_drawdown": drawdown,
        "terminal_cash_quote": terminal["cash_quote"],
        "unpriced_assets": terminal["unpriced_assets"],
        "orders": {s: sum(r.status == s for r in result.records) for s in statuses},
        "network_fees_quote": sum(r.network_fee_quote for r in result.records),
        "final_balances": result.final_balances,
        "evidence": "synthetic_engineering_check_only",
        "edge_established": False,
    }


def code_fingerprint(root: Path) -> str:
    files = sorted((root / "src" / "memecoin_ai").glob("*.py"))
    if not files:
        raise ValidationError("source checkout required for reproducible run provenance")
    payload = {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in files}
    return digest(canonical(payload).encode())


def git_identity(root: Path) -> dict[str, object]:
    def command(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        return {
            "commit": command("rev-parse", "HEAD"),
            "dirty": bool(command("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": True}


class RunRecorder:
    def __init__(self, root: Path, output: Path, dataset_id: str, config: dict[str, Any]) -> None:
        provenance = {
            "schema_version": 1,
            "dataset_id": dataset_id,
            "config": config,
            "source_sha256": code_fingerprint(root),
            "git": git_identity(root),
            "package_version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
        }
        self.path = output
        self.path.mkdir(parents=True, exist_ok=False)
        self._write("manifest.json", provenance)
        self._write("status.json", {"status": "running"})

    def _write(self, filename: str, value: object) -> None:
        temporary = self.path / (filename + ".tmp")
        temporary.write_text(canonical(value) + "\n")
        temporary.replace(self.path / filename)

    def complete(self, result: ReplayResult, initial_cash: int) -> None:
        if json.loads((self.path / "status.json").read_text())["status"] != "running":
            raise ValidationError("run already finalized")
        self._write("result.json", asdict(result))
        self._write("metrics.json", summarize(result, initial_cash))
        hashes = {
            name: digest((self.path / name).read_bytes())
            for name in ("manifest.json", "result.json", "metrics.json")
        }
        self._write("status.json", {"status": "completed", "sha256": hashes})

    def fail(self, error_type: str) -> None:
        self._write("status.json", {"status": "failed", "error_type": error_type})


def verify_run(path: Path) -> dict[str, Any]:
    status: dict[str, Any] = json.loads((path / "status.json").read_text())
    if status.get("status") != "completed":
        raise ValidationError("run is not completed")
    expected = {"manifest.json", "metrics.json", "result.json"}
    if set(status.get("sha256", {})) != expected:
        raise ValidationError("invalid run checksum manifest")
    for name, sha in status["sha256"].items():
        if digest((path / name).read_bytes()) != sha:
            raise ValidationError("run artifact checksum mismatch")
    return status


@dataclass(frozen=True)
class PromotionEvidence:
    real_oos: bool
    execution_calibrated: bool
    protocol_frozen: bool
    paper_complete: bool
    risk_passed: bool
    improvement_lower_bound: float


def promotion_blockers(evidence: PromotionEvidence, minimum_improvement: float) -> tuple[str, ...]:
    if not math.isfinite(minimum_improvement) or minimum_improvement < 0:
        raise ValidationError("invalid promotion threshold")
    blockers = [
        name
        for name in (
            "real_oos",
            "execution_calibrated",
            "protocol_frozen",
            "paper_complete",
            "risk_passed",
        )
        if not getattr(evidence, name)
    ]
    if (
        not math.isfinite(evidence.improvement_lower_bound)
        or evidence.improvement_lower_bound <= minimum_improvement
    ):
        blockers.append("insufficient_evidence_of_improvement")
    return tuple(blockers)
