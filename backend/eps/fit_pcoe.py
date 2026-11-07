"""Fit battery parameters from the NASA PCoE Li-ion aging data in data/pcoe/.

Source: B. Saha and K. Goebel (2007), "Battery Data Set", NASA Prognostics Data Repository,
NASA Ames Research Center, Moffett Field, CA. Cells B0005, B0006, B0007, B0018 (18650, 2 Ah rated, 24 degC).

What is fitted (per cell, then averaged):
- OCV(SOC): pseudo-OCV from the first 3 constant-current 2 A discharges, V_oc = V_measured + |I| * R0,
  with R0 = Re + Rct from the first impedance sweep. SOC = 1 - Ah discharged / Capacity of that cycle.
  Only loaded samples (|I| > 1.5 A) are used. This is an approximation of a true rest OCV (no hysteresis model).
- R0: Re + Rct of the first and last impedance sweep (BOL and EOL).
- Coulombic efficiency: discharge Ah / Ah of the charge right before it (median over the first 20 pairs).
- Capacity fade: least-squares slope of Capacity vs discharge-cycle index, as a fraction of the first capacity.

Run: python -m backend.eps.fit_pcoe [data/pcoe]  -> prints the values that backend/eps/params.py records.
Needs scipy (only this script; the simulator itself does not read .mat files).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

CELLS = ("B0005", "B0006", "B0007", "B0018")
SOC_GRID = np.round(np.linspace(0.0, 1.0, 21), 2)
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "pcoe"


def available(data_dir: Path = DEFAULT_DIR) -> bool:
    return all((Path(data_dir) / f"{c}.mat").exists() for c in CELLS)


def _load(data_dir: Path, cell: str) -> list[dict]:
    import scipy.io as sio

    return sio.loadmat(str(Path(data_dir) / f"{cell}.mat"), simplify_cells=True)[cell]["cycle"]


def _real(x) -> float:
    return float(np.real(x))


def fit_cell(cycles: list[dict]) -> dict:
    dis = [c["data"] for c in cycles if c["type"] == "discharge"]
    imp = [c["data"] for c in cycles if c["type"] == "impedance"]
    r0_bol = _real(imp[0]["Re"]) + _real(imp[0]["Rct"])
    r0_eol = _real(imp[-1]["Re"]) + _real(imp[-1]["Rct"])

    curves = []
    for d in dis[:3]:
        v, i, t, cap = (np.asarray(d[k], float) for k in ("Voltage_measured", "Current_measured", "Time", "Capacity"))
        q = np.concatenate([[0.0], np.cumsum(-0.5 * (i[1:] + i[:-1]) * np.diff(t))]) / 3600.0  # Ah out
        loaded = i < -1.5
        soc = 1.0 - q[loaded] / float(cap)
        ocv = v[loaded] + np.abs(i[loaded]) * r0_bol
        order = np.argsort(soc)
        curves.append(np.interp(SOC_GRID, soc[order], ocv[order]))
    ocv = np.mean(curves, axis=0)

    # coulombic efficiency: pair each discharge with the charge right before it
    effs, last_charge_ah = [], None
    for c in cycles:
        d = c["data"]
        if c["type"] == "charge":
            i, t = np.asarray(d["Current_measured"], float), np.asarray(d["Time"], float)
            last_charge_ah = float(np.trapezoid(np.clip(i, 0.0, None), t)) / 3600.0
        elif c["type"] == "discharge" and last_charge_ah:
            effs.append(float(d["Capacity"]) / last_charge_ah)
            last_charge_ah = None
    eta = float(np.median([e for e in effs[:20] if 0.8 < e <= 1.0]))

    caps = np.array([float(d["Capacity"]) for d in dis])
    slope = np.polyfit(np.arange(len(caps)), caps, 1)[0]
    return {
        "ocv": ocv, "r0_bol": r0_bol, "r0_eol": r0_eol, "eta": eta,
        "fade_per_cycle": float(-slope / caps[0]), "cap_first": float(caps[0]), "cap_last": float(caps[-1]),
    }


def fit(data_dir: Path = DEFAULT_DIR) -> dict:
    cells = {c: fit_cell(_load(data_dir, c)) for c in CELLS}
    ocv = np.mean([v["ocv"] for v in cells.values()], axis=0)
    # OCV must be monotonic in SOC for the simulator; enforce it with a running max.
    ocv = np.maximum.accumulate(ocv)
    mean = lambda k: float(np.mean([v[k] for v in cells.values()]))  # noqa: E731
    return {
        "soc_grid": SOC_GRID.tolist(),
        "ocv_cell_v": [round(float(x), 4) for x in ocv],
        "r0_cell_bol_ohm": round(mean("r0_bol"), 4),
        "r0_cell_eol_ohm": round(mean("r0_eol"), 4),
        "coulombic_eff": round(mean("eta"), 4),
        "fade_per_cycle": round(mean("fade_per_cycle"), 6),
        "cap_first_ah": round(mean("cap_first"), 4),
        "cap_last_ah": round(mean("cap_last"), 4),
        "per_cell": {c: {k: (round(v, 6) if isinstance(v, float) else None) for k, v in d.items() if k != "ocv"}
                     for c, d in cells.items()},
    }


if __name__ == "__main__":
    import json

    out = fit(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR)
    print(json.dumps(out, indent=1))
