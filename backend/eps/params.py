"""Every EPS parameter, with its source. "assumed: ..." means engineering judgment, not data.

PCoE values come from backend/eps/fit_pcoe.py run on data/pcoe/ (NASA Ames PCoE Li-ion aging data):
  B. Saha and K. Goebel (2007), "Battery Data Set", NASA Prognostics Data Repository,
  NASA Ames Research Center, Moffett Field, CA.
These are laboratory 18650 cells, not flight hardware; the pack below is assumed to be built from them.
test_params.py re-runs the fit when data/pcoe/ is present and checks the numbers here still match.

Units: SI unless the name says otherwise (_c = degC, _wh, _ah, _deg). Power in W, current in A.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PCOE = "fitted: NASA PCoE battery data, Saha & Goebel 2007 (backend/eps/fit_pcoe.py)"


@dataclass(frozen=True)
class Param:
    value: Any
    unit: str
    source: str


TRUTH: dict[str, Param] = {
    # ---- solar array: two sun-tracking wings (1-axis about the orbit normal) ----
    "panels": Param(("PY", "MY"), "-", "assumed: two deployable wings, +Y and -Y"),
    "solar_flux": Param(1361.0, "W/m^2", "Kopp & Lean 2011, total solar irradiance at 1 AU (held constant)"),
    "panel_area": Param(0.13, "m^2", "assumed: per wing, sized for ~25 % margin at beta 30 deg"),
    "cell_efficiency": Param(0.295, "-", "assumed: triple-junction GaAs cell, BOL"),
    "packing_factor": Param(0.90, "-", "assumed: cell area / panel area"),
    "regulator_eff": Param(0.95, "-", "assumed: MPPT regulator efficiency"),
    # Incidence: 1-axis tracking about the orbit normal leaves cos(beta) as the incidence factor.
    "incidence_model": Param("cos_beta", "-", "assumed: 1-axis tracking wings, incidence factor cos(beta)"),
    # ---- battery: 4s3p pack of PCoE-type 18650 cells ----
    "n_series": Param(4, "-", "assumed: 4s3p pack"),
    "n_parallel": Param(3, "-", "assumed: 4s3p pack"),
    "cell_capacity_ah": Param(1.9095, "Ah", PCOE + ", mean first-discharge capacity of B0005/6/7/18"),
    "soc_grid": Param(
        (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5,
         0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0), "-", PCOE),
    "ocv_cell_v": Param(
        (2.8871, 3.4747, 3.6082, 3.6582, 3.6879, 3.7101, 3.7288, 3.7455, 3.7625, 3.7819, 3.8047,
         3.8304, 3.8583, 3.8883, 3.9202, 3.9545, 3.991, 4.0315, 4.0756, 4.1302, 4.2379),
        "V", PCOE + ", pseudo-OCV = V + |I| R0 on 2 A discharges, mean of 4 cells"),
    "r0_cell_ohm": Param(0.1286, "ohm", PCOE + ", Re + Rct of the first EIS sweep, mean of 4 cells"),
    "r0_cell_eol_ohm": Param(0.1535, "ohm", PCOE + ", Re + Rct of the last EIS sweep (30 % fade)"),
    "coulombic_eff": Param(0.9822, "-", PCOE + ", discharge Ah / preceding charge Ah, median of first 20"),
    "fade_per_cycle": Param(0.002107, "1/cycle", PCOE + ", linear capacity fade, fraction of BOL per cycle"),
    "r_temp_b": Param(3000.0, "K", "assumed: Arrhenius factor for R0 vs temperature, R = R25 exp(B (1/T - 1/T25))"),
    "charge_min_temp_c": Param(0.0, "degC", "assumed: no charging below 0 degC (standard Li-ion rule)"),
    "charge_c_rate": Param(0.5, "1/h", "assumed: max charge current 0.5 C"),
    "taper_soc": Param(0.90, "-", "assumed: charge current tapers linearly to 0 between this SOC and 1"),
    "discharge_c_rate": Param(2.0, "1/h", "assumed: max discharge current 2 C"),
    "v_shed_cell": Param(3.30, "V", "assumed: undervoltage load-shed threshold per cell (13.2 V pack)"),
    # ---- loads (W when on) ----
    "load_power": Param(
        {"BUS": 7.0, "COMMS": 6.0, "ADCS": 6.0, "PAYLOAD": 30.0, "HEATER": 8.0, "AUX": 10.0}, "W",
        "assumed: smallsat budget; PAYLOAD scales with duty, HEATER is thermostat-controlled"),
    "initial_on": Param({"PAYLOAD": True, "HEATER": True, "AUX": False}, "-",
                        "assumed: nominal ops; essential loads are always on"),
    "initial_payload_duty": Param(0.5, "-", "assumed"),
    "initial_heater_setpoint_c": Param(5.0, "degC", "assumed"),
    "heater_hysteresis_c": Param(1.0, "degC", "assumed: thermostat band (+/- half of this)"),
    "safe_heater_setpoint_c": Param(0.0, "degC", "assumed: survival setpoint in SAFE_MODE"),
    # ---- one-node battery thermal model ----
    "thermal_capacity": Param(2000.0, "J/K", "assumed: pack plus mounting plate"),
    "thermal_conductance": Param(0.4, "W/K", "assumed: pack to structure"),
    "sink_sun_c": Param(15.0, "degC", "assumed: structure temperature in sunlight"),
    "sink_eclipse_c": Param(-5.0, "degC", "assumed: structure temperature in eclipse"),
    # ---- initial state ----
    "initial_soc": Param(0.90, "-", "assumed"),
    "initial_t_bat_c": Param(15.0, "degC", "assumed"),
    # ---- unit-to-unit spread of the truth satellites (seeded, 1 sigma) ----
    "spread_capacity": Param(0.01, "-", "assumed: cell lot capacity spread"),
    "spread_array": Param(0.01, "-", "assumed: array output spread"),
    "spread_load": Param(0.02, "-", "assumed: load power spread"),
    # ---- sensors: (noise sigma, quantization LSB, calibration-bias sigma) ----
    "sensors": Param(
        {
            "soc": (0.003, 0.001, 0.005),
            "v_bus": (0.02, 0.01, 0.01),
            "i_bat": (0.02, 0.005, 0.005),
            "i_array": (0.01, 0.005, 0.005),
            "p_load": (0.2, 0.1, 0.1),
            "t_bat_c": (0.2, 0.1, 0.3),
        },
        "channel units", "assumed: typical 12-bit housekeeping ADC"),
}


class EpsParams:
    """Parameter values by attribute: EpsParams().panel_area. Overrides: EpsParams(panel_area=0.1)."""

    def __init__(self, table: dict[str, Param] = TRUTH, **overrides: Any) -> None:
        unknown = set(overrides) - set(table)
        if unknown:
            raise KeyError(f"unknown EPS parameter(s): {sorted(unknown)}")
        self._values = {k: p.value for k, p in table.items()} | overrides
        self._table = table

    def __getattr__(self, name: str) -> Any:
        try:
            return self.__dict__["_values"][name]
        except KeyError:
            raise AttributeError(name) from None

    def source(self, name: str) -> str:
        return self._table[name].source

    # derived values
    @property
    def capacity_ah(self) -> float:
        return self.cell_capacity_ah * self.n_parallel

    @property
    def r0_ohm(self) -> float:
        return self.r0_cell_ohm * self.n_series / self.n_parallel

    @property
    def v_shed(self) -> float:
        return self.v_shed_cell * self.n_series
