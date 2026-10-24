"""No-process tests for the private Paperclip lab launcher contract."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering.paperclip_lab_server import (
    PAPERCLIP_PARENT_PIN,
    PAPERCLIP_PIN,
    PAPERCLIP_SOURCE_CHAIN,
    PAPERCLIP_STOP_CUSTODY_PINS,
    PAPERCLIP_UPSTREAM_PIN,
    LabPaths,
    LabServerError,
    _owned_process,
    _read_manifest,
    _validate_source,
    build_command,
    build_environment,
)


class PaperclipLabServerTests(unittest.TestCase):
    def test_source_pin_is_exact_commit_with_exact_predecessor_chain(self):
        self.assertEqual(
            PAPERCLIP_SOURCE_CHAIN,
            (
                "0e0c1eb22c879203337cee9710471c6fd30b00b1",
                "f24926c8e7fd231e4cac127532ed642247b222c2",
                "09ec93b95111d7d5e8b9c4a867f37e30d7462de1",
                "d2f6dedfe456473e7cf74799a1ac687ea90a1484",
                "16ae5c543a18db635a03c14f81e4ab7921d9c9a6",
                "35fbfa29a1b2ebaf81019d8022fdf60d70628ead",
                "a0cdea58195eed97c6f1ba4518c88826c778bb02",
                "407762fa09288fad6a9e39771efc5d738b57be7b",
                "0f478de9d9ef0dbac4e2f32bd4f80e8f28dcd9fb",
                "67ae03445e7c4a13d06ebbd1894fcc339f44907b",
                "d142b1dee5c25980169109f0d6161a8c4c7380a2",
                "01302914b5501fbb274b39477e087ac1692d9b6a",
                "b853ab3fa7addbbdba51087bb8e46dc89ca29fda",
                "fecf7b18def542fb6b58e7e665011e66599ffffb",
                "519c892646ec7b06996e990e9e728f794e1a689e",
                "bd1a12fbf94068884cb2d65db3d2fdab5f0a9595",
                "043f2514e67c50320433a0013fb228a75a51325c",
            ),
        )
        self.assertEqual(PAPERCLIP_PIN, "0e0c1eb22c879203337cee9710471c6fd30b00b1")
        self.assertEqual(PAPERCLIP_PARENT_PIN, "f24926c8e7fd231e4cac127532ed642247b222c2")
        self.assertEqual(PAPERCLIP_UPSTREAM_PIN, "043f2514e67c50320433a0013fb228a75a51325c")
        with tempfile.TemporaryDirectory() as directory:
            paths = LabPaths.resolve(Path(directory))
            paths.source.mkdir()
            with patch(
                "engineering.paperclip_lab_server._run",
                side_effect=(*PAPERCLIP_SOURCE_CHAIN, ""),
            ):
                self.assertEqual(_validate_source(paths), PAPERCLIP_PIN)

    def test_source_pin_rejects_unexpected_exact_predecessor(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = LabPaths.resolve(Path(directory))
            paths.source.mkdir()
            with patch(
                "engineering.paperclip_lab_server._run",
                side_effect=(PAPERCLIP_PIN, "unexpected-parent"),
            ):
                with self.assertRaisesRegex(LabServerError, "parent_pin_mismatch"):
                    _validate_source(paths)

    def test_source_pin_rejects_unexpected_second_predecessor(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = LabPaths.resolve(Path(directory))
            paths.source.mkdir()
            with patch(
                "engineering.paperclip_lab_server._run",
                side_effect=(*PAPERCLIP_SOURCE_CHAIN[:2], "unexpected-ancestor"),
            ):
                with self.assertRaisesRegex(LabServerError, "ancestor_pin_mismatch"):
                    _validate_source(paths)

    def test_source_pin_rejects_unexpected_upstream_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = LabPaths.resolve(Path(directory))
            paths.source.mkdir()
            with patch(
                "engineering.paperclip_lab_server._run",
                side_effect=(
                    *PAPERCLIP_SOURCE_CHAIN[:-1],
                    "unexpected-upstream",
                ),
            ):
                with self.assertRaisesRegex(LabServerError, "upstream_pin_mismatch"):
                    _validate_source(paths)

    def test_source_pin_metadata_binds_cumulative_patch_and_source_only_review(self):
        engineering = Path(__file__).resolve().parents[1]
        pin_path = engineering / "paperclip" / "source-pin.json"
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        patch_path = pin_path.parent / pin["patch"]
        self.assertEqual(pin["schema"], "brain-paperclip-source-v5")
        self.assertEqual(pin["upstream_commit"], PAPERCLIP_UPSTREAM_PIN)
        self.assertEqual(pin["predecessor_commit"], PAPERCLIP_PARENT_PIN)
        self.assertEqual(
            pin["predecessor_chain"],
            list(PAPERCLIP_SOURCE_CHAIN[1:]),
        )
        self.assertEqual(pin["derivative_commit"], PAPERCLIP_PIN)
        self.assertEqual(pin["patch_sha256"], hashlib.sha256(patch_path.read_bytes()).hexdigest())
        self.assertEqual(pin["patch_tree_equivalence"]["status"], "verified")
        self.assertEqual(
            pin["patch_tree_equivalence"]["derivative_tree"],
            "8a23b5f7890218053645ad5a5e04e90903fee41d",
        )
        self.assertEqual(pin["source_review"]["status"], "accepted_source_only")
        self.assertEqual(
            pin["source_review"]["commit"],
            PAPERCLIP_PIN,
        )
        self.assertEqual(
            pin["source_review"]["parent"],
            PAPERCLIP_PARENT_PIN,
        )
        self.assertEqual(
            pin["source_review"]["receipt_sha256"],
            "02432d4fa62695822c1cf1334425cdf91797d047cba8372c9db707f49785c813",
        )
        self.assertEqual(
            [record["commit"] for record in pin["source_review_chain"][-5:]],
            [
                "0f478de9d9ef0dbac4e2f32bd4f80e8f28dcd9fb",
                "407762fa09288fad6a9e39771efc5d738b57be7b",
                "16ae5c543a18db635a03c14f81e4ab7921d9c9a6",
                "09ec93b95111d7d5e8b9c4a867f37e30d7462de1",
                PAPERCLIP_PIN,
            ],
        )
        self.assertEqual(
            pin["source_review_chain"][-1]["commit"],
            PAPERCLIP_PIN,
        )
        self.assertEqual(pin["source_review_chain"][-1]["status"], "accepted_source_only")
        self.assertEqual(
            pin["source_review_chain"][-2]["commit"],
            "09ec93b95111d7d5e8b9c4a867f37e30d7462de1",
        )
        reviewed_commits = {record["commit"] for record in pin["source_review_chain"]}
        self.assertNotIn("a0cdea58195eed97c6f1ba4518c88826c778bb02", reviewed_commits)
        self.assertNotIn("35fbfa29a1b2ebaf81019d8022fdf60d70628ead", reviewed_commits)
        self.assertIn(PAPERCLIP_PIN, reviewed_commits)
        self.assertNotIn(PAPERCLIP_PARENT_PIN, reviewed_commits)
        self.assertEqual(
            PAPERCLIP_SOURCE_CHAIN[:9],
            (
                "0e0c1eb22c879203337cee9710471c6fd30b00b1",
                "f24926c8e7fd231e4cac127532ed642247b222c2",
                "09ec93b95111d7d5e8b9c4a867f37e30d7462de1",
                "d2f6dedfe456473e7cf74799a1ac687ea90a1484",
                "16ae5c543a18db635a03c14f81e4ab7921d9c9a6",
                "35fbfa29a1b2ebaf81019d8022fdf60d70628ead",
                "a0cdea58195eed97c6f1ba4518c88826c778bb02",
                "407762fa09288fad6a9e39771efc5d738b57be7b",
                "0f478de9d9ef0dbac4e2f32bd4f80e8f28dcd9fb",
            ),
        )
        self.assertEqual(pin["source_history"][0]["status"], "rejected")
        self.assertEqual(
            pin["source_history"][0]["commit"], "d142b1dee5c25980169109f0d6161a8c4c7380a2"
        )
        self.assertEqual(
            pin["prior_accepted_source_review"]["commit"],
            "16ae5c543a18db635a03c14f81e4ab7921d9c9a6",
        )
        self.assertEqual(
            pin["prior_accepted_source_review"]["receipt_sha256"],
            "8151ea3ff6ab3198c75e4c335885a46752c17ef9e5f2a8de3bc71070f2411bf6",
        )
        self.assertEqual(
            next(
                record["receipt_sha256"]
                for record in pin["source_review_chain"]
                if record["commit"] == "0f478de9d9ef0dbac4e2f32bd4f80e8f28dcd9fb"
            ),
            "3cd914f4bff9b72bd44776cb6ca662e92ad1625d47b62241d6fb016ad255e3b2",
        )
        self.assertEqual(set(pin["stop_custody_source_pins"]), PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertIn("16ae5c543a18db635a03c14f81e4ab7921d9c9a6", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertIn("09ec93b95111d7d5e8b9c4a867f37e30d7462de1", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("d2f6dedfe456473e7cf74799a1ac687ea90a1484", PAPERCLIP_STOP_CUSTODY_PINS)
        rejected_intermediate = next(
            record
            for record in pin["source_history"]
            if record["commit"] == "d2f6dedfe456473e7cf74799a1ac687ea90a1484"
        )
        self.assertEqual(rejected_intermediate["status"], "rejected")
        self.assertNotIn(rejected_intermediate["commit"], reviewed_commits)
        self.assertEqual(pin["runtime_acceptance"], "pending")
        rejected_wake_fix = next(
            record for record in pin["source_history"] if record["commit"] == PAPERCLIP_PARENT_PIN
        )
        self.assertEqual(rejected_wake_fix["status"], "rejected")
        self.assertNotIn(rejected_wake_fix["commit"], reviewed_commits)
        self.assertNotIn(rejected_wake_fix["commit"], PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn(PAPERCLIP_PIN, PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertEqual(
            pin["patch_tree_equivalence"]["upstream_tree"],
            "547374c4e8abe51ff22b8cecc51644800f794dd6",
        )

    def test_command_uses_the_pinned_source_loader_and_private_env_file(self):
        paths = LabPaths.resolve(Path("/synthetic/lab"))
        command = build_command(paths, Path("/synthetic/node/bin/node"))
        self.assertEqual(
            command,
            [
                "/synthetic/node/bin/node",
                "--env-file=/synthetic/lab/state/instances/lab/.env",
                "--import",
                "/synthetic/lab/paperclip/server/node_modules/tsx/dist/loader.mjs",
                "server/src/index.ts",
            ],
        )

    def test_environment_is_allowlisted_and_uses_private_lab_roots(self):
        paths = LabPaths.resolve(Path("/synthetic/lab"))
        environment = build_environment(paths, Path("/synthetic/node/bin/node"))
        self.assertEqual(
            set(environment),
            {
                "HOME",
                "PAPERCLIP_HOME",
                "CODEX_HOME",
                "PAPERCLIP_INSTANCE_ID",
                "PAPERCLIP_CONFIG",
                "PAPERCLIP_UI_DEV_MIDDLEWARE",
                "PAPERCLIP_RUNNER_NETWORK_ACCESS",
                "PAPERCLIP_LISTEN_HOST",
                "PAPERCLIP_LISTEN_PORT",
                "HOST",
                "PORT",
                "PAPERCLIP_ANNOUNCEMENTS_ENABLED",
                "HEARTBEAT_SCHEDULER_ENABLED",
                "PATH",
                "TMPDIR",
                "LANG",
            },
        )
        self.assertEqual(environment["HOME"], "/synthetic/lab/home")
        self.assertEqual(environment["PAPERCLIP_HOME"], "/synthetic/lab/state")
        self.assertEqual(environment["CODEX_HOME"], "/synthetic/lab/synthetic-codex-home")
        self.assertEqual(environment["PAPERCLIP_INSTANCE_ID"], "lab")
        self.assertEqual(
            environment["PAPERCLIP_CONFIG"],
            "/synthetic/lab/state/instances/lab/config.json",
        )
        self.assertEqual(environment["PAPERCLIP_RUNNER_NETWORK_ACCESS"], "disabled")
        self.assertEqual(environment["PAPERCLIP_LISTEN_HOST"], "127.0.0.1")
        self.assertEqual(environment["PAPERCLIP_LISTEN_PORT"], "3310")
        self.assertEqual(environment["HOST"], "127.0.0.1")
        self.assertEqual(environment["PORT"], "3310")
        self.assertEqual(environment["PAPERCLIP_ANNOUNCEMENTS_ENABLED"], "false")
        self.assertEqual(environment["HEARTBEAT_SCHEDULER_ENABLED"], "true")
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("PAPERCLIP_AGENT_JWT_SECRET", environment)

    def test_pid_custody_manifest_requires_exact_shape_and_pinned_source(self):
        expected = {
            "schema": "paperclip-lab-process-v3",
            "pid": 1234,
            "birth_identity": "darwin:1234:1792660000:000001:501",
            "command_sha256": "a" * 64,
            "source_pin": PAPERCLIP_PIN,
            "upstream_pin": PAPERCLIP_UPSTREAM_PIN,
            "node_version": "v24.22.0",
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "pid.json"
            manifest.write_text(json.dumps(expected), encoding="utf-8")
            manifest.chmod(0o600)
            self.assertEqual(_read_manifest(manifest), expected)
            malformed = {**expected, "extra": "ignored fields are not safe"}
            manifest.write_text(json.dumps(malformed), encoding="utf-8")
            manifest.chmod(0o600)
            with self.assertRaisesRegex(LabServerError, "manifest_invalid"):
                _read_manifest(manifest)

    def test_old_source_custody_is_opt_in_for_owned_process_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = LabPaths.resolve(Path(directory))
            paths.pid_file.parent.mkdir(parents=True)
            birth_identity = "darwin:1234:1792660000:000001:501"
            command = (
                f"node --env-file={paths.env_file} --import {paths.loader} server/src/index.ts"
            )
            for old_pin in sorted(PAPERCLIP_STOP_CUSTODY_PINS):
                manifest = {
                    "schema": "paperclip-lab-process-v3",
                    "pid": 1234,
                    "birth_identity": birth_identity,
                    "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                    "source_pin": old_pin,
                    "upstream_pin": PAPERCLIP_UPSTREAM_PIN,
                    "node_version": "v24.22.0",
                }
                paths.pid_file.write_text(json.dumps(manifest), encoding="utf-8")
                paths.pid_file.chmod(0o600)
                with self.assertRaisesRegex(LabServerError, "manifest_invalid"):
                    _read_manifest(paths.pid_file)
                self.assertEqual(
                    _read_manifest(paths.pid_file, allow_historical_source=True), manifest
                )
                with (
                    patch(
                        "engineering.paperclip_lab_server._process_info",
                        return_value=("synthetic-start", command),
                    ),
                    patch(
                        "engineering.paperclip_lab_server.process_identity",
                        return_value=birth_identity,
                    ),
                ):
                    self.assertEqual(
                        _owned_process(paths, allow_historical_source=True)[1], "running"
                    )
                    with self.assertRaisesRegex(LabServerError, "manifest_invalid"):
                        _owned_process(paths)
            unsupported = {**manifest, "source_pin": PAPERCLIP_UPSTREAM_PIN}
            paths.pid_file.write_text(json.dumps(unsupported), encoding="utf-8")
            paths.pid_file.chmod(0o600)
            with self.assertRaisesRegex(LabServerError, "manifest_invalid"):
                _read_manifest(paths.pid_file, allow_historical_source=True)
            rejected_intermediate = {
                **manifest,
                "source_pin": "d2f6dedfe456473e7cf74799a1ac687ea90a1484",
            }
            paths.pid_file.write_text(json.dumps(rejected_intermediate), encoding="utf-8")
            paths.pid_file.chmod(0o600)
            with self.assertRaisesRegex(LabServerError, "manifest_invalid"):
                _read_manifest(paths.pid_file, allow_historical_source=True)
            rejected_wake_fix = {
                **manifest,
                "source_pin": "f24926c8e7fd231e4cac127532ed642247b222c2",
            }
            paths.pid_file.write_text(json.dumps(rejected_wake_fix), encoding="utf-8")
            paths.pid_file.chmod(0o600)
            with self.assertRaisesRegex(LabServerError, "manifest_invalid"):
                _read_manifest(paths.pid_file, allow_historical_source=True)


if __name__ == "__main__":
    unittest.main()
