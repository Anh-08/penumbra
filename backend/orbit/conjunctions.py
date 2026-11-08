"""Conjunctions with SIMULATED debris (T31). Fleet orbits stay real (cached OMM, SGP4); the debris is not.

Debris file (`data/debris/debris.json`): a list of {object_id: "DEB-nn", epoch: POSIX s, r_km: [x, y, z],
v_km_s: [vx, vy, vz]} in the GCRS frame Skyfield uses for satellite positions. Debris is propagated with a
two-body model (RK4); that is enough for a simulated object and keeps everything offline and deterministic.

Conjunction search: on the model's 10 s grid, every local minimum of the satellite-debris distance below
CONJ_THRESHOLD_KM is refined to 0.1 s (time of closest approach, miss distance).

Manoeuvre windows (simple documented model): three windows ending WINDOW_LEADS_S before TCA (all at least
CAM_LEAD_S before it). An along-track burn of dv at lead time L shifts the satellite at TCA by about
3 * dv * L, so the dv for a target separation D is D / (3 L): early windows are cheap, late ones expensive.
Burn time = dv * MASS_KG / THRUST_N; power_w = PROPULSION_POWER_W during slew + burn.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from contracts import CAM_LEAD_S, Conjunction, ManeuverWindow

MU_KM3_S2 = 398600.4418
CONJ_THRESHOLD_KM = 1.0          # alert screening distance; re-passes farther out need no manoeuvre
COARSE_SCREEN_KM = 60.0          # > half the distance covered between two 10 s grid points
TARGET_MISS_KM = 2.0
WINDOW_LEADS_S = (4.0 * 3600, 2.5 * 3600, 1.0 * 3600)     # W1 early (cheap), W2, W3 late (expensive)
WINDOW_LEN_S = 600.0
MASS_KG = 15.0                 # assumed smallsat wet mass
THRUST_N = 0.05                # assumed cold-gas thruster
PROPULSION_POWER_W = 45.0      # assumed slew + valve drivers + heaters while burning
DEFAULT_DEBRIS = Path(__file__).resolve().parents[2] / "data" / "debris" / "debris.json"


def _accel(r):
    d = np.linalg.norm(r, axis=-1, keepdims=True)
    return -MU_KM3_S2 * r / d**3


def propagate(r0, v0, t0: float, times: np.ndarray, step_s: float = 10.0) -> np.ndarray:
    """Two-body positions (km) at `times`, from the state (r0, v0) at t0. RK4 with a fixed step."""
    return propagate_states(r0, v0, t0, times, step_s)[0]


def propagate_states(r0, v0, t0: float, times: np.ndarray, step_s: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """Two-body (positions km, velocities km/s) at `times`, from the state (r0, v0) at t0. RK4, fixed step."""
    times = np.asarray(times, float)
    out = np.empty((len(times), 3))
    vout = np.empty((len(times), 3))
    for sign in (1.0, -1.0):
        sel = (times >= t0) if sign > 0 else (times < t0)
        if not sel.any():
            continue
        targets = np.sort(times[sel]) if sign > 0 else np.sort(times[sel])[::-1]
        r, v, t = np.array(r0, float), np.array(v0, float), float(t0)
        pos = {}
        for tt in targets:
            while (tt - t) * sign > 1e-9:
                h = sign * min(step_s, abs(tt - t))
                k1v, k1r = _accel(r), v
                k2v, k2r = _accel(r + 0.5 * h * k1r), v + 0.5 * h * k1v
                k3v, k3r = _accel(r + 0.5 * h * k2r), v + 0.5 * h * k2v
                k4v, k4r = _accel(r + h * k3r), v + h * k3v
                r = r + h / 6.0 * (k1r + 2 * k2r + 2 * k3r + k4r)
                v = v + h / 6.0 * (k1v + 2 * k2v + 2 * k3v + k4v)
                t += h
            pos[tt] = (r.copy(), v.copy())
        idx = np.nonzero(sel)[0]
        for i in idx:
            out[i], vout[i] = pos[times[i]]
    return out, vout


def load_debris(path: str | Path = DEFAULT_DEBRIS) -> list[dict]:
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _sat_km(model, sat_id: str, times: np.ndarray) -> np.ndarray:
    """Satellite GCRS positions (km) at POSIX `times`: the model's cached grid, or TT arithmetic from its start."""
    times = np.asarray(times, float)
    if times.shape == model.t.shape and np.array_equal(times, model.t):
        t = model._times()
    else:
        start = model._times()[0]
        t = model.assets.timescale.tt_jd(start.tt + (times - float(model.t[0])) / 86400.0)
    return model.assets.satellites[sat_id].at(t).position.km.T


def plant_debris(model, sat_id: str, tca: float, miss_km: float, crossing_deg: float, object_id: str) -> dict:
    """A debris state that passes `sat_id` at `tca` with `miss_km` separation (simulated, for scenarios/tests)."""
    from datetime import datetime, timezone

    ts = model.assets.timescale
    state = model.assets.satellites[sat_id].at(ts.from_datetime(datetime.fromtimestamp(tca, timezone.utc)))
    r = np.array(state.position.km)
    v = np.array(state.velocity.km_per_s)
    r_hat = r / np.linalg.norm(r)
    th = np.radians(crossing_deg)
    v_d = v * np.cos(th) + np.cross(r_hat, v) * np.sin(th) + r_hat * np.dot(r_hat, v) * (1 - np.cos(th))
    rel = v_d - v
    off = np.cross(rel, r_hat)
    off = off / np.linalg.norm(off) * miss_km          # offset perpendicular to the relative velocity
    return {"object_id": object_id, "epoch": float(tca), "r_km": (r + off).tolist(), "v_km_s": v_d.tolist(),
            "simulated": True, "note": f"planted to pass {sat_id} (crossing {crossing_deg} deg)"}


def windows_for(tca: float, miss_km: float, t_min: float, leads_s=None) -> list[ManeuverWindow]:
    """Windows ending `leads_s` (default WINDOW_LEADS_S) before TCA; a debris record may set its own leads."""
    out = []
    for wid, lead in zip(("W1", "W2", "W3"), leads_s or WINDOW_LEADS_S):
        t_end = tca - lead
        t_start = t_end - WINDOW_LEN_S
        if t_start < t_min or t_end > tca - CAM_LEAD_S:
            continue
        burn_lead = tca - t_start
        dv = max(0.0, TARGET_MISS_KM - miss_km) * 1000.0 / (3.0 * burn_lead)       # m/s
        burn_s = max(1.0, dv * MASS_KG / THRUST_N)
        miss_after = float(np.hypot(miss_km, 3.0 * dv * burn_lead / 1000.0))
        out.append(ManeuverWindow(window_id=wid, t_start=t_start, t_end=t_end, dv_mps=round(dv, 4),
                                  burn_s=round(burn_s, 1), power_w=PROPULSION_POWER_W,
                                  miss_after_km=round(miss_after, 3)))
    return out


def find_conjunctions(model, debris: list[dict] | None = None, sat_ids=None) -> list[Conjunction]:
    """Every close approach of a debris object to a fleet satellite inside the model's grid."""
    debris = load_debris() if debris is None else debris
    t = model.t
    sat_ids = list(sat_ids or model.assets.satellites)
    found = []
    for obj in debris:
        d_pos, d_vel = propagate_states(obj["r_km"], obj["v_km_s"], obj["epoch"], t)
        for sat in sat_ids:
            s_pos = _sat_km(model, sat, t)
            dist = np.linalg.norm(s_pos - d_pos, axis=1)
            # coarse screen: grid points are up to ~76 km apart at ~7.6 km/s relative speed, so look at every
            # local minimum within COARSE_SCREEN_KM and refine it
            for k in np.nonzero(dist < COARSE_SCREEN_KM)[0]:
                if 0 < k < len(t) - 1 and dist[k] <= dist[k - 1] and dist[k] <= dist[k + 1]:
                    fine = np.arange(t[k - 1], t[k + 1], 0.1)
                    # refine from the grid state just before, so the fine search integrates 20 s, not hours
                    fd = np.linalg.norm(_sat_km(model, sat, fine) - propagate(d_pos[k - 1], d_vel[k - 1], t[k - 1],
                                                                             fine, step_s=0.5), axis=1)
                    j = int(np.argmin(fd))
                    if fd[j] <= CONJ_THRESHOLD_KM:
                        found.append((float(fine[j]), sat, obj["object_id"], float(fd[j]), obj.get("window_leads_s")))
    found.sort(key=lambda x: x[:4])
    out = []
    for i, (tca, sat, oid, miss, leads) in enumerate(found, 1):
        wins = windows_for(tca, miss, float(t[0]), leads)
        if wins:
            out.append(Conjunction(conj_id=f"CJ-{i}", sat_id=sat, object_id=oid, tca=round(tca, 1),
                                   miss_km=round(miss, 3), windows=wins))
    return out
