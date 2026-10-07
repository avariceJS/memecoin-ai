"""Research inference from a pinned, standalone artifact; no trainer/store/runtime required."""

import json

import torch

from .models import construct, input_tensor
from .schema import feature_names
from .storage import digest, safe_path, verify_files


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
        if (
            self.report["status"] != "completed"
            or self.report["production_eligible"] is not False
            or self.report["binding"]["model"] != self.spec
            or self.report["statistics"] != self.stats
            or self.report["binding"]["statistics"] != digest(self.stats)
        ):
            raise ValueError("experience_prediction_binding")
        self.model = construct(self.spec).to(device)
        self.model.load_state_dict(
            torch.load(artifact / "weights.pt", map_location="cpu", weights_only=True), strict=True
        )
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
            return self.model(tensor).cpu().numpy()
