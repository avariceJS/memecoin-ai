"""Engineering fixtures for workflow safety; not a Cursor runtime/model test."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))
import check_scope  # noqa: E402
import workflow  # noqa: E402


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads((APP / "memory/WORK_STATE.json").read_text())
        # Independent fixtures remain meaningful after real project tasks advance.
        self.state["overall"] = "IN_PROGRESS"
        self.state["blockers"] = []
        self.state["model_binding"].update(
            status="UNVERIFIED", observed_executor=None, evidence=None
        )
        for task in self.state["tasks"]:
            task.update(
                status="PENDING",
                write_files=[],
                checks=[],
                reviews=[],
                summary="",
                reference_viewed=False,
                reference_sha256=None,
            )

    def done(self, index):
        task = self.state["tasks"][index]
        task["status"] = "DONE"
        task["checks"] = [{"command": "fixture-only", "result": "PASS"}]
        task["reviews"] = [
            {
                "kind": kind,
                "result": "PASS",
                "evidence": "fixture-only",
                "reviewed_revision": "fixture",
            }
            for kind in task["required_reviews"]
        ]

    def bind(self):
        self.state["model_binding"].update(
            status="VERIFIED",
            observed_executor="composer-2.5[fast=false]",
            evidence="fixture: native task card, not self-report",
        )

    def test_pending_recovery_and_native_definitions(self):
        workflow.validate(self.state)
        self.assertEqual(
            workflow.render(self.state), workflow.render(json.loads(json.dumps(self.state)))
        )
        workflow.check_definitions()

    def test_false_completion_and_missing_proof(self):
        self.state["overall"] = "COMPLETE"
        with self.assertRaisesRegex(ValueError, "Premature"):
            workflow.validate(self.state)
        self.state["overall"] = "IN_PROGRESS"
        self.state["tasks"][1]["status"] = "DONE"
        with self.assertRaisesRegex(ValueError, "Unverified"):
            workflow.validate(self.state)

    def test_model_config_is_not_observed_runtime(self):
        self.done(0)
        with self.assertRaisesRegex(ValueError, "execution not verified"):
            workflow.validate(self.state)
        self.bind()
        workflow.validate(self.state)
        self.state["model_binding"]["observed_executor"] = "inherit"
        with self.assertRaises(ValueError):
            workflow.validate(self.state)

    def test_dependency_and_cycle(self):
        self.state["tasks"][2]["status"] = "RUNNING"
        with self.assertRaisesRegex(ValueError, "Dependency"):
            workflow.validate(self.state)
        self.state["tasks"][2]["status"] = "PENDING"
        self.state["tasks"][0]["depends_on"] = ["P1.1"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            workflow.validate(self.state)

    def test_no_external_stop_while_independent_work_exists(self):
        self.state["overall"] = "BLOCKED_NEEDS_USER"
        self.state["blockers"] = ["fixture: account setting"]
        self.state["tasks"][0].update(status="BLOCKED_EXTERNAL", blocker="fixture: verify picker")
        with self.assertRaisesRegex(ValueError, "Independent"):
            workflow.validate(self.state)
        self.state["tasks"][1].update(status="BLOCKED_EXTERNAL", blocker="fixture: external action")
        workflow.validate(self.state)

    def test_lease_bounds_collision_and_compact_summary(self):
        self.done(0)
        self.bind()
        self.done(1)
        left, right = self.state["tasks"][2:4]
        for path in [
            "src/core.py",
            "apps/control-center/../../src/core.py",
            ".cursor/agents/not-cc.md",
            "apps/control-center/memory/WORK_STATE.json",
        ]:
            left["write_files"] = [path]
            with self.assertRaises(ValueError):
                workflow.validate(self.state)
        left.update(write_files=["apps/control-center/web/a.ts"], status="RUNNING")
        right.update(write_files=left["write_files"], status="RUNNING")
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            workflow.validate(self.state)
        right["write_files"] = ["apps/control-center/web/b.ts"]
        workflow.validate(self.state)
        left["summary"] = "x" * 3001
        with self.assertRaisesRegex(ValueError, "Summary"):
            workflow.validate(self.state)

    def test_ancestor_lease_and_noncanonical_shared_path_denied(self):
        self.done(0)
        self.bind()
        self.done(1)
        left, right = self.state["tasks"][2:4]
        left.update(status="RUNNING", write_files=["apps/control-center/web/components"])
        right.update(status="RUNNING", write_files=["apps/control-center/web/components/Card.tsx"])
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            workflow.validate(self.state)
        left["write_files"] = []
        right["write_files"] = ["apps/control-center/web/./package.json"]
        with self.assertRaisesRegex(ValueError, "Noncanonical"):
            workflow.validate(self.state)
        right["write_files"] = ["apps/control-center/web"]
        with self.assertRaisesRegex(ValueError, "Shared"):
            workflow.validate(self.state)

    def test_missing_reference_blocks_visual_only_not_state_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.png"
            self.assertEqual(workflow.reference_status(missing), "MISSING")
            workflow.validate(self.state)
            self.assertIn("IN_PROGRESS", workflow.render(self.state))
            with self.assertRaisesRegex(ValueError, "Visual gate"):
                workflow.visual_preflight(missing)
            missing.write_bytes(b"different")
            self.assertEqual(workflow.reference_status(missing), "CHANGED")
            with self.assertRaises(ValueError):
                workflow.visual_preflight(missing)

    def test_readonly_agent_cannot_get_write_lease(self):
        self.state["tasks"][1]["write_files"] = ["apps/control-center/web/a.ts"]
        with self.assertRaisesRegex(ValueError, "Readonly"):
            workflow.validate(self.state)

    def test_visual_gate_requires_both_image_views_and_actual_screenshot(self):
        self.done(0)
        self.bind()
        self.done(1)
        self.done(2)
        task = self.state["tasks"][2]
        with self.assertRaisesRegex(ValueError, "Builder"):
            workflow.validate(self.state)
        task.update(reference_viewed=True, reference_sha256=workflow.REFERENCE_SHA)
        with self.assertRaisesRegex(ValueError, "visual comparison"):
            workflow.validate(self.state)
        review = next(r for r in task["reviews"] if r["kind"] == "visual")
        review.update(
            reference_viewed=True,
            reference_sha256=workflow.REFERENCE_SHA,
            screenshot="fixture-only screenshot",
        )
        workflow.validate(self.state)
        review["reference_sha256"] = "wrong"
        with self.assertRaises(ValueError):
            workflow.validate(self.state)


class GitScopeRegressionTests(unittest.TestCase):
    def setUp(self):
        # Disposable independent Git repo under owned ignored artifacts, never main/index.
        local = APP / ".local"
        local.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=local)
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q", "-b", check_scope.BRANCH)
        self.git("config", "user.name", "UI fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "core.txt").write_text("original\n")
        self.git("add", "core.txt")
        self.git("commit", "-qm", "fixture base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.repo), *args], stderr=subprocess.STDOUT
        )

    def guard(self):
        with (
            patch.object(check_scope, "ROOT", self.repo),
            patch.object(check_scope, "BASE", self.base),
        ):
            check_scope.main()

    def test_staged_core_edit_hidden_by_working_copy_is_denied(self):
        (self.repo / "core.txt").write_text("changed\n")
        self.git("add", "core.txt")
        (self.repo / "core.txt").write_text("original\n")
        self.assertEqual(self.git("diff", "--name-only", self.base), b"")
        with self.assertRaisesRegex(SystemExit, "core.txt"):
            self.guard()

    def test_core_to_ui_rename_is_denied(self):
        (self.repo / "apps/control-center").mkdir(parents=True)
        self.git("mv", "core.txt", "apps/control-center/core.txt")
        with self.assertRaisesRegex(SystemExit, "core.txt"):
            self.guard()

    def test_only_named_agents_and_owned_files(self):
        (self.repo / ".cursor/agents").mkdir(parents=True)
        p = self.repo / ".cursor/agents/cc-ui-builder.md"
        p.write_text("fixture")
        self.guard()
        p.rename(p.with_name("other.md"))
        with self.assertRaisesRegex(SystemExit, "other.md"):
            self.guard()


if __name__ == "__main__":
    unittest.main()
