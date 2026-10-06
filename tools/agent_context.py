#!/usr/bin/env python3
"""Build a bounded, task-specific agent context packet from tracked project docs."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_CHARS = 5_000
MAX_PACKET_CHARS = 24_000

PROFILES: dict[str, tuple[str, ...]] = {
    "operator": (
        "docs/brain-operator-manual.md",
        "docs/brain-autonomous-operator.md",
    ),
    "data": (
        "memory/data-and-execution.md",
        "docs/brain-training-expansion-results.md",
    ),
    "ml": (
        "memory/evaluation.md",
        "memory/research.md",
        "docs/brain-training-expansion-results.md",
    ),
    "architecture": (
        "memory/architecture.md",
        "memory/project.md",
        "docs/brain-autonomous-operator.md",
    ),
    "review": (
        "memory/now.md",
        "memory/decisions.md",
        "docs/contracts.md",
    ),
    "docs": (
        "memory/now.md",
        "memory/README.md",
        "memory/changes.md",
    ),
}


def _read(path: Path, limit: int | None = MAX_FILE_CHARS) -> str:
    if not path.is_file():
        return f"[missing: {path.relative_to(ROOT)}]"
    content = path.read_text(encoding="utf-8")
    if limit is not None and len(content) > limit:
        return content[:limit] + f"\n[…обрезано; полный файл: {path.relative_to(ROOT)}]"
    return content


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode:
        return result.stderr.strip() or "[git unavailable]"
    return result.stdout.strip() or "(clean/empty)"


def _query_snippets(query: str, paths: tuple[str, ...], limit: int = 8) -> str:
    terms = [term.casefold() for term in re.findall(r"[^\W_]+", query) if len(term) > 2]
    if not terms:
        return ""
    found: list[str] = []
    for rel in paths:
        path = ROOT / rel
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if any(term in line.casefold() for term in terms):
                start, end = max(0, index - 1), min(len(lines), index + 2)
                excerpt = "\n".join(lines[start:end])
                found.append(f"### {rel}:{index + 1}\n{excerpt}")
                if len(found) >= limit:
                    return "\n\n".join(found)
    return "\n\n".join(found) or "Совпадений в выбранных источниках нет."


def build_packet(task: str, query: str | None = None) -> str:
    if task not in PROFILES:
        raise ValueError(f"unknown task profile: {task}")
    sources = ("AGENTS.md", "memory/now.md", *PROFILES[task])
    chunks = [
        f"# Контекст задачи: {task}",
        "Сводка — навигация; критические решения сверяйте с первичными документами/evidence.",
        "## Git status\n" + _git("status", "--short", "--branch"),
        "## Recent commits\n" + _git("log", "-5", "--oneline", "--decorate"),
    ]
    # Operating instructions and hard boundaries must never be prefix-truncated.
    for rel in ("AGENTS.md", "memory/now.md"):
        chunks.append(f"## Source: {rel}\n" + _read(ROOT / rel, limit=None))
    chunks.append("## Profile source index\n" + "\n".join(PROFILES[task]))
    if len("\n\n".join(chunks)) > MAX_PACKET_CHARS - 500:
        raise ValueError("mandatory_context_exceeds_budget: read mandatory sources directly")
    if query:
        excerpts = _query_snippets(query, sources)
        chunks.append("## Search excerpts (profile sources only)\n" + excerpts)
    for rel in dict.fromkeys(PROFILES[task]):
        if rel in ("AGENTS.md", "memory/now.md"):
            continue
        path = ROOT / rel
        pin = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        chunks.append(f"## Source: {rel}\nSHA256: {pin}\n" + _read(path))
    packet = "\n\n".join(chunks)
    if len(packet) > MAX_PACKET_CHARS:
        packet = packet[:MAX_PACKET_CHARS] + "\n[…packet capped; open referenced sources as needed]"
    estimate = (len(packet) + 3) // 4
    return packet + f"\n\n<!-- rough context estimate: ~{estimate} tokens; chars/4 heuristic -->\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(PROFILES), required=True)
    parser.add_argument("--query", help="keywords for bounded lexical excerpts")
    args = parser.parse_args()
    print(build_packet(args.task, args.query))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
