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
        "operator": "docs/operator-runtime.md",
        "data": "docs/brain-source-architecture.md",
        "ml": "docs/training-evaluation.md",
        "architecture": "docs/brain-development.md",
        "review": "docs/contracts.md",
        "docs": "docs/INDEX.md",
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
    assert "docs/operator-runtime.md" in packet
    assert len(packet) <= 24_200
    assert (ROOT / "AGENTS.md").read_text() in packet
    assert (ROOT / "memory/now.md").read_text() in packet


def test_query_is_limited_to_selected_sources() -> None:
    packet = run_packet("--task", "operator", "--query", "sleep recovery")
    assert "Search excerpts (profile sources only)" in packet
    assert "memory/evaluation.md:" not in packet
