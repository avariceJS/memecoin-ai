"""Research inference from a pinned, standalone artifact; no trainer/store/runtime required."""

import json

import numpy as np
import torch

from .learning import inverse_predictions, validate_statistics
from .models import construct, input_tensor
from .schema import feature_names
from .storage import digest, file_hash, safe_path, verify_files


class Predictor:
    def __init__(self, artifact, pin, *, device="cpu"):
        artifact = safe_path(artifact)
        verify_files(artifact, pin)
        self.pin, self.device = pin, device
        if device not in {"cpu", "cuda"}:
            raise ValueError("experience_prediction_device")
        self.spec = json.loads((artifact / "model.json").read_text())
        self.stats = json.loads((artifact / "statistics.json").read_text())
        self.report = json.loads((artifact / "report.json").read_text())
        validate_statistics(self.stats, self.spec["schema"])
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

            audit = json.loads((artifact / "diversity.json").read_text())
            manifest = json.loads((artifact / "diversity-manifest.json").read_text())
            exposure = self.report["exposure"]
            if (
                digest(manifest) != self.report["binding"]["diversity"]
                or manifest["report.json"] != file_hash(artifact / "diversity.json")
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
        self.model.load_state_dict(
            torch.load(artifact / "weights.pt", map_location="cpu", weights_only=True), strict=True
        )
        if any(
            not torch.isfinite(value).all()
            for value in (*self.model.parameters(), *self.model.buffers())
        ):
            raise ValueError("experience_prediction_nonfinite_state")
        self.model.eval()

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
