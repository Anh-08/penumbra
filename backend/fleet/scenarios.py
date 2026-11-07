"""Demo scenarios (CONTRACTS.md): single-stuck-load here; fault-storm and beta-season come with T27.

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


def single_stuck_load(seed: int = 0, sat_ids: tuple[str, ...] = SAT_IDS, hours: float = 24.0) -> Scenario:
    """SAT-17's AUX relay sticks on two hours in, while it flies the payload at full duty.

    SAT-17 (beta about 1 deg, 40 % of each orbit in shadow) is the tightest power budget in the fleet.
    Healthy, it carries payload duty 1.0 (SOC stays above 0.5); with 10 W of AUX it does not, and SOC
    reaches the 0.30 floor about 3.5 h after onset. LOAD_OFF(AUX) cannot help (the relay is stuck);
    cutting the payload duty can. Every other satellite flies duty 0.5 and stays healthy.
    """
    focus = "SAT-17"
    sat_ids = tuple(sat_ids)
    if focus not in sat_ids:
        raise ValueError(f"single-stuck-load needs {focus} in the fleet")
    env, contacts, model = orbit_environment(sat_ids, hours=hours)
    # the fault: AUX stuck on from T0 + 2 h (kept inside the world from here on)
    world = EpsWorld(env, [FaultSpec(kind="stuck_on_load", target=focus, params={"load": "AUX"},
                                     onset_t=T0 + 2 * 3600.0)], seed=seed, soc0=0.75)
    # standing tasking, known to the ground: the focus satellite flies its payload at full duty
    world.execute(focus, [Command(t_exec=T0, name="SET_PAYLOAD_DUTY", args={"duty": 1.0})])
    initial = InitialState.nominal(len(sat_ids), soc=0.75, params=planning_params())
    return Scenario(name="single-stuck-load", seed=seed, env=env, contacts=contacts, world=world,
                    focus_sat=focus, initial=initial, orbit=model)


SCENARIOS = {"single-stuck-load": single_stuck_load}


def build(name: str, seed: int = 0, **kw) -> Scenario:
    if name not in SCENARIOS:
        raise ValueError(f"unknown or not yet available scenario {name!r}")
    return SCENARIOS[name](seed=seed, **kw)
