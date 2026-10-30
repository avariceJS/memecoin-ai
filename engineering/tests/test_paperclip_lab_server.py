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
        self.assertEqual(PAPERCLIP_PIN, "15aee8eb62c13d90f8e8e462db6c3b83b33f858b")
        self.assertEqual(PAPERCLIP_PARENT_PIN, "e7075c6660859eed0990f352d525b40356411e6a")
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

    def test_source_pin_bundle_records_incremental_accepted_review(self):
        engineering = Path(__file__).resolve().parents[1]
        manifest_path = engineering / "paperclip-source-bundles" / "ccbe6e7183b4bae56ba857c17a190ecc92c51910.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle_path = manifest_path.with_suffix(".bundle")
        bundle = bundle_path.read_bytes()
        self.assertEqual(manifest["schema"], "paperclip-managed-native-code-custody-v1")
        self.assertEqual(manifest["native_source_commit"], "ccbe6e7183b4bae56ba857c17a190ecc92c51910")
        self.assertEqual(manifest["native_source_tree"], "d5c46161fa8cfdef45aec5d677879e8d52a8cbb6")
        self.assertEqual(
            manifest["prerequisite_commit"],
            "240c623f381ceb84bda190270cafee3454ae17fc",
        )
        self.assertEqual(
            manifest["incremental_commits"],
            ["6dec1acae77171a4a25c2620858edb043f119373", "ccbe6e7183b4bae56ba857c17a190ecc92c51910"],
        )
        self.assertEqual(
            manifest["changed_source_paths"],
            [
                "server/src/__tests__/heartbeat-process-recovery.test.ts",
                "server/src/services/recovery/legacy-continuation.test.ts",
                "server/src/services/recovery/legacy-continuation.ts",
                "server/src/services/recovery/service.ts",
            ],
        )
        self.assertEqual(manifest["bundle_file"], bundle_path.name)
        self.assertEqual(manifest["bundle_bytes"], len(bundle))
        self.assertEqual(manifest["bundle_sha256"], hashlib.sha256(bundle).hexdigest())
        self.assertEqual(
            manifest["source_acceptance_receipt_sha256"],
            "164844cd283960848962396b58dd4a7aaca1c492386b99d3208b9d7a01e6f204",
        )
        self.assertIn("Source custody only", manifest["limits"])

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

    def test_accepted_process_rights_candidate_has_custody_without_runtime_admission(self):
        engineering = Path(__file__).resolve().parents[1]
        manifest_path = engineering / "paperclip-source-bundles" / "5b5540f5418977ab2d861f7da3ee3dc7554d0601.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle = manifest_path.with_suffix(".bundle").read_bytes()
        self.assertEqual(manifest["state"], "accepted-source-custody-not-runtime-admission")
        self.assertEqual(manifest["native_source_commit"], "5b5540f5418977ab2d861f7da3ee3dc7554d0601")
        self.assertEqual(manifest["prerequisite_commit"], "ccbe6e7183b4bae56ba857c17a190ecc92c51910")
        self.assertEqual(manifest["bundle_bytes"], len(bundle))
        self.assertEqual(manifest["bundle_sha256"], hashlib.sha256(bundle).hexdigest())
        self.assertEqual(manifest["source_acceptance_receipt_sha256"], "22ba69c67ee6edc444244915591903274f6135e5fd9408ec65c8cd028f0d0726")
        self.assertIn("no runtime admission", manifest["limits"])
        self.assertIn("ccbe6e7183b4bae56ba857c17a190ecc92c51910", PAPERCLIP_STOP_CUSTODY_PINS)

    def test_accepted_recovery_transaction_candidate_has_custody_without_runtime_admission(self):
        engineering = Path(__file__).resolve().parents[1]
        manifest_path = engineering / "paperclip-source-bundles" / "b31d6598dd93d4e597874b5e4b12c2e3e0127eb7.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle = manifest_path.with_suffix(".bundle").read_bytes()
        self.assertEqual(manifest["state"], "accepted-source-custody-not-runtime-admission")
        self.assertEqual(manifest["native_source_commit"], "b31d6598dd93d4e597874b5e4b12c2e3e0127eb7")
        self.assertEqual(manifest["prerequisite_commit"], "5b5540f5418977ab2d861f7da3ee3dc7554d0601")
        self.assertEqual(manifest["incremental_commits"], ["b31d6598dd93d4e597874b5e4b12c2e3e0127eb7"])
        self.assertEqual(manifest["bundle_bytes"], len(bundle))
        self.assertEqual(manifest["bundle_sha256"], hashlib.sha256(bundle).hexdigest())
        self.assertEqual(
            manifest["source_acceptance_receipt"],
            "state/productive-20261030/recovery-transaction-review.json",
        )
        self.assertEqual(
            manifest["source_acceptance_receipt_sha256"],
            "f602905a085c6110b7d8f52dce52c4b943d06510603971898617a9c6648080d2",
        )
        self.assertIn("no runtime admission", manifest["limits"])
        self.assertNotIn("b31d6598dd93d4e597874b5e4b12c2e3e0127eb7", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertIn("5b5540f5418977ab2d861f7da3ee3dc7554d0601", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertIn("5b5540f5418977ab2d861f7da3ee3dc7554d0601", PAPERCLIP_CLEANUP_ONLY_PINS)

    def test_accepted_scoped_recovery_permissions_candidate_has_incremental_custody(self):
        engineering = Path(__file__).resolve().parents[1]
        commit = "15aee8eb62c13d90f8e8e462db6c3b83b33f858b"
        manifest_path = engineering / "paperclip-source-bundles" / f"{commit}.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle = manifest_path.with_suffix(".bundle").read_bytes()
        self.assertEqual(manifest["state"], "accepted-source-custody-not-runtime-admission")
        self.assertEqual(manifest["native_source_commit"], commit)
        self.assertEqual(manifest["native_source_tree"], "545a1e0a3a3e5a64f1d0588e795afbabe1599280")
        self.assertEqual(manifest["prerequisite_commit"], "b31d6598dd93d4e597874b5e4b12c2e3e0127eb7")
        self.assertEqual(manifest["incremental_commits"], [
            "645bf29bf8deb41f857a970199d58909927635d2",
            "920d2a32fffbe711bc3cb078b9091d6604e55acc",
            "e7075c6660859eed0990f352d525b40356411e6a",
            commit,
        ])
        self.assertEqual(manifest["bundle_bytes"], len(bundle))
        self.assertEqual(manifest["bundle_sha256"], hashlib.sha256(bundle).hexdigest())
        self.assertEqual(manifest["source_acceptance_receipt_sha256"], "7113f1eb0eb3f72b9ac33e4606fba9102cc182a075b16cbe537b8a7a32950132")
        self.assertEqual(manifest["changed_source_paths"], [
            "packages/shared/src/validators/agent.ts",
            "server/src/__tests__/agent-permissions-routes.test.ts",
            "server/src/__tests__/issue-recovery-actions.test.ts",
            "server/src/routes/agents.ts",
            "server/src/routes/issues.ts",
        ])
        self.assertIn("no runtime admission", manifest["limits"])
        pin = json.loads((engineering / "paperclip" / "source-pin.json").read_text(encoding="utf-8"))
        self.assertEqual(pin["managed_source"]["commit"], commit)
        self.assertEqual(pin["managed_source"]["tree"], manifest["native_source_tree"])
        self.assertEqual(pin["managed_source"]["ancestry"], [commit, "e7075c6660859eed0990f352d525b40356411e6a",
            "920d2a32fffbe711bc3cb078b9091d6604e55acc", "645bf29bf8deb41f857a970199d58909927635d2",
            "b31d6598dd93d4e597874b5e4b12c2e3e0127eb7"])
        self.assertEqual(PAPERCLIP_PIN, commit)

    def test_source_pin_metadata_binds_cumulative_patch_and_source_only_review(self):
        engineering = Path(__file__).resolve().parents[1]
        pin_path = engineering / "paperclip" / "source-pin.json"
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        patch_path = pin_path.parent / pin["patch"]
        self.assertEqual(pin["schema"], "brain-paperclip-source-v6")
        self.assertEqual(pin["upstream_commit"], PAPERCLIP_UPSTREAM_PIN)
        self.assertEqual(pin["predecessor_commit"], PAPERCLIP_PATCH_PARENT_PIN)
        self.assertEqual(
            pin["predecessor_chain"],
            list(PAPERCLIP_PATCH_SOURCE_CHAIN[1:]),
        )
        self.assertEqual(pin["derivative_commit"], PAPERCLIP_PATCH_PIN)
        self.assertEqual(pin["patch_sha256"], hashlib.sha256(patch_path.read_bytes()).hexdigest())
        self.assertEqual(pin["patch_tree_equivalence"]["status"], "verified")
        self.assertEqual(
            pin["patch_tree_equivalence"]["derivative_tree"],
            "d01cfc0f7a898dadcc91c99af4e4d4cc72c8a27b",
        )
        self.assertEqual(pin["source_review"]["status"], "accepted_source_only")
        self.assertEqual(
            pin["source_review"]["commit"],
            PAPERCLIP_PATCH_PIN,
        )
        self.assertEqual(
            pin["source_review"]["parent"],
            PAPERCLIP_PATCH_PARENT_PIN,
        )
        self.assertEqual(
            pin["source_review"]["receipt_sha256"],
            "737aec6399ec3c5b82f95ffd900ea01aee85998cfcfb480e2838a02d17a5beae",
        )
        self.assertEqual(
            [record["commit"] for record in pin["source_review_chain"][-2:]],
            [
                "44b4a1c13d23094283914b8326c902fea9d0d994",
                PAPERCLIP_PATCH_PIN,
            ],
        )
        self.assertEqual(
            pin["source_review_chain"][-1]["commit"],
            PAPERCLIP_PATCH_PIN,
        )
        self.assertEqual(pin["source_review_chain"][-1]["status"], "accepted_source_only")
        self.assertEqual(
            pin["source_review_chain"][-2]["commit"],
            "44b4a1c13d23094283914b8326c902fea9d0d994",
        )
        reviewed_commits = {record["commit"] for record in pin["source_review_chain"]}
        self.assertNotIn("a0cdea58195eed97c6f1ba4518c88826c778bb02", reviewed_commits)
        self.assertNotIn("35fbfa29a1b2ebaf81019d8022fdf60d70628ead", reviewed_commits)
        self.assertIn("0e0c1eb22c879203337cee9710471c6fd30b00b1", reviewed_commits)
        self.assertIn(PAPERCLIP_PATCH_PIN, reviewed_commits)
        self.assertNotIn(PAPERCLIP_PATCH_PARENT_PIN, reviewed_commits)
        self.assertEqual(
            PAPERCLIP_PATCH_SOURCE_CHAIN[:7],
            (
                "3eb3e6d50b91707bb6c06b7dfef9a608643f87fd",
                "d560a09538a255c779e4a10b14d41a3114bf10f2",
                "44b4a1c13d23094283914b8326c902fea9d0d994",
                "b89d1f5e82f7275e372087799f21566721814029",
                "b00893db62c0e1719f731c94f20c9c70cecfac11",
                "c9a2ac81b0c7cdcf6903b2e39ab29b55d9cf9343",
                "aa0cdc262ea5a1d81ef95aa9a7dadc0f981a9e24",
            ),
        )
        self.assertEqual(pin["source_history"][0]["status"], "rejected")
        self.assertEqual(
            pin["source_history"][0]["commit"], "d142b1dee5c25980169109f0d6161a8c4c7380a2"
        )
        self.assertEqual(
            pin["prior_accepted_source_review"]["commit"],
            "0e0c1eb22c879203337cee9710471c6fd30b00b1",
        )
        self.assertEqual(
            pin["prior_accepted_source_review"]["receipt_sha256"],
            "02432d4fa62695822c1cf1334425cdf91797d047cba8372c9db707f49785c813",
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
        self.assertIn("0e0c1eb22c879203337cee9710471c6fd30b00b1", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertIn("aa0cdc262ea5a1d81ef95aa9a7dadc0f981a9e24", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("89cd827fdb36ede8d12fce16302cf595c88d2ade", PAPERCLIP_STOP_CUSTODY_PINS)
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
            record
            for record in pin["source_history"]
            if record["commit"] == "f24926c8e7fd231e4cac127532ed642247b222c2"
        )
        self.assertEqual(rejected_wake_fix["status"], "rejected")
        self.assertNotIn(rejected_wake_fix["commit"], reviewed_commits)
        self.assertNotIn(rejected_wake_fix["commit"], PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertIn(PAPERCLIP_PATCH_PIN, PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertIn("240c623f381ceb84bda190270cafee3454ae17fc", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertIn("8aa17aa0600aa952ff50af9373a05a01bd6125a8", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("8d994c9f56224b65944e3fab02add1d7fe8431e6", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("1d9eccf898ec00f9f59ef5f536b69154c211a359", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("ffc6a3c844faa92dcad570f0a1184434a9a3d6d0", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("4f969bc74ea0aca1c2a81edde74796c92f221d1c", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("9667379fcc3e0247ac632659850fbedf59b99229", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("aa0c5b08f0e8ce09343ffd146f3441e984ede8f7", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("1637ad0b50c17c5896784b95c9fb97a31defbf4c", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertIn("15f93e91bd7a561e6b4c3460ccd619923e15165b", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("15f93e91bd7a561e6b4c3460ccd619923e15165b", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("2905d23263ceb188689cd68624332bfa2d56b600", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("2905d23263ceb188689cd68624332bfa2d56b600", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("d25b4b51f9f81298c49aea42957fb487f7a17ae5", PAPERCLIP_CLEANUP_ONLY_PINS)
        self.assertNotIn("d25b4b51f9f81298c49aea42957fb487f7a17ae5", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn(PAPERCLIP_PATCH_PIN, PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn(PAPERCLIP_PIN, PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("aa0c5b08f0e8ce09343ffd146f3441e984ede8f7", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("1637ad0b50c17c5896784b95c9fb97a31defbf4c", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("c9a2ac81b0c7cdcf6903b2e39ab29b55d9cf9343", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("b00893db62c0e1719f731c94f20c9c70cecfac11", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("b89d1f5e82f7275e372087799f21566721814029", PAPERCLIP_STOP_CUSTODY_PINS)
        self.assertNotIn("d560a09538a255c779e4a10b14d41a3114bf10f2", PAPERCLIP_STOP_CUSTODY_PINS)
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
