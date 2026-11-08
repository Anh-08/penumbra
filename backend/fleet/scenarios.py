"""Demo scenarios (CONTRACTS.md): single-stuck-load, fault-storm and beta-season.

This is the one place outside backend/eps that writes fault specs: a scenario has to say what goes
wrong. They go straight into an EpsWorld, which keeps them private; nothing here hands them out
again (the detector and the agent only see Scenario.world's telemetry).

Orbit inputs come from backend/orbit (OrbitModel: cached OMM elements, DE421, 10 deg contacts): real
illumination with penumbra, real contact windows, real beta angles. The synthetic circular orbit
(backend/eps/synthetic.py) stays available for fast unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

from contracts import SAT_IDS, Command, Contact, FaultSpec
from backend.eps import synthetic
from backend.eps.faults import EpsWorld
from backend.eps.planning import InitialState, planning_params
from backend.eps.truth import Environment, contact_mask

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc).timestamp()   # scenario epoch, inside the OMM epoch span
HORIZON_S = 24 * 3600.0


@dataclass
class Scenario:
    name: str
    seed: int
    env: Environment
    contacts: list[Contact]
    world: EpsWorld
    focus_sat: str | None              # the satellite the story is about (for the UI), not the fault spec
    initial: InitialState              # what the ground believes at T0 (commanded config, SOC, temperature)
    orbit: object | None = field(default=None, repr=False)   # backend.orbit.OrbitModel (globe positions etc.)
    conjunctions: list = field(default_factory=list)          # T31: Conjunctions with SIMULATED debris

    def next_contact(self, sat_id: str, t: float) -> Contact | None:
        """The contact in progress at t, else the next one to start; None if no more in the horizon."""
        best = None
        for c in self.contacts:
            if c.sat_id == sat_id and c.los >= t and (best is None or c.aos < best.aos):
                best = c
        return best


def contacts_from_mask(t: np.ndarray, mask: np.ndarray, sat_ids: tuple[str, ...],
                       station_id: str = "SVALBARD") -> list[Contact]:
    """Contact windows from a (S, N) in-contact mask (synthetic orbit only)."""
    out = []
    for i, s in enumerate(sat_ids):
        m = np.concatenate([[0], mask[i].astype(int), [0]])
        d = np.diff(m)
        for a, b in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
            out.append(Contact(sat_id=s, station_id=station_id, aos=float(t[a]), los=float(t[b - 1]),
                               max_el_deg=45.0))
    return out


@lru_cache(maxsize=4)
def _orbit_model(t0: float, horizon_s: int):
    from backend.orbit import OrbitModel, load_fleet

    return OrbitModel(load_fleet(), t0=t0, horizon_s=horizon_s)


def orbit_environment(sat_ids: tuple[str, ...], hours: float = 24.0, t0: float = T0):
    """(Environment, contacts, OrbitModel) for these satellites from the real orbit."""
    model = _orbit_model(float(t0), int(round(hours * 3600.0)))
    contacts = [c for c in model.contacts() if c.sat_id in sat_ids]
    env = Environment(t=model.t, sat_ids=tuple(sat_ids),
                      illum=np.stack([model.illumination(s) for s in sat_ids]),
                      in_contact=contact_mask(model.t, contacts, tuple(sat_ids)),
                      beta_deg=np.array([model.beta_deg(s) for s in sat_ids]))
    return env, contacts, model


def synthetic_environment(sat_ids: tuple[str, ...], hours: float = 24.0, beta_deg: float = 20.0):
    env = synthetic.environment(sat_ids, hours=hours, t0=T0, beta_deg=beta_deg,
                                contact_every_s=5400.0, contact_len_s=600.0)
    return env, contacts_from_mask(env.t, env.in_contact, env.sat_ids), None


def single_stuck_load(seed: int = 0, sat_ids: tuple[str, ...] = SAT_IDS, hours: float = 24.0,
                      focus: str | None = None) -> Scenario:
    """SAT-09's AUX line (25 W) sticks on two hours in, while it flies the payload at full duty.

    SAT-09 (beta about 42 deg, 35 % of each orbit in shadow) carries duty 1.0 when healthy. With the AUX
    line stuck on it does not: SOC heads for the 0.30 floor within about three hours of onset.
    LOAD_OFF(AUX) cannot help (the relay is stuck); cutting the payload duty or switching the payload off
    can. Every other satellite flies duty 0.5 and stays healthy.

    `focus` moves the fault elsewhere; a fleet subset without SAT-09 (one-satellite benchmarks) puts it on
    its first satellite.
    """
    sat_ids = tuple(sat_ids)
    focus = focus or ("SAT-09" if "SAT-09" in sat_ids else sat_ids[0])
    if focus not in sat_ids:
        raise ValueError(f"single-stuck-load: focus {focus} is not in the fleet")
    faults_ = [FaultSpec(kind="stuck_on_load", target=focus, params={"load": "AUX"}, onset_t=T0 + 2 * 3600.0)]
    # standing tasking, known to the ground: the focus satellite flies its payload at full duty
    tasking = {focus: [Command(t_exec=T0, name="SET_PAYLOAD_DUTY", args={"duty": 1.0})]}
    return _world("single-stuck-load", seed, sat_ids, hours, faults_, soc0={focus: 0.80}, tasking=tasking,
                  focus=focus)


STORM_ONSET_S = 2 * 3600.0


def _whole_seconds(c):
    """A conjunction with its times on whole seconds. The orbit time grid carries ~1e-5 s of float noise that differs
    between machines; a burn executes at its window start and that time goes into the plan hash."""
    wins = [w.model_copy(update={"t_start": float(round(w.t_start)), "t_end": float(round(w.t_end))}) for w in c.windows]
    return c.model_copy(update={"tca": float(round(c.tca)), "windows": wins})


def _world(name, seed, sat_ids, hours, faults_, soc0, tasking, focus=None, with_conjunctions=False) -> Scenario:
    env, contacts, model = orbit_environment(tuple(sat_ids), hours=hours)
    soc = np.array([soc0.get(s, 0.75) for s in env.sat_ids])
    conj = [_whole_seconds(c) for c in model.conjunctions() if c.sat_id in env.sat_ids] if with_conjunctions else []
    maneuvers = None
    if conj:
        from backend.eps.maneuver import windows_by_sat

        maneuvers = windows_by_sat(conj)
    world = EpsWorld(env, faults_, seed=seed, soc0=soc, maneuvers=maneuvers)
    for s, cmds in tasking.items():
        if s in env.sat_ids:
            world.execute(s, cmds)
    initial = InitialState.nominal(len(env.sat_ids), soc=soc, params=planning_params())
    return Scenario(name=name, seed=seed, env=env, contacts=contacts, world=world, focus_sat=focus,
                    initial=initial, orbit=model, conjunctions=conj)


def fault_storm(seed: int = 0, sat_ids: tuple[str, ...] = SAT_IDS, hours: float = 24.0) -> Scenario:
    """Three faults at once, T0 + 2 h, with three outcomes by design (CONTRACTS.md):

    - SAT-12 (beta 43 deg, 35 % shadow) loses a quarter of its array while flying the payload at full
      duty: a duty cut fixes it with a wide margin, so a flight rule (FR-3) auto-executes it (Tier 1).
    - SAT-07 (79 % sun) has its 25 W AUX line stuck on while flying at full duty from a low SOC: the fix
      commands the suspect unit, so the Flight Director must approve it (Tier 2).
    - SAT-19 (39 % shadow) has lost 70 % of its capacity and its SOC reads 0.15 high: no plan that keeps
      the payload passes every plausible hypothesis, so the verifier says infeasible and SAFE_MODE.
    Everyone else flies duty 0.5 and stays healthy.
    """
    onset = T0 + STORM_ONSET_S
    faults_ = [
        FaultSpec(kind="array_degradation", target="SAT-12", params={"loss": 0.25, "panel": "ALL"}, onset_t=onset),
        FaultSpec(kind="stuck_on_load", target="SAT-07", params={"load": "AUX"}, onset_t=onset),
        FaultSpec(kind="capacity_fade", target="SAT-19", params={"remaining": 0.30}, onset_t=onset),
        FaultSpec(kind="sensor_bias", target="SAT-19", params={"channel": "soc", "bias": 0.15}, onset_t=onset),
    ]
    full = [Command(t_exec=T0, name="SET_PAYLOAD_DUTY", args={"duty": 1.0})]
    return _world("fault-storm", seed, sat_ids, hours, faults_,
                  soc0={"SAT-12": 0.80, "SAT-07": 0.50, "SAT-19": 0.70},
                  tasking={"SAT-12": full, "SAT-07": full, "SAT-19": full}, focus=None)


def beta_season(seed: int = 0, sat_ids: tuple[str, ...] = SAT_IDS, hours: float = 24.0) -> Scenario:
    """The whole fleet's beta drifts 40 deg from its design attitude at T0 + 2 h (cos 40 = 0.77 of the
    array): an environmental cause that peer comparison must attribute to the fleet, not to hardware."""
    faults_ = [FaultSpec(kind="beta_season", target="FLEET", params={"extra_beta_deg": 40.0},
                         onset_t=T0 + STORM_ONSET_S)]
    return _world("beta-season", seed, sat_ids, hours, faults_, soc0={}, tasking={}, focus=None)


def conjunction(seed: int = 0, sat_ids: tuple[str, ...] = SAT_IDS, hours: float = 24.0) -> Scenario:
    """T31: SIMULATED debris DEB-01 passes SAT-17 0.3 km away at T0 + 5.2 h (data/debris/debris.json).

    SAT-17 (40 % of each orbit in shadow, the tightest budget) flies its payload at full duty from SOC 0.74 and
    recovers only ~5 % SOC per orbit. Three burn windows: W1 (cheap delta-v) inside its second eclipse while the
    battery is still low, W2 and W3 (larger delta-v) in sunlight, W3 with the battery charged. A burn draws the
    window's power for slew + burn with the payload off, so W1 risks the SOC floor and W3 does not: the power-coupled
    choice is the verifier's. No hardware fault; every other satellite is nominal.
    """
    focus = "SAT-17"
    tasking = {focus: [Command(t_exec=T0, name="SET_PAYLOAD_DUTY", args={"duty": 1.0})]}
    return _world("conjunction", seed, sat_ids, hours, [], soc0={focus: 0.74}, tasking=tasking, focus=focus,
                  with_conjunctions=True)


SCENARIOS = {"single-stuck-load": single_stuck_load, "fault-storm": fault_storm, "beta-season": beta_season,
             "conjunction": conjunction}


def build(name: str, seed: int = 0, **kw) -> Scenario:
    if name not in SCENARIOS:
        raise ValueError(f"unknown or not yet available scenario {name!r}")
    return SCENARIOS[name](seed=seed, **kw)
