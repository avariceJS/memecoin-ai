import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from engineering.verifier_manifest import (
    VerifierManifestError,
    load_verifier_manifest,
    resolve_verifier_manifest,
)

H = "a" * 64
RELEASE = "b" * 64
POLICY = "c" * 64


def reseal(binding):
    binding.pop("binding_sha256", None)
    binding["binding_sha256"] = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return binding


class VerifierManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.trusted = self.root / "trusted"
        self.trusted.mkdir()
        (self.trusted / "acceptance.py").write_bytes(b"synthetic immutable harness\n")
        self.harness_sha = hashlib.sha256((self.trusted / "acceptance.py").read_bytes()).hexdigest()
        self.binding = {
            "schema": "brain-task-execution-binding-v1",
            "controller_release_sha256": RELEASE,
            "integration_policy_sha256": POLICY,
            "verifiers": [
                {
                    "acceptance_id": "synthetic-acceptance",
                    "acceptance_sha256": H,
                    "policy_id": "synthetic-policy-v1",
                    "commands": [["/trusted/python", "{checkout}/acceptance.py", "{task_id}"]],
                    "env": {"LANG": "C"},
                    "harness_sha256": self.harness_sha,
                    "result_schema": "synthetic-result-v1",
                    "case_field": "case",
                    "passed_field": "passed",
                }
            ],
        }
        self.binding["binding_sha256"] = hashlib.sha256(
            json.dumps(self.binding, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.manifest = {
            "schema": "brain-verifier-manifest-v1",
            "controller_release_sha256": RELEASE,
            "integration_policy_sha256": POLICY,
            "verifiers": [
                {
                    **self.binding["verifiers"][0],
                    "harness_path": "acceptance.py",
                }
            ],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolves_exact_binding_and_harness_bytes(self):
        resolved = resolve_verifier_manifest(self.binding, self.manifest, trusted_root=self.trusted)
        self.assertEqual(resolved[0]["harness_path"], "acceptance.py")
        self.assertEqual(resolved[0]["harness_sha256"], self.harness_sha)

    def test_rejects_release_policy_or_verifier_drift(self):
        for mutate, error in (
            (lambda item: item.update(controller_release_sha256="d" * 64), "binding_mismatch"),
            (lambda item: item.update(integration_policy_sha256="e" * 64), "binding_mismatch"),
            (lambda item: item["verifiers"][0].update(policy_id="untrusted"), "record_mismatch"),
        ):
            candidate = json.loads(json.dumps(self.manifest))
            mutate(candidate)
            with self.subTest(error=error), self.assertRaisesRegex(VerifierManifestError, error):
                resolve_verifier_manifest(self.binding, candidate, trusted_root=self.trusted)

    def test_rejects_untrusted_paths_symlinks_and_artifact_drift(self):
        for path, error in (("../outside.py", "path_invalid"), ("missing.py", "unavailable")):
            candidate = json.loads(json.dumps(self.manifest))
            candidate["verifiers"][0]["harness_path"] = path
            with self.subTest(path=path), self.assertRaisesRegex(VerifierManifestError, error):
                resolve_verifier_manifest(self.binding, candidate, trusted_root=self.trusted)

        outside = self.root / "outside.py"
        outside.write_text("not trusted")
        (self.trusted / "linked.py").symlink_to(outside)
        linked = json.loads(json.dumps(self.manifest))
        linked["verifiers"][0]["harness_path"] = "linked.py"
        with self.assertRaisesRegex(VerifierManifestError, "symlink"):
            resolve_verifier_manifest(self.binding, linked, trusted_root=self.trusted)

        (self.trusted / "acceptance.py").write_bytes(b"changed harness\n")
        with self.assertRaisesRegex(VerifierManifestError, "hash_mismatch"):
            resolve_verifier_manifest(self.binding, self.manifest, trusted_root=self.trusted)

    def test_load_requires_exact_canonical_manifest_hash(self):
        path = self.root / "manifest.json"
        raw = json.dumps(self.manifest, sort_keys=True, separators=(",", ":")).encode()
        path.write_bytes(raw)
        self.assertEqual(
            load_verifier_manifest(path, expected_sha256=hashlib.sha256(raw).hexdigest()),
            self.manifest,
        )
        with self.assertRaisesRegex(VerifierManifestError, "hash_mismatch"):
            load_verifier_manifest(path, expected_sha256="f" * 64)
        path.unlink()
        path.symlink_to(self.root / "elsewhere.json")
        with self.assertRaisesRegex(VerifierManifestError, "symlink"):
            load_verifier_manifest(path, expected_sha256="f" * 64)

    def test_binding_requires_both_valid_release_and_policy_hashes(self):
        for field, value in (
            ("controller_release_sha256", None),
            ("integration_policy_sha256", "x"),
        ):
            binding = json.loads(json.dumps(self.binding))
            binding[field] = value
            reseal(binding)
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(VerifierManifestError, "binding_invalid"),
            ):
                resolve_verifier_manifest(binding, self.manifest, trusted_root=self.trusted)

        invalid = json.loads(json.dumps(self.binding))
        invalid["binding_sha256"] = "0" * 64
        with self.assertRaisesRegex(VerifierManifestError, "binding_invalid"):
            resolve_verifier_manifest(invalid, self.manifest, trusted_root=self.trusted)

    def test_loader_rejects_deep_noncanonical_and_oversized_json(self):
        path = self.root / "manifest.json"
        for raw in (
            b' {"schema": "brain-verifier-manifest-v1"} ',
            b"[" * 1100 + b"0" + b"]" * 1100,
            b"{" + b'"x":' + b"1" * 5000 + b"}",
        ):
            path.write_bytes(raw)
            with self.subTest(size=len(raw)), self.assertRaises(VerifierManifestError):
                load_verifier_manifest(path, expected_sha256=hashlib.sha256(raw).hexdigest())

        path.write_bytes(b"x" * 1_000_001)
        with self.assertRaisesRegex(VerifierManifestError, "too_large"):
            load_verifier_manifest(
                path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
            )

    def test_loader_rejects_non_regular_files_without_blocking(self):
        path = self.root / "manifest.fifo"
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO creation is unavailable")
        os.mkfifo(path)
        with self.assertRaisesRegex(VerifierManifestError, "not_regular"):
            load_verifier_manifest(path, expected_sha256="f" * 64)

    def test_resolved_metadata_is_detached_from_binding_and_manifest(self):
        resolved = resolve_verifier_manifest(self.binding, self.manifest, trusted_root=self.trusted)
        resolved[0]["commands"][0][0] = "/mutated"
        resolved[0]["env"]["LANG"] = "mutated"
        self.assertEqual(self.binding["verifiers"][0]["commands"][0][0], "/trusted/python")
        self.assertEqual(self.manifest["verifiers"][0]["env"]["LANG"], "C")


if __name__ == "__main__":
    unittest.main()
