"""Focused checks for the bounded native delivery adapter."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering import paperclip_native_delivery as delivery


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args], input=data, capture_output=True, check=False
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout


def init_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["/usr/bin/git", "init", "--quiet", str(path)], check=True)
    git(
        path,
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "--allow-empty",
        "--no-gpg-sign",
        "-m",
        "seed",
    )
    return git(path, "rev-parse", "HEAD").decode().strip()


class NativeDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="native-delivery-")
        self.root = Path(self.temp.name)
        self.seed = self.root / "seed"
        self.base = init_repo(self.seed)
        self.paths = ["src/new_module.py", "tests/test_new_module.py"]
        self.work = self.root / "work"
        init_repo(self.work)
        for path, data in (
            (self.paths[0], b"VALUE = 1\n"),
            (self.paths[1], b"assert True\n"),
        ):
            target = self.work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        git(self.work, "add", "--", *self.paths)
        self.patch = git(self.work, "diff", "--cached", "--binary")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_normalizer_accepts_only_exact_new_regular_source_and_test_files(self) -> None:
        files = delivery.normalize_patch(self.patch, self.seed, self.base, self.paths)
        self.assertEqual(files, {self.paths[0]: b"VALUE = 1\n", self.paths[1]: b"assert True\n"})

        extra = self.work / "README.md"
        extra.write_text("unexpected\n")
        git(self.work, "add", "README.md")
        with self.assertRaisesRegex(delivery.DeliveryError, "candidate_path_set_invalid"):
            delivery.normalize_patch(
                git(self.work, "diff", "--cached", "--binary"), self.seed, self.base, self.paths
            )

    def test_normalizer_rejects_non_new_files_and_executable_modes(self) -> None:
        (self.seed / self.paths[0]).parent.mkdir(parents=True)
        (self.seed / self.paths[0]).write_bytes(b"occupied\n")
        git(self.seed, "add", self.paths[0])
        git(
            self.seed,
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "occupied",
        )
        occupied = git(self.seed, "rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(delivery.DeliveryError, "candidate_paths_not_new_in_seed"):
            delivery.normalize_patch(self.patch, self.seed, occupied, self.paths)

        git(self.work, "update-index", "--chmod=+x", self.paths[0])
        with self.assertRaisesRegex(delivery.DeliveryError, "candidate_file_mode_invalid"):
            delivery.normalize_patch(
                git(self.work, "diff", "--cached", "--binary"), self.seed, self.base, self.paths
            )

    def test_qa_result_binds_exact_source_and_file_hashes(self) -> None:
        files = delivery.normalize_patch(self.patch, self.seed, self.base, self.paths)
        hashes = {p: hashlib.sha256(files[p]).hexdigest() for p in self.paths}
        patch_hash = hashlib.sha256(self.patch).hexdigest()
        result = {
            "schema": delivery.QA_SCHEMA,
            "verdict": "accept",
            "source_task_id": "source-task",
            "source_patch_sha256": patch_hash,
            "files": hashes,
            "checks": [{"name": "focused checks", "result": "passed"}],
        }
        delivery.validate_qa(
            json.dumps(result).encode(),
            "source-task",
            patch_hash,
            {p: {"sha256": h} for p, h in hashes.items()},
            self.paths,
        )
        for altered in (
            {**result, "files": {**hashes, self.paths[0]: "0" * 64}},
            {**result, "files": {self.paths[0]: hashes[self.paths[0]]}},
            {**result, "source_patch_sha256": "0" * 64},
            {**result, "verdict": "reject"},
        ):
            with self.subTest(altered=altered):
                with self.assertRaises(delivery.DeliveryError):
                    delivery.validate_qa(
                        json.dumps(altered).encode(),
                        "source-task",
                        patch_hash,
                        {p: {"sha256": h} for p, h in hashes.items()},
                        self.paths,
                    )

    def test_fixed_path_safety_and_immutable_write_collision(self) -> None:
        root = self.root / "qa"
        root.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (root / "src").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(delivery.DeliveryError, "target_parent_invalid"):
            delivery.target(root, "src/new.py", create=True)
        with self.assertRaisesRegex(delivery.DeliveryError, "relative_target_invalid"):
            delivery.target(root, "../escape")

        collision = self.root / "candidate.py"
        collision.write_bytes(b"owner data")
        with self.assertRaisesRegex(delivery.DeliveryError, "target_collision"):
            delivery.write_once(collision, b"worker data", 0o400)
        self.assertEqual(collision.read_bytes(), b"owner data")

    def test_wrong_current_actor_cannot_complete_a_pinned_task(self) -> None:
        api = delivery.Paperclip.__new__(delivery.Paperclip)
        api.m = {
            "company_id": "00000000-0000-4000-8000-000000000001",
            "stage": {
                "task_id": "00000000-0000-4000-8000-000000000002",
                "agent_id": "00000000-0000-4000-8000-000000000003",
            },
        }
        api.agent_id = "00000000-0000-4000-8000-000000000004"
        api.run_id = "00000000-0000-4000-8000-000000000005"
        with patch.object(
            api,
            "request",
            return_value={"id": api.agent_id, "companyId": api.m["company_id"]},
        ):
            with self.assertRaisesRegex(delivery.DeliveryError, "current_actor_mismatch"):
                api.current("stage")

    def test_saved_native_run_shape_uses_id_and_included_subscription_billing(self) -> None:
        company = "00000000-0000-4000-8000-000000000011"
        task_id = "00000000-0000-4000-8000-000000000012"
        agent_id = "00000000-0000-4000-8000-000000000013"
        run_id = "00000000-0000-4000-8000-000000000014"
        api = delivery.Paperclip.__new__(delivery.Paperclip)
        api.m = {"company_id": company}
        run_record = {
            "id": run_id,
            "issueId": task_id,
            "nativeIssueId": task_id,
            "agentId": agent_id,
            "companyId": company,
            "runtimeMode": "native",
            "status": "succeeded",
            "runnerProfileJson": {
                "nativeExecutionInput": {
                    "provider": {"kind": "codex", "model": "gpt-6-luna", "reasoningEffort": "high"}
                }
            },
            "usageJson": {"billingType": "subscription_included"},
        }

        def response(_method: str, route: str, **_kwargs):
            if route == f"/issues/{task_id}/runs":
                return [
                    {"id": run_id, "issueId": task_id, "agentId": agent_id, "status": "succeeded"}
                ]
            if route == f"/heartbeat-runs/{run_id}":
                return run_record
            raise AssertionError(route)

        with patch.object(api, "request", side_effect=response):
            run = api.latest_run({"task_id": task_id, "agent_id": agent_id})
        self.assertEqual(run["runId"], run_id)


if __name__ == "__main__":
    unittest.main()
