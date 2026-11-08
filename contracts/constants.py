"""Fixed names and ids from CONTRACTS.md. Tunable numbers live in backend/verify/constants.py, not here."""
from __future__ import annotations

# Time
DT = 10.0  # sim grid step, seconds
HORIZON_S = 24 * 3600.0  # scenario horizon from T0, seconds

# Ids
SAT_IDS: tuple[str, ...] = tuple(f"SAT-{i:02d}" for i in range(1, 25))
STATION_IDS: tuple[str, ...] = ("SVALBARD", "TROLL", "FAIRBANKS")
MIN_ELEVATION_DEG = 10.0  # contact windows

SAT_ID_PATTERN = r"^SAT-(0[1-9]|1[0-9]|2[0-4])$"
PLAN_ID_PATTERN = r"^P-\d+$"
SIM_ID_PATTERN = r"^S-\d+$"
APPROVAL_ID_PATTERN = r"^AP-\d+$"
ALERT_ID_PATTERN = r"^AL-\d+$"
FLIGHT_RULE_PATTERN = r"^FR-\d+$"

# EPS loads
ESSENTIAL_LOADS: tuple[str, ...] = ("BUS", "COMMS", "ADCS")
NON_ESSENTIAL_LOADS: tuple[str, ...] = ("PAYLOAD", "HEATER", "AUX")
LOADS: tuple[str, ...] = ESSENTIAL_LOADS + NON_ESSENTIAL_LOADS

# Telemetry channels. Panel names are owned by backend/eps; use array_channel(panel).
TELEMETRY_SCALAR_CHANNELS: tuple[str, ...] = ("soc", "v_bus", "i_bat", "p_load", "t_bat_c")


def array_channel(panel: str) -> str:
    return f"i_array_{panel}"


def load_channel(load: str) -> str:
    return f"load_{load}"


# Faults and hypotheses (H1..H5 follow FAULT_KINDS order)
FAULT_KINDS: tuple[str, ...] = (
    "array_degradation",
    "stuck_on_load",
    "capacity_fade",
    "sensor_bias",
    "beta_season",
)
HYPOTHESES: tuple[str, ...] = ("H0", "H1", "H2", "H3", "H4", "H5")
HYPOTHESIS_KIND: dict[str, str | None] = {"H0": None, **{f"H{i + 1}": k for i, k in enumerate(FAULT_KINDS)}}
HYPOTHESIS_LABELS: dict[str, str] = {
    "H0": "Nominal",
    "H1": "Array degradation",
    "H2": "Stuck-on load",
    "H3": "Capacity fade",
    "H4": "Sensor bias",
    "H5": "Beta season",
}

# Commands (allowlist). Argument names per command; SI units unless the name says otherwise.
COMMAND_ARGS: dict[str, tuple[str, ...]] = {
    "LOAD_OFF": ("load",),  # non-essential loads only
    "LOAD_ON": ("load",),
    "SET_PAYLOAD_DUTY": ("duty",),  # 0..1
    "SET_HEATER_SETPOINT": ("setpoint_c",),  # -5..20 degC
    "SAFE_MODE": (),
}
COMMAND_NAMES: tuple[str, ...] = tuple(COMMAND_ARGS)
# T31: the collision-avoidance burn, kept apart so code built on the five commands above keeps working until it
# opts in. The Command model accepts the union.
CAM_COMMAND_ARGS: dict[str, tuple[str, ...]] = {"CAM_BURN": ("window_id",)}   # a backend-offered window W1..W3
ALL_COMMAND_ARGS: dict[str, tuple[str, ...]] = {**COMMAND_ARGS, **CAM_COMMAND_ARGS}
ALL_COMMAND_NAMES: tuple[str, ...] = tuple(ALL_COMMAND_ARGS)
PAYLOAD_DUTY_RANGE = (0.0, 1.0)
HEATER_SETPOINT_RANGE_C = (-5.0, 20.0)

# Conjunctions (T31): every manoeuvre window ends at least this long before the time of closest approach.
CAM_LEAD_S = 1800.0
CAM_WINDOW_IDS: tuple[str, ...] = ("W1", "W2", "W3")
CONJ_ID_PATTERN = r"^CJ-\d+$"
DEBRIS_ID_PATTERN = r"^DEB-\d{2}$"

# Agent tools (exact names)
AGENT_TOOLS: tuple[str, ...] = (
    "get_fleet_status",
    "get_telemetry",
    "score_hypotheses",
    "propose_diagnostic_test",
    "propose_plan",
    "simulate",
    "schedule_uplink",
    "request_approval",
)
# T31 tools, kept apart so code built on the eight above keeps working until it adds them.
AGENT_TOOLS_CAM: tuple[str, ...] = ("get_conjunctions",)

# Demo director
DEMO_STATES: tuple[str, ...] = ("attract", "storm", "agent", "approval", "fastforward", "reveal")
# Clock speed per demo state (x real time); 0 = paused.
# storm runs at 600x until the first alert; fastforward at 600x through the next eclipse.
DEMO_SPEED: dict[str, float] = {
    "attract": 120.0,
    "storm": 600.0,
    "agent": 0.0,
    "approval": 0.0,
    "fastforward": 600.0,
    "reveal": 0.0,
}
DEMO_SCENARIOS: tuple[str, ...] = ("single-stuck-load", "fault-storm", "beta-season", "conjunction")

# Recorder / replay
RECORDER_KINDS: tuple[str, ...] = (
    "alert",
    "model_request",
    "model_response",
    "tool_call",
    "tool_result",
    "approval",
    "approval_refused",
    "execution",
)
REPLAY_PACE_MS_DEFAULT = 700

# WebSocket server -> client message types
WS_MESSAGE_TYPES: tuple[str, ...] = (
    "fleet_state",
    "demo_state",
    "alert",
    "callout",
    "trace_event",
    "hypotheses",
    "matrix",
    "trajectory",
    "schedule",
    "approval_request",
    "approval_refused",
    "execution",
    "reveal",
)
