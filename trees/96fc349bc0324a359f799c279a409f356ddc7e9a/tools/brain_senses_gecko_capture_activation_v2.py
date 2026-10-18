"""Add replay-context binding without changing frozen activation-v1 records."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from memecoin_research_storage import StorageError
from tools import brain_senses_gecko_capture_activation_v1 as activation_v1
from tools import brain_senses_gecko_capture_replay_context_v1 as replay_context
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

SCHEMA = "brain-senses-gecko-capture-activation-v2"
SCHEMA_VERSION = 2
ACTIVATION_DIRECTORY = "control/gecko-capture-activation-v2"
MAX_RECORD_BYTES = 128 * 1024
V1_RECORD_KEYS = {
    "schema",
    "schema_version",
    "activation_id",
    "activation_path",
    "phase",
    "source_commit",
    "source_manifest_sha256",
    "source_map",
    "service_root",
    "binding_sha256",
    "certificate_sha256",
    "policy_sha256",
    "population_sha256",
    "plan_sha256",
    "storage",
    "collector",
    "watchdog",
    "predecessor_certificate_sha256",
    "supervisor_certificate_sha256",
}


class ActivationV2Error(ValueError):
    """Activation-v2 or its replay-context binding is invalid."""


@dataclass(frozen=True)
class VerifiedActivationV2:
    activation_id: str
    record_sha256: str
    record: Mapping[str, Any]
    replayed_certificate: Mapping[str, Any]


def activation_path(activation_id: str) -> str:
    # Keep ID validation identical to the v1 protocol without changing its bytes.
    activation_v1.activation_path(activation_id)
    return f"{ACTIVATION_DIRECTORY}/{activation_id}.json"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=activation_v1._pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, activation_v1.ActivationError) as exc:
        raise ActivationV2Error("activation_v2_json_invalid") from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise ActivationV2Error("activation_v2_not_canonical")
    return value


def _v1_record(record: Mapping[str, Any]) -> dict[str, Any]:
    adapted = dict(record)
    adapted.update(
        schema=activation_v1.SCHEMA,
        schema_version=activation_v1.SCHEMA_VERSION,
        activation_path=activation_v1.activation_path(cast(str, record.get("activation_id"))),
    )
    adapted.pop("replay_context_path", None)
    adapted.pop("replay_context_sha256", None)
    return adapted


def prepare_activation_record_v2(
    *,
    replay_context_raw: bytes,
    replay_context_sha256: str,
    replay_context_path: str,
    replay_observed_now_ns: int,
    **v1_arguments: Any,
) -> dict[str, Any]:
    """Prepare an activation-v2 record from the unchanged v1 contract plus context."""
    activation_id = cast(str, v1_arguments.get("activation_id"))
    expected_context_path = replay_context.context_path(activation_id)
    if (
        replay_context_path != expected_context_path
        or _sha(replay_context_raw) != replay_context_sha256
    ):
        raise ActivationV2Error("activation_v2_context_pin_mismatch")
    service_root = cast(str, v1_arguments.get("service_root"))
    certificate_sha256 = cast(str, v1_arguments.get("certificate_sha256"))
    try:
        replayed = replay_context.verify_replay_context(
            v1_arguments["writer"],
            context_raw=replay_context_raw,
            expected_activation_id=activation_id,
            expected_context_sha256=replay_context_sha256,
            expected_successor_root=service_root,
            expected_certificate_sha256=certificate_sha256,
            observed_now_ns=replay_observed_now_ns,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ActivationV2Error("activation_v2_context_invalid") from exc
    base = activation_v1.prepare_activation_record(**v1_arguments)
    if replayed.get("certificate_sha256") != base.get("certificate_sha256"):
        raise ActivationV2Error("activation_v2_certificate_mismatch")
    result = dict(base)
    result.update(
        schema=SCHEMA,
        schema_version=SCHEMA_VERSION,
        activation_path=activation_path(activation_id),
        replay_context_path=replay_context_path,
        replay_context_sha256=replay_context_sha256,
    )
    return result


def create_activation_record_v2(
    *,
    writer: DescriptorWriter,
    record: Mapping[str, Any],
    data_root_fd: int,
    revalidate_storage: Callable[[], Any],
) -> str:
    """Create an immutable activation-v2 record after checking its context pin."""
    if type(record) is not dict or record.get("schema") != SCHEMA:
        raise ActivationV2Error("activation_v2_schema_invalid")
    if set(record) != V1_RECORD_KEYS | {"replay_context_path", "replay_context_sha256"}:
        raise ActivationV2Error("activation_v2_record_shape_invalid")
    activation_id = cast(str, record.get("activation_id"))
    path = activation_path(activation_id)
    if record.get("activation_path") != path:
        raise ActivationV2Error("activation_v2_path_invalid")
    try:
        context_raw = cast(
            bytes,
            writer.read_bytes(
                cast(str, record.get("replay_context_path")), max_bytes=2 * 1024 * 1024
            ),
        )
        replay_context.verify_replay_context(
            writer,
            context_raw=context_raw,
            expected_activation_id=activation_id,
            expected_context_sha256=cast(str, record.get("replay_context_sha256")),
            expected_successor_root=cast(str, record.get("service_root")),
            expected_certificate_sha256=cast(str, record.get("certificate_sha256")),
        )
    except Exception as exc:
        raise ActivationV2Error("activation_v2_context_prewrite_failed") from exc
    raw = canonical_json(dict(record))
    writer_stat = os.fstat(writer.root_fd)
    data_stat = os.fstat(data_root_fd)
    if (writer_stat.st_dev, writer_stat.st_ino) != (data_stat.st_dev, data_stat.st_ino):
        raise ActivationV2Error("activation_v2_writer_root_mismatch")
    try:
        revalidate_storage()
        writer.atomic_write(path, raw, create_only=True)
        revalidate_storage()
        if writer.read_bytes(path, max_bytes=MAX_RECORD_BYTES) != raw:
            raise ActivationV2Error("activation_v2_readback_mismatch")
    except StorageError as exc:
        if "external_writer_create_only_collision" in exc.args:
            raise ActivationV2Error("activation_v2_create_only_collision") from exc
        raise ActivationV2Error("activation_v2_create_failed") from exc
    except ActivationV2Error:
        raise
    except Exception as exc:
        raise ActivationV2Error("activation_v2_create_failed") from exc
    return _sha(raw)


def validate_activation_record_v2(
    *,
    record_raw: bytes,
    expected_activation_id: str,
    replay_context_raw: bytes,
    observed_now_ns: int,
    **v1_validation_arguments: Any,
) -> VerifiedActivationV2:
    """Validate activation-v2, then delegate unchanged v1 source/launch checks."""
    record = _parse(record_raw)
    expected_keys = V1_RECORD_KEYS | {"replay_context_path", "replay_context_sha256"}
    context_path = replay_context.context_path(expected_activation_id)
    if (
        set(record) != expected_keys
        or record.get("schema") != SCHEMA
        or record.get("schema_version") != SCHEMA_VERSION
        or record.get("activation_id") != expected_activation_id
        or record.get("activation_path") != activation_path(expected_activation_id)
        or record.get("replay_context_path") != context_path
        or type(record.get("replay_context_sha256")) is not str
        or _sha(replay_context_raw) != record.get("replay_context_sha256")
    ):
        raise ActivationV2Error("activation_v2_record_binding_mismatch")
    try:
        replayed = replay_context.verify_replay_context(
            v1_validation_arguments["writer"],
            context_raw=replay_context_raw,
            expected_activation_id=expected_activation_id,
            expected_context_sha256=cast(str, record["replay_context_sha256"]),
            expected_successor_root=cast(str, record["service_root"]),
            expected_certificate_sha256=cast(str, record["certificate_sha256"]),
            observed_now_ns=observed_now_ns,
        )
        adapted = _v1_record(record)
        activation_v1.validate_activation_record(
            record_raw=canonical_json(adapted),
            expected_activation_id=expected_activation_id,
            **v1_validation_arguments,
        )
    except Exception as exc:
        raise ActivationV2Error("activation_v2_validation_failed") from exc
    if replayed.get("certificate_sha256") != record.get("certificate_sha256"):
        raise ActivationV2Error("activation_v2_certificate_mismatch")
    return VerifiedActivationV2(
        activation_id=expected_activation_id,
        record_sha256=_sha(record_raw),
        record=record,
        replayed_certificate=cast(Mapping[str, Any], replayed["replayed_certificate"]),
    )


__all__ = [
    "SCHEMA",
    "SCHEMA_VERSION",
    "ACTIVATION_DIRECTORY",
    "ActivationV2Error",
    "VerifiedActivationV2",
    "activation_path",
    "prepare_activation_record_v2",
    "create_activation_record_v2",
    "validate_activation_record_v2",
]
