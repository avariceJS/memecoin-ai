import copy
import itertools
import json
import os
import subprocess
import sys
import unittest

from memecoin_experience.research_budget_frontier import analyze


LIMITATIONS = [
    "Costs and additive benefits are caller declarations; no measured utility, uncertainty or interactions are inferred.",
    "The frontier describes feasible subsets only; it does not authorize, schedule or execute research.",
    "Exact results are limited to at most 16 options and 65536 subsets; one canonical witness is retained per frontier point.",
]
DEMO = {"budget_units": 5, "options": [
    {"id": "a", "cost_units": 2, "benefit_units": 3},
    {"id": "b", "cost_units": 3, "benefit_units": 4},
    {"id": "c", "cost_units": 4, "benefit_units": 5},
    {"id": "d", "cost_units": 3, "benefit_units": 4},
]}


def oracle(payload):
    options, budget = payload["options"], payload["budget_units"]
    classes = {}
    for size in range(len(options) + 1):
        for indexes in itertools.combinations(range(len(options)), size):
            chosen = [options[i] for i in indexes]
            cost = sum(item["cost_units"] for item in chosen)
            benefit = sum(item["benefit_units"] for item in chosen)
            if cost <= budget:
                key = cost, benefit
                ids = tuple(sorted(item["id"] for item in chosen))
                candidate = (len(ids), ids)
                if key not in classes or candidate < classes[key]:
                    classes[key] = candidate
    points = []
    for cost, benefit in classes:
        if not any(c2 <= cost and b2 >= benefit and (c2 < cost or b2 > benefit)
                   for c2, b2 in classes):
            points.append({"cost_units": cost, "benefit_units": benefit,
                           "option_ids": list(classes[cost, benefit][1])})
    points.sort(key=lambda point: point["cost_units"])
    return {"schema": "research-budget-frontier-v1",
            "summary": {"option_count": len(options), "budget_units": budget,
                        "subset_count_bound": 2 ** len(options),
                        "frontier_point_count": len(points)},
            "frontier": points, "limitations": LIMITATIONS}


class ResearchBudgetFrontierTests(unittest.TestCase):
    def test_demonstration_and_permutation(self):
        expected = oracle(DEMO)
        self.assertEqual(analyze(DEMO), expected)
        self.assertEqual([p["option_ids"] for p in expected["frontier"]],
                         [[], ["a"], ["b"], ["c"], ["a", "b"]])
        before = copy.deepcopy(DEMO)
        self.assertEqual(analyze(DEMO), analyze({"budget_units": 5,
                         "options": list(reversed(DEMO["options"]))}))
        self.assertEqual(DEMO, before)

    def test_empty_zero_and_boundary_semantics(self):
        self.assertEqual(analyze({"budget_units": 0, "options": []}), oracle({"budget_units": 0, "options": []}))
        fixture = {"budget_units": 0, "options": [
            {"id": "a", "cost_units": 0, "benefit_units": 1},
            {"id": "b", "cost_units": 0, "benefit_units": 0},
            {"id": "c", "cost_units": 1, "benefit_units": 0},
        ]}
        self.assertEqual(analyze(fixture)["frontier"], [{"cost_units": 0, "benefit_units": 1, "option_ids": ["a"]}])
        all_zero = {"budget_units": 2, "options": [
            {"id": "z", "cost_units": 0, "benefit_units": 0},
            {"id": "A", "cost_units": 0, "benefit_units": 0}]}
        self.assertEqual(analyze(all_zero)["frontier"][0]["option_ids"], [])
        for budget in (0, 2, 3, 99):
            payload = {"budget_units": budget, "options": DEMO["options"]}
            self.assertEqual(analyze(payload), oracle(payload))

    def test_canonical_witness_ties(self):
        payload = {"budget_units": 4, "options": [
            {"id": "z", "cost_units": 2, "benefit_units": 3},
            {"id": "A", "cost_units": 1, "benefit_units": 1},
            {"id": "a", "cost_units": 1, "benefit_units": 2},
            {"id": "b", "cost_units": 1, "benefit_units": 2},
            {"id": "c", "cost_units": 0, "benefit_units": 0},
            {"id": "d", "cost_units": 0, "benefit_units": 0},
        ]}
        self.assertEqual(analyze(payload), oracle(payload))

    def test_exhaustive_tiny_catalogues(self):
        identifiers = "abc"
        for size in range(4):
            for values in itertools.product(itertools.product(range(3), repeat=2), repeat=size):
                options = [{"id": identifiers[i], "cost_units": pair[0], "benefit_units": pair[1]}
                           for i, pair in enumerate(values)]
                for budget in range(7):
                    payload = {"budget_units": budget, "options": options}
                    self.assertEqual(analyze(payload), oracle(payload), (payload,))

    def test_maximum_catalogue(self):
        options = [{"id": f"i{i:02}", "cost_units": 1_000_000,
                    "benefit_units": 1_000_000} for i in range(16)]
        result = analyze({"budget_units": 16_000_000, "options": options})
        self.assertEqual(result["summary"], {"option_count": 16, "budget_units": 16_000_000,
                         "subset_count_bound": 65536, "frontier_point_count": 17})
        self.assertEqual(result["frontier"][-1]["option_ids"], [f"i{i:02}" for i in range(16)])

    def test_invalid_api_inputs_have_exact_error_and_preserve(self):
        valid = {"budget_units": 0, "options": []}
        invalid = [None, [], {}, {"budget_units": True, "options": []},
                   {"budget_units": 0.0, "options": []}, {"budget_units": -1, "options": []},
                   {"budget_units": 16_000_001, "options": []},
                   {"budget_units": 0, "options": [None]},
                   {"budget_units": 0, "options": [{"id": "a", "cost_units": 0, "benefit_units": 0, "x": 1}]},
                   {"budget_units": 0, "options": [{"id": "é", "cost_units": 0, "benefit_units": 0}]},
                   {"budget_units": 0, "options": [{"id": "a", "cost_units": True, "benefit_units": 0}]},
                   {"budget_units": 0, "options": [{"id": "a", "cost_units": 1_000_001, "benefit_units": 0}]},
                   {"budget_units": 0, "options": [{"id": "a", "cost_units": 0, "benefit_units": 0}] * 2},
                   {"budget_units": 0, "options": [{"id": str(i), "cost_units": 0, "benefit_units": 0} for i in range(17)]}]
        for payload in invalid:
            before = copy.deepcopy(payload)
            with self.assertRaises(ValueError) as caught:
                analyze(payload)
            self.assertEqual(str(caught.exception), "research_budget_frontier_invalid_input")
            self.assertEqual(payload, before)
        self.assertEqual(analyze(valid)["frontier"][0]["option_ids"], [])

    def test_cli_contract(self):
        env = dict(os.environ, PYTHONPATH="src")
        module = "memecoin_experience.research_budget_frontier"
        valid = json.dumps(DEMO, separators=(",", ":")).encode()
        proc = subprocess.run([sys.executable, "-m", module], input=valid, capture_output=True, env=env)
        self.assertEqual((proc.returncode, proc.stderr), (0, b""))
        self.assertEqual(proc.stdout, (json.dumps(analyze(DEMO), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
        invalid_inputs = [b"", b"{} {}", b'{"budget_units":0,"options":[],"options":[]}',
                          b'{"budget_units":NaN,"options":[]}', b"\xff", b" " * 1_048_577,
                          b"{" + b"[" * 1100 + b"]" * 1100 + b"}"]
        for data in invalid_inputs:
            bad = subprocess.run([sys.executable, "-m", module], input=data, capture_output=True, env=env)
            self.assertEqual((bad.returncode, bad.stdout, bad.stderr),
                             (2, b"", b'{"error":"research_budget_frontier_invalid_input"}\n'))
        extra = subprocess.run([sys.executable, "-m", module, "extra"], input=valid, capture_output=True, env=env)
        self.assertEqual((extra.returncode, extra.stdout, extra.stderr),
                         (2, b"", b'{"error":"research_budget_frontier_invalid_input"}\n'))


if __name__ == "__main__":
    unittest.main()
