"""Developer-only Git guard. Never imported/exposed by the HTTP adapter."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = "HEAD"
BRANCH = "research/active"  # Fixture/default development name, not a scope authorization.


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


AGENT_NAMES = (
    "artifact-analyst",
    "ui-builder",
    "adapter-builder",
    "test-debugger",
    "visual-reviewer",
    "boundary-reviewer",
)
AGENT_PATHS = {f".cursor/agents/cc-{name}.md" for name in AGENT_NAMES}


def allowed(path):
    return (
        path.startswith(("apps/control-center/", "docs/control-center/"))
        or path == ".cursor/rules/control-center.mdc"
        or path in AGENT_PATHS
    )


def main(base=None):
    base = BASE if base is None else base
    base = git("rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    changed = git("diff", "--no-renames", "--name-only", "-z", base).split(b"\0")
    changed += git("diff", "--cached", "--no-renames", "--name-only", "-z", base).split(b"\0")
    changed += git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    refused = [p.decode() for p in changed if p and not allowed(p.decode())]
    if refused:
        raise SystemExit("Outside UI scope: " + ", ".join(refused))
    print("PASS: pending/range changes owned by Control Center; baseline " + base)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=BASE,
        help="Review changes since this commit; default HEAD checks pending edits.",
    )
    main(parser.parse_args().base)
