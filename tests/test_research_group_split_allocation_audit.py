from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import sys
import unittest
from copy import deepcopy

from memecoin_experience.group_split_allocation_audit import analyze


LIMITATIONS = [
    "This audits only the explicitly supplied row/group declarations and allocation records; it cannot establish that they describe a real or complete population.",
    "A clean allocation does not establish quota feasibility, temporal ordering, maturity, representativeness, independence, data-access or training qualification.",
    "Duplicate, unknown, missing and leakage counts use the explicit declared rows and assignment records; omitted source rows cannot be inferred.",
]


def _reference(payload: dict) -> dict:
    """Small independent, deliberately direct oracle for valid payloads."""
    groups: dict[str, list[str]] = {}
    known = {row["id"] for row in payload["rows"]}
    for row in payload["rows"]:
        groups.setdefault(row["group_id"], []).append(row["id"])
    known_records: dict[str, list[str]] = {row_id: [] for row_id in known}
    unknown = []
    for index, item in enumerate(payload["assignments"]):
        if item["row_id"] in known:
            known_records[item["row_id"]].append(item["split"])
        else:
            unknown.append({"assignment_index": index, "row_id": item["row_id"], "split": item["split"]})
    missing = sorted(row_id for row_id in known if not known_records[row_id])
    duplicates = sorted(
        ({"row_id": row_id, "assignment_count": len(splits), "extra_assignment_count": len(splits) - 1}
         for row_id, splits in known_records.items() if len(splits) > 1),
        key=lambda item: item["row_id"],
    )
    leakage = []
    for group_id, members in groups.items():
        labels = sorted({label for row_id in members for label in known_records[row_id]})
        if len(labels) > 1:
            leakage.append({"group_id": group_id, "split_labels": labels, "row_ids": sorted(members)})
    leakage.sort(key=lambda item: item["group_id"])
    unknown.sort(key=lambda item: item["assignment_index"])
    assigned = sum(bool(records) for records in known_records.values())
    missing_count = len(missing)
    duplicate_extra = sum(item["extra_assignment_count"] for item in duplicates)
    group_count = len(groups)
    row_summary = {
        "expected_row_count": len(known), "assigned_unique_row_count": assigned,
        "missing_row_count": missing_count, "duplicate_row_id_count": len(duplicates),
        "duplicate_extra_assignment_count": duplicate_extra, "unknown_assignment_count": len(unknown),
        "group_count": group_count,
    }
    leaked_count = len(leakage)
    summary = {
        "status": "findings" if missing_count or duplicates or unknown or leakage else "clean",
        "expected_row_count": len(known), "assigned_unique_row_count": assigned,
        "missing_row_count": missing_count, "duplicate_row_id_count": len(duplicates),
        "duplicate_extra_assignment_count": duplicate_extra, "unknown_assignment_count": len(unknown),
        "group_count": group_count, "leaked_group_count": leaked_count,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return {
        "schema": "brain-research-diagnostic.v1", "kind": "group_split_allocation_audit",
        "input_sha256": hashlib.sha256(canonical).hexdigest(), "summary": summary,
        "details": {
            "missing_row_ids": missing, "duplicate_assignments": duplicates,
            "unknown_assignments": unknown, "group_leakage": leakage,
            "row_assignment_summary": row_summary,
            "group_leakage_summary": {
                "checked_group_count": group_count, "leaked_group_count": leaked_count,
                "affected_row_count": sum(len(item["row_ids"]) for item in leakage),
            },
        },
        "limitations": LIMITATIONS,
    }


def _cli(data: bytes, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    return subprocess.run(
        [sys.executable, "-m", "memecoin_experience.group_split_allocation_audit", *args],
        input=data, capture_output=True, env=env,
    )


class GroupSplitAllocationAuditTests(unittest.TestCase):
    def test_hand_cases_match_independent_reference(self) -> None:
        cases = [
            {"rows": [{"id": "a", "group_id": "g1"}, {"id": "b", "group_id": "g1"}, {"id": "c", "group_id": "g2"}],
             "assignments": [{"row_id": "a", "split": "train"}, {"row_id": "b", "split": "train"}, {"row_id": "c", "split": "holdout"}]},
            {"rows": [{"id": "a", "group_id": "g1"}, {"id": "b", "group_id": "g1"}, {"id": "c", "group_id": "g2"}],
             "assignments": [{"row_id": "a", "split": "train"}, {"row_id": "a", "split": "holdout"}, {"row_id": "c", "split": "validation"}, {"row_id": "ghost", "split": "test"}]},
            {"rows": [], "assignments": []},
            {"rows": [{"id": "a", "group_id": "g"}, {"id": "b", "group_id": "g"}],
             "assignments": [{"row_id": "a", "split": "train"}, {"row_id": "a", "split": "train"}, {"row_id": "b", "split": "train"}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                before = deepcopy(payload)
                self.assertEqual(analyze(payload), _reference(payload))
                self.assertEqual(payload, before)

    def test_multi_assignment_independently_causes_duplicate_and_leakage(self) -> None:
        payload = {"rows": [{"id": "a", "group_id": "g"}, {"id": "b", "group_id": "g"}],
                   "assignments": [{"row_id": "a", "split": "x"}, {"row_id": "a", "split": "y"}, {"row_id": "b", "split": "y"}]}
        result = analyze(payload)
        self.assertEqual(result["details"]["duplicate_assignments"], [{"row_id": "a", "assignment_count": 2, "extra_assignment_count": 1}])
        self.assertEqual(result["details"]["group_leakage"], [{"group_id": "g", "split_labels": ["x", "y"], "row_ids": ["a", "b"]}])

    def test_permutations_preserve_semantics_but_hash_tracks_input(self) -> None:
        payload = {"rows": [{"id": "b", "group_id": "g"}, {"id": "a", "group_id": "g"}],
                   "assignments": [{"row_id": "a", "split": "train"}, {"row_id": "a", "split": "test"}, {"row_id": "b", "split": "test"}]}
        expected = analyze(payload)
        for rows, assignments in itertools.product((payload["rows"], list(reversed(payload["rows"]))),
                                                     (payload["assignments"], list(reversed(payload["assignments"])) )):
            permuted = {"rows": rows, "assignments": assignments}
            actual = analyze(permuted)
            self.assertEqual({k: v for k, v in actual.items() if k != "input_sha256"},
                             {k: v for k, v in expected.items() if k != "input_sha256"})
            self.assertEqual(actual["input_sha256"], hashlib.sha256(json.dumps(permuted, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest())

    def test_validation_boundaries(self) -> None:
        good = {"rows": [{"id": "a", "group_id": "g"}], "assignments": [{"row_id": "a", "split": "custom.label:1"}]}
        invalid = [
            None, [], {}, {**good, "extra": 1}, {"rows": (), "assignments": []},
            {"rows": [], "assignments": ()}, {"rows": [{"id": "a", "group_id": "g", "x": 1}], "assignments": []},
            {"rows": [{"id": True, "group_id": "g"}], "assignments": []},
            {"rows": [{"id": "a", "group_id": None}], "assignments": []},
            {"rows": [{"id": "a", "group_id": "g"}, {"id": "a", "group_id": "h"}], "assignments": []},
            {"rows": [], "assignments": [{"row_id": "a", "split": 2}]},
            {"rows": [], "assignments": [{"row_id": "a", "split": "é"}]},
            {"rows": [{"id": "a" * 65, "group_id": "g"}], "assignments": []},
            {"rows": [], "assignments": [{"row_id": "a", "split": "x", "extra": 1}]},
            {"rows": [{"id": "a", "group_id": "g"}] * 10001, "assignments": []},
            {"rows": [], "assignments": [{"row_id": "a", "split": "x"}] * 20001},
        ]
        for payload in invalid:
            with self.subTest(payload=str(payload)[:100]):
                with self.assertRaisesRegex(ValueError, "^group_split_allocation_audit_invalid_input$"):
                    analyze(payload)

    def test_cli_success_hash_and_errors(self) -> None:
        payload = {"rows": [{"id": "a", "group_id": "g"}], "assignments": [{"row_id": "a", "split": "train"}]}
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        good = _cli(encoded)
        self.assertEqual((good.returncode, good.stderr), (0, b""))
        self.assertEqual(json.loads(good.stdout), _reference(payload))
        expected_stdout = json.dumps(_reference(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
        self.assertEqual(good.stdout, expected_stdout)
        for data in (b'{"rows":[],"rows":[],"assignments":[]}', b'{"rows":[],"assignments":[],"x":NaN}', b"\xff", b"{} {}"):
            bad = _cli(data)
            self.assertEqual((bad.returncode, bad.stdout, bad.stderr),
                             (2, b"", b'{"error":"group_split_allocation_audit_invalid_input"}\n'))
        args = _cli(encoded, "extra")
        self.assertEqual((args.returncode, args.stdout, args.stderr),
                         (2, b"", b'{"error":"group_split_allocation_audit_invalid_input"}\n'))


if __name__ == "__main__":
    unittest.main()
