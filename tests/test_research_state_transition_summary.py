"""Focused offline checks for categorical transitions within declared episodes."""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import unittest
from collections import Counter
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
COMMAND = [sys.executable, "-m", "memecoin_experience.state_transition_summary"]


def fixture():
    return {
        "states": ["C", "B", "A"], "max_gap_ns": 5,
        "episodes": [
            {"id": "e1", "observations": [
                {"id": "a", "event_ns": 0, "state": "A"},
                {"id": "b", "event_ns": 3, "state": "B"},
                {"id": "c", "event_ns": 4, "state": None},
                {"id": "d", "event_ns": 5, "state": "A"},
                {"id": "e", "event_ns": 20, "state": "A"},
                {"id": "f", "event_ns": 22, "state": "A"},
                {"id": "g", "event_ns": 25, "state": "B"},
                {"id": "h", "event_ns": 27, "state": "A"},
            ]},
            {"id": "e2", "observations": [
                {"id": "i", "event_ns": 28, "state": "B"},
                {"id": "j", "event_ns": 30, "state": "B"},
            ]},
            {"id": "e3", "observations": []},
            {"id": "e4", "observations": [
                {"id": "k", "event_ns": 0, "state": None},
                {"id": "l", "event_ns": 1, "state": None},
            ]},
        ],
    }


def oracle(payload):
    """Independent neighbor enumeration; intentionally no production helpers."""
    states = sorted(payload["states"])
    pair_counts = Counter()
    outgoing = Counter()
    reasons = Counter()
    excluded = []
    candidates = included = missing = observations = 0
    for episode in payload["episodes"]:
        rows = episode["observations"]
        observations += len(rows)
        missing += sum(row["state"] is None for row in rows)
        candidates += max(0, len(rows) - 1)
        for index in range(len(rows) - 1):
            left, right = rows[index], rows[index + 1]
            gap = right["event_ns"] - left["event_ns"]
            if left["state"] is None and right["state"] is None:
                reason = "missing_both"
            elif left["state"] is None:
                reason = "missing_from"
            elif right["state"] is None:
                reason = "missing_to"
            elif gap > payload["max_gap_ns"]:
                reason = "gap_exceeded"
            else:
                pair_counts[left["state"], right["state"]] += 1
                outgoing[left["state"]] += 1
                included += 1
                continue
            reasons[reason] += 1
            excluded.append((episode["id"], index, left["id"], right["id"], gap, reason))
    reason_counts = {name: reasons[name] for name in
                     ("missing_both", "missing_from", "missing_to", "gap_exceeded")}
    transitions = []
    for origin in states:
        for destination in states:
            count = pair_counts[origin, destination]
            denominator = outgoing[origin]
            ratio = Fraction(count, denominator) if denominator else None
            transitions.append((origin, destination, count, denominator, ratio))
    return states, observations, missing, candidates, included, reason_counts, transitions, sorted(excluded)


class StateTransitionSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from memecoin_experience.state_transition_summary import analyze
        cls.analyze = staticmethod(analyze)

    def test_hand_fixture_and_fraction_oracle(self):
        payload = fixture()
        before = copy.deepcopy(payload)
        result = self.analyze(payload)
        states, observations, missing, candidates, included, reasons, matrix, excluded = oracle(payload)
        self.assertEqual(payload, before)
        self.assertEqual(result["states"], states)
        summary = result["summary"]
        self.assertEqual((summary["episode_count"], observations, missing, candidates, included),
                         (4, 12, 3, 9, 5))
        self.assertEqual(summary["excluded_reason_counts"], reasons)
        self.assertEqual(summary["excluded_edge_count"], 4)
        actual_excluded = [(row["episode_id"], row["edge_index"], row["from_id"],
                            row["to_id"], row["gap_ns"], row["reason"])
                           for row in result["excluded_edges"]]
        self.assertEqual(actual_excluded, excluded)
        for row, expected in zip(result["transitions"], matrix):
            origin, destination, count, denominator, fraction = expected
            self.assertEqual((row["from_state"], row["to_state"], row["count"], row["denominator"]),
                             (origin, destination, count, denominator))
            if fraction is None:
                self.assertIsNone(row["proportion"])
            else:
                self.assertTrue(math.isclose(row["proportion"], float(fraction),
                                             rel_tol=1e-12, abs_tol=1e-12))
        self.assertEqual(result["transitions"][0]["count"], 1)  # A -> A
        self.assertEqual(sum(row["count"] for row in result["transitions"]), included)

    def test_permutation_empty_and_boundaries(self):
        payload = fixture()
        expected = self.analyze(payload)
        payload["states"].reverse()
        payload["episodes"].reverse()
        self.assertEqual(self.analyze(payload), expected)
        added = copy.deepcopy(payload)
        added["episodes"].append({"id": "empty", "observations": []})
        changed = self.analyze(added)
        self.assertEqual(changed["summary"]["episode_count"], 5)
        for key in expected:
            if key != "summary":
                self.assertEqual(changed[key], expected[key])
        self.assertEqual({"states": ["A"], "episodes": [], "max_gap_ns": 0}["episodes"], [])
        only = {"states": ["A", "unused"], "episodes": [{"id": "one", "observations": [
            {"id": "x", "event_ns": 0, "state": "A"},
            {"id": "y", "event_ns": 1, "state": "A"},
        ]}], "max_gap_ns": 1}
        out = self.analyze(only)
        self.assertEqual(out["transitions"][0]["count"], 1)
        self.assertIsNone(out["transitions"][2]["proportion"])
        only["max_gap_ns"] = 0
        self.assertEqual(self.analyze(only)["summary"]["included_edge_count"], 0)

    def test_small_fixed_oracle_table(self):
        for case in range(24):
            states = ["A", "B", "C"][:1 + case % 3]
            rows = []
            size = case % 7
            for index in range(size):
                value = None if (case + index) % 4 == 0 else states[(case + index) % len(states)]
                rows.append({"id": f"r{index}", "event_ns": index * (1 + case % 3), "state": value})
            payload = {"states": states, "max_gap_ns": case % 5,
                       "episodes": [{"id": "ep", "observations": rows}]}
            result = self.analyze(payload)
            expected = oracle(payload)
            self.assertEqual(result["summary"]["candidate_edge_count"], expected[3])
            self.assertEqual(result["summary"]["included_edge_count"], expected[4])
            for row, item in zip(result["transitions"], expected[6]):
                self.assertEqual((row["from_state"], row["to_state"], row["count"], row["denominator"]), item[:4])
                if item[4] is not None:
                    self.assertTrue(math.isclose(row["proportion"], float(item[4]), rel_tol=1e-12, abs_tol=1e-12))

    def test_validation_categories_and_cross_episode_ids(self):
        base = fixture()
        duplicate_elsewhere = copy.deepcopy(base)
        duplicate_elsewhere["episodes"][1]["observations"][0]["id"] = "a"
        self.analyze(duplicate_elsewhere)
        mutations = []
        for key in ("extra",):
            bad = copy.deepcopy(base); bad[key] = 1; mutations.append(bad)
        bad = copy.deepcopy(base); bad["states"] = ["A", "A"]; mutations.append(bad)
        bad = copy.deepcopy(base); bad["max_gap_ns"] = True; mutations.append(bad)
        bad = copy.deepcopy(base); bad["max_gap_ns"] = 10**400; mutations.append(bad)
        bad = copy.deepcopy(base); bad["episodes"][0]["observations"][1]["event_ns"] = 0; mutations.append(bad)
        bad = copy.deepcopy(base); bad["episodes"][0]["observations"][0]["event_ns"] = True; mutations.append(bad)
        bad = copy.deepcopy(base); bad["episodes"][0]["observations"][0]["state"] = "unknown"; mutations.append(bad)
        bad = copy.deepcopy(base); bad["episodes"][0]["observations"][0]["extra"] = 2; mutations.append(bad)
        bad = copy.deepcopy(base); bad["episodes"][0]["observations"][1]["id"] = "a"; mutations.append(bad)
        bad = copy.deepcopy(base); bad["episodes"][1]["id"] = "e1"; mutations.append(bad)
        for payload in mutations:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, r"^state_transition_summary_"):
                    self.analyze(payload)

    def test_accepted_upper_bounds(self):
        states = [f"S{i}" for i in range(32)]
        episodes = []
        for episode_index in range(1000):
            observations = [{"id": f"r{index}", "event_ns": index,
                             "state": states[index % len(states)]}
                            for index in range(10)]
            episodes.append({"id": f"e{episode_index}", "observations": observations})
        result = self.analyze({"states": states, "episodes": episodes, "max_gap_ns": 1})
        self.assertEqual(result["summary"]["observation_count"], 10000)
        self.assertEqual(len(result["transitions"]), 1024)

    def test_actual_cli_success_and_strict_failures(self):
        payload = fixture()
        good = subprocess.run(COMMAND, input=json.dumps(payload).encode(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=ENV, cwd=ROOT)
        self.assertEqual(good.returncode, 0)
        self.assertEqual(good.stderr, b"")
        self.assertEqual(json.loads(good.stdout), self.analyze(payload))
        invalid_inputs = [
            b'{"states":[],"states":[],"episodes":[],"max_gap_ns":0}',
            b'{"states":["A"],"episodes":[{"id":"e","id":"f","observations":[]}],"max_gap_ns":0}',
            b'{"x":NaN}', b"{", b"{} {}", b"\xff", b" " * (8 * 1024 * 1024 + 1),
        ]
        for raw in invalid_inputs:
            proc = subprocess.run(COMMAND, input=raw, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=ENV, cwd=ROOT)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, b"")
            self.assertEqual(len(proc.stderr.splitlines()), 1)
            self.assertTrue(proc.stderr.startswith(b"state_transition_summary_"))
        args = subprocess.run(COMMAND + ["--help"], input=b"{}", stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=ENV, cwd=ROOT)
        self.assertEqual((args.returncode, args.stdout), (2, b""))


if __name__ == "__main__":
    unittest.main()
