"""Local development-state checks; never imported by the read-only HTTP adapter."""

import argparse
import hashlib
import json
from pathlib import Path

from check_scope import AGENT_NAMES, allowed
from check_scope import main as check_git_scope

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
STATE = APP / "memory/WORK_STATE.json"
STATUS = APP / "memory/STATUS.md"
REFERENCE = "docs/control-center/references/dashboard-concept-v1.png"
REFERENCE_SHA = "dd03716ed059c4bdf85726489460d4c07acb6836a872553ccf2157bb8dd014d6"
SHARED = {
    "apps/control-center/memory/WORK_STATE.json",
    "apps/control-center/memory/STATUS.md",
    "apps/control-center/web/package.json",
    "apps/control-center/web/package-lock.json",
    "apps/control-center/scripts/check_scope.py",
}
TASK_STATES = {"PENDING", "RUNNING", "NEEDS_FIX", "REVIEW", "DONE", "BLOCKED_EXTERNAL"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def overlaps(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def validate(state):
    require(state["schema_version"] == 1, "Unsupported workflow schema")
    require(state["scope"] == "control-center-web-v1-phases-0-7", "Scope drift")
    require(state["overall"] in {"IN_PROGRESS", "COMPLETE", "BLOCKED_NEEDS_USER"}, "Overall")
    tasks = {t["id"]: t for t in state["tasks"]}
    require(len(tasks) == len(state["tasks"]) and tasks, "Duplicate/empty tasks")
    require(state["current_task"] in tasks, "Unknown current task")
    visited, visiting = set(), set()

    def visit(task_id):
        require(task_id in tasks, "Missing dependency")
        require(task_id not in visiting, "Dependency cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)
    writers = set()
    running_writers = 0
    for task in tasks.values():
        require(task["status"] in TASK_STATES, "Unknown task state")
        require(task["role"] in {"coordinator", *AGENT_NAMES}, "Unknown role")
        require(len(task["summary"]) <= 3000, "Summary too large")
        if task["role"] in {"artifact-analyst", "visual-reviewer", "boundary-reviewer"}:
            require(not task["write_files"], "Readonly agent has write lease")
        for path in task["write_files"]:
            require(not Path(path).is_absolute() and ".." not in Path(path).parts, "Unsafe lease")
            require(
                path == Path(path).as_posix() and not any(c in path for c in "*?[]\\"),
                "Noncanonical lease",
            )
            require(allowed(path), "Lease outside UI scope")
            if task["role"] != "coordinator":
                require(
                    not any(overlaps(path, shared) for shared in SHARED)
                    and not path.startswith(".cursor/"),
                    "Shared writer",
                )
                require(not overlaps(path, "apps/control-center/memory"), "State writer")
        if task["status"] == "RUNNING":
            if task["write_files"]:
                running_writers += 1
                require(running_writers <= 2, "More than two concurrent writers")
            for path in task["write_files"]:
                require(
                    not any(overlaps(path, writer) for writer in writers),
                    "Overlapping write leases",
                )
                writers.add(path)
        if task["status"] in {"RUNNING", "REVIEW", "DONE"}:
            require(
                all(tasks[d]["status"] == "DONE" for d in task["depends_on"]),
                "Dependency unfinished",
            )
        if task["status"] == "BLOCKED_EXTERNAL":
            require(task["blocker"].strip(), "External blocker needs cause/action")
        if task["status"] == "DONE":
            require(
                task["checks"]
                and all(c["result"] == "PASS" and c["command"] for c in task["checks"]),
                "Unverified completion",
            )
            for kind in task["required_reviews"]:
                reviews = [
                    r for r in task["reviews"] if r["kind"] == kind and r["result"] == "PASS"
                ]
                require(
                    reviews and all(r["evidence"] and r["reviewed_revision"] for r in reviews),
                    "Missing review receipt",
                )
                if kind == "visual":
                    require(
                        task["reference_viewed"] and task["reference_sha256"] == REFERENCE_SHA,
                        "Builder did not view reference",
                    )
                    require(
                        all(
                            r.get("reference_viewed")
                            and r.get("reference_sha256") == REFERENCE_SHA
                            and r.get("screenshot")
                            for r in reviews
                        ),
                        "Missing actual visual comparison",
                    )
    binding = state["model_binding"]
    require(binding["configured_executor"] == "composer-2.5[fast=false]", "Executor policy drift")
    require(binding["status"] in {"UNVERIFIED", "VERIFIED"}, "Model binding status")
    if binding["status"] == "VERIFIED":
        require(
            binding["observed_executor"] == "composer-2.5[fast=false]" and binding["evidence"],
            "Native model execution not verified",
        )
    else:
        require(not binding["observed_executor"], "Unverified binding has observed executor")
    if state["overall"] == "COMPLETE":
        require(all(t["status"] == "DONE" for t in tasks.values()), "Premature COMPLETE")
    if state["overall"] == "BLOCKED_NEEDS_USER":
        require(state["blockers"], "No external blockers")
        ready = [
            t
            for t in tasks.values()
            if t["status"] in {"PENDING", "RUNNING", "NEEDS_FIX", "REVIEW"}
            and all(tasks[d]["status"] == "DONE" for d in t["depends_on"])
        ]
        require(not ready, "Independent work still available")
    return state


def check_definitions():
    """Check our deliberately small documented frontmatter subset, not Cursor runtime."""
    for role in AGENT_NAMES:
        path = ROOT / f".cursor/agents/cc-{role}.md"
        require(not path.is_symlink(), "Agent definition symlink")
        parts = path.read_text().split("---", 2)
        require(len(parts) == 3 and not parts[0], "Frontmatter missing")
        fields = {}
        for line in parts[1].strip().splitlines():
            key, value = line.split(":", 1)
            require(key not in fields, "Duplicate frontmatter field")
            fields[key] = value.strip()
        expected_readonly = role in {"artifact-analyst", "visual-reviewer", "boundary-reviewer"}
        require(
            set(fields) == {"name", "description", "model", "readonly", "is_background"},
            "Unexpected native field",
        )
        require(fields["name"] == "cc-" + role, "Agent name mismatch")
        require(fields["model"] == "composer-2.5[fast=false]", "Unexpected model")
        require(fields["readonly"] == str(expected_readonly).lower(), "Readonly mismatch")
        require(fields["is_background"] == "false", "Background disabled for this team")
        require(
            "AGENT-TEAM.md" in parts[2] and "apps/control-center/AGENTS.md" in parts[2],
            "Missing required rules",
        )


def reference_status(path=None):
    path = path or ROOT / REFERENCE
    if path.is_symlink():
        return "INVALID_SYMLINK"
    if not path.is_file():
        return "MISSING"
    return (
        "AVAILABLE" if hashlib.sha256(path.read_bytes()).hexdigest() == REFERENCE_SHA else "CHANGED"
    )


def visual_preflight(path=None):
    require(reference_status(path) == "AVAILABLE", "Visual gate: reference missing/changed")


def render(state):
    tasks = state["tasks"]
    active = next(t for t in tasks if t["id"] == state["current_task"])
    done = sum(t["status"] == "DONE" for t in tasks)
    lines = [
        "# Control Center — STATUS",
        "",
        "Generated from WORK_STATE.json; не редактировать вручную.",
        "",
        f"- Overall: **{state['overall']}** — web v1, {done}/{len(tasks)} tasks; Tauri вне scope.",
        f"- Current phase: **{active['phase']}**",
        f"- Current task: **{active['id']} — {active['title']}**",
        f"- Last completed: {state['last_completed']}",
        f"- Last verified commit: `{state['last_verified_commit']}`; "
        "latest фактически: `git log -1 --oneline`.",
        f"- Checks: {state['last_checks']}",
        f"- Model binding: {state['model_binding']['status']} (config ≠ runtime).",
        f"- Blockers: {'; '.join(state['blockers']) or 'нет глобального blocker'}",
        f"- Next action: **{state['next_action']}**",
        "",
        "| Task | Role | State |",
        "|---|---|---|",
    ]
    lines += [f"| {t['id']} {t['title']} | {t['role']} | {t['status']} |" for t in tasks]
    lines += ["", "## Ограничения", ""] + ["- " + item for item in state["limitations"]]
    lines += [
        "",
        "Interface requests: " + "; ".join(state["interface_requests"]),
        "",
        "Recovery: docs/control-center/GROK-PROMPT.md → RESUME. "
        "State — UI workflow, не core truth.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--visual", action="store_true", help="Require exact reference for visual work"
    )
    args = parser.parse_args()
    check_git_scope()
    state = validate(json.loads(STATE.read_text()))
    check_definitions()
    ref_status = reference_status()
    if args.visual:
        visual_preflight()
    if ref_status != "AVAILABLE":
        print("WARNING: reference " + ref_status + "; visual work locked, independent work allowed")
    expected = render(state)
    if args.render:
        temporary = STATUS.with_suffix(".md.tmp")
        temporary.write_text(expected)
        temporary.replace(STATUS)
    require(STATUS.read_text() == expected, "STATUS stale: run workflow.py --render")
    print(
        "PASS: state, dependencies, leases, agent definitions, reference and STATUS; "
        "native execution NOT tested"
    )


if __name__ == "__main__":
    main()
