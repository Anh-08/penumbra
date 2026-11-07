"""Demo scenarios (CONTRACTS.md): single-stuck-load here; fault-storm and beta-season come with T27.

This is the one place outside backend/eps that writes fault specs: a scenario has to say what goes
wrong. They go straight into an EpsWorld, which keeps them private; nothing here hands them out
again (the detector and the agent only see Scenario.world's telemetry).

Orbit inputs: until backend/orbit provides illumination and contacts (T03/T04), scenarios use the
synthetic circular orbit from backend/eps/synthetic.py. Contacts are derived from its mask.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from contracts import SAT_IDS, Contact, FaultSpec
from backend.eps import synthetic
from backend.eps.faults import EpsWorld
from backend.eps.params import EpsParams
from backend.eps.planning import InitialState, planning_params
from backend.eps.truth import Environment

T0 = 1_794_000_000.0          # scenario epoch (UTC); replaced by the OMM epoch once orbit is wired
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


def _environment(sat_ids, beta_deg, hours=HORIZON_S / 3600.0) -> Environment:
    return synthetic.environment(sat_ids, hours=hours, t0=T0, beta_deg=beta_deg,
                                 contact_every_s=5400.0, contact_len_s=600.0)


def single_stuck_load(seed: int = 0, sat_ids: tuple[str, ...] = SAT_IDS, hours: float = 24.0) -> Scenario:
    """SAT-07's AUX relay sticks on two hours in, while it flies the payload hard (duty 0.8).

    Healthy, the array carries duty 0.8. With 10 W of AUX it does not, and SOC trends down to critical.
    LOAD_OFF(AUX) cannot help (the relay is stuck); cutting the payload duty can.
    """
    focus = "SAT-07"
    if focus not in sat_ids:
        raise ValueError("single-stuck-load needs SAT-07 in the fleet")
    env = _environment(tuple(sat_ids), beta_deg=20.0, hours=hours)
    params = EpsParams(initial_payload_duty=0.8)
    # the fault: AUX stuck on from T0 + 2 h (kept inside the world from here on)
    world = EpsWorld(env, [FaultSpec(kind="stuck_on_load", target=focus, params={"load": "AUX"},
                                     onset_t=T0 + 2 * 3600.0)], seed=seed, params=params, soc0=0.8)
    initial = InitialState.nominal(len(sat_ids), soc=0.8, params=planning_params(initial_payload_duty=0.8))
    return Scenario(name="single-stuck-load", seed=seed, env=env,
                    contacts=contacts_from_mask(env.t, env.in_contact, env.sat_ids),
                    world=world, focus_sat=focus, initial=initial)


SCENARIOS = {"single-stuck-load": single_stuck_load}


def build(name: str, seed: int = 0, **kw) -> Scenario:
    if name not in SCENARIOS:
        raise ValueError(f"unknown or not yet available scenario {name!r}")
    return SCENARIOS[name](seed=seed, **kw)
