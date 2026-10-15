"""Research inference from a pinned, standalone artifact; no trainer/store/runtime required."""

import errno
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import torch

from .learning import validate_statistics
from .models import construct, input_tensor
from .schema import feature_names
from .storage import PIN, digest, file_hash, safe_path
from .target_transforms import implementation_pin, inverse_predictions


class Predictor:
    def __init__(self, artifact, pin, *, device="cpu"):
        artifact = safe_path(artifact)
        self.pin, self.device = pin, device
        if device not in {"cpu", "cuda"}:
            raise ValueError("experience_prediction_device")
        with _verified_artifact_members(artifact, pin) as members:
            self.spec = _read_json_member(members, "model.json")
            self.stats = _read_json_member(members, "statistics.json")
            self.report = _read_json_member(members, "report.json")
            diversity = None
            if "diversity.json" in members and "diversity-manifest.json" in members:
                diversity_report = members["diversity.json"]
                diversity_report.seek(0)
                diversity_hash = hashlib.file_digest(diversity_report, "sha256").hexdigest()
                diversity_report.seek(0)
                diversity = (
                    _read_json_member(members, "diversity.json"),
                    _read_json_member(members, "diversity-manifest.json"),
                    diversity_hash,
                )
            weights_stream = members.pop("weights.pt")
        try:
            _finish_predictor(self, device, weights_stream, diversity)
        finally:
            weights_stream.close()

    def predict(self, frames):
        schema = self.spec["schema"]
        tensor = input_tensor(
            frames,
            self.stats,
            self.device,
            context=schema["context"],
            features=len(feature_names(schema)),
        )
        with torch.no_grad():
            raw_prediction = self.model(tensor)
            if not torch.isfinite(raw_prediction).all():
                raise ValueError("experience_prediction_nonfinite_raw_output")
            prediction = inverse_predictions(raw_prediction.cpu().numpy(), self.stats)
        if not np.isfinite(prediction).all():
            raise ValueError("experience_prediction_nonfinite_output")
        return prediction


def _finish_predictor(self, device, weights_stream, diversity):
    validate_statistics(self.stats, self.spec["schema"])
    report_binding = self.report.get("binding", {})
    self.inference_entry_bound = "inference_entry_sha" in report_binding
    if (
        self.inference_entry_bound
        and report_binding["inference_entry_sha"] != inference_entry_sha()
    ):
        raise ValueError("experience_prediction_entry_binding")
    self.inference_transform_bound = "inference_transform" in report_binding
    if (
        self.inference_transform_bound
        and report_binding["inference_transform"] != implementation_pin()
    ):
        raise ValueError("experience_prediction_transform_binding")
    if (
        self.report["status"] != "completed"
        or self.report["production_eligible"] is not False
        or self.report["binding"]["model"] != self.spec
        or self.report["statistics"] != self.stats
        or self.report["binding"]["statistics"] != digest(self.stats)
    ):
        raise ValueError("experience_prediction_binding")
    if "diversity" in self.report["binding"]:
        from memecoin_population.diversity import OBJECTIVE

        if diversity is None:
            raise ValueError("experience_prediction_diversity_inventory")
        audit, manifest, audit_hash = diversity
        exposure = self.report["exposure"]
        if (
            digest(manifest) != self.report["binding"]["diversity"]
            or manifest["report.json"] != audit_hash
            or audit["binding"]["snapshot"] != self.report["binding"]["snapshot"]
            or audit["binding"]["split"] != self.report["binding"]["plan"]
            or audit["representation"] != digest(self.spec["schema"])
            or exposure["binding"]["audit"] != self.report["binding"]["diversity"]
            or exposure["binding"]["objective"] != OBJECTIVE
            or exposure["binding"]["steps"] != self.report["steps"]
            or exposure["binding"]["rows_seen"] != self.report["rows_seen"]
            or not audit["training_allowed"]
        ):
            raise ValueError("experience_prediction_diversity_binding")
    self.model = construct(self.spec).to(device)
    weights_stream.seek(0)
    weights = torch.load(weights_stream, map_location="cpu", weights_only=True)
    self.model.load_state_dict(weights, strict=True)
    if any(
        not torch.isfinite(value).all()
        for value in (*self.model.parameters(), *self.model.buffers())
    ):
        raise ValueError("experience_prediction_nonfinite_state")
    self.model.eval()


def inference_entry_sha():
    """Return the current standalone prediction entrypoint source digest."""
    return file_hash(Path(__file__))


@contextmanager
def _verified_artifact_members(artifact, pin):
    """Capture each regular member through a held directory fd and verify captured bytes."""
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("experience_manifest_pin")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(artifact, flags)
    members = {}
    try:
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            raise ValueError("experience_artifact_directory_type")
        inventory = set(os.listdir(directory_fd))
        manifest_fd = _open_regular_member(directory_fd, "manifest.json")
        try:
            manifest_raw = _read_descriptor(manifest_fd, limit=16 * 1024 * 1024)
        finally:
            os.close(manifest_fd)
        if hashlib.sha256(manifest_raw).hexdigest() != pin:
            raise ValueError("experience_manifest_pin")
        expected = json.loads(manifest_raw)
        if (
            not isinstance(expected, dict)
            or any(
                not isinstance(name, str)
                or Path(name).name != name
                or name in {"", ".", "..", "manifest.json"}
                or not isinstance(checksum, str)
                or not PIN.fullmatch(checksum)
                for name, checksum in expected.items()
            )
            or inventory - {"manifest.json"} != set(expected)
        ):
            raise ValueError("experience_inventory")
        if not {"model.json", "statistics.json", "report.json", "weights.pt"}.issubset(expected):
            raise ValueError("experience_inventory")
        captured_names = {
            "model.json",
            "statistics.json",
            "report.json",
            "weights.pt",
            "diversity.json",
            "diversity-manifest.json",
        }
        for name, checksum in sorted(expected.items()):
            descriptor = _open_regular_member(directory_fd, name)
            captured = (
                tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
                if name in captured_names
                else None
            )
            try:
                hasher = hashlib.sha256()
                while block := os.read(descriptor, 1024 * 1024):
                    hasher.update(block)
                    if captured is not None:
                        captured.write(block)
                if hasher.hexdigest() != checksum:
                    raise ValueError("experience_checksum")
                if captured is not None:
                    captured.seek(0)
                    members[name] = captured
            except BaseException:
                if captured is not None:
                    captured.close()
                raise
            finally:
                os.close(descriptor)
        if set(os.listdir(directory_fd)) != inventory:
            raise ValueError("experience_inventory")
        yield members
    finally:
        for captured in members.values():
            captured.close()
        os.close(directory_fd)


def _open_regular_member(directory_fd, name):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("experience_symlink") from None
        raise ValueError("experience_artifact_member_open") from None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("experience_artifact_member_type")
    return descriptor


def _read_descriptor(descriptor, *, limit):
    chunks = []
    size = 0
    while block := os.read(descriptor, 1024 * 1024):
        size += len(block)
        if size > limit:
            raise ValueError("experience_manifest_size")
        chunks.append(block)
    return b"".join(chunks)


def _read_json_member(members, name):
    stream = members[name]
    stream.seek(0)
    return json.load(stream)
