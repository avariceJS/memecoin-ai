"""Create-only descriptor references for replaying the Gecko v2 cutover.

The context contains paths and hashes, not authority. Every referenced byte is
re-read through the held data-root descriptor and the cutover certificate is
reconstructed by the independent chain-v2 implementation on each verification.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping
from typing import Any, cast

from tools import brain_senses_gecko_capture_only_v2_handoff_v1 as handoff
from tools import brain_senses_gecko_scout_chain_v2 as chain_v2
from tools import brain_senses_gecko_usage_inventory_v4 as inventory_v4
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

SCHEMA = "brain-senses-gecko-capture-replay-context-v1"
SCHEMA_VERSION = 1
ACTIVATION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
ROOT_PREFIX = "services/gecko-market-v1/capture-only-v2"
ROOT_CONTEXT_DIRECTORY = "control/gecko-capture-root-context-v1"
ROOT_CONTEXT_SCHEMA = "brain-senses-gecko-capture-root-context-v1"
ROOT_METADATA_FILES = (
    "binding.json",
    "policy.json",
    "predecessor-cutover.json",
    "chain/00000000.json",
)
INPUT_NAMES = (
    "handoff_raw",
    "successor_root",
    "expected_handoff_genesis",
    "predecessor_attempts",
    "predecessor_slots",
    "predecessor_chain_records",
    "policy",
    "predecessor_file_prefix_sha256",
    "predecessor_last_plan_sha256",
    "coordinator_prefix_sha256",
    "coordinator_usage_sha256",
    "preinit_census_sha256",
    "preinit_snapshot_set_sha256",
    "observed_now_ns",
)
CERTIFICATE_INPUT_NAMES = INPUT_NAMES
HISTORICAL_CUTOVER_NS = handoff.UNPLANNED_GAP_CLOSE_NS


class ReplayContextError(ValueError):
    """Replay context, a referenced immutable input, or the root census drifted."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash(value: Any) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def context_path(activation_id: str) -> str:
    if type(activation_id) is not str or ACTIVATION_ID_RE.fullmatch(activation_id) is None:
        raise ReplayContextError("replay_context_activation_id_invalid")
    return f"control/gecko-capture-activation-v2/{activation_id}.replay-context-v1.json"


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReplayContextError("replay_context_duplicate_key")
        result[key] = value
    return result


def _object(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ReplayContextError) as exc:
        raise ReplayContextError(reason) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise ReplayContextError(reason)
    return value


def _safe_path(value: Any) -> bool:
    return (
        type(value) is str
        and not value.startswith("/")
        and "\x00" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and (value.startswith("services/") or value.startswith("control/"))
    )


def _read_ref(writer: DescriptorWriter, ref: Mapping[str, Any]) -> bytes:
    if (
        type(ref) is not dict
        or set(ref) != {"path", "sha256"}
        or not _safe_path(ref.get("path"))
        or not _hash(ref.get("sha256"))
    ):
        raise ReplayContextError("replay_context_input_ref_shape")
    try:
        raw = writer.read_bytes(cast(str, ref["path"]), max_bytes=32 * 1024 * 1024)
    except Exception as exc:
        raise ReplayContextError("replay_context_input_unavailable") from exc
    if _sha(raw) != ref["sha256"]:
        raise ReplayContextError("replay_context_input_digest_mismatch")
    return raw


def _decode_input(name: str, raw: bytes) -> Any:
    if name == "handoff_raw":
        return raw
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ReplayContextError) as exc:
        raise ReplayContextError("replay_context_input_json_invalid") from exc
    if canonical_json(value) != raw:
        raise ReplayContextError("replay_context_input_not_canonical")
    return value


def _registered_roots(writer: DescriptorWriter) -> list[dict[str, Any]]:
    try:
        names = writer.list_names(ROOT_PREFIX)
    except FileNotFoundError as exc:
        raise ReplayContextError("replay_context_root_census_missing") from exc
    roots: list[dict[str, Any]] = []
    for name in names:
        if ACTIVATION_ID_RE.fullmatch(name) is None:
            raise ReplayContextError("replay_context_unregistered_root_name")
        root = f"{ROOT_PREFIX}/{name}"
        root_stat = writer.stat(root)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise ReplayContextError("replay_context_root_not_directory")
        files: list[dict[str, str]] = []
        for suffix in ROOT_METADATA_FILES:
            path = f"{root}/{suffix}"
            try:
                raw = writer.read_bytes(path, max_bytes=4 * 1024 * 1024)
            except Exception as exc:
                raise ReplayContextError("replay_context_root_metadata_missing") from exc
            files.append({"path": path, "sha256": _sha(raw)})
        handoff_path = f"{root}/{handoff.JOURNAL_NAME}"
        try:
            handoff_raw = writer.read_bytes(handoff_path, max_bytes=256 * 1024)
        except FileNotFoundError:
            pass
        except Exception as exc:
            raise ReplayContextError("replay_context_handoff_read_failed") from exc
        else:
            files.append({"path": handoff_path, "sha256": _sha(handoff_raw)})
        roots.append({"root": root, "files": files})
    if not roots:
        raise ReplayContextError("replay_context_empty_root_census")
    return roots


def _root_context_path(root: str) -> str:
    release_id = root.rsplit("/", 1)[-1]
    if ACTIVATION_ID_RE.fullmatch(release_id) is None:
        raise ReplayContextError("replay_context_root_id_invalid")
    return f"{ROOT_CONTEXT_DIRECTORY}/{release_id}.json"


def _load_root_contexts(
    writer: DescriptorWriter,
    roots: list[dict[str, Any]],
    *,
    observed_now_ns: int,
) -> tuple[list[dict[str, str]], dict[str, Any], dict[str, Any]]:
    """Load and replay explicit per-root contexts; never infer a missing one."""
    refs: list[dict[str, str]] = []
    handoffs: dict[str, Any] = {}
    v2_chains: dict[str, Any] = {}
    for row in roots:
        root = cast(str, row["root"])
        path = _root_context_path(root)
        try:
            raw = writer.read_bytes(path, max_bytes=8 * 1024 * 1024)
        except Exception as exc:
            raise ReplayContextError(f"replay_context_root_context_missing:{path}") from exc
        context = _object(raw, "replay_context_root_context_invalid")
        if (
            set(context) != {"schema", "schema_version", "root", "root_kind", "inputs"}
            or context.get("schema") != ROOT_CONTEXT_SCHEMA
            or context.get("schema_version") != 1
            or context.get("root") != root
        ):
            raise ReplayContextError("replay_context_root_context_binding")
        root_kind = context.get("root_kind")
        inputs = context.get("inputs")
        refs.append({"root": root, "path": path, "sha256": _sha(raw)})
        if root_kind == "chain_v2":
            if type(inputs) is not dict or set(inputs) != {
                "binding",
                "policy",
                "certificate",
                "certificate_input_refs",
            }:
                raise ReplayContextError("replay_context_v2_root_inputs")
            loaded = {
                name: _decode_input(name, _read_ref(writer, ref))
                for name, ref in inputs.items()
                if name != "certificate_input_refs"
            }
            input_refs = _decode_input(
                "certificate_input_refs", _read_ref(writer, inputs["certificate_input_refs"])
            )
            cert_inputs = _certificate_inputs(writer, input_refs)
            if (
                type(loaded["binding"]) is not dict
                or type(loaded["policy"]) is not dict
                or type(loaded["certificate"]) is not dict
                or loaded["binding"].get("root") != root
            ):
                raise ReplayContextError("replay_context_v2_root_input_type")
            try:
                chain_names = tuple(writer.list_names(f"{root}/chain"))
                if chain_names != tuple(f"{index:08d}.json" for index in range(len(chain_names))):
                    raise ReplayContextError("replay_context_v2_root_chain_file_closure")
                loaded["chain_records"] = [
                    _object(
                        writer.read_bytes(f"{root}/chain/{name}", max_bytes=2 * 1024 * 1024),
                        "replay_context_v2_root_chain_json",
                    )
                    for name in chain_names
                ]
            except ReplayContextError:
                raise
            except Exception as exc:
                raise ReplayContextError("replay_context_v2_root_chain_read") from exc
            try:
                chain_v2.verify_chain(
                    loaded["policy"],
                    loaded["certificate"],
                    loaded["chain_records"],
                    certificate_inputs=cert_inputs,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ReplayContextError("replay_context_v2_root_replay") from exc
            if loaded["binding"].get("policy_sha256") != loaded["policy"].get(
                "policy_sha256"
            ) or loaded["binding"].get("cutover_certificate_sha256") != loaded["certificate"].get(
                "certificate_sha256"
            ):
                raise ReplayContextError("replay_context_v2_root_binding")
            v2_chains[root] = {
                "certificate_inputs": cert_inputs,
                "observed_now_ns": observed_now_ns,
            }
        elif root_kind == "handoff_v1":
            required = {
                "expected_genesis",
                "predecessor_attempts",
                "predecessor_slots",
                "expected_storage",
            }
            if type(inputs) is not dict or set(inputs) != required:
                raise ReplayContextError("replay_context_handoff_root_inputs")
            loaded = {
                name: _decode_input(name, _read_ref(writer, ref)) for name, ref in inputs.items()
            }
            if (
                type(loaded["expected_genesis"]) is not dict
                or type(loaded["predecessor_attempts"]) is not list
                or type(loaded["predecessor_slots"]) is not list
                or type(loaded["expected_storage"]) is not dict
            ):
                raise ReplayContextError("replay_context_handoff_root_input_type")
            try:
                raw_journal = writer.read_bytes(
                    f"{root}/{handoff.JOURNAL_NAME}", max_bytes=256 * 1024
                )
                handoff.verify_journal(
                    raw_journal,
                    successor_root=root,
                    expected_genesis=loaded["expected_genesis"],
                    predecessor_attempts=loaded["predecessor_attempts"],
                    predecessor_slots=loaded["predecessor_slots"],
                    observed_now_ns=observed_now_ns,
                )
            except (OSError, KeyError, TypeError, ValueError) as exc:
                raise ReplayContextError("replay_context_handoff_root_replay") from exc
            if loaded["expected_storage"] != loaded["expected_genesis"].get("storage"):
                raise ReplayContextError("replay_context_handoff_root_storage")
            handoffs[root] = loaded
        else:
            raise ReplayContextError("replay_context_root_kind_invalid")
    return refs, handoffs, v2_chains


def _validate_registered_roots(writer: DescriptorWriter, rows: Any) -> list[dict[str, Any]]:
    if type(rows) is not list:
        raise ReplayContextError("replay_context_registered_roots_shape")
    canonical_rows = _registered_roots(writer)
    if canonical_json(rows) != canonical_json(canonical_rows):
        raise ReplayContextError("replay_context_registered_roots_drift")
    return canonical_rows


def _certificate_inputs(writer: DescriptorWriter, refs: Any) -> dict[str, Any]:
    if type(refs) is not dict or set(refs) != set(INPUT_NAMES):
        raise ReplayContextError("replay_context_certificate_input_set")
    paths = [cast(dict[str, Any], ref).get("path") for ref in refs.values() if type(ref) is dict]
    if len(paths) != len(INPUT_NAMES) or len(set(paths)) != len(paths):
        raise ReplayContextError("replay_context_duplicate_input_path")
    loaded = {name: _decode_input(name, _read_ref(writer, refs[name])) for name in INPUT_NAMES}
    if (
        type(loaded["handoff_raw"]) is not bytes
        or type(loaded["successor_root"]) is not str
        or type(loaded["expected_handoff_genesis"]) is not dict
        or type(loaded["predecessor_attempts"]) is not list
        or type(loaded["predecessor_slots"]) is not list
        or type(loaded["predecessor_chain_records"]) is not list
        or type(loaded["policy"]) is not dict
        or any(not _hash(loaded[name]) for name in INPUT_NAMES[7:13])
        or type(loaded["observed_now_ns"]) is not int
        or loaded["observed_now_ns"] != HISTORICAL_CUTOVER_NS
    ):
        raise ReplayContextError("replay_context_certificate_input_type")
    return loaded


def build_replay_context(
    writer: DescriptorWriter,
    *,
    activation_id: str,
    successor_root: str,
    certificate_sha256: str,
    input_paths: Mapping[str, str],
) -> dict[str, Any]:
    """Build the one immutable control context from already-persisted inputs."""
    context_path(activation_id)
    if (
        not _hash(certificate_sha256)
        or type(successor_root) is not str
        or not successor_root.startswith(f"{ROOT_PREFIX}/")
        or successor_root.count("/") != 3
        or type(input_paths) is not dict
        or set(input_paths) != set(INPUT_NAMES)
    ):
        raise ReplayContextError("replay_context_builder_input_invalid")
    refs: dict[str, dict[str, str]] = {}
    for name in INPUT_NAMES:
        path = input_paths[name]
        if not _safe_path(path):
            raise ReplayContextError("replay_context_input_path_invalid")
        try:
            raw = writer.read_bytes(path, max_bytes=32 * 1024 * 1024)
        except Exception as exc:
            raise ReplayContextError("replay_context_input_unavailable") from exc
        refs[name] = {"path": path, "sha256": _sha(raw)}
    inputs = _certificate_inputs(writer, refs)
    if inputs["successor_root"] != successor_root:
        raise ReplayContextError("replay_context_successor_mismatch")
    try:
        certificate = chain_v2.build_cutover_certificate(**inputs)
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayContextError("replay_context_certificate_replay_failed") from exc
    if certificate.get("certificate_sha256") != certificate_sha256:
        raise ReplayContextError("replay_context_certificate_sha_mismatch")
    registered = _registered_roots(writer)
    if not any(row["root"] == successor_root for row in registered):
        raise ReplayContextError("replay_context_successor_not_registered")
    root_contexts, _handoffs, _v2_chains = _load_root_contexts(
        writer, registered, observed_now_ns=HISTORICAL_CUTOVER_NS
    )
    core = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "activation_id": activation_id,
        "context_path": context_path(activation_id),
        "successor_root": successor_root,
        "certificate_sha256": certificate_sha256,
        "certificate_inputs": refs,
        "registered_roots": registered,
        "registered_root_contexts": root_contexts,
    }
    return {**core, "context_sha256": _sha(canonical_json(core))}


def create_replay_context(
    writer: DescriptorWriter,
    *,
    activation_id: str,
    successor_root: str,
    certificate_sha256: str,
    input_paths: Mapping[str, str],
    revalidate_storage: Any,
) -> tuple[bytes, str]:
    """Publish one immutable context after revalidating the held data-root."""
    context = build_replay_context(
        writer,
        activation_id=activation_id,
        successor_root=successor_root,
        certificate_sha256=certificate_sha256,
        input_paths=input_paths,
    )
    raw = canonical_json(context)
    path = context_path(activation_id)
    try:
        revalidate_storage()
        writer.atomic_write(path, raw, create_only=True)
        revalidate_storage()
        observed = writer.read_bytes(path, max_bytes=2 * 1024 * 1024)
    except Exception as exc:
        raise ReplayContextError("replay_context_create_failed") from exc
    if observed != raw:
        raise ReplayContextError("replay_context_readback_mismatch")
    return raw, _sha(raw)


def verify_replay_context(
    writer: DescriptorWriter,
    *,
    context_raw: bytes,
    expected_activation_id: str,
    expected_context_sha256: str,
    expected_successor_root: str,
    expected_certificate_sha256: str,
    observed_now_ns: int = HISTORICAL_CUTOVER_NS,
) -> dict[str, Any]:
    """Re-read all pinned inputs, reject census drift, and recompute the certificate."""
    context = _object(context_raw, "replay_context_invalid")
    core = dict(context)
    claimed = core.pop("context_sha256", None)
    if (
        set(context)
        != {
            "schema",
            "schema_version",
            "activation_id",
            "context_path",
            "successor_root",
            "certificate_sha256",
            "certificate_inputs",
            "registered_roots",
            "registered_root_contexts",
            "context_sha256",
        }
        or context.get("schema") != SCHEMA
        or context.get("schema_version") != SCHEMA_VERSION
        or context.get("activation_id") != expected_activation_id
        or context.get("context_path") != context_path(expected_activation_id)
        or context.get("successor_root") != expected_successor_root
        or context.get("certificate_sha256") != expected_certificate_sha256
        or not _hash(expected_context_sha256)
        or _sha(canonical_json(core)) != claimed
        or _sha(context_raw) != expected_context_sha256
    ):
        raise ReplayContextError("replay_context_binding_mismatch")
    roots = _validate_registered_roots(writer, context.get("registered_roots"))
    if type(observed_now_ns) is not int or observed_now_ns < HISTORICAL_CUTOVER_NS:
        raise ReplayContextError("replay_context_observed_time_invalid")
    root_contexts, expected_handoffs, expected_v2_chains = _load_root_contexts(
        writer, roots, observed_now_ns=observed_now_ns
    )
    if canonical_json(context.get("registered_root_contexts")) != canonical_json(root_contexts):
        raise ReplayContextError("replay_context_registered_root_context_drift")
    inputs = _certificate_inputs(writer, context.get("certificate_inputs"))
    if inputs["successor_root"] != expected_successor_root:
        raise ReplayContextError("replay_context_successor_mismatch")
    try:
        certificate = chain_v2.build_cutover_certificate(**inputs)
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayContextError("replay_context_certificate_replay_failed") from exc
    if certificate.get("certificate_sha256") != expected_certificate_sha256:
        raise ReplayContextError("replay_context_certificate_sha_mismatch")
    # The full census replay is the final independent join across all roots and coordinator usage.
    try:
        inventory = inventory_v4.build_usage_inventory(
            writer,
            required_successor_root=expected_successor_root,
            expected_handoffs=expected_handoffs,
            expected_v2_chains=expected_v2_chains,
            observed_now_ns=observed_now_ns,
        )
    except Exception as exc:
        raise ReplayContextError("replay_context_full_inventory_replay") from exc
    capture_roots = (
        inventory.get("root_inventory", {}).get("collections", {}).get("capture-only-v2", {})
    )
    expected_ids = [row["root"].rsplit("/", 1)[-1] for row in roots]
    if capture_roots.get("children") != expected_ids:
        raise ReplayContextError("replay_context_inventory_root_set")
    return {**context, "replayed_certificate": certificate, "replayed_inventory": inventory}


__all__ = [
    "SCHEMA",
    "SCHEMA_VERSION",
    "ROOT_CONTEXT_SCHEMA",
    "ROOT_CONTEXT_DIRECTORY",
    "ReplayContextError",
    "build_replay_context",
    "context_path",
    "create_replay_context",
    "verify_replay_context",
]
