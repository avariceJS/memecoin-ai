from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import unittest
from copy import deepcopy

from memecoin_experience.timestamp_order_audit import analyze


def _hand_input() -> dict:
    return {
        "events": [
            {"id": "a", "lo_ns": 0, "hi_ns": 2},
            {"id": "b", "lo_ns": 2, "hi_ns": 4},
            {"id": "c", "lo_ns": 5, "hi_ns": 5},
        ],
        "pairs": [
            {"before": "a", "after": "b"},
            {"before": "a", "after": "c"},
            {"before": "c", "after": "a"},
        ],
    }


def _expected_hand_output() -> dict:
    return {
        "schema": "timestamp-order-audit-v1",
        "summary": {
            "event_count": 3,
            "pair_count": 3,
            "definite_before_count": 1,
            "definite_after_count": 1,
            "indeterminate_count": 1,
            "contradiction_count": 1,
        },
        "pairs": [
            {"before": "a", "after": "b", "order": "indeterminate", "declared_order_status": "unresolved"},
            {"before": "a", "after": "c", "order": "definite_before", "declared_order_status": "consistent"},
            {"before": "c", "after": "a", "order": "definite_after", "declared_order_status": "contradiction"},
        ],
        "limitations": [
            "Only caller-declared intervals and selected pairs are analyzed.",
            "Closed event-time intervals do not establish clock accuracy, availability, receipt time, or causality.",
            "Indeterminate order is unresolved, not a contradiction; no global consistency is inferred.",
        ],
    }


def _cli(input_bytes: bytes, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    return subprocess.run(
        [sys.executable, "-m", "memecoin_experience.timestamp_order_audit", *args],
        input=input_bytes,
        capture_output=True,
        env=env,
    )


class TimestampOrderAuditTests(unittest.TestCase):
    def test_hand_oracle_nonmutation_and_permutation_invariance(self) -> None:
        payload = _hand_input()
        original = deepcopy(payload)
        expected = _expected_hand_output()
        self.assertEqual(analyze(payload), expected)
        self.assertEqual(payload, original)
        permuted = {"events": list(reversed(payload["events"])), "pairs": list(reversed(payload["pairs"]))}
        self.assertEqual(analyze(permuted), expected)

    def test_empty_input_and_unselected_event_count(self) -> None:
        empty = analyze({"events": [], "pairs": []})
        self.assertEqual(empty["summary"], {
            "event_count": 0,
            "pair_count": 0,
            "definite_before_count": 0,
            "definite_after_count": 0,
            "indeterminate_count": 0,
            "contradiction_count": 0,
        })
        result = analyze({"events": [
            {"id": "a", "lo_ns": 0, "hi_ns": 0},
            {"id": "b", "lo_ns": 1, "hi_ns": 1},
            {"id": "unused", "lo_ns": 2, "hi_ns": 2},
        ], "pairs": [{"before": "a", "after": "b"}]})
        self.assertEqual(result["summary"]["event_count"], 3)
        self.assertEqual(result["summary"]["pair_count"], 1)

    def test_small_interval_enumeration_oracle_and_reverse_relation(self) -> None:
        for alo, ahi, blo, bhi in itertools.product(range(4), repeat=4):
            if alo > ahi or blo > bhi:
                continue
            assignments = [(a, b) for a in range(alo, ahi + 1) for b in range(blo, bhi + 1)]
            if all(a < b for a, b in assignments):
                expected, reverse = "definite_before", "definite_after"
            elif all(a > b for a, b in assignments):
                expected, reverse = "definite_after", "definite_before"
            else:
                expected = reverse = "indeterminate"
            events = [
                {"id": "a", "lo_ns": alo, "hi_ns": ahi},
                {"id": "b", "lo_ns": blo, "hi_ns": bhi},
            ]
            forward = analyze({"events": events, "pairs": [{"before": "a", "after": "b"}]})["pairs"][0]["order"]
            backward = analyze({"events": events, "pairs": [{"before": "b", "after": "a"}]})["pairs"][0]["order"]
            self.assertEqual((forward, backward), (expected, reverse))

    def test_invalid_api_payloads_have_exact_error(self) -> None:
        invalid = [
            {},
            {"events": [], "pairs": [], "extra": 1},
            {"events": (), "pairs": []},
            {"events": [{"id": "a", "lo_ns": True, "hi_ns": 1}], "pairs": []},
            {"events": [{"id": "a", "lo_ns": 0.0, "hi_ns": 1}], "pairs": []},
            {"events": [{"id": "a", "lo_ns": 2, "hi_ns": 1}], "pairs": []},
            {"events": [{"id": "a", "lo_ns": 0, "hi_ns": 9223372036854775808}], "pairs": []},
            {"events": [{"id": "é", "lo_ns": 0, "hi_ns": 0}], "pairs": []},
            {"events": [{"id": "a" * 65, "lo_ns": 0, "hi_ns": 0}], "pairs": []},
            {"events": [{"id": "a", "lo_ns": 0, "hi_ns": 0}, {"id": "a", "lo_ns": 0, "hi_ns": 0}], "pairs": []},
            {"events": [{"id": "a", "lo_ns": 0, "hi_ns": 0}], "pairs": [{"before": "a", "after": "a"}]},
            {"events": [{"id": "a", "lo_ns": 0, "hi_ns": 0}, {"id": "b", "lo_ns": 0, "hi_ns": 0}], "pairs": [{"before": "a", "after": "b"}, {"before": "a", "after": "b"}]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "^timestamp_order_audit_invalid_input$"):
                    analyze(payload)

    def test_exact_and_over_maximum_bounds(self) -> None:
        events = [{"id": f"e{i}", "lo_ns": 0, "hi_ns": 0} for i in range(256)]
        self.assertEqual(analyze({"events": events, "pairs": []})["summary"]["event_count"], 256)
        with self.assertRaisesRegex(ValueError, "^timestamp_order_audit_invalid_input$"):
            analyze({"events": events + [{"id": "overflow", "lo_ns": 0, "hi_ns": 0}], "pairs": []})
        maximum = "a" * 64
        result = analyze({"events": [
            {"id": maximum, "lo_ns": 9223372036854775807, "hi_ns": 9223372036854775807},
            {"id": "b", "lo_ns": 0, "hi_ns": 0},
        ], "pairs": [{"before": "b", "after": maximum}]})
        self.assertEqual(result["pairs"][0]["order"], "definite_before")
        pair_events = [{"id": f"e{i}", "lo_ns": i, "hi_ns": i} for i in range(34)]
        pair_list = [{"before": f"e{i}", "after": f"e{j}"} for i in range(34) for j in range(34) if i != j][:1024]
        self.assertEqual(analyze({"events": pair_events, "pairs": pair_list})["summary"]["pair_count"], 1024)
        with self.assertRaisesRegex(ValueError, "^timestamp_order_audit_invalid_input$"):
            analyze({"events": pair_events, "pairs": pair_list + [{"before": "e33", "after": "e0"}]})

    def test_cli_valid_parity_and_rejects_malformed_streams(self) -> None:
        encoded = json.dumps(_hand_input()).encode()
        good = _cli(encoded)
        self.assertEqual(good.returncode, 0)
        self.assertEqual(good.stderr, b"")
        expected = json.dumps(_expected_hand_output(), sort_keys=True, separators=(",", ":")) + "\n"
        self.assertEqual(good.stdout, expected.encode())
        for bad in (
            b'{"events":[],"events":[],"pairs":[]}',
            b'{"events":[{"id":"a","lo_ns":0,"hi_ns":0}],"pairs":[],"x":{"x":NaN}}',
            b'{"events":[],"pairs":[]} {}',
            b"\xff",
            b" " * 1048577,
        ):
            with self.subTest(input_prefix=bad[:32]):
                result = _cli(bad)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b'{"error":"timestamp_order_audit_invalid_input"}\n')
        args = _cli(encoded, "unexpected")
        self.assertEqual((args.returncode, args.stdout, args.stderr), (2, b"", b'{"error":"timestamp_order_audit_invalid_input"}\n'))


if __name__ == "__main__":
    unittest.main()
