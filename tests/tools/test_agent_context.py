from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_packet(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/agent_context.py"), *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_profiles_are_scoped_and_known() -> None:
    expected = {
        "operator": "docs/brain-operator-manual.md",
        "data": "memory/data-and-execution.md",
        "ml": "memory/evaluation.md",
        "architecture": "memory/architecture.md",
        "review": "docs/contracts.md",
        "docs": "memory/README.md",
    }
    for task, source in expected.items():
        packet = run_packet("--task", task)
        assert f"## Source: {source}" in packet
        assert "## Source: memory/state.md" not in packet
        assert len(packet) <= 24_200


def test_packet_contains_git_and_context_map() -> None:
    packet = run_packet("--task", "operator")
    assert "## Git status" in packet
    assert "memory/now.md" in packet
    assert "docs/brain-operator-manual.md" in packet
    assert len(packet) <= 24_200


def test_query_is_limited_to_selected_sources() -> None:
    packet = run_packet("--task", "operator", "--query", "sleep recovery")
    assert "Search excerpts (profile sources only)" in packet
    assert "memory/decisions.md:" not in packet
