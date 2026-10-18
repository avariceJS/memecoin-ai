"""Read-only B13 operator report. Does not start services or rewrite ledgers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from memecoin_operator.history import build_report, exclusive_output, render_markdown


def _write_new(path: Path, text: str) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать read-only журнал оператора B13")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--now-ns", type=int)
    args = parser.parse_args(argv)
    if args.now_ns is not None and args.now_ns < 0:
        print("now-ns должен быть неотрицательным", file=sys.stderr)
        return 2
    targets = [args.output]
    if args.markdown_output is not None:
        if args.markdown_output == args.output:
            print("markdown-output совпадает с output", file=sys.stderr)
            return 2
        targets.append(args.markdown_output)
    for target in targets:
        reason = exclusive_output(args.root, target)
        if reason:
            print(f"выходной файл отклонён: {reason}", file=sys.stderr)
            return 2
    report = build_report(args.root, args.now_ns)
    body = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    try:
        _write_new(args.output, body)
        if args.markdown_output is not None:
            _write_new(args.markdown_output, render_markdown(report))
    except FileExistsError:
        print("выходной файл уже существует", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"cutoff_ns": report["cutoff_ns"], "ok": report["ok"], "schema": report["schema"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
