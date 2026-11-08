"""Tunable numbers for scoring and verification (not contracts; CONTRACTS.md points here)."""

# ---------------- hypothesis scoring (T16) ----------------
# Prior over H0..H5 when an alert asks "what is wrong with this satellite?".
PRIOR = {"H0": 1 / 6, "H1": 1 / 6, "H2": 1 / 6, "H3": 1 / 6, "H4": 1 / 6, "H5": 1 / 6}
# Base rate for satellites other than the one being scored, used by score_fleet: most are healthy.
# It makes "three satellites each lost array power on their own" much less likely than one
# fleet-wide beta season.
FLEET_BASE_RATE = {"H0": 0.80, "H1": 0.05, "H2": 0.05, "H3": 0.05, "H4": 0.05}
# "Nominal" (H0) includes ordinary unit-to-unit tolerance, so the planning-vs-truth mismatch is not
# mistaken for a fault. Symmetric grids; the same order as the verifier's Monte Carlo sigma (T12).
H0_CAPACITY_SCALE = (0.95, 1.0, 1.05)
H0_ARRAY_SCALE = (0.97, 1.0, 1.03)
# Grid sizes for the continuous hypothesis parameters.
GRID_ARRAY_LOSS = 21
GRID_EXTRA_BETA = 21
GRID_CAPACITY = 6
GRID_BIAS = 4
# A hypothesis is plausible if its posterior is at least this (the top one always is).
PLAUSIBLE_MIN = 0.05

# Residual sigma per telemetry channel, reading units. Each covers sensor noise and calibration bias
# plus the planning-vs-truth model mismatch, measured on nominal runs (see test_scoring.py).
#   mean:   pass-mean residual, prediction restarted at the start of the same pass
#   within: one sample's deviation from its pass mean (step responses during a diagnostic)
#   far:    residual at the next pass, predicted through the gap from the previous pass
SIGMA = {   # about 1.5x the nominal RMS measured over 6 seeds x beta 0/25/45 deg (H0, synthetic orbit)
    "soc":        {"mean": 0.004, "within": 0.005, "far": 0.015},
    "v_bus":      {"mean": 0.28, "within": 0.12, "far": 0.30},
    "i_bat":      {"mean": 0.15, "within": 0.15, "far": 0.12},
    "p_load":     {"mean": 0.9, "within": 0.3, "far": 0.9},
    "t_bat_c":    {"mean": 0.3, "within": 0.35, "far": 1.6},
    "i_array":    {"mean": 0.12, "within": 0.11, "far": 0.09},
}
# Near full charge the predicted charge current follows the taper, so an error in the restart SOC
# (reading noise + calibration bias) becomes a current error: d(i)/d(SOC) = I_max / (1 - taper_soc).
# Segments that restart above (taper_soc - 0.02) add that to the current sigma.
SOC_RESTART_SIGMA = 0.01
# Samples within a pass are correlated (model error); the within-pass term counts as this many
# independent samples per channel and pass.
WITHIN_EFFECTIVE_N = 6
# Next-pass residual uses the mean of its first samples only (before any commanded step).
FAR_SAMPLES = 6

# ---------------- limits (hard constraints) ----------------
SOC_MIN = 0.30
V_MIN = 13.6          # V, 3.4 V/cell; the satellite sheds loads at 13.2 V
T_MIN_C = 0.0         # no charging below 0 degC
T_MAX_C = 40.0

# ---------------- verification (T12) ----------------
MONTE_CARLO_N = 100
# 1-sigma Monte Carlo spread around each hypothesis, drawn per sample. They cover unit spread and the
# planning-vs-truth mismatch (planning capacity is ~4.5 % optimistic, array ~2.7 % pessimistic).
MC_SIGMA = {
    "array_efficiency": 0.03,   # relative
    "capacity": 0.05,           # relative
    "load_power": 0.03,         # relative
    "initial_soc": 0.02,        # absolute SOC
    "soc_sensor_bias": 0.01,    # absolute SOC reading bias (shifts the true starting SOC)
}
# A hypothesis passes when at least this fraction of its Monte Carlo samples meets every hard constraint.
PASS_RATE_REQUIRED = 0.95
# A kind "plan" is judged to the end of the scenario horizon (24 h from T0). A kind "diagnostic" is
# judged over its own window: first t_exec to the last step plus one dwell, plus one orbit.
DIAGNOSTIC_DWELL_S = 60.0
ORBIT_S = 5700.0
# trajectory() is for drawing only: one point per this many seconds.
TRAJECTORY_STEP_S = 60.0

# ---------------- detection (T34) ----------------
# A satellite is anomalous when the nominal hypothesis drops below this posterior.
DETECT_ANOMALY_P_H0 = 0.5
# Detection asks "is anything wrong?", so faults are rare a priori (diagnosis after an alert uses PRIOR).
DETECT_PRIOR = {"H0": 0.75, "H1": 0.05, "H2": 0.05, "H3": 0.05, "H4": 0.05, "H5": 0.05}
# Score a satellite twice per pass: after this many samples (first look) and when the pass ends.
DETECT_MIN_NEW_SAMPLES = 6
# Detection looks at the most recent passes only, which bounds its cost.
DETECT_WINDOW_PASSES = 4
# SOC projection for time to critical stops after this long.
DETECT_PROJECTION_S = 12 * 3600.0
# Severity from time to critical (SOC reaching SOC_MIN under the worst plausible hypothesis):
# critical if it comes within CRITICAL_TTC_S or before the next contact (+ margin), warning if it
# comes inside the horizon, watch if the satellite is anomalous but SOC never gets there.
CRITICAL_TTC_S = 3 * 3600.0
CONTACT_MARGIN_S = 600.0

# ---------------- conjunctions (T31) ----------------
CONJ_CRITICAL_S = 2 * 3600.0     # time to TCA below this: critical
CONJ_WARNING_S = 6 * 3600.0      # below this: warning; else watch

# ---------------- tier policy (T23) ----------------
# Tier 1 (auto-execute under a flight rule) needs at least this worst-case SOC margin above SOC_MIN.
TIER1_MARGIN = 0.20
# A diagnostic only has to pass verification over its own window (B's 21:58Z rule).
TIER1_MARGIN_DIAGNOSTIC = 0.0
