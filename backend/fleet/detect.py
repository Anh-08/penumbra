"""Detector: alerts from downlinked telemetry (T34). Never reads fault specs.

For each satellite, whenever new telemetry has come down, the detector:
1. scores the hypotheses with the T16 residual machinery (backend.verify.scoring, fleet-coupled);
2. projects SOC forward with the planning model from the last downlinked state, under every plausible
   hypothesis (MAP parameters) and the commands already sent; time_to_critical_s is the earliest time
   SOC reaches SOC_MIN, None if it never does inside the horizon;
3. sets the severity and, for every new or escalated warning/critical, queues one `alert` and one
   `callout` message.
"""
from __future__ import annotations

import itertools
import re
from collections.abc import Callable

import numpy as np

from contracts import Alert, CalloutMsg, Command, Contact
from backend.eps import planning
from backend.eps.truth import Environment
from backend.verify import constants as K
from backend.verify import scoring

SEVERITY_RANK = {None: 0, "watch": 1, "warning": 2, "critical": 3}
CALLOUT_TEMPLATE = "Flight, {sat_id} battery trending low, {{{alert_id}.time_to_critical_min}} minutes to critical"
PLACEHOLDER = re.compile(r"\{(AL-\d+)\.time_to_critical_min\}")
REASON_KEYS = {
    "H0": "soc_trend_low", "H1": "array_output_low", "H2": "load_higher_than_commanded",
    "H3": "battery_capacity_low", "H4": "sensor_inconsistent", "H5": "fleet_array_output_low",
}


class Detector:
    def __init__(
        self,
        env: Environment,
        contacts: list[Contact],
        telemetry: Callable[[str], dict[str, np.ndarray]],
        commands: Callable[[str], list[Command]],
        initial: planning.InitialState,
        params=None,
        conjunctions: Callable[[], list] | None = None,
    ) -> None:
        """conjunctions() -> every Conjunction in the horizon (T31): conjunction alerts and burn loads."""
        self._all_conjunctions = conjunctions or (lambda: [])
        self._cj_alerts: dict[str, Alert] = {}
        self.env = env
        self.contacts = contacts
        self._telemetry = telemetry
        self._commands = commands
        self.initial = initial
        self.params = params or planning.planning_params()
        self._ids = itertools.count(1)
        self._n_seen = {s: 0 for s in env.sat_ids}
        self._in_pass = {s: False for s in env.sat_ids}
        self._looked = {s: False for s in env.sat_ids}     # first look done in the current pass
        self._evidence: dict[str, scoring.SatEvidence] = {}
        self._scores: dict[str, scoring.Score] = {}
        self._active: dict[str, Alert] = {}
        self._by_id: dict[str, Alert] = {}
        self._outbox: list[dict] = []

    # ----------------------------------------------------------------- public
    def detect(self, t: float) -> list[Alert]:
        """Active alerts at time t, after scoring any satellite with new downlinked data."""
        due = []
        for s in self.env.sat_ids:
            tm = self._telemetry(s)
            seen = np.isfinite(tm["soc"])
            n = int(seen.sum())
            in_pass = bool(seen.size and seen[-1] and tm["t"][-1] >= t - 1.5 * self.env.dt)
            if in_pass and not self._in_pass[s]:
                self._looked[s] = False
            first_look = in_pass and not self._looked[s] and n - self._n_seen[s] >= K.DETECT_MIN_NEW_SAMPLES
            pass_ended = not in_pass and n > self._n_seen[s]
            if first_look or pass_ended:
                self._evidence[s] = self._evidence_for(s, tm)
                self._n_seen[s] = n
                self._looked[s] = self._looked[s] or first_look
                due.append(s)
            self._in_pass[s] = in_pass
        if due:
            self._scores = scoring.score_fleet(self._evidence, prior=K.DETECT_PRIOR)
            for s in due:
                self._update(s, t, self._telemetry(s))
        self._update_conjunctions(t)
        power = [self._active[s] for s in self.env.sat_ids if s in self._active]
        return power + [self._cj_alerts[k] for k in sorted(self._cj_alerts)]

    def _update_conjunctions(self, t: float) -> None:
        """One alert per conjunction in the horizon, severity by time to TCA (T31); cleared once a burn executed."""
        for cj in self._all_conjunctions():
            burned = any(c.name == "CAM_BURN" and c.t_exec <= cj.tca for c in self._commands(cj.sat_id))
            if cj.tca <= t or burned:
                self._cj_alerts.pop(cj.conj_id, None)
                continue
            to_tca = cj.tca - t
            sev = "critical" if to_tca < K.CONJ_CRITICAL_S else "warning" if to_tca < K.CONJ_WARNING_S else "watch"
            nxt = self._next_contact(cj.sat_id, t)
            old = self._cj_alerts.get(cj.conj_id)
            if old is not None and SEVERITY_RANK[sev] <= SEVERITY_RANK[old.severity]:
                upd = old.model_copy(update={"time_to_critical_s": to_tca, "next_contact_aos": nxt.aos if nxt else None})
                self._cj_alerts[cj.conj_id] = upd
                self._by_id[upd.alert_id] = upd
                continue
            alert = Alert(alert_id=f"AL-{next(self._ids)}", sat_id=cj.sat_id, severity=sev, time_to_critical_s=to_tca,
                          next_contact_aos=nxt.aos if nxt else None, reason_key="conjunction")
            self._cj_alerts[cj.conj_id] = alert
            self._by_id[alert.alert_id] = alert
            if sev in ("warning", "critical"):
                self._outbox.append({"type": "alert", "data": alert})
                text = (f"Flight, {cj.sat_id} conjunction with {cj.object_id}, "
                        f"{{{alert.alert_id}.time_to_critical_min}} minutes to closest approach")
                self._outbox.append({"type": "callout", "data": CalloutMsg(
                    text=self.render(text), priority=1 if sev == "critical" else 2)})

    def drain_messages(self) -> list[dict]:
        """Queued WS messages, [{"type": "alert" | "callout", "data": model}], oldest first."""
        out, self._outbox = self._outbox, []
        return out

    def scores(self, sat_id: str) -> scoring.Score | None:
        return self._scores.get(sat_id)

    def alert(self, alert_id: str) -> Alert:
        return self._by_id[alert_id]

    def render(self, text: str) -> str:
        """Fill {AL-n.time_to_critical_min} placeholders with whole minutes."""
        def sub(m):
            a = self._by_id.get(m.group(1))
            if a is None or a.time_to_critical_s is None:
                return "unknown"
            return str(int(round(a.time_to_critical_s / 60.0)))
        return PLACEHOLDER.sub(sub, text)

    # ----------------------------------------------------------------- internals
    def _row(self, sat_id: str) -> int:
        return self.env.sat_ids.index(sat_id)

    def _init_for(self, sat_id: str) -> planning.InitialState:
        i = self._row(sat_id)
        ini = self.initial
        return planning.InitialState(soc=ini.soc[i:i + 1], t_bat_c=ini.t_bat_c[i:i + 1], switch=ini.switch[i:i + 1],
                                     payload_duty=ini.payload_duty[i:i + 1],
                                     heater_setpoint_c=ini.heater_setpoint_c[i:i + 1])

    def _evidence_for(self, sat_id: str, tm: dict) -> scoring.SatEvidence:
        i = self._row(sat_id)
        runs = scoring.passes(np.isfinite(tm["soc"]))
        t_from = float(tm["t"][runs[-K.DETECT_WINDOW_PASSES][0]]) if len(runs) > K.DETECT_WINDOW_PASSES else None
        return scoring.evidence(sat_id, self.env.t, self.env.illum[i], self.env.beta_deg[i], tm,
                                commands=self._commands(sat_id), init=self._init_for(sat_id), t_from=t_from,
                                params=self.params)

    def time_to_critical(self, sat_id: str, t: float, tm: dict, score: scoring.Score) -> float | None:
        """Earliest SOC_MIN crossing after t under the plausible hypotheses, from the last downlink."""
        cross = self._crossings(sat_id, t, tm, score)
        if not cross:
            return None
        return max(0.0, min(cross.values()) - t)

    def _crossings(self, sat_id: str, t: float, tm: dict, score: scoring.Score) -> dict[str, float]:
        """{hypothesis: time SOC reaches SOC_MIN} for the plausible hypotheses that get there."""
        seen = np.nonzero(np.isfinite(tm["soc"]))[0]
        if seen.size == 0:
            return {}
        j = seen[-1]
        t_last = float(tm["t"][j])
        i = self._row(sat_id)
        k0 = int(np.searchsorted(self.env.t, t_last))
        k1 = min(len(self.env.t), k0 + int(K.DETECT_PROJECTION_S / self.env.dt))
        t_fwd = self.env.t[k0:k1]
        if len(t_fwd) < 2:
            return {}
        cmds = [c for c in self._commands(sat_id)]
        from backend.eps.maneuver import windows_by_sat

        windows = windows_by_sat(self._all_conjunctions()).get(sat_id)
        full = planning.build_controls(self.env.t, [[cmds]], self._init_for(sat_id), self.params, maneuvers=[windows])
        ctl = full.window(k0, k1)
        names = tuple(score.plausible)
        hp = {h: {k: v for k, v in score.map_params.get(h, {}).items() if k != "onset_after_t"} for h in names}
        hyps = scoring._candidate_hyps(
            [scoring.Candidate(h, hp[h]) for h in names], 1, self.params)
        init = planning.InitialState(
            soc=np.array([float(tm["soc"][j])]),
            t_bat_c=np.array([float(tm["t_bat_c"][j]) if np.isfinite(tm["t_bat_c"][j]) else self.initial.t_bat_c[i]]),
            switch=ctl.switch[:, 0, :, 0], payload_duty=ctl.duty[:, 0, 0], heater_setpoint_c=ctl.setpoint[:, 0, 0])
        res = planning.simulate_batch(t_fwd, self.env.illum[i:i + 1, k0:k1], self.env.beta_deg[i:i + 1, k0:k1], ctl,
                                      init, hyps, params=self.params, soc_threshold=K.SOC_MIN)
        cross = res.t_soc_below[0, :, 0, 0]
        return {h: float(c) for h, c in zip(names, cross) if np.isfinite(c)}

    def _severity(self, ttc: float | None, anomalous: bool, t: float, nxt: Contact | None) -> str | None:
        if ttc is not None:
            until_contact = (nxt.aos - t) if nxt is not None else np.inf
            if ttc <= K.CRITICAL_TTC_S or ttc <= max(until_contact, 0.0) + K.CONTACT_MARGIN_S:
                return "critical"
            return "warning"
        return "watch" if anomalous else None

    def _next_contact(self, sat_id: str, t: float) -> Contact | None:
        best = None
        for c in self.contacts:
            if c.sat_id == sat_id and c.los >= t and (best is None or c.aos < best.aos):
                best = c
        return best

    def _update(self, sat_id: str, t: float, tm: dict) -> None:
        sc = self._scores[sat_id]
        anomalous = sc.posterior["H0"] < K.DETECT_ANOMALY_P_H0
        cross = self._crossings(sat_id, t, tm, sc)
        ttc = max(0.0, min(cross.values()) - t) if cross else None
        # raise only when something is wrong or nominal physics itself runs the battery down;
        # a merely-plausible fault on a healthy-looking satellite does not page the Flight Director
        if not anomalous and "H0" not in cross:
            ttc = None
        nxt = self._next_contact(sat_id, t)
        sev = self._severity(ttc, anomalous, t, nxt)
        old = self._active.get(sat_id)
        if sev is None:
            self._active.pop(sat_id, None)
            return
        top = sc.top if anomalous else "H0"
        reason = REASON_KEYS[top]
        if old is not None and SEVERITY_RANK[sev] <= SEVERITY_RANK[old.severity]:
            # same episode: refresh the numbers, keep the id, no new message
            upd = old.model_copy(update={"time_to_critical_s": ttc, "next_contact_aos": nxt.aos if nxt else None,
                                         "severity": old.severity if SEVERITY_RANK[sev] < SEVERITY_RANK[old.severity]
                                         else sev})
            self._active[sat_id] = upd
            self._by_id[upd.alert_id] = upd
            return
        alert = Alert(alert_id=f"AL-{next(self._ids)}", sat_id=sat_id, severity=sev, time_to_critical_s=ttc,
                      next_contact_aos=nxt.aos if nxt else None, reason_key=reason)
        self._active[sat_id] = alert
        self._by_id[alert.alert_id] = alert
        if sev in ("warning", "critical"):
            self._outbox.append({"type": "alert", "data": alert})
            text = CALLOUT_TEMPLATE.format(sat_id=sat_id, alert_id=alert.alert_id)
            self._outbox.append({"type": "callout", "data": CalloutMsg(
                text=self.render(text), priority=1 if sev == "critical" else 2)})
