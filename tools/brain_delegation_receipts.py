"""Export narrow Cursor execution receipts, never full prompts, reasoning or tool logs."""

import argparse
import json
import time
from pathlib import Path

from memecoin_ml.data import sha
from memecoin_runner.ledger import publish

OWNERS = {
    "operator": [
        "control.py",
        "web.py",
        "runtime.py",
        "test_control.py",
        "test_web.py",
        "test_runtime.py",
    ],
    "source": [
        "acquisition.py",
        "memecoin_continuous/store.py",
        "test_acquisition.py",
        "test_consistent_view.py",
    ],
    "domain-tests": ["test_adapters.py", "test_recovery.py", "test_learning_ml.py"],
    "browser": ["tools/brain_operator_e2e.cjs", "tests/operator/browser_fixture.py"],
    "history": ["history.py", "tools/brain_operator_report.py", "test_history.py"],
    "readmodel": ["readmodel.py", "test_readmodel.py"],
    "security": ["test_security_integration.py"],
    "background": ["background.py", "test_background.py"],
    "repair": ["repair.py", "test_repair.py"],
    "polish": ["web.py", "test_ui_polish.py"],
    "source-review": [
        "acquisition.py",
        "memecoin_continuous/store.py",
        "test_acquisition.py",
        "test_consistent_view.py",
    ],
    "operator-review": [
        "control.py",
        "runtime.py",
        "test_control.py",
        "test_web.py",
        "test_runtime.py",
    ],
    "domain-followup": ["test_deployment.py", "test_pipeline_integration.py"],
    "history-review": ["history.py", "telemetry.py", "test_history.py", "test_telemetry.py"],
    "browser-review": ["tools/brain_operator_e2e.cjs", "tests/operator/browser_fixture.py"],
    "readmodel-review": ["readmodel.py", "test_readmodel.py"],
    "source-handoff": ["acquisition.py", "test_acquisition.py"],
    "background-review": ["background.py", "test_background.py"],
    "handoff-tests": ["test_handoff_integration.py"],
    "host": ["host.py", "test_host.py"],
    "domain-final": ["test_deployment.py", "test_pipeline_integration.py"],
    "stop-security": ["test_stop_effects_security.py"],
    "polish-final": ["web.py", "test_ui_polish.py"],
    "paper-ui": ["web.py", "test_ui_polish.py"],
    "browser-final": ["tools/brain_operator_e2e.cjs", "tests/operator/browser_fixture.py"],
    "evidence-final": ["docs/brain-b13-acceptance.md", "docs/brain-operator-evidence.md"],
    "history-path": ["history.py", "test_history.py", "docs/brain-b13-acceptance.md"],
    "operator-projection": ["control.py", "runtime.py", "test_control.py"],
    "auto-audit": ["test_auto_cycle_integration.py"],
    "source-history": ["source_history.py", "test_source_history.py"],
    "operator-stop": [
        "control.py",
        "runtime.py",
        "test_control.py",
        "test_runtime.py",
        "test_web.py",
    ],
}


def collect(folder):
    reports = []
    for name, ownership in OWNERS.items():
        path = folder / ("grok-" + name + ".jsonl")
        if not path.exists():
            continue
        init, final = None, None
        tool_calls = 0
        for line in path.open():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") == "system" and row.get("subtype") == "init":
                init = row
            if row.get("type") == "tool_call" and row.get("subtype") == "completed":
                tool_calls += 1
            if row.get("type") == "result":
                final = row
        reports.append(
            dict(
                worker=name,
                requested_model="grok-4.7-xhigh",
                actual_model=init.get("model") if init else None,
                session_id=init.get("session_id") if init else None,
                task_sha=sha(folder / ("grok-" + name + ".txt")),
                owned_files=ownership,
                state="finished" if final else "running",
                completed_tool_calls=tool_calls,
                is_error=final.get("is_error") if final else None,
                duration_ms=final.get("duration_ms") if final else None,
                source_log=str(path),
                log_sha=sha(path),
                independently_billed_quota_unknown=True,
                result_evidence=[
                    {"path": str(review), "sha256": sha(review)}
                    for review in sorted(folder.glob("*review.md"))
                    if name.split("-")[0] in review.name
                    or (name.startswith("operator") and review.name.startswith("security"))
                    or (name.startswith("browser") and review.name.startswith("visual"))
                    or (name.startswith("domain") and review.name.startswith("deployment"))
                    or (name == "paper-ui" and review.name.startswith("paper-ui"))
                ],
            )
        )
    return dict(
        checked_ns=time.time_ns(),
        workers=reports,
        implementation_commits="parent integration commits; workers instructed not to commit",
        tests="see final combined acceptance evidence; receipts do not invent test results",
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = collect(args.directory)
    publish(args.output, result)
    print(json.dumps({"workers": len(result["workers"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
