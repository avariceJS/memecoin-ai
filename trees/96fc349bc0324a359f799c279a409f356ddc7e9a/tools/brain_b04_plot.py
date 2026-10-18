"""Plot fixed B04 paired normalization results; no selection or training."""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "work/brain-b02/plot-deps"))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

results = json.loads((BASE / "work/brain-b04/ablation01/result.json").read_text())
fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
labels = [f"Fold {i + 1}: {kind}" for i in range(2) for kind in ["linear", "GRU"]]
for ax, role in zip(axes, ["validation", "evaluation"], strict=True):
    values = []
    intervals = []
    for e in results["experiments"]:
        for kind in ["linear", "gru"]:
            m = e["metrics"][role]
            values.append(
                100
                * (
                    1
                    - m[kind + "_winsor"]["pool_macro_pinball"]
                    / m[kind + "_control"]["pool_macro_pinball"]
                )
            )
            intervals.append(e["paired"][role][kind]["overall"]["ci"]["winsor"])
    y = list(range(4))
    ax.barh(y, values, color=["#397da8" if v >= 0 else "#b65a4d" for v in values], alpha=0.8)
    if role == "evaluation":
        for i, interval in enumerate(intervals):
            ax.plot([v * 100 for v in interval], [i, i], color="black", linewidth=1.5)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set(title=role, xlabel="Улучшение pinball к control, % (больше — лучше)")
    ax.grid(axis="x", alpha=0.2)
fig.suptitle("B04: train-only winsorization; те же модели, folds и seeds")
fig.text(
    0.5,
    0.01,
    "Evaluation уже exposed; линии — exploratory 95% paired 1h-bootstrap CI. Promotion нет.",
    ha="center",
    fontsize=9,
)
fig.tight_layout(rect=(0, 0.05, 1, 0.95))
fig.savefig(BASE / "work/brain-b04/normalization.png", dpi=140)
p = BASE / "docs/evidence/brain-b04-normalization.svg"
fig.savefig(p)
p.write_text("\n".join(line.rstrip() for line in p.read_text().splitlines()) + "\n")
