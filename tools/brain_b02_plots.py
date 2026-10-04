"""Static B02 research figures from sealed results, no model selection."""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "work/brain-b02/plot-deps"))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def main():
    curves = json.loads((BASE / "work/brain-b02/curves02/result.json").read_text())
    coverage = json.loads((BASE / "work/brain-b02/snapshot01/coverage.json").read_text())
    folder = BASE / "docs/evidence"
    colors = dict(empirical="#444444", linear="#ce7b00", gru="#1674ba")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for i, ax in enumerate(axes):
        entries = [e for e in curves["experiments"] if e["fold"] == i]
        for candidate, color in colors.items():
            ax.plot(
                [e["counts"]["train"] for e in entries],
                [e["metrics"]["evaluation"][candidate]["pool_macro_pinball"] for e in entries],
                marker="o",
                label=candidate,
                color=color,
            )
        ax.set(
            title=f"Fold {i + 1}: неизменный evaluation",
            xlabel="Train decisions",
            ylabel="Pool-macro pinball (меньше — лучше)",
            yscale="log",
        )
        ax.grid(alpha=0.2)
        ax.legend()
    fig.suptitle("B02: больше истории; те же linear / GRU, ensemble seeds 7/19")
    fig.text(
        0.5,
        0.005,
        (
            "Exploratory, train одного пула. Epochs=10; "
            "больше данных также даёт больше optimizer steps."
        ),
        ha="center",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(folder / "brain-b02-learning-curves.svg")
    fig.savefig(BASE / "work/brain-b02/learning-curves.png", dpi=150)
    plt.close(fig)
    before = coverage["before"]["per_pool"]
    after = coverage["after"]["per_pool"]
    fig, ax = plt.subplots(figsize=(10, 5.6))
    y = list(range(len(before)))
    ax.barh(
        [n - 0.18 for n in y],
        [p["observed_fraction"] * 100 for p in before],
        height=0.35,
        label="До B02 history",
        color="#b5b5b5",
    )
    ax.barh(
        [n + 0.18 for n in y],
        [p["observed_fraction"] * 100 for p in after],
        height=0.35,
        label="После +505 bars",
        color="#1674ba",
    )
    ax.set_yticks(
        y, [p["pool"][:8] + "…  возрастная группа " + str(p["age_bucket"]) for p in after]
    )
    ax.set(
        xlim=(0, 105),
        xlabel="Наблюдаемые calendar slots, %",
        title="B02 coverage: до 10 суток или возраста пула; 6/12 покрыты полностью",
    )
    ax.grid(axis="x", alpha=0.2)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(folder / "brain-b02-coverage.svg")
    fig.savefig(BASE / "work/brain-b02/coverage.png", dpi=150)
    for name in ("brain-b02-coverage.svg", "brain-b02-learning-curves.svg"):
        path = folder / name
        path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
    print("matplotlib", matplotlib.__version__)


if __name__ == "__main__":
    main()
