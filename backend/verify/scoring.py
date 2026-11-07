"""Residual-based hypothesis scoring (T16).

Compares downlinked telemetry with the planning model under every hypothesis H0..H5 and turns the
residuals into a posterior. It reads telemetry only, never fault specs.

How:
- Downlinked telemetry comes in passes (contacts) with gaps between them. Each pass j starts a segment:
  the planning model restarts from that pass's measured SOC and battery temperature, predicts the
  pass itself (near term) and the start of the next pass (far term, through the gap and any eclipse).
  Restarting at each pass keeps model error bounded and handles data gaps.
- Each hypothesis has a grid of parameters (candidates); a sensor-bias candidate shifts the predicted
  reading of its channel (and, for SOC, the restart state).
- The fault may start inside the window: a hypothesis' likelihood averages over its parameters and
  over the segment where the fault starts (segments before it are scored as nominal).
- beta_season is fleet-wide: score_fleet() scores it on all satellites together, which separates it
  from a single satellite's array degradation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from contracts import HYPOTHESES, HYPOTHESIS_LABELS, Command, HypothesisScores
from backend.eps import planning
from backend.eps.params import EpsParams

from . import constants as K

FAULT_OF = {f"H{i + 1}": k for i, k in enumerate(
    ("array_degradation", "stuck_on_load", "capacity_fade", "sensor_bias", "beta_season"))}


# ----------------------------------------------------------------------------- candidates
@dataclass(frozen=True)
class Candidate:
    hyp: str
    params: dict = field(default_factory=dict, hash=False, compare=False)


def candidate_grid(params: EpsParams | None = None) -> list[Candidate]:
    """Parameter grid per hypothesis, spanning params.fault_ranges."""
    p = params or planning.planning_params()
    rg = p.fault_ranges
    out = [Candidate("H0", {"capacity_scale": c, "array_scale": a})
           for c in K.H0_CAPACITY_SCALE for a in K.H0_ARRAY_SCALE]
    lo, hi = rg["array_degradation"]["loss"]
    for loss in np.linspace(lo, hi, K.GRID_ARRAY_LOSS):
        for panel in rg["array_degradation"]["panel"]:
            out.append(Candidate("H1", {"loss": float(loss), "panel": panel}))
    for load in rg["stuck_on_load"]["load"]:
        out.append(Candidate("H2", {"load": load}))
    lo, hi = rg["capacity_fade"]["remaining"]
    for rem in np.linspace(lo, hi, K.GRID_CAPACITY):
        out.append(Candidate("H3", {"remaining": float(rem)}))
    for ch in rg["sensor_bias"]["channel"]:
        lo, hi = rg["sensor_bias"]["bias"][ch]
        for b in np.linspace(lo, hi, K.GRID_BIAS):
            for sign in (1.0, -1.0):
                out.append(Candidate("H4", {"channel": ch, "bias": float(sign * b)}))
    lo, hi = rg["beta_season"]["extra_beta_deg"]
    for d in np.linspace(lo, hi, K.GRID_EXTRA_BETA):
        out.append(Candidate("H5", {"extra_beta_deg": float(d)}))
    return out


def _candidate_hyps(cands: list[Candidate], n_rows: int, p: EpsParams) -> planning.Hypotheses:
    """Hypotheses arrays (rows, K) where entry k is candidate k. Non-SOC sensor bias needs no physics."""
    panels = tuple(p.panels)
    k_n = len(cands)
    hy = planning.Hypotheses(
        names=tuple(c.hyp for c in cands),
        array_factor=np.ones((n_rows, k_n, len(panels))),
        capacity_factor=np.ones((n_rows, k_n)),
        r0_factor=np.ones((n_rows, k_n)),
        stuck_on=np.zeros((n_rows, k_n, len(planning.LOADS)), bool),
        extra_beta_deg=np.zeros((n_rows, k_n)),
        soc_offset=np.zeros((n_rows, k_n)),
    )
    for ki, c in enumerate(cands):
        if c.hyp == "H0":
            hy.capacity_factor[:, ki] *= c.params.get("capacity_scale", 1.0)
            hy.array_factor[:, ki] *= c.params.get("array_scale", 1.0)
            continue
        for r in range(n_rows):
            hy._apply(r, ki, FAULT_OF[c.hyp], c.params, p, panels)
    return hy


# ----------------------------------------------------------------------------- evidence
def passes(seen: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous runs of True as (start, stop) index pairs, stop exclusive."""
    seen = np.asarray(seen, bool)
    if not seen.any():
        return []
    d = np.diff(np.concatenate([[0], seen.astype(int), [0]]))
    return list(zip(np.nonzero(d == 1)[0].tolist(), np.nonzero(d == -1)[0].tolist()))


def _sigma(ch: str) -> dict:
    return K.SIGMA["i_array" if ch.startswith("i_array_") else ch]


@dataclass
class SatEvidence:
    sat_id: str
    candidates: list[Candidate]
    seg_ll: np.ndarray            # (J, K) log-likelihood of each segment under each candidate
    seg_t: np.ndarray             # (J,) start time of each segment

    def hyp_loglik(self) -> tuple[dict[str, float], dict[str, dict]]:
        """Marginal log-likelihood per hypothesis (over parameters and fault onset) and MAP params."""
        j_n = self.seg_ll.shape[0]
        hyps = [c.hyp for c in self.candidates]
        ll: dict[str, float] = {}
        best: dict[str, dict] = {}
        if j_n == 0:
            return {h: 0.0 for h in HYPOTHESES}, {}
        nominal = self._nominal_per_segment()
        # prefix[j0] = sum of nominal segments before onset segment j0
        prefix = np.concatenate([[0.0], np.cumsum(nominal)])[:j_n]
        for h in HYPOTHESES:
            ks = [k for k, x in enumerate(hyps) if x == h]
            if h == "H0":
                ll[h] = float(_logmeanexp(self.seg_ll[:, ks].sum(axis=0)))   # one tolerance draw for the window
                kb = ks[int(np.argmax(self.seg_ll[:, ks].sum(axis=0)))]
                best[h] = dict(self.candidates[kb].params)
                continue
            seg = self.seg_ll[:, ks]                                   # (J, Kh)
            suffix = np.flip(np.cumsum(np.flip(seg, 0), 0), 0)         # sum over j >= j0, (J, Kh)
            tot = prefix[:, None] + suffix                             # (j0, Kh)
            ll[h] = float(_logmeanexp(tot.ravel()))
            j0, kb = np.unravel_index(int(np.argmax(tot)), tot.shape)
            best[h] = {**self.candidates[ks[kb]].params, "onset_after_t": float(self.seg_t[j0])}
        return ll, best


    def ll_with_onset(self, h: str, tau: float) -> np.ndarray:
        """Log-likelihood per candidate of `h` when the fault starts at time tau (nominal before).

        The segment containing tau counts as faulty. Returns (Kh,); empty evidence gives zeros.
        """
        ks = [k for k, c in enumerate(self.candidates) if c.hyp == h]
        j_n = self.seg_ll.shape[0]
        if j_n == 0:
            return np.zeros(len(ks))
        j0 = int(np.clip(np.searchsorted(self.seg_t, tau, side="right") - 1, 0, j_n - 1))
        return self._nominal_per_segment()[:j0].sum() + self.seg_ll[j0:, ks].sum(axis=0)

    def _nominal_per_segment(self) -> np.ndarray:
        """(J,) nominal log-likelihood per segment, averaged over the H0 tolerance grid."""
        k0 = [k for k, c in enumerate(self.candidates) if c.hyp == "H0"]
        x = self.seg_ll[:, k0]
        m = x.max(axis=1, keepdims=True)
        return (m + np.log(np.mean(np.exp(x - m), axis=1, keepdims=True)))[:, 0]


def _logmeanexp(x: np.ndarray) -> float:
    m = float(np.max(x))
    return m + float(np.log(np.mean(np.exp(x - m))))


def evidence(
    sat_id: str,
    t_grid: np.ndarray,
    illum: np.ndarray,
    beta_deg,
    telemetry: dict[str, np.ndarray],
    commands: list[Command] = (),
    init: planning.InitialState | None = None,
    t_from: float | None = None,
    params: EpsParams | None = None,
    candidates: list[Candidate] | None = None,
) -> SatEvidence:
    """Per-segment log-likelihoods of one satellite's downlinked telemetry under every candidate.

    telemetry: {"t": times on t_grid, <channel>: values (NaN = not downlinked)}, e.g. EpsWorld.telemetry().
    commands: every command executed on this satellite (ground knowledge), init: state at t_grid[0].
    """
    p = params or planning.planning_params()
    cands = candidates or candidate_grid(p)
    t_grid = np.asarray(t_grid, float)
    n = len(t_grid)
    dt = float(t_grid[1] - t_grid[0])
    illum = np.asarray(illum, float).reshape(-1)
    beta = np.broadcast_to(np.asarray(beta_deg, float), (n,))
    init = init or planning.InitialState.nominal(1, params=p)

    idx = np.searchsorted(t_grid, np.asarray(telemetry["t"], float))
    obs = {ch: np.full(n, np.nan) for ch in telemetry if ch != "t" and not ch.startswith("load_")}
    for ch in obs:
        obs[ch][idx] = np.asarray(telemetry[ch], float)
    seen = np.isfinite(obs["soc"])
    if t_from is not None:
        seen &= t_grid >= t_from
    runs = passes(seen)
    j_n = len(runs)
    if j_n == 0:
        return SatEvidence(sat_id, cands, np.zeros((0, len(cands))), np.zeros(0))

    # segment j: from the start of pass j to the end of the near pass, plus FAR_SAMPLES into pass j+1
    starts = [a for a, _ in runs]
    ends = [(runs[j + 1][0] + K.FAR_SAMPLES) if j + 1 < j_n else runs[j][1] for j in range(j_n)]
    ends = [min(e, n) for e in ends]
    l_max = max(e - s for s, e in zip(starts, ends))

    ctl_full = planning.build_controls(t_grid, [[list(commands)]], init, p)

    def window(a: np.ndarray, s: int) -> np.ndarray:
        """Slice of length l_max starting at s along the last axis, edge-padded."""
        seg = a[..., s:s + l_max]
        pad = l_max - seg.shape[-1]
        if pad:
            seg = np.concatenate([seg, np.repeat(seg[..., -1:], pad, axis=-1)], axis=-1)
        return seg

    ctl = planning.Controls(
        switch=np.stack([window(ctl_full.switch[0], s) for s in starts]),
        duty=np.stack([window(ctl_full.duty[0], s) for s in starts]),
        setpoint=np.stack([window(ctl_full.setpoint[0], s) for s in starts]),
        n_commands=np.zeros((j_n, 1), int),
    )
    seg_illum = np.stack([window(illum, s) for s in starts])
    seg_beta = np.stack([window(beta, s) for s in starts])

    def first_mean(ch: str, s: int, e: int) -> float:
        v = obs[ch][s:e]
        v = v[np.isfinite(v)][:3]
        return float(v.mean()) if v.size else np.nan

    soc0 = np.array([first_mean("soc", a, b) for a, b in runs])
    t0c = np.array([first_mean("t_bat_c", a, b) for a, b in runs])
    t0c = np.where(np.isfinite(t0c), t0c, init.t_bat_c[0])
    seg_init = planning.InitialState(
        soc=soc0, t_bat_c=t0c,
        switch=ctl.switch[:, 0, :, 0], payload_duty=ctl.duty[:, 0, 0], heater_setpoint_c=ctl.setpoint[:, 0, 0])
    hyps = _candidate_hyps(cands, j_n, p)
    t_rel = t_grid[0] + dt * np.arange(l_max)
    res = planning.simulate_batch(t_rel, seg_illum, seg_beta, ctl, seg_init, hyps, params=p, record_channels=True)

    bias = {ch: np.array([c.params["bias"] if c.hyp == "H4" and c.params.get("channel") == ch else 0.0
                          for c in cands]) for ch in obs}
    seg_ll = np.zeros((j_n, len(cands)))
    for j, (a, b) in enumerate(runs):
        near = slice(0, b - a)
        far = slice(runs[j + 1][0] - a, ends[j] - a) if j + 1 < j_n else None
        for ch, o in obs.items():
            sg = _sigma(ch)
            pred = res.channels[ch][j, :, 0, 0, :] + bias[ch][:, None]          # (K, l_max)
            ob = window(o, a)
            # near term: pass-mean residual and within-pass shape
            on, pn = ob[near], pred[:, near]
            ok = np.isfinite(on)
            if ok.sum() >= 2:
                r = on[ok][None, :] - pn[:, ok]                                   # (K, n)
                m = r.mean(axis=1)
                seg_ll[j] -= 0.5 * m**2 / sg["mean"] ** 2
                d = r - m[:, None]
                seg_ll[j] -= 0.5 * K.WITHIN_EFFECTIVE_N * np.mean(d**2, axis=1) / sg["within"] ** 2
            # far term: start of the next pass, predicted from this pass
            if far is not None:
                of, pf = ob[far], pred[:, far]
                ok = np.isfinite(of)
                if ok.any():
                    m = (of[ok][None, :] - pf[:, ok]).mean(axis=1)
                    seg_ll[j] -= 0.5 * m**2 / sg["far"] ** 2
    return SatEvidence(sat_id, cands, seg_ll, t_grid[starts])


# ----------------------------------------------------------------------------- posterior
@dataclass
class Score:
    sat_id: str
    posterior: dict[str, float]
    plausible: list[str]
    map_params: dict[str, dict]
    loglik: dict[str, float]

    def to_contract(self) -> HypothesisScores:
        return HypothesisScores(scores=self.posterior, plausible=self.plausible)

    @property
    def labels(self) -> dict[str, str]:
        return dict(HYPOTHESIS_LABELS)

    @property
    def top(self) -> str:
        return max(self.posterior, key=self.posterior.get)


def _normalize(log_terms: dict[str, float], prior: dict[str, float]) -> dict[str, float]:
    x = np.array([log_terms[h] + np.log(prior[h]) for h in HYPOTHESES])
    x = np.exp(x - x.max())
    x /= x.sum()
    return {h: float(v) for h, v in zip(HYPOTHESES, x)}


def _plausible(post: dict[str, float]) -> list[str]:
    top = max(post, key=post.get)
    return [h for h in HYPOTHESES if post[h] >= K.PLAUSIBLE_MIN or h == top]


def posterior(ev: SatEvidence, prior: dict[str, float] | None = None) -> Score:
    ll, best = ev.hyp_loglik()
    post = _normalize(ll, prior or K.PRIOR)
    return Score(ev.sat_id, post, _plausible(post), best, ll)


def score_fleet(evs: dict[str, SatEvidence], prior: dict[str, float] | None = None) -> dict[str, Score]:
    """Posterior per satellite with beta_season (H5) treated as one fleet-wide hypothesis.

    For satellite s: P(H5) uses H5 on every satellite with one shared extra beta and one shared onset
    time (a beta season is a single fleet event). P(Hk on s), k <= 4, uses Hk on s and, for every
    other satellite j, its evidence marginalized over H0..H4 with the fleet base rate
    (constants.FLEET_BASE_RATE), so independent faults on many satellites are unlikely a priori.
    """
    prior = prior or K.PRIOR
    per = {s: ev.hyp_loglik() for s, ev in evs.items()}
    single = [h for h in HYPOTHESES if h != "H5"]
    log_q = {h: np.log(K.FLEET_BASE_RATE[h]) for h in single}
    marg = {}
    for s, (ll, _) in per.items():
        x = np.array([log_q[h] + ll[h] for h in single])
        marg[s] = float(x.max() + np.log(np.exp(x - x.max()).sum()))
    taus = np.unique(np.concatenate([ev.seg_t for ev in evs.values()] + [np.zeros(0)]))
    if taus.size:
        grid = np.stack([sum(ev.ll_with_onset("H5", tau) for ev in evs.values()) for tau in taus])  # (tau, K5)
        fleet_h5 = _logmeanexp(grid.ravel())
    else:
        fleet_h5 = 0.0
    out = {}
    for s, (ll, best) in per.items():
        others = sum(v for o, v in marg.items() if o != s)
        terms = {h: ll[h] + others for h in single}
        terms["H5"] = fleet_h5
        post = _normalize(terms, prior)
        out[s] = Score(s, post, _plausible(post), best, terms)
    return out
