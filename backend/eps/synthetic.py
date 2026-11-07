"""Synthetic circular-orbit environment for EPS tests and offline development.

The real inputs come from backend/orbit (B2). This stand-in gives the same kind of arrays: illumination
with a penumbra (fraction of the solar disc visible, from disc-overlap area) and periodic contacts.
"""
from __future__ import annotations

import numpy as np

from contracts import DT

from .truth import Environment

MU_EARTH = 3.986004418e14   # m^3/s^2
R_EARTH = 6378137.0          # m
R_SUN = 6.957e8              # m
AU = 1.495978707e11          # m


def period_s(alt_km: float) -> float:
    a = R_EARTH + alt_km * 1e3
    return 2.0 * np.pi * np.sqrt(a**3 / MU_EARTH)


def _disc_overlap_fraction(rs, re, d):
    """Fraction of disc (radius rs) covered by disc (radius re) at centre distance d (angles, radians)."""
    rs, re, d = np.broadcast_arrays(np.asarray(rs, float), np.asarray(re, float), np.asarray(d, float))
    out = np.zeros_like(d)
    full = d <= re - rs
    out[full] = 1.0
    part = (d < rs + re) & ~full
    dd, a, b = d[part], rs[part], re[part]
    c1 = np.clip((dd**2 + a**2 - b**2) / (2 * dd * a), -1.0, 1.0)
    c2 = np.clip((dd**2 + b**2 - a**2) / (2 * dd * b), -1.0, 1.0)
    area = (a**2 * np.arccos(c1) + b**2 * np.arccos(c2)
            - 0.5 * np.sqrt(np.clip((-dd + a + b) * (dd + a - b) * (dd - a + b) * (dd + a + b), 0.0, None)))
    out[part] = area / (np.pi * a**2)
    return np.clip(out, 0.0, 1.0)


def illumination(t: np.ndarray, alt_km: float = 500.0, beta_deg: float = 0.0, phase: float = 0.0) -> np.ndarray:
    """Visible fraction of the solar disc for a circular orbit with the given beta angle."""
    r = R_EARTH + alt_km * 1e3
    theta = 2.0 * np.pi * (np.asarray(t, float) - float(np.asarray(t).flat[0])) / period_s(alt_km) + phase
    b = np.radians(beta_deg)
    pos = r * np.stack([np.cos(theta), np.sin(theta), np.zeros_like(theta)], axis=-1)
    sun = np.array([np.cos(b), 0.0, np.sin(b)])
    to_earth = -pos
    to_sun = sun * AU - pos
    d_e = np.linalg.norm(to_earth, axis=-1)
    d_s = np.linalg.norm(to_sun, axis=-1)
    cosang = np.einsum("ij,ij->i", to_earth, to_sun) / (d_e * d_s)
    sep = np.arccos(np.clip(cosang, -1.0, 1.0))
    covered = _disc_overlap_fraction(np.arcsin(R_SUN / d_s), np.arcsin(R_EARTH / d_e), sep)
    return 1.0 - covered


def environment(
    sat_ids=("SAT-01",),
    hours: float = 24.0,
    t0: float = 1_794_000_000.0,
    alt_km: float = 500.0,
    beta_deg: float | list[float] = 0.0,
    contact_every_s: float = 3 * 3600.0,
    contact_len_s: float = 600.0,
    in_contact_all: bool = False,
) -> Environment:
    """Environment for len(sat_ids) satellites, phase-shifted along the orbit, with periodic contacts."""
    t = t0 + np.arange(0.0, hours * 3600.0, DT)
    s = len(sat_ids)
    betas = np.broadcast_to(np.asarray(beta_deg, float), (s,))
    illum = np.stack([illumination(t, alt_km, betas[i], phase=2 * np.pi * i / max(s, 1)) for i in range(s)])
    if in_contact_all:
        contact = np.ones((s, len(t)), bool)
    else:
        rel = (t - t0)[None, :] + (np.arange(s) * 900.0)[:, None]
        contact = (rel % contact_every_s) < contact_len_s
    return Environment(t=t, sat_ids=tuple(sat_ids), illum=illum, in_contact=contact, beta_deg=betas)
