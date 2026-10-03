"""Developer-only Git guard. Never imported/exposed by the HTTP adapter."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = "6a7e079"
BRANCH = "control-center/observatory-handoff"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def allowed(path):
    return (
        path.startswith(("apps/control-center/", "docs/control-center/"))
        or path == ".cursor/rules/control-center.mdc"
    )


def main():
    if git("branch", "--show-current").decode().strip() != BRANCH:
        raise SystemExit("Wrong branch: do not switch/modify other worktrees automatically.")
    changed = git("diff", "--name-only", "-z", BASE).split(b"\0")
    changed += git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    refused = [p.decode() for p in changed if p and not allowed(p.decode())]
    if refused:
        raise SystemExit("Outside UI scope: " + ", ".join(refused))
    print("PASS: dedicated branch; all changes owned by Control Center; baseline " + BASE)


if __name__ == "__main__":
    main()
