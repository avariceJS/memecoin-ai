"""Experimental encoders behind common action-conditioned distribution heads."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from memecoin_ai.causal import FEATURE_NAMES

KINDS = ("linear", "mlp", "gru", "tcn", "transformer")
INPUTS = 2 * len(FEATURE_NAMES) + 1
QUANTILES = (0.1, 0.5, 0.9)


@dataclass(frozen=True)
class ModelConfig:
    kind: str = "gru"
    width: int = 16
    context: int = 8

    def __post_init__(self) -> None:
        if self.kind not in KINDS or not 4 <= self.width <= 1024 or self.width % 4:
            raise ValueError("invalid model/width")
        if not 1 <= self.context <= 256:
            raise ValueError("invalid context")

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


class Brain(nn.Module):
    """Conditional predictor, not a portfolio/trading policy. No cross-sample normalization."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        w = config.width
        if config.kind not in ("linear", "mlp"):
            self.input = nn.Linear(INPUTS, w)
        if config.kind == "gru":
            self.gru = nn.GRU(w, w, batch_first=True)
        elif config.kind == "tcn":
            self.convs = nn.ModuleList([nn.Conv1d(w, w, 3, dilation=d) for d in (1, 2)])
        elif config.kind == "transformer":
            layer = nn.TransformerEncoderLayer(w, 4, w * 2, dropout=0.0, batch_first=True)
            self.transformer = nn.TransformerEncoder(layer, 1, enable_nested_tensor=False)
            self.position = nn.Parameter(torch.zeros(1, config.context, w))
        elif config.kind == "mlp":
            self.mlp = nn.Sequential(
                nn.Linear(INPUTS * config.context, w), nn.GELU(), nn.Linear(w, w)
            )
        head_inputs = INPUTS + 4 if config.kind == "linear" else w + 4
        self.head = nn.Linear(head_inputs, 6)

    def forward(self, history: Tensor, lengths: Tensor, action: Tensor) -> tuple[Tensor, Tensor]:
        batch = torch.arange(history.shape[0], device=history.device)
        positions = torch.arange(history.shape[1], device=history.device)
        mask = positions[None, :] >= lengths[:, None]
        # Padding is erased before any encoder, including MLP; valid prefix is causal.
        history = history.masked_fill(mask[:, :, None], 0)
        if self.config.kind == "linear":
            latent = history[batch, lengths - 1]
        elif self.config.kind == "mlp":
            latent = self.mlp(history.flatten(1))
        else:
            encoded = self.input(history)
            if self.config.kind == "gru":
                encoded, _ = self.gru(encoded)
            elif self.config.kind == "tcn":
                encoded = encoded.transpose(1, 2)
                for dilation, conv in zip((1, 2), self.convs, strict=True):
                    encoded = encoded + F.gelu(conv(F.pad(encoded, (2 * dilation, 0))))
                encoded = encoded.transpose(1, 2)
            else:
                causal = torch.ones(
                    history.shape[1], history.shape[1], device=history.device, dtype=torch.bool
                ).triu(1)
                encoded = self.transformer(
                    encoded + self.position, mask=causal, src_key_padding_mask=mask
                )
            latent = encoded[batch, lengths - 1]
        # Fixed unit conversion, not fitted on future action/label statistics.
        normalized_action = action / action.new_tensor([1.0, 0.001, 10.0, 60.0])
        output = self.head(torch.cat((latent, normalized_action), dim=-1))
        median = output[:, 0]
        quantiles = torch.stack(
            (median - F.softplus(output[:, 1]), median, median + F.softplus(output[:, 2])), dim=1
        )
        return quantiles, output[:, 3:]
