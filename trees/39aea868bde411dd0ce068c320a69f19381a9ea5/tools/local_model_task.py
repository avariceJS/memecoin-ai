"""Run one bounded, read-only Ollama task over explicitly named repository text files."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path

from worker_preflight import NoRedirect, command, ollama_metadata


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run(repo, paths, output, model, task):
    repo = repo.resolve()
    packet, inputs = [], {}
    for name in paths:
        path = repo / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(repo)
            or Path(name).parts[0] not in {"tools", "src", "tests", "docs"}
            or path.suffix not in {".py", ".md", ".command"}
            or any(s in name.lower() for s in ("holdout", "f01", "d43", "secret"))
        ):
            raise ValueError("task_input_not_allowed")
        raw = path.read_bytes()
        inputs[name] = sha(raw)
        numbered = "\n".join(f"{i}: {s}" for i, s in enumerate(raw.decode().splitlines(), 1))
        packet.append(f"SOURCE {name}\n{numbered}")
    prompt = (
        task
        + "\nCite exact path:line. Preserve errors/STOP and uncertainties.\n"
        + "\n".join(packet)
    )
    if len(prompt.encode()) > 20000:
        raise ValueError("task_input_too_large")
    catalog = ollama_metadata("tags")
    found = [m for m in catalog["models"] if m["name"] == model]
    if len(found) != 1:
        raise ValueError("model_not_installed")
    output.mkdir(parents=True, exist_ok=False)
    base = command(["git", "rev-parse", "HEAD"], repo)
    request = dict(
        model=model,
        prompt=prompt,
        stream=False,
        think=False,
        keep_alive="30s",
        options=dict(num_ctx=8192, num_predict=900, temperature=0, seed=7),
    )
    (output / "input.json").write_text(json.dumps(request, indent=2) + "\n")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    started = time.time_ns()
    receipt = dict(
        task=task,
        base_commit=base,
        node="mac2",
        requested_model=model,
        catalog=found[0],
        ownership=[],
        inputs=inputs,
        input_sha256=sha(prompt.encode()),
        started_ns=started,
        billed_tokens=None,
        tests=[],
        blockers=[],
        review="pending",
        memory_before=command(["memory_pressure", "-Q"]),
        swap_before=command(["sysctl", "-n", "vm.swapusage"]),
    )
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps(request).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(req, timeout=240) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("response_too_large")
        result = json.loads(raw)
        answer = result.get("response", "")
        (output / "answer.txt").write_text(answer)
        receipt.update(
            actual_model=result.get("model"),
            output_sha256=sha(answer.encode()),
            done=result.get("done"),
            done_reason=result.get("done_reason"),
            usage={
                k: result.get(k)
                for k in (
                    "prompt_eval_count",
                    "eval_count",
                    "load_duration",
                    "total_duration",
                    "prompt_eval_duration",
                    "eval_duration",
                )
            },
            loaded=ollama_metadata("ps"),
            memory_after=command(["memory_pressure", "-Q"]),
            swap_after=command(["sysctl", "-n", "vm.swapusage"]),
            process_rss=command(["ps", "-axo", "comm=,rss="]),
            exit_code=0,
        )
        # Keep only model process measurements, never unrelated process inventory.
        receipt["process_rss"] = [
            s for s in (receipt["process_rss"] or "").splitlines() if "ollama" in s
        ]
    except (OSError, ValueError) as exc:
        receipt.update(exit_code=1, blockers=[type(exc).__name__])
    finally:
        receipt.update(
            finished_ns=time.time_ns(), final_commit=command(["git", "rev-parse", "HEAD"], repo)
        )
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt["exit_code"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--model", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("files", nargs="+")
    a = p.parse_args()
    return run(a.repo, a.files, a.output, a.model, a.task)


if __name__ == "__main__":
    raise SystemExit(main())
