"""Strict parser for controller-owned acceptance result contracts."""

from __future__ import annotations

import json
import math
from typing import Any


class VerifierResultError(ValueError):
    """A trusted acceptance command did not return its declared passing result."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise VerifierResultError("verifier_result_duplicate_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise VerifierResultError("verifier_result_nonstandard_number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise VerifierResultError("verifier_result_nonstandard_number")
    return number


def validate_verifier_result(stdout: str, verifier: dict[str, Any]) -> dict[str, Any]:
    """Require schema, acceptance identity and literal boolean success from one harness result."""
    if type(stdout) is not str or type(verifier) is not dict:
        raise VerifierResultError("verifier_result_invalid")
    acceptance_id = verifier.get("acceptance_id")
    schema = verifier.get("result_schema")
    case_field = verifier.get("case_field")
    passed_field = verifier.get("passed_field")
    contract_values = (acceptance_id, schema, case_field, passed_field)
    if any(type(value) is not str or not value for value in contract_values):
        raise VerifierResultError("verifier_result_contract_invalid")
    if len(stdout.encode("utf-8")) > 200_000:
        raise VerifierResultError("verifier_result_too_large")
    try:
        value = json.loads(
            stdout,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except VerifierResultError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise VerifierResultError("verifier_result_invalid_json") from None
    if type(value) is not dict:
        raise VerifierResultError("verifier_result_not_object")
    if value.get("schema") != schema:
        raise VerifierResultError("verifier_result_schema_mismatch")
    if value.get(case_field) != acceptance_id:
        raise VerifierResultError("verifier_result_acceptance_mismatch")
    if value.get(passed_field) is not True:
        raise VerifierResultError("verifier_result_not_passing")
    return value
