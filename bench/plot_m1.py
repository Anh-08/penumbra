"""Render the M1 scientific comparison from the saved JSON result."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt


ROOT = Path(__file__).resolve().parents[1]


def render_plot(result: dict, path: Path = ROOT / "bench/results/m1.png") -> Path:
    capacity = result["capacity_fade"]
    panels = result["array_degradation"]
    real_capacity = capacity["real_normalized_capacity"]
    cycles = range(1, len(real_capacity) + 1)

    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.6), constrained_layout=False)
    fig.patch.set_facecolor("white")
    fig.suptitle("M1: sanity check, not validation", fontsize=16, weight="bold", y=0.97)

    battery = axes[0]
    battery.plot(cycles, real_capacity, color="#1769aa", linewidth=1.8, label="Real PCoE cell")
    battery.axhline(
        capacity["simulated_remaining_fraction"],
        color="#d26818",
        linewidth=2,
        linestyle="--",
        label="Simulated fixed fade state",
    )
    battery.set_title("Battery capacity")
    battery.set_xlabel("Discharge cycle")
    battery.set_ylabel("Capacity / first discharge")
    battery.set_ylim(0.55, 1.08)
    battery.grid(alpha=0.2)
    battery.legend(loc="lower left", frameon=False, fontsize=9)
    battery.text(
        0.02, 0.05,
        f"Endpoint gap: {capacity['endpoint_abs_gap']:.3f}",
        transform=battery.transAxes,
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": "#dddddd"},
    )

    array = axes[1]
    real_failed = panels["real_failed_ratios"]
    real_healthy = panels["real_healthy_ratios"]
    values = [
        *real_failed,
        *real_healthy,
        panels["simulated_fault_ratio"],
        panels["simulated_nominal_ratio"],
    ]
    labels = [
        "Real: failed A", "Real: failed B", "Real: healthy A", "Real: healthy B",
        "Simulated: degraded", "Simulated: nominal",
    ]
    colors = ["#1769aa"] * 4 + ["#d26818"] * 2
    array.barh(labels[::-1], values[::-1], color=colors[::-1], height=0.64)
    array.set_title("+Y panel output relative to peers")
    array.set_xlabel("90th-percentile power ratio (real) / current ratio (simulated)")
    array.set_xlim(0, max(values) * 1.16)
    array.grid(axis="x", alpha=0.2)
    array.set_axisbelow(True)
    for index, value in enumerate(values[::-1]):
        array.text(value + max(values) * 0.015, index, f"{value:.3f}", va="center", fontsize=8)

    fig.text(
        0.02, 0.02,
        "Real: NASA PCoE (Saha & Goebel 2007); BIRDS EPS (Jara & Lepcha 2022). "
        "Simulated: Penumbra truth EPS. Different hardware and orientations limit comparison.",
        fontsize=8,
        color="#555555",
    )
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.19, top=0.86, wspace=0.37)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140, facecolor="white", metadata={"Title": result["label"]})
    plt.close(fig)
    return path


if __name__ == "__main__":
    with (ROOT / "bench/results/m1.json").open(encoding="utf-8") as source:
        render_plot(json.load(source))
