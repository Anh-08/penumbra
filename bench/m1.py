"""M1 data-to-model sanity check; this is not model validation."""

import json
from pathlib import Path

import numpy as np
from openpyxl import load_workbook
from scipy.io import loadmat

from backend.eps.faults import FaultSpec, build_modifiers
from backend.eps.truth import Environment, run


ROOT = Path(__file__).resolve().parents[1]
FACES = ("py", "px", "mz", "mx", "pz")


def _capacity_curve(path: Path) -> list[float]:
    cell = loadmat(path, simplify_cells=True)[path.stem]
    capacities = [
        float(cycle["data"]["Capacity"])
        for cycle in cell["cycle"]
        if cycle["type"] == "discharge"
    ]
    if not capacities or capacities[0] <= 0:
        raise ValueError("PCoE discharge capacity is missing")
    return [float(value / capacities[0]) for value in capacities]


def _panel_power_ratio(path: Path) -> float:
    """Use the first normal telemetry sheet and engineering-unit power columns."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = next(sheet for sheet in workbook.worksheets if not sheet.title.startswith("Test"))
        rows = sheet.iter_rows(values_only=True)
        columns = {name: index for index, name in enumerate(next(rows))}
        power = {face: [] for face in FACES}
        for row in rows:
            timestamp = row[0]
            if not isinstance(timestamp, (int, float)) or not np.isfinite(timestamp):
                continue
            for face in FACES:
                voltage_mv = row[columns[f"V{face} (mV)"]]
                current_ma = row[columns[f"I{face} (mA)"]]
                if (
                    isinstance(voltage_mv, (int, float))
                    and isinstance(current_ma, (int, float))
                    and np.isfinite(voltage_mv)
                    and np.isfinite(current_ma)
                    and 0 <= voltage_mv <= 10_000
                    and 0 <= current_ma <= 2_000
                ):
                    power[face].append(voltage_mv * current_ma * 1e-6)
        if any(not values for values in power.values()):
            raise ValueError("BIRDS panel power data is missing")
        p90 = {face: float(np.percentile(values, 90)) for face, values in power.items()}
        reference = float(np.median([p90[face] for face in FACES if face != "py"]))
        if reference <= 0:
            raise ValueError("BIRDS reference panel power is zero")
        return p90["py"] / reference
    finally:
        workbook.close()


def _simulated_signatures() -> tuple[float, float, float]:
    """Measure the existing EPS fault effects, rather than inventing a proxy."""
    t = 1_794_074_400.0 + np.arange(60, dtype=float) * 10.0
    shape = (1, len(t))
    env = Environment(
        t=t,
        sat_ids=("SAT-01",),
        illum=np.ones(shape),
        in_contact=np.ones(shape, dtype=bool),
        beta_deg=np.zeros(shape),
    )
    capacity_fault = FaultSpec(
        kind="capacity_fade", target="SAT-01", params={"remaining": 0.7}, onset_t=float(t[0])
    )
    capacity_mods = build_modifiers([capacity_fault], env)
    remaining = float(capacity_mods.capacity_factor[0, -1])

    array_fault = FaultSpec(
        kind="array_degradation",
        target="SAT-01",
        params={"loss": 0.6, "panel": "PY"},
        onset_t=float(t[0]),
    )
    nominal = run(env, seed=0)
    degraded = run(env, mods=build_modifiers([array_fault], env), seed=0)
    nominal_ratio = float(np.mean(nominal.i_array[0, 0]) / np.mean(nominal.i_array[0, 1]))
    degraded_ratio = float(np.mean(degraded.i_array[0, 0]) / np.mean(degraded.i_array[0, 1]))
    return remaining, nominal_ratio, degraded_ratio


def build_result(data_dir: Path = ROOT / "data") -> dict:
    """Build one reproducible, anonymized comparison from cached inputs."""
    curve = _capacity_curve(data_dir / "pcoe/B0005.mat")
    remaining, nominal_ratio, degraded_ratio = _simulated_signatures()
    result = {
        "label": "sanity check, not validation",
        "inputs": {
            "real": [
                "NASA PCoE battery discharge capacity, B0005 (Saha and Goebel 2007)",
                "BIRDS 1U CubeSat on-orbit EPS telemetry (Jara and Lepcha 2022, doi:10.17632/8kp25ycf63.1)",
            ],
            "simulated": [
                "Penumbra truth EPS capacity_fade, remaining=0.7",
                "Penumbra truth EPS array_degradation, PY panel loss=0.6",
            ],
        },
        "capacity_fade": {
            "discharge_cycles": len(curve),
            "real_normalized_capacity": curve,
            "simulated_remaining_fraction": remaining,
            "endpoint_abs_gap": abs(curve[-1] - remaining),
            "metric_unit": "fraction of first-discharge capacity",
            "method": "Real capacity is normalized to the first discharge. The simulated fault is a fixed capacity state, not an aging forecast.",
        },
        "array_degradation": {
            "workbooks_used": 0,
            "real_failed_ratios": [],
            "real_healthy_ratios": [],
            "simulated_nominal_ratio": nominal_ratio,
            "simulated_fault_ratio": degraded_ratio,
            "simulated_panel_loss": 0.6,
            "method": "Each real ratio is the first normal sheet's +Y 90th-percentile panel power divided by the median 90th-percentile power of the other four faces. The two lowest ratios are labeled observed failures. The simulated ratio is PY/MY array current in full sun.",
        },
        "limitations": [
            "PCoE cells are laboratory 18650 batteries, not flight hardware; the EPS fault is a static state.",
            "BIRDS face orientations and power electronics differ from Penumbra's two-wing model; ratios are qualitative.",
            "BIRDS sheets provide elapsed time only, so no absolute UTC alignment was attempted.",
        ],
    }
    bird_files = sorted((data_dir / "birds").glob("*.xlsx"))
    if bird_files:
        ratios = sorted(_panel_power_ratio(path) for path in bird_files)
        result["array_degradation"]["workbooks_used"] = len(ratios)
        result["array_degradation"]["real_failed_ratios"] = ratios[:2]
        result["array_degradation"]["real_healthy_ratios"] = ratios[2:]
        result["array_degradation"]["failed_vs_simulated_abs_gap"] = abs(
            float(np.median(ratios[:2])) - degraded_ratio
        )
    else:
        result["array_degradation"]["status"] = "BIRDS input absent"
    return result


def write_result(result: dict, path: Path = ROOT / "bench/results/m1.json") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return path


if __name__ == "__main__":
    write_result(build_result())
