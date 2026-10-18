"""Zero-provider-GET owner/process qualification, with hashed argv only."""

import argparse
import json
import plistlib
import subprocess
import time
import urllib.request
from pathlib import Path

from memecoin_curriculum.research_guard import argv_identity, inventory, owner_guard
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish


def add_rule(rules, pins, value, args, role):
    exe = Path(args[0]).resolve()
    binaries = {str(exe): sha(exe)}
    app = exe.parent.parent / "Resources/Python.app/Contents/MacOS/Python"
    if app.is_file():
        binaries[str(app)] = sha(app)
    rules.append(
        dict(
            label=value["Label"],
            role=role,
            argv_sha=argv_identity(args),
            executables=list(binaries.values()),
        )
    )
    pins.update(binaries)


def rules_from_launchagents(directory):
    rules, pins = [], {}
    loaded = {
        line.split()[-1]
        for line in subprocess.check_output(
            ["launchctl", "list"], text=True, timeout=10
        ).splitlines()[1:]
        if line.split()
    }
    for path in sorted(directory.glob("ai.memecoin*.plist")):
        value = plistlib.loads(path.read_bytes())
        if value.get("Label") not in loaded:
            continue
        args = value.get("ProgramArguments", [])
        if value["Label"] == "ai.memecoin.d43.calendar01":
            if len(args) != 5 or args[1] != "-B" or args[3] != "run-due":
                raise ValueError("calendar_launcher_shape")
            root = Path(args[4])
            script = root / "runtime/tools/calendar_schedule.py"
            plan = read(root / "plan.json")
            if (
                Path(args[2]) != script
                or sha(script) != sha(Path(__file__).with_name("calendar_schedule.py"))
                or time.time_ns() <= max(plan["due_ns"]) + plan["spec"]["lateness_seconds"] * 10**9
            ):
                raise ValueError("calendar_not_retired_or_code_changed")
            # Public schedule metadata only, never references/windows/outcomes.
            for p in (path, root / "plan.json", script):
                pins[str(p)] = sha(p)
            add_rule(rules, pins, value, args, "retired_calendar_supervisor")
            continue
        if len(args) < 8 or args[1:4] != ["-I", "-B", "-c"]:
            raise ValueError("unqualified_launch_shape:" + path.name)
        code = args[4]
        module, entrypoint = None, "runtime.py"
        if (
            code == "import sys;sys.path.insert(0,sys.argv.pop(1));"
            "from memecoin_paper.runtime import main;main()"
        ):
            if len(args) != 8 or args[-1] not in ("ui", "paper", "train"):
                raise ValueError("unqualified_paper_launcher")
            root = Path(args[6])
            role = "paper_ui" if args[-1] == "ui" else "retired_paper_supervisor"
            if role != "paper_ui" and (read(root / "config.json")["expires_ns"] > time.time_ns()):
                raise ValueError("active_paper_service")
        elif (
            code == "import sys;sys.path.insert(0,sys.argv.pop(1));"
            "from memecoin_continuous.runtime import main;main()"
        ):
            if len(args) != 8 or args[-1] not in ("source", "audit"):
                raise ValueError("unqualified_source_launcher")
            root = Path(args[6])
            if read(root / "config.json")["expires_ns"] > time.time_ns():
                raise ValueError("source_not_retired")
            role = "retired_source_supervisor"
        elif code == (
            "import sys; sys.path.insert(0,sys.argv.pop(1)); "
            "from memecoin_brain_b04.service import main; main()"
        ):
            root = Path(args[6])
            ref = read(root / "deployment.json")
            if (
                len(args) != 8
                or args[7] != ref["pin"]
                or Path(args[5]) != Path(ref["bundle"]) / "runtime"
                or read(root / "config.json")["expires_ns"] > time.time_ns()
            ):
                raise ValueError("b04_not_retired_or_binding")
            role, module, entrypoint = "retired_b04_supervisor", "memecoin_brain_b04", "service.py"
        elif code == (
            "import os,sys;os.environ.pop('MEMECOIN_OPERATOR_ADAPTER',None);"
            "os.environ.pop('MEMECOIN_SYNTHETIC_TICK',None);"
            "sys.path.insert(0,sys.argv.pop(1));from pathlib import Path;"
            "from memecoin_operator.deployment import guard;guard(Path(sys.argv.pop(1)));"
            "from memecoin_operator.runtime import main;main()"
        ):
            root = Path(args[6])
            if args[7:] != ["--root", str(root), "--port", "8792"]:
                raise ValueError("unqualified_operator_launcher")
            role = "paused_operator"
        else:
            raise ValueError("unqualified_launch_module:" + path.name)
        # Freeze the actual runtime entrypoint and configuration. The research worker
        # never edits these files or asks launchd to start/stop/reload anything.
        runtime = Path(args[5])
        module = module or (
            "memecoin_paper"
            if "paper" in role
            else ("memecoin_continuous" if "source" in role else "memecoin_operator")
        )
        for p in (path, root / "config.json", runtime / module / entrypoint):
            pins[str(p)] = sha(p)
        # Pin imported helpers as well, not just the launcher entrypoint.
        for p in sorted(runtime.glob("memecoin_*/*.py")):
            pins[str(p)] = sha(p)
        add_rule(rules, pins, value, args, role)
    return rules, pins


def qualify(base, output, seconds):
    if not 15 <= seconds <= 180:
        raise ValueError("qualification_duration")
    source = base / "work/brain-b13/source-repaired02"
    operator = base / "work/brain-b13/operator04"
    config = read(source / "config.json")
    from memecoin_operator.acquisition import _admit_expiry_stop

    # Existing exact STOP semantics, read-only mode: no raw label/index access.
    stop = _admit_expiry_stop(source, config["expires_ns"], time.time_ns(), audit=False)
    if not stop:
        raise ValueError("retired_source_receipt_required")
    with urllib.request.urlopen("http://127.0.0.1:8792/api/status", timeout=10) as response:
        status = json.load(response)
    if (
        status["mode"] != "paused"
        or status["phase"] != "paused"
        or any(j["status"] in ("queued", "running") for j in status["jobs"])
        or any(e["expires_ns"] > time.time_ns() for e in status["exams"])
    ):
        raise ValueError("owner_not_quiescent")
    rules, pins = rules_from_launchagents(Path.home() / "Library/LaunchAgents")
    event_path = max((source / "journal").glob("event-*.json"))
    event = read(event_path)
    state = event["payload"]["state"]
    if state["failures"] or state["cooldown_ns"] > time.time_ns():
        raise ValueError("source_last_state_not_healthy")
    for p in (
        source / "config.json",
        source / "deployment.json",
        source / "STOP",
        source / stop["incident_name"],
        event_path,
        operator / "config.json",
        operator / "deployment.json",
    ):
        pins[str(p)] = sha(p)
    output.mkdir(parents=True, exist_ok=False)
    observations = []
    deadline = time.monotonic() + seconds
    owner = dict(
        pins=pins,
        revision=status["revision"],
        source_expires_ns=config["expires_ns"],
        cooldown_until_ns=max(state["cooldown_ns"], state["last_ns"] + 900 * 10**9),
        operator_db=str(operator / "operator.sqlite"),
        source_journal=str(source / "journal"),
        source_last_event=str(event_path),
        exam_expiries_ns=[e["expires_ns"] for e in status["exams"]],
    )
    while time.monotonic() < deadline:
        owner_guard(dict(retired_owner=owner, expires_ns=time.time_ns() + 30 * 10**9))
        observations.append(inventory(rules))
        time.sleep(2)
    result = dict(
        ready=all(o["ready"] for o in observations),
        provider_requests=0,
        consumer_rules=rules,
        observations=observations,
        source_stop_admission=stop,
        retired_owner=owner,
        champion=status["champion"],
        operator_warnings=status["warnings"],
        source_debit=state["attempts"],
        finished_ns=time.time_ns(),
    )
    publish(output / "qualification.json", result)
    seal(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    result = qualify(args.base, args.output, args.seconds)
    print(
        json.dumps(
            dict(
                ready=result["ready"],
                observations=len(result["observations"]),
                unknown=[
                    r for o in result["observations"] for r in o["processes"] if not r["allowed"]
                ],
            )
        )
    )


if __name__ == "__main__":
    main()
