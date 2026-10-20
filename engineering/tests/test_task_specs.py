"""Focused security and immutability checks for task-spec validation."""

import copy
import unittest

from engineering.task_specs import validate_task_spec

H = "a" * 64
BASE = "b" * 40
SPEC = {
    "schema": "brain-task-spec-v1",
    "id": "EXP-010",
    "objective_id": "offline-brain-engineering",
    "team": "Experience",
    "title": "Synthetic schema validation",
    "brief": "Validate synthetic input.",
    "base_sha": BASE,
    "scope": ["src/memecoin_experience/schema.py", "tests/test_schema.py"],
    "dependencies": [],
    "contracts": {"schema-validation": {"version": 2, "proposal_sha256": H}},
    "risk": "low",
    "capabilities": ["offline_code", "synthetic_tests"],
    "resources": {"max_attempts": 3, "max_turn_seconds": 600, "estimated_wpu": 1},
    "acceptance": [{"id": "schema-shape-v2", "sha256": H}],
    "evidence_refs": ["synthetic-fixture:malformed-shape"],
}
ENVELOPE = {
    "schema": "brain-task-envelope-v1",
    "objective_id": "offline-brain-engineering",
    "teams": ["Experience", "Platform"],
    "allowed_paths": SPEC["scope"] + ["tools/agent_context.py"],
    "capabilities": ["offline_code", "synthetic_tests"],
    "max_attempts": 3,
    "max_turn_seconds": 1200,
    "allowed_contracts": {"schema-validation": {"2": H}},
    "base_sha": BASE,
    "risk_levels": ["low"],
}
REGISTRY = {
    "schema-shape-v2": {
        "sha256": H,
        "objective_id": "offline-brain-engineering",
        "scope": SPEC["scope"],
        "contracts": SPEC["contracts"],
        "independent": True,
        "frozen": True,
        "risk": "low",
    }
}


def invoke(spec=None, envelope=None, registry=None, tasks=None):
    return validate_task_spec(
        copy.deepcopy(SPEC if spec is None else spec),
        envelope=copy.deepcopy(ENVELOPE if envelope is None else envelope),
        acceptance_registry=copy.deepcopy(REGISTRY if registry is None else registry),
        existing_tasks=copy.deepcopy([] if tasks is None else tasks),
    )


class TaskSpecTests(unittest.TestCase):
    def test_valid_copy_is_isolated(self):
        original = copy.deepcopy(SPEC)
        result = invoke(original)
        result["scope"].append("extra.py")
        self.assertEqual(original, SPEC)
        self.assertEqual(len(original["scope"]), 2)

    def test_rejects_extra_fields_and_json_nonfinite(self):
        for update in (
            {"commands": ["run anything"]},
            {"resources": {**SPEC["resources"], "estimated_wpu": float("nan")}},
        ):
            proposal = {**SPEC, **update}
            with self.subTest(update=update), self.assertRaises(ValueError):
                invoke(proposal)

    def test_rejects_unsafe_or_unapproved_paths(self):
        for path in (
            "../escape.py",
            "/tmp/escape.py",
            "src/../evil.py",
            r"src\evil.py",
            ".git/config",
            ".codex/config.toml",
            "engineering/supervisor.py",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                invoke({**SPEC, "scope": [path]})

    def test_rejects_untrusted_acceptance_and_resource_escalation(self):
        registry = copy.deepcopy(REGISTRY)
        registry["schema-shape-v2"]["independent"] = False
        with self.assertRaises(ValueError):
            invoke(registry=registry)
        with self.assertRaises(ValueError):
            invoke({**SPEC, "resources": {**SPEC["resources"], "max_attempts": True}})
        with self.assertRaises(ValueError):
            invoke({**SPEC, "resources": {**SPEC["resources"], "max_turn_seconds": 1201}})

    def test_rejects_oversized_integers_with_stable_code(self):
        cases = (
            {"resources": {**SPEC["resources"], "estimated_wpu": 10**400}},
            {"contracts": {"schema-validation": {"version": 10**5000, "proposal_sha256": H}}},
        )
        for update in cases:
            with self.subTest(update=update.keys()), self.assertRaisesRegex(
                ValueError, "^invalid_json_value$"
            ):
                invoke({**SPEC, **update})

    def test_envelope_containers_are_checked_before_membership(self):
        for key, value in (("teams", None), ("risk_levels", 2)):
            envelope = {**ENVELOPE, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(
                ValueError, "^invalid_envelope$"
            ):
                invoke(envelope=envelope)

    def test_existing_dependency_lists_and_targets_are_validated(self):
        good = {
            "id": "CTX-001",
            "state": "pending",
            "scope": [],
            "dependencies": [],
        }
        for value in ("CTX-002", {"target": "CTX-002"}, None):
            with self.subTest(dependencies=value), self.assertRaisesRegex(
                ValueError, "^invalid_existing_tasks$"
            ):
                invoke(tasks=[{**good, "dependencies": value}])
        with self.assertRaisesRegex(ValueError, "^unknown_dependency$"):
            invoke(tasks=[{**good, "dependencies": ["MISSING-001"]}])

    def test_existing_states_and_scopes_are_validated(self):
        overlap = {
            "id": "CTX-001",
            "state": "RUNNING",
            "scope": SPEC["scope"][:1],
            "dependencies": [],
        }
        with self.assertRaisesRegex(ValueError, "^invalid_existing_tasks$"):
            invoke(tasks=[overlap])
        malformed = {
            "id": "CTX-001",
            "state": "pending",
            "scope": [None],
            "dependencies": [],
        }
        with self.assertRaisesRegex(ValueError, "^invalid_scope$"):
            invoke(tasks=[malformed])
        duplicate_scope = {
            "id": "CTX-001",
            "state": "pending",
            "scope": ["src/file.py", "src/file.py"],
            "dependencies": [],
        }
        with self.assertRaisesRegex(ValueError, "^invalid_existing_tasks$"):
            invoke(tasks=[duplicate_scope])

    def test_long_valid_dependency_chain_is_iterative(self):
        tasks = []
        for number in range(1, 1001):
            tasks.append(
                {
                    "id": f"CTX-{number:03d}",
                    "state": "pending",
                    "scope": [],
                    "dependencies": [] if number == 1 else [f"CTX-{number - 1:03d}"],
                }
            )
        proposal = {**SPEC, "dependencies": ["CTX-1000"]}
        self.assertEqual(invoke(proposal, tasks=tasks), proposal)

    def test_registry_contract_versions_reject_bool_and_float(self):
        for version in (True, 2.0):
            registry = copy.deepcopy(REGISTRY)
            registry["schema-shape-v2"]["contracts"]["schema-validation"][
                "version"
            ] = version
            with self.subTest(version=version), self.assertRaisesRegex(
                ValueError, "^untrusted_acceptance$"
            ):
                invoke(registry=registry)

    def test_dependencies_and_owned_scope(self):
        dependency = {
            "id": "CTX-001",
            "state": "running",
            "scope": ["tools/agent_context.py"],
            "dependencies": [],
        }
        proposal = {**SPEC, "dependencies": ["CTX-001"]}
        self.assertEqual(invoke(proposal, tasks=[dependency]), proposal)
        with self.assertRaises(ValueError):
            invoke(proposal, tasks=[{**dependency, "scope": SPEC["scope"][:1]}])
        with self.assertRaises(ValueError):
            invoke({**SPEC, "dependencies": ["MISSING-001"]}, tasks=[])
        cyclic = {"id": "CTX-001", "state": "pending", "scope": [], "dependencies": [SPEC["id"]]}
        with self.assertRaises(ValueError):
            invoke(proposal, tasks=[cyclic])
        with self.assertRaises(ValueError):
            invoke(tasks=[{**dependency, "id": SPEC["id"]}])


if __name__ == "__main__":
    unittest.main()
