import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engineering.verifier_manifest as verifier_manifest
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
        self.interpreter = self.root / "python"
        self.interpreter.write_bytes(b"synthetic pinned interpreter\n")
        self.interpreter.chmod(0o755)
        self.interpreter_sha = hashlib.sha256(self.interpreter.read_bytes()).hexdigest()
        self.binding = {
            "schema": "brain-task-execution-binding-v1",
            "controller_release_sha256": RELEASE,
            "integration_policy_sha256": POLICY,
            "verifiers": [
                {
                    "acceptance_id": "synthetic-acceptance",
                    "acceptance_sha256": H,
                    "policy_id": "synthetic-policy-v1",
                    "commands": [
                        ["{interpreter}", "-I", "-S", "-B", "{harness}", "{task_id}", "{checkout}"]
                    ],
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
            "schema": "brain-verifier-manifest-v4",
            "controller_release_sha256": RELEASE,
            "integration_policy_sha256": POLICY,
            "interpreter_path": str(self.interpreter.resolve()),
            "interpreter_sha256": self.interpreter_sha,
            "interpreter_runtime_path": str(self.interpreter.resolve()),
            "interpreter_runtime_cdhash": "d" * 40,
            "verifiers": [
                {
                    **self.binding["verifiers"][0],
                    "harness_path": "acceptance.py",
                }
            ],
        }

    def resolve(self, binding=None, manifest=None, *, configured_interpreter=None):
        with patch(
            "engineering.verifier_manifest.static_code_directory_hash", return_value="d" * 40
        ):
            return resolve_verifier_manifest(
                binding or self.binding,
                manifest or self.manifest,
                trusted_root=self.trusted,
                configured_interpreter=configured_interpreter or self.interpreter,
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolves_exact_binding_and_harness_bytes(self):
        resolved = self.resolve(self.binding, self.manifest)
        self.assertEqual(resolved[0]["harness_path"], "acceptance.py")
        self.assertEqual(resolved[0]["harness_sha256"], self.harness_sha)

    def test_regular_artifact_read_rejects_same_size_change_during_read(self):
        artifact = self.trusted / "changing.py"
        artifact.write_bytes(b"original")
        actual_fdopen = os.fdopen

        class MutatingStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def read(self, size):
                raw = self.stream.read(size)
                artifact.write_bytes(b"mutated!")
                return raw

        with patch(
            "engineering.verifier_manifest.os.fdopen",
            side_effect=lambda *args, **kwargs: MutatingStream(actual_fdopen(*args, **kwargs)),
        ):
            with self.assertRaisesRegex(
                VerifierManifestError, "verifier_harness_changed_while_reading"
            ):
                verifier_manifest._read_regular(
                    artifact,
                    symlink_code="verifier_harness_symlink",
                    unavailable_code="verifier_harness_unavailable",
                    regular_code="verifier_harness_not_regular",
                    too_large_code="verifier_harness_too_large",
                    changed_code="verifier_harness_changed_while_reading",
                    max_bytes=100,
                )

    def test_regular_artifact_read_classifies_stable_oversize(self):
        artifact = self.trusted / "oversized.py"
        artifact.write_bytes(b"x" * 10)
        with self.assertRaisesRegex(VerifierManifestError, "verifier_harness_too_large"):
            verifier_manifest._read_regular(
                artifact,
                symlink_code="verifier_harness_symlink",
                unavailable_code="verifier_harness_unavailable",
                regular_code="verifier_harness_not_regular",
                too_large_code="verifier_harness_too_large",
                changed_code="verifier_harness_changed_while_reading",
                max_bytes=7,
            )

    def test_rejects_release_policy_or_verifier_drift(self):
        for mutate, error in (
            (lambda item: item.update(controller_release_sha256="d" * 64), "binding_mismatch"),
            (lambda item: item.update(integration_policy_sha256="e" * 64), "binding_mismatch"),
            (lambda item: item["verifiers"][0].update(policy_id="untrusted"), "record_mismatch"),
        ):
            candidate = json.loads(json.dumps(self.manifest))
            mutate(candidate)
            with self.subTest(error=error), self.assertRaisesRegex(VerifierManifestError, error):
                self.resolve(self.binding, candidate)

    def test_rejects_untrusted_paths_symlinks_and_artifact_drift(self):
        for path, error in (("../outside.py", "path_invalid"), ("missing.py", "unavailable")):
            candidate = json.loads(json.dumps(self.manifest))
            candidate["verifiers"][0]["harness_path"] = path
            with self.subTest(path=path), self.assertRaisesRegex(VerifierManifestError, error):
                self.resolve(self.binding, candidate)

        outside = self.root / "outside.py"
        outside.write_text("not trusted")
        (self.trusted / "linked.py").symlink_to(outside)
        linked = json.loads(json.dumps(self.manifest))
        linked["verifiers"][0]["harness_path"] = "linked.py"
        with self.assertRaisesRegex(VerifierManifestError, "symlink"):
            self.resolve(self.binding, linked)

        (self.trusted / "acceptance.py").write_bytes(b"changed harness\n")
        with self.assertRaisesRegex(VerifierManifestError, "hash_mismatch"):
            self.resolve(self.binding, self.manifest)

    def test_interpreter_identity_requires_canonical_executable_bytes(self):
        for field, value, error in (
            (
                "interpreter_path",
                str((self.root / "missing").resolve()),
                "interpreter_unavailable",
            ),
            ("interpreter_sha256", "0" * 64, "interpreter_hash_mismatch"),
            (
                "interpreter_path",
                str(self.root) + "/./python",
                "interpreter_path_not_canonical",
            ),
        ):
            candidate = json.loads(json.dumps(self.manifest))
            candidate[field] = value
            with (
                self.subTest(field=field, value=value),
                self.assertRaisesRegex(VerifierManifestError, error),
            ):
                self.resolve(self.binding, candidate)

        non_executable = self.root / "not-executable"
        non_executable.write_bytes(b"file")
        non_executable.chmod(0o644)
        candidate = json.loads(json.dumps(self.manifest))
        candidate["interpreter_path"] = str(non_executable.resolve())
        candidate["interpreter_sha256"] = hashlib.sha256(non_executable.read_bytes()).hexdigest()
        with self.assertRaisesRegex(VerifierManifestError, "interpreter_not_executable"):
            self.resolve(self.binding, candidate, configured_interpreter=non_executable.resolve())

        symlink = self.root / "python-link"
        symlink.symlink_to(self.interpreter)
        candidate = json.loads(json.dumps(self.manifest))
        candidate["interpreter_path"] = str(symlink)
        with self.assertRaisesRegex(VerifierManifestError, "interpreter_path_not_canonical"):
            self.resolve(self.binding, candidate, configured_interpreter=symlink)

    def test_interpreter_identity_rejects_mutation_during_descriptor_read(self):
        original_read = os.read
        changed = False

        def read_then_mutate(fd, size):
            nonlocal changed
            data = original_read(fd, size)
            if data and not changed:
                changed = True
                self.interpreter.write_bytes(b"X" * len(data))
            return data

        with patch("engineering.verifier_manifest.os.read", side_effect=read_then_mutate):
            with self.assertRaisesRegex(VerifierManifestError, "interpreter_changed_while_reading"):
                self.resolve(self.binding, self.manifest)

    def test_manifest_command_must_reference_harness_in_result_command(self):
        candidate = json.loads(json.dumps(self.manifest))
        binding = json.loads(json.dumps(self.binding))
        commands = [
            ["{interpreter}", "-I", "-S", "-B", "{task_id}"],
            ["{interpreter}", "-I", "-S", "-B", "{harness}"],
        ]
        candidate["verifiers"][0]["commands"] = commands
        binding["verifiers"][0]["commands"] = commands
        unsigned = {key: value for key, value in binding.items() if key != "binding_sha256"}
        binding["binding_sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.assertRaisesRegex(VerifierManifestError, "harness_command_unbound"):
            self.resolve(binding, candidate)

    def test_manifest_result_command_must_execute_harness_as_script(self):
        candidate = json.loads(json.dumps(self.manifest))
        binding = json.loads(json.dumps(self.binding))
        commands = [["{interpreter}", "-I", "-S", "-B", "-c", "print('{}')", "{harness}"]]
        candidate["verifiers"][0]["commands"] = commands
        binding["verifiers"][0]["commands"] = commands
        unsigned = {key: value for key, value in binding.items() if key != "binding_sha256"}
        binding["binding_sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.assertRaisesRegex(VerifierManifestError, "harness_command_unbound"):
            self.resolve(binding, candidate)

    def test_v5_binds_every_command_identity_and_result_contract(self):
        native = self.root / "native-check"
        native.write_bytes(b"synthetic second verifier executable\n")
        native.chmod(0o755)
        native_sha = hashlib.sha256(native.read_bytes()).hexdigest()
        second_script = self.trusted / "second.py"
        second_script.write_bytes(b"synthetic second trusted script\n")
        second_script_sha = hashlib.sha256(second_script.read_bytes()).hexdigest()
        commands = [
            ["{interpreter}", "-I", "-S", "-B", "{harness}", "{task_id}", "{checkout}"],
            [
                str(self.interpreter.resolve()),
                "-I",
                "-S",
                "-B",
                str(second_script.resolve()),
                "--check",
            ],
            [str(native.resolve()), "--check", "{checkout}"],
        ]
        binding = json.loads(json.dumps(self.binding))
        binding["verifiers"][0]["commands"] = commands
        reseal(binding)
        manifest = json.loads(json.dumps(self.manifest))
        manifest["schema"] = "brain-verifier-manifest-v5"
        manifest["verifiers"][0]["commands"] = commands
        manifest["verifiers"][0]["command_bindings"] = [
            {
                "executable_path": str(self.interpreter.resolve()),
                "executable_sha256": self.interpreter_sha,
                "runtime_path": str(self.interpreter.resolve()),
                "runtime_cdhash": "d" * 40,
                "script_path": "acceptance.py",
                "script_sha256": self.harness_sha,
                "result": {
                    "kind": "acceptance_json",
                    "acceptance_id": "synthetic-acceptance",
                    "result_schema": "synthetic-result-v1",
                    "case_field": "case",
                    "passed_field": "passed",
                },
            },
            {
                "executable_path": str(self.interpreter.resolve()),
                "executable_sha256": self.interpreter_sha,
                "runtime_path": str(self.interpreter.resolve()),
                "runtime_cdhash": "d" * 40,
                "script_path": "second.py",
                "script_sha256": second_script_sha,
                "result": {
                    "kind": "acceptance_json",
                    "acceptance_id": "second-acceptance",
                    "result_schema": "second-result-v1",
                    "case_field": "case",
                    "passed_field": "passed",
                },
            },
            {
                "executable_path": str(native.resolve()),
                "executable_sha256": native_sha,
                "runtime_path": str(native.resolve()),
                "runtime_cdhash": "d" * 40,
                "script_path": None,
                "script_sha256": None,
                "result": {"kind": "exit_zero"},
            },
        ]
        resolved = self.resolve(binding, manifest)
        self.assertEqual(len(resolved[0]["command_bindings"]), 3)
        self.assertEqual(resolved[0]["command_bindings"][1]["script_path"], "second.py")
        self.assertEqual(
            resolved[0]["command_bindings"][2]["executable_path"], str(native.resolve())
        )
        self.assertEqual(resolved[0]["command_bindings"][2]["result"], {"kind": "exit_zero"})

        for mutate, error in (
            (lambda item: item["verifiers"][0]["command_bindings"].pop(), "bindings_mismatch"),
            (
                lambda item: item["verifiers"][0]["command_bindings"][1].update(
                    script_sha256="0" * 64
                ),
                "hash_mismatch",
            ),
            (
                lambda item: item["verifiers"][0]["command_bindings"][2].update(
                    result={"kind": "exit_zero", "extra": True}
                ),
                "result_invalid",
            ),
            (
                lambda item: item["verifiers"][0]["command_bindings"][2].update(
                    result={"kind": []}
                ),
                "result_invalid",
            ),
            (
                lambda item: item["verifiers"][0]["command_bindings"][2].update(
                    result={"kind": {}}
                ),
                "result_invalid",
            ),
        ):
            candidate = json.loads(json.dumps(manifest))
            mutate(candidate)
            with self.subTest(error=error), self.assertRaisesRegex(VerifierManifestError, error):
                self.resolve(binding, candidate)

        second_script.write_bytes(b"changed trusted script\n")
        with self.assertRaisesRegex(VerifierManifestError, "script_hash_mismatch"):
            self.resolve(binding, manifest)

    def test_v4_multicommand_manifest_fails_closed_without_per_command_pins(self):
        commands = [
            ["{interpreter}", "-I", "-S", "-B", "{harness}"],
            ["/usr/bin/true"],
        ]
        binding = json.loads(json.dumps(self.binding))
        binding["verifiers"][0]["commands"] = commands
        reseal(binding)
        manifest = json.loads(json.dumps(self.manifest))
        manifest["verifiers"][0]["commands"] = commands
        with self.assertRaisesRegex(VerifierManifestError, "command_identity_missing"):
            self.resolve(binding, manifest)

    def test_v5_rejects_empty_later_command_as_typed_manifest_error(self):
        commands = [
            ["{interpreter}", "-I", "-S", "-B", "{harness}"],
            [],
        ]
        binding = json.loads(json.dumps(self.binding))
        binding["verifiers"][0]["commands"] = commands
        reseal(binding)
        manifest = json.loads(json.dumps(self.manifest))
        manifest["schema"] = "brain-verifier-manifest-v5"
        manifest["verifiers"][0]["commands"] = commands
        manifest["verifiers"][0]["command_bindings"] = [None, None]
        with self.assertRaisesRegex(VerifierManifestError, "harness_command_unbound"):
            self.resolve(binding, manifest)

    def test_manifest_v3_requires_isolated_no_site_startup(self):
        for prefix in (
            ["{interpreter}", "{harness}"],
            ["{interpreter}", "-S", "-B", "{harness}"],
            ["{interpreter}", "-I", "-B", "-S", "{harness}"],
            ["{interpreter}", "-I", "-S", "-B", "-c", "pass", "{harness}"],
        ):
            candidate = json.loads(json.dumps(self.manifest))
            binding = json.loads(json.dumps(self.binding))
            commands = [prefix + ["{task_id}", "{checkout}"]]
            candidate["verifiers"][0]["commands"] = commands
            binding["verifiers"][0]["commands"] = commands
            reseal(binding)
            with (
                self.subTest(prefix=prefix),
                self.assertRaisesRegex(VerifierManifestError, "harness_command_unbound"),
            ):
                self.resolve(binding, candidate)

    def test_manifest_v3_is_not_accepted_as_v4(self):
        candidate = json.loads(json.dumps(self.manifest))
        candidate["schema"] = "brain-verifier-manifest-v3"
        with self.assertRaisesRegex(VerifierManifestError, "binding_mismatch"):
            self.resolve(self.binding, candidate)

    def test_manifest_requires_runtime_code_directory_hash(self):
        for value in (None, 1, "d" * 39, "D" * 40, "x" * 40):
            candidate = json.loads(json.dumps(self.manifest))
            candidate["interpreter_runtime_cdhash"] = value
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(VerifierManifestError, "interpreter_runtime_cdhash_invalid"),
            ):
                self.resolve(self.binding, candidate)

    def test_manifest_runtime_path_must_be_canonical_and_match_code_hash(self):
        for path, error in (
            ("relative/runtime", "runtime_path_invalid"),
            (str(self.root / "missing"), "runtime_identity_invalid"),
        ):
            candidate = json.loads(json.dumps(self.manifest))
            candidate["interpreter_runtime_path"] = path
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(VerifierManifestError, error),
            ):
                self.resolve(self.binding, candidate)

        candidate = json.loads(json.dumps(self.manifest))
        candidate["interpreter_runtime_cdhash"] = "e" * 40
        with self.assertRaisesRegex(VerifierManifestError, "runtime_cdhash_mismatch"):
            self.resolve(self.binding, candidate)

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
                self.resolve(binding, self.manifest)

        invalid = json.loads(json.dumps(self.binding))
        invalid["binding_sha256"] = "0" * 64
        with self.assertRaisesRegex(VerifierManifestError, "binding_invalid"):
            self.resolve(invalid, self.manifest)

    def test_loader_rejects_deep_noncanonical_and_oversized_json(self):
        path = self.root / "manifest.json"
        for raw in (
            b' {"schema": "brain-verifier-manifest-v4"} ',
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
        resolved = self.resolve(self.binding, self.manifest)
        resolved[0]["commands"][0][0] = "/mutated"
        resolved[0]["env"]["LANG"] = "mutated"
        self.assertEqual(self.binding["verifiers"][0]["commands"][0][0], "{interpreter}")
        self.assertEqual(self.manifest["verifiers"][0]["env"]["LANG"], "C")


if __name__ == "__main__":
    unittest.main()
