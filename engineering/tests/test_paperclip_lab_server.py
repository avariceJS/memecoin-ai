"""No-process tests for the private Paperclip lab launcher contract."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering.paperclip_lab_server import (
    PAPERCLIP_CLEANUP_ONLY_PINS,
    PAPERCLIP_PARENT_PIN,
    PAPERCLIP_PATCH_PARENT_PIN,
    PAPERCLIP_PATCH_PIN,
    PAPERCLIP_PATCH_SOURCE_CHAIN,
    PAPERCLIP_PIN,
    PAPERCLIP_SOURCE_CHAIN,
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
                "d06cbd5cd012e178a0e519d329eed36b8943fac9",
                "76c6912266283ee4e8dfd80fa9c0821eeb40b614",
                "b09fe3c3b9295e3e558dcbb4fc8d2a347782f837",
                "69cefa8d4887d2a3835668823ff854fce9c95cc2",
                "1e7d18239e6dac28254e5b44a3959eea63cf0f3b",
                "e169417665e2ef61df18103eecab33701e1b06f5",
                "15aee8eb62c13d90f8e8e462db6c3b83b33f858b",
                "e7075c6660859eed0990f352d525b40356411e6a",
                "920d2a32fffbe711bc3cb078b9091d6604e55acc",
                "645bf29bf8deb41f857a970199d58909927635d2",
                "b31d6598dd93d4e597874b5e4b12c2e3e0127eb7",
                "5b5540f5418977ab2d861f7da3ee3dc7554d0601",
                "ccbe6e7183b4bae56ba857c17a190ecc92c51910",
                "6dec1acae77171a4a25c2620858edb043f119373",
                "240c623f381ceb84bda190270cafee3454ae17fc",
                "bf91730cc459792ba640378b97da6890511755e8",
                "8aa17aa0600aa952ff50af9373a05a01bd6125a8",
                "2905d23263ceb188689cd68624332bfa2d56b600",
                "15f93e91bd7a561e6b4c3460ccd619923e15165b",
                "d25b4b51f9f81298c49aea42957fb487f7a17ae5",
                *PAPERCLIP_PATCH_SOURCE_CHAIN,
            ),
        )
        self.assertEqual(PAPERCLIP_PIN, "d06cbd5cd012e178a0e519d329eed36b8943fac9")
        self.assertEqual(PAPERCLIP_PARENT_PIN, "76c6912266283ee4e8dfd80fa9c0821eeb40b614")
        self.assertEqual(PAPERCLIP_UPSTREAM_PIN, "043f2514e67c50320433a0013fb228a75a51325c")
        self.assertEqual(PAPERCLIP_PATCH_PIN, "3eb3e6d50b91707bb6c06b7dfef9a608643f87fd")
        self.assertEqual(PAPERCLIP_PATCH_PARENT_PIN, "d560a09538a255c779e4a10b14d41a3114bf10f2")
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


    def test_rejected_and_interim_native_descendants_cannot_launch(self):
        for old_pin in (
            "6dec1acae77171a4a25c2620858edb043f119373",
            "240c623f381ceb84bda190270cafee3454ae17fc",
            "8d994c9f56224b65944e3fab02add1d7fe8431e6",
            "1d9eccf898ec00f9f59ef5f536b69154c211a359",
            "ffc6a3c844faa92dcad570f0a1184434a9a3d6d0",
            "4f969bc74ea0aca1c2a81edde74796c92f221d1c",
            "9667379fcc3e0247ac632659850fbedf59b99229",
            "aa0c5b08f0e8ce09343ffd146f3441e984ede8f7",
            "1637ad0b50c17c5896784b95c9fb97a31defbf4c",
            "8aa17aa0600aa952ff50af9373a05a01bd6125a8",
            "2905d23263ceb188689cd68624332bfa2d56b600",
            "15f93e91bd7a561e6b4c3460ccd619923e15165b",
            "3eb3e6d50b91707bb6c06b7dfef9a608643f87fd",
            "d25b4b51f9f81298c49aea42957fb487f7a17ae5",
            "aa0cdc262ea5a1d81ef95aa9a7dadc0f981a9e24",
            "c9a2ac81b0c7cdcf6903b2e39ab29b55d9cf9343",
            "b00893db62c0e1719f731c94f20c9c70cecfac11",
            "b89d1f5e82f7275e372087799f21566721814029",
            "44b4a1c13d23094283914b8326c902fea9d0d994",
            "d560a09538a255c779e4a10b14d41a3114bf10f2",
        ):
            with self.subTest(old_pin=old_pin), tempfile.TemporaryDirectory() as directory:
                paths = LabPaths.resolve(Path(directory))
                paths.source.mkdir()
                with patch(
                    "engineering.paperclip_lab_server._run",
                    side_effect=(old_pin,),
                ):
                    with self.assertRaisesRegex(LabServerError, "paperclip_source_pin_mismatch"):
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
        self.assertIn(PAPERCLIP_PARENT_PIN, PAPERCLIP_CLEANUP_ONLY_PINS)
        with tempfile.TemporaryDirectory() as directory:
            paths = LabPaths.resolve(Path(directory))
            paths.pid_file.parent.mkdir(parents=True)
            birth_identity = "darwin:1234:1792660000:000001:501"
            command = (
                f"node --env-file={paths.env_file} --import {paths.loader} server/src/index.ts"
            )
            for old_pin in sorted(PAPERCLIP_CLEANUP_ONLY_PINS):
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
