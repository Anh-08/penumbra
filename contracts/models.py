"""Pydantic v2 models for every structure in CONTRACTS.md.

Conventions: `t`-like fields are float POSIX seconds (UTC); SI units unless the field name
says otherwise (_deg, _c, _wh, _ms, _km). Optional fields default to None.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .constants import (
    ALERT_ID_PATTERN,
    ALL_COMMAND_ARGS,
    APPROVAL_ID_PATTERN,
    CAM_LEAD_S,
    CONJ_ID_PATTERN,
    DEBRIS_ID_PATTERN,
    ESSENTIAL_LOADS,
    FLIGHT_RULE_PATTERN,
    HEATER_SETPOINT_RANGE_C,
    LOADS,
    PAYLOAD_DUTY_RANGE,
    PLAN_ID_PATTERN,
    SAT_ID_PATTERN,
    SIM_ID_PATTERN,
)

# ---------- id and enum types ----------
SatId = Annotated[str, StringConstraints(pattern=SAT_ID_PATTERN)]
PlanId = Annotated[str, StringConstraints(pattern=PLAN_ID_PATTERN)]
SimId = Annotated[str, StringConstraints(pattern=SIM_ID_PATTERN)]
ApprovalId = Annotated[str, StringConstraints(pattern=APPROVAL_ID_PATTERN)]
AlertId = Annotated[str, StringConstraints(pattern=ALERT_ID_PATTERN)]
FlightRuleId = Annotated[str, StringConstraints(pattern=FLIGHT_RULE_PATTERN)]
ConjId = Annotated[str, StringConstraints(pattern=CONJ_ID_PATTERN)]
DebrisId = Annotated[str, StringConstraints(pattern=DEBRIS_ID_PATTERN)]
WindowId = Literal["W1", "W2", "W3"]
StationId = Literal["SVALBARD", "TROLL", "FAIRBANKS"]
Hypothesis = Literal["H0", "H1", "H2", "H3", "H4", "H5"]
LoadName = Literal["BUS", "COMMS", "ADCS", "PAYLOAD", "HEATER", "AUX"]
FaultKind = Literal["array_degradation", "stuck_on_load", "capacity_fade", "sensor_bias", "beta_season"]
Severity = Literal["watch", "warning", "critical"]
CommandName = Literal["LOAD_OFF", "LOAD_ON", "SET_PAYLOAD_DUTY", "SET_HEATER_SETPOINT", "SAFE_MODE", "CAM_BURN"]
PlanKind = Literal["plan", "diagnostic", "maneuver"]
RankStatus = Literal["feasible", "infeasible"]
TriageStatus = Literal[
    "queued", "working", "auto_executed", "awaiting_approval", "executed", "safe_mode_recommended"
]
Solver = Literal["cpsat", "greedy"]
Tier = Literal[1, 2]
DemoState = Literal["attract", "storm", "agent", "approval", "fastforward", "reveal"]
RecorderKind = Literal[
    "alert", "model_request", "model_response", "tool_call", "tool_result",
    "approval", "approval_refused", "execution",
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------- orbit ----------
class Contact(Frozen):
    sat_id: SatId
    station_id: StationId
    aos: float
    los: float
    max_el_deg: float

    @model_validator(mode="after")
    def _order(self) -> "Contact":
        if self.los <= self.aos:
            raise ValueError("contact los must be after aos")
        return self


class SatPosition(Model):
    """One entry of positions(t)."""
    lat_deg: float
    lon_deg: float
    alt_km: float
    illum: float = Field(ge=0.0, le=1.0)


# ---------- EPS ----------
class FaultSpec(Frozen):
    """Never leaves backend/eps, except through reveal()."""
    kind: FaultKind
    target: SatId | Literal["FLEET"]
    params: dict[str, Any] = Field(default_factory=dict)
    onset_t: float


# ---------- detect ----------
class Alert(Model):
    alert_id: AlertId
    sat_id: SatId
    severity: Severity
    time_to_critical_s: float | None  # None: SOC never reaches SOC_MIN inside the horizon
    next_contact_aos: float | None
    reason_key: str


# ---------- commands and plans ----------
class Command(Frozen):
    """Allowlisted command. args keys per command: see constants.ALL_COMMAND_ARGS (COMMAND_ARGS + CAM_COMMAND_ARGS).

    LOAD_OFF/LOAD_ON: {"load": <LOAD>}; SET_PAYLOAD_DUTY: {"duty": 0..1};
    SET_HEATER_SETPOINT: {"setpoint_c": -5..20}; SAFE_MODE: {}.
    """
    t_exec: float
    name: CommandName
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_numbers(cls, data: Any) -> Any:
        # 1 and 1.0 must hash the same: numeric args are stored as float.
        if isinstance(data, dict) and isinstance(data.get("args"), dict):
            data = {**data, "args": {k: float(v) if type(v) is int else v for k, v in data["args"].items()}}
        return data

    @model_validator(mode="after")
    def _check_args(self) -> "Command":
        expected = set(ALL_COMMAND_ARGS[self.name])
        if set(self.args) != expected:
            raise ValueError(f"{self.name} takes args {sorted(expected)}, got {sorted(self.args)}")
        if self.name in ("LOAD_OFF", "LOAD_ON"):
            load = self.args["load"]
            if load not in LOADS:
                raise ValueError(f"unknown load {load!r}")
            if self.name == "LOAD_OFF" and load in ESSENTIAL_LOADS:
                raise ValueError(f"LOAD_OFF on essential load {load} is not allowed")
        elif self.name == "SET_PAYLOAD_DUTY":
            _check_range("duty", self.args["duty"], PAYLOAD_DUTY_RANGE)
        elif self.name == "SET_HEATER_SETPOINT":
            _check_range("setpoint_c", self.args["setpoint_c"], HEATER_SETPOINT_RANGE_C)
        elif self.name == "CAM_BURN":
            if self.args["window_id"] not in ("W1", "W2", "W3"):
                raise ValueError(f"CAM_BURN window_id must be W1..W3, got {self.args['window_id']!r}")
        return self


def _check_range(name: str, value: Any, bounds: tuple[float, float]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    lo, hi = bounds
    if not lo <= value <= hi:
        raise ValueError(f"{name}={value} outside [{lo}, {hi}]")


class Plan(Frozen):
    """The uplink must be a real contact window of sat_id (checked by the agent safety layer
    against orbit.contacts()); this model checks what it can on its own."""
    plan_id: PlanId
    sat_id: SatId
    kind: PlanKind
    uplink: Contact
    commands: list[Command]
    created_by: str

    @model_validator(mode="after")
    def _check(self) -> "Plan":
        if self.uplink.sat_id != self.sat_id:
            raise ValueError("uplink contact belongs to another satellite")
        for c in self.commands:
            if c.t_exec < self.uplink.aos:
                raise ValueError("t_exec must be >= uplink.aos")
        burns = [c for c in self.commands if c.name == "CAM_BURN"]
        if self.kind == "maneuver" and len(burns) != 1:
            raise ValueError("a maneuver plan has exactly one CAM_BURN (plus optional power commands)")
        if self.kind != "maneuver" and burns:
            raise ValueError("CAM_BURN only goes in a kind 'maneuver' plan")
        return self

    @property
    def plan_hash(self) -> str:
        return plan_hash(self)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def plan_hash(plan: Plan) -> str:
    """sha256(canonical_json({sat_id, kind, uplink, commands})), hex digest."""
    body = plan.model_dump(mode="json", include={"sat_id", "kind", "uplink", "commands"})
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


# ---------- conjunctions (T31) ----------
class ManeuverWindow(Frozen):
    """A backend-offered burn window. The model picks a window_id; it never writes a delta-v."""
    window_id: WindowId
    t_start: float
    t_end: float
    dv_mps: float = Field(ge=0.0)
    burn_s: float = Field(gt=0.0)
    power_w: float = Field(ge=0.0)        # extra bus load during slew + burn + propulsion electronics
    miss_after_km: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _order(self) -> "ManeuverWindow":
        if self.t_end <= self.t_start:
            raise ValueError("window t_end must be after t_start")
        return self


class Conjunction(Frozen):
    """A predicted close approach with SIMULATED debris (orbits, Sun and contacts stay real)."""
    conj_id: ConjId
    sat_id: SatId
    object_id: DebrisId
    tca: float                             # time of closest approach
    miss_km: float = Field(ge=0.0)
    windows: list[ManeuverWindow] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _windows(self) -> "Conjunction":
        ids = [w.window_id for w in self.windows]
        if len(set(ids)) != len(ids):
            raise ValueError("window ids must be unique")
        if any(w.t_end > self.tca - CAM_LEAD_S for w in self.windows):
            raise ValueError("every window ends at least CAM_LEAD_S before tca")
        return self

    def window(self, window_id: str) -> ManeuverWindow:
        return next(w for w in self.windows if w.window_id == window_id)


# ---------- verification ----------
class HypStats(Model):
    pass_rate: float = Field(ge=0.0, le=1.0)
    min_soc_p5: float
    min_v_bus: float
    t_bat_min_c: float
    t_bat_max_c: float
    payload_uptime: float


class SimResult(Model):
    sim_id: SimId
    plan_id: PlanId
    plan_hash: str
    per_hyp: dict[Hypothesis, HypStats]
    plausible: list[Hypothesis]
    passes_all: bool
    worst_margin_soc: float
    payload_uptime_worst: float
    n_commands: int


class RankResult(Model):
    ordered: list[PlanId]
    status: RankStatus


class Trajectory(Model):
    t: list[float]
    soc_p5: list[float]
    soc_p50: list[float]
    soc_p95: list[float]


# ---------- fleet ----------
class TriageEntry(Model):
    sat_id: SatId
    severity: Severity
    time_to_critical_s: float | None
    next_contact_aos: float | None
    priority: float
    status: TriageStatus


class PeerCompare(Model):
    env_score: float
    hw_score: float
    explanation_keys: list[str]


# ---------- scheduler ----------
class ScheduleRequest(Model):
    plan_id: PlanId
    sat_id: SatId
    priority: float
    deadline_t: float
    duration_s: float


class Assignment(Model):
    plan_id: PlanId
    station_id: StationId
    start_t: float
    end_t: float


class ScheduleResult(Model):
    assignments: list[Assignment]
    unscheduled: list[PlanId]
    solver: Solver
    optimal: bool
    solve_ms: float


# ---------- agent tools ----------
class HypothesisScores(Model):
    """score_hypotheses(sat_id) result."""
    scores: dict[Hypothesis, float]  # posterior per hypothesis
    plausible: list[Hypothesis]


class ToolError(Model):
    error: str


class PlanRef(Model):
    plan_id: PlanId


class ApprovalTicket(Model):
    """request_approval(plan_id) result."""
    approval_id: ApprovalId
    tier: Tier


# ---------- approval ----------
class ApproveRequest(Model):
    """POST /api/approve body."""
    approval_id: ApprovalId
    plan_hash: str


class ApprovalRefused(Model):
    """Refusal returned by POST /api/approve (and logged to the recorder)."""
    refused: Literal[True] = True
    reason_key: str
    failing_hypotheses: list[Hypothesis]


# ---------- flight recorder ----------
class RecorderEvent(Model):
    ts: float
    run_id: str
    seq: int
    kind: RecorderKind
    payload: dict[str, Any]
    plan_hash: str | None = None
    tokens: int | None = None
    latency_ms: float | None = None


# ---------- WebSocket messages (content of each message type) ----------
class SatState(Model):
    sat_id: SatId
    lat_deg: float
    lon_deg: float
    alt_km: float
    illum: float = Field(ge=0.0, le=1.0)
    soc: float | None  # None until first downlink
    status: str


class FleetStateMsg(Model):
    t: float
    speed: float
    sun_direction: tuple[float, float, float]  # unit vector, ECEF
    sats: list[SatState]


class DemoStateMsg(Model):
    state: DemoState
    speed: float


class CalloutMsg(Model):
    text: str
    priority: int
    audio_url: str | None = None


class HypothesesMsg(Model):
    sat_id: SatId
    scores: dict[Hypothesis, float]
    labels: dict[Hypothesis, str]
    plausible: list[Hypothesis]


class MatrixMsg(Model):
    sat_id: SatId
    results: list[SimResult]
    complete: bool


class ApprovalRequestMsg(Model):
    approval_id: ApprovalId
    plan_id: PlanId
    plan_hash: str
    sat_id: SatId
    tier: Tier
    sim_id: SimId


class ApprovalRefusedMsg(Model):
    approval_id: ApprovalId
    reason_key: str
    failing_hypotheses: list[Hypothesis]


class ExecutionMsg(Model):
    plan_id: PlanId
    sat_id: SatId
    status: str
    t: float
    flight_rule: FlightRuleId | None = None


class Diagnosis(Model):
    H: Hypothesis
    label: str
    posterior: float


class TruthSoc(Model):
    t: list[float]
    soc: list[float]


class RevealMsg(Model):
    sat_id: SatId
    truth: FaultSpec
    diagnosed: Diagnosis
    truth_soc: TruthSoc


# ---------- REST bodies ----------
class ScenarioRequest(Model):
    name: Literal["single-stuck-load", "fault-storm", "beta-season", "conjunction"]
    seed: int


class DemoRequest(Model):
    state: DemoState


class ClockRequest(Model):
    speed: float = Field(ge=0.0)
