"""Trusted held-out pilot acceptance, copied outside worker writable roots.

Usage: python pilot_acceptance.py EXP-001 CHECKOUT
       python pilot_acceptance.py PLAT-001 CONSUMER --producer-checkout PRODUCER
       python pilot_acceptance.py CTX-001 CHECKOUT

Read access: this script, stdlib/interpreter, supplied checkout/src and the one
case's tools file. Write access: an isolated temporary root only. All fixtures
are synthetic. No datasets, model code, credentials, runtime or network is used.
Candidate modules and CLI execute only inside the caller's OS sandbox. The CTX
canary target must be readable in that sandbox so a denial cannot mask a defect.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True


class Rejection(AssertionError):
    """A candidate failed independent acceptance."""


def require(condition, message):
    if not condition:
        raise Rejection(message)


def fixtures():
    """Independent golden fixtures; do not use worker constructors as an oracle."""
    v1 = {
        "schema": "brain-experience-v1",
        "feature_names": [
            "log_return_1",
            "log_close_open",
            "log_high_low",
            "log_volume_usd",
            "upper_wick",
            "lower_wick",
            "volume_vs_past",
            "log_return_3",
            "log_return_6",
            "past_return_rms",
            "past_positive_fraction",
            "zero_volume",
        ],
        "context": 8,
        "feature_history": 8,
        "horizons": [1, 3],
        "bar_seconds": 300,
        "historical_online_availability_known": False,
        "target": "origin_close_log_return_no_execution",
        "partial_labels": True,
    }
    v2 = {
        "schema": "brain-experience-v2",
        "layout": "dense-sequence-f64-v1",
        "context": 2,
        "features": [{"name": "synthetic_input", "unit": "count", "modality": "synthetic"}],
        "targets": [{"name": "synthetic_target", "unit": "count", "kind": "synthetic"}],
        "representation": {"name": "synthetic", "version": "1", "code_sha": "a" * 64},
        "availability": "historical_replay",
    }
    domain = {
        "version": "typed-coordinate-domain-v1",
        "name": "synthetic_clock",
        "network": "offline",
        "kind": "synthetic_count",
        "unit": "tick",
    }
    pin = hashlib.sha256(
        json.dumps(domain, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    v3 = {
        **copy.deepcopy(v2),
        "schema": "brain-experience-v3",
        "coordinate_domain": domain,
        "coordinate_domain_pin": pin,
        "coordinate_domains": [{"descriptor": domain, "pin": pin}],
        "sample_contract": {
            "version": "coordinate-sample-contract-v1",
            "identity_rule": "stable_source_identity_plus_native_coordinate_position_v1",
            "position_rule": "domain_defined_native_ordered_position_v1",
            "event_order_rule": "coordinate_then_position_then_identity_v1",
            "dependency_rule": "typed_coordinate_half_open_extents_with_utc_availability_v1",
            "decision_rule": "actual_utc_decision_or_null_only_for_historical_replay_v1",
            "target_rule": "typed_coordinate_window_maturity_completion_and_evidence_v1",
            "eligibility_rule": "coordinate_role_extent_purge_and_utc_knowledge_cutoff_v1",
        },
    }
    return v1, v2, v3


def valid_cases():
    v1, v2, v3 = fixtures()
    cases = [v1, v2, v3]
    # v3 supports a registry with several domains, not just the primary domain.
    multi_domain = copy.deepcopy(v3)
    domain = {**multi_domain["coordinate_domain"], "name": "second_synthetic_clock"}
    pin = hashlib.sha256(
        json.dumps(domain, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    multi_domain["coordinate_domains"].append({"descriptor": domain, "pin": pin})
    multi_domain["coordinate_domains"].sort(key=lambda item: item["pin"])
    cases.append(multi_domain)
    for base in (v2, v3):
        for context in (1, 65536):
            value = copy.deepcopy(base)
            value["context"] = context
            value["availability"] = "point_in_time"
            value["features"].append({"name": "another", "unit": "arbitrary", "modality": "custom"})
            value["targets"].append({"name": "another", "unit": "arbitrary", "kind": "custom"})
            cases.append(value)
    return cases


def malformed_cases():
    values = [None, False, True, 0, 4.2, "", "brain-experience-v2", [], ["schema"], {}]
    for base in fixtures():
        for key in base:
            value = copy.deepcopy(base)
            del value[key]
            values.append(value)
        value = copy.deepcopy(base)
        value["unexpected"] = True
        values.append(value)
    for base in fixtures()[1:]:
        for key, wrongs in {
            "schema": [None, [], {}, "brain-experience-v999"],
            "layout": [None, [], {}, "other"],
            "context": [None, True, False, 0, -1, 65537, 1.0, "2", [], {}],
            "availability": [None, [], {}, "future"],
            "representation": [
                None,
                [],
                "text",
                1,
                {},
                {"name": [], "version": "1", "code_sha": "a" * 64},
                {"name": "x", "version": "1", "code_sha": []},
                {"name": "x", "version": "1", "code_sha": "A" * 64},
            ],
            "features": [
                None,
                {},
                "text",
                [],
                [None],
                [[]],
                ["x"],
                [1],
                [{"name": [], "unit": "x", "modality": "x"}],
                [{"name": {}, "unit": "x", "modality": "x"}],
                [{"name": "x", "unit": "x"}],
            ],
            "targets": [
                None,
                {},
                "text",
                [],
                [None],
                [[]],
                ["x"],
                [1],
                [{"name": [], "unit": "x", "kind": "x"}],
                [{"name": "x", "unit": None, "kind": "x"}],
            ],
        }.items():
            for wrong in wrongs:
                value = copy.deepcopy(base)
                value[key] = wrong
                values.append(value)
        for key in ("features", "targets"):
            value = copy.deepcopy(base)
            value[key].append(copy.deepcopy(value[key][0]))
            values.append(value)
    for key, wrongs in {
        "coordinate_domain": [None, [], {}, "text"],
        "coordinate_domain_pin": [None, [], {}, "z" * 64, "0" * 64],
        "coordinate_domains": [
            None,
            {},
            "text",
            [],
            [None],
            [[]],
            [{"descriptor": {}, "pin": "0" * 64}],
        ],
        "sample_contract": [None, [], {}, "text"],
    }.items():
        for wrong in wrongs:
            value = copy.deepcopy(fixtures()[2])
            value[key] = wrong
            values.append(value)
    return values


def load_schema(checkout):
    sys.path.insert(0, str(checkout / "src"))
    module = importlib.import_module("memecoin_experience.schema")
    require(
        Path(module.__file__).resolve().is_relative_to(checkout / "src"),
        "schema import escaped checkout",
    )
    for name, loaded in tuple(sys.modules.items()):
        if name.startswith("memecoin_") and getattr(loaded, "__file__", None):
            require(
                Path(loaded.__file__).resolve().is_relative_to(checkout / "src"),
                f"transitive import escaped checkout: {name}",
            )
    return module


def experience(checkout):
    validate = load_schema(checkout).validate
    good = valid_cases()
    bad = malformed_cases()
    for index, value in enumerate(good):
        before = copy.deepcopy(value)
        require(validate(value) is value, f"valid fixture {index}: return identity changed")
        require(value == before, f"valid fixture {index}: input mutated")
    for index, value in enumerate(bad):
        before = copy.deepcopy(value)
        try:
            validate(value)
        except ValueError:
            pass
        except Exception as exc:
            raise Rejection(f"malformed fixture {index}: leaked {type(exc).__name__}") from exc
        else:
            raise Rejection(f"malformed fixture {index}: accepted")
        require(value == before, f"malformed fixture {index}: input mutated")
    return {"valid_cases": len(good), "malformed_cases": len(bad)}


def platform(checkout, producer, *, strict_json=False):
    tool = checkout / "tools" / "brain_schema_check.py"
    require(tool.is_file() and not tool.is_symlink(), "consumer CLI missing or symlinked")
    # Bootstrap only imports the supplied producer and executes the one consumer CLI.
    bootstrap = (
        "import runpy,sys;src=sys.argv[1];sys.argv=sys.argv[2:];"
        "sys.path.insert(0,src);runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG") if key in os.environ}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    examples = [(json.dumps(value), True) for value in valid_cases()]
    examples += [(json.dumps(value), False) for value in malformed_cases()]
    examples += [(value, False) for value in ("{", "", "null trailing", '{"schema":', "[1,]")]
    if strict_json:
        base = json.dumps(valid_cases()[0])[:-1]
        examples += [
            (base + ', "extra": {"duplicate": 1, "duplicate": 2}}', False),
            (base + ', "extra": 1, "extra": 2}', False),
            (base + ', "extra": NaN}', False),
            (base + ', "extra": Infinity}', False),
            (base + ', "extra": -Infinity}', False),
        ]
    with tempfile.TemporaryDirectory(prefix="org-platform-heldout-") as tmp:
        path = Path(tmp) / "synthetic.json"
        for index, (raw, expected) in enumerate(examples):
            path.write_text(raw, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    bootstrap,
                    str(producer / "src"),
                    str(tool),
                    str(path),
                ],
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            require(
                result.returncode == (0 if expected else 2),
                f"CLI fixture {index}: exit {result.returncode}: {result.stderr[:300]}",
            )
            try:
                response = json.loads(result.stdout)
            except (ValueError, TypeError) as exc:
                raise Rejection(f"CLI fixture {index}: stdout is not one JSON document") from exc
            require(
                isinstance(response, dict) and set(response) == {"schema", "valid", "error"},
                f"CLI fixture {index}: response keys",
            )
            require(
                response["schema"] == "brain-schema-check-v1" and response["valid"] is expected,
                f"CLI fixture {index}: schema/result",
            )
            require(
                response["error"] is None
                if expected
                else isinstance(response["error"], str) and bool(response["error"]),
                f"CLI fixture {index}: error contract",
            )
    return {"cli_cases": len(examples)}


def context(checkout):
    source = checkout / "tools" / "agent_context.py"
    require(source.is_file() and not source.is_symlink(), "context tool missing or symlinked")
    spec = importlib.util.spec_from_file_location("heldout_agent_context", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = "HELDOUT_SYMLINK_CANARY_853729"
    pin = hashlib.sha256(marker.encode()).hexdigest()
    checked = 0
    with tempfile.TemporaryDirectory(prefix="org-context-heldout-") as tmp:
        parent = Path(tmp)
        outside = parent / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text(marker, encoding="utf-8")
        (outside / "contracts.md").write_text(marker, encoding="utf-8")
        require(
            (outside / "secret.md").read_text(encoding="utf-8") == marker,
            "canary target must be readable; OS denial would mask source validation",
        )
        for mode in ("mandatory", "profile", "ancestor", "internal"):
            root = parent / mode
            root.mkdir()
            (root / "memory").mkdir()
            (root / "AGENTS.md").write_text("safe instructions", encoding="utf-8")
            (root / "memory" / "now.md").write_text("safe state", encoding="utf-8")
            if mode == "ancestor":
                (root / "docs").symlink_to(outside, target_is_directory=True)
            else:
                (root / "docs").mkdir()
                (root / "docs" / "contracts.md").write_text("safe contract", encoding="utf-8")
                if mode == "mandatory":
                    (root / "AGENTS.md").unlink()
                    (root / "AGENTS.md").symlink_to(outside / "secret.md")
                elif mode == "profile":
                    (root / "docs" / "contracts.md").unlink()
                    (root / "docs" / "contracts.md").symlink_to(outside / "secret.md")
                else:
                    (root / "internal.md").write_text(marker, encoding="utf-8")
                    (root / "docs" / "contracts.md").unlink()
                    (root / "docs" / "contracts.md").symlink_to(root / "internal.md")
            target = root / ("AGENTS.md" if mode == "mandatory" else "docs/contracts.md")
            attempted = []
            resolved_target = target.resolve()
            read_text, read_bytes = Path.read_text, Path.read_bytes

            def watched_text(
                path,
                *args,
                _targets=(target, resolved_target),
                _attempted=attempted,
                _read=read_text,
                **kwargs,
            ):
                if path in _targets:
                    _attempted.append("text")
                return _read(path, *args, **kwargs)

            def watched_bytes(
                path,
                *args,
                _targets=(target, resolved_target),
                _attempted=attempted,
                _read=read_bytes,
                **kwargs,
            ):
                if path in _targets:
                    _attempted.append("bytes")
                return _read(path, *args, **kwargs)

            module.ROOT = root
            module.PROFILES = {"review": ("docs/contracts.md",)}
            module._git = lambda *args: "(heldout git stub)"
            with (
                patch.object(Path, "read_text", watched_text),
                patch.object(Path, "read_bytes", watched_bytes),
            ):
                for action in (
                    lambda target=target: module._read(target),
                    lambda target=target, root=root: module._query_snippets(
                        "HELDOUT SYMLINK CANARY", (str(target.relative_to(root)),)
                    ),
                    lambda: module.build_packet("review", "HELDOUT SYMLINK CANARY"),
                ):
                    try:
                        result = action()
                    except ValueError:
                        result = "[explicit rejection]"
                    require(isinstance(result, str), f"{mode}: result type")
                    require(
                        marker not in result and pin not in result,
                        f"{mode}: canary content/hash leaked",
                    )
                    checked += 1
            require(not attempted, f"{mode}: source bytes read before rejection: {attempted}")
    return {"symlink_cases": checked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("EXP-001", "PLAT-001", "PLAT-002", "CTX-001"))
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--producer-checkout", type=Path)
    args = parser.parse_args()
    checkout = args.checkout.resolve(strict=True)
    try:
        if args.case == "EXP-001":
            detail = experience(checkout)
        elif args.case in ("PLAT-001", "PLAT-002"):
            detail = platform(
                checkout,
                (args.producer_checkout or checkout).resolve(strict=True),
                strict_json=args.case == "PLAT-002",
            )
        else:
            detail = context(checkout)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema": "org-pilot-acceptance-v1",
                    "case": args.case,
                    "passed": False,
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
        )
        return 1
    print(
        json.dumps(
            {"schema": "org-pilot-acceptance-v1", "case": args.case, "passed": True, **detail}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
