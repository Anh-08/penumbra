"""T22: single-stuck-load end to end.

alert (detector) -> agent run (Gemini, stub or replay) -> Flight Director approval -> execution ->
sim clock through the next eclipse. Everything goes into one flight record (runs/<dir>/<run_id>.jsonl).

    python -m backend.fleet.demo                      # stub model (or the run named by PENUMBRA_REPLAY)
    python -m backend.fleet.demo --live               # Gemini (GEMINI_MODEL, GEMINI_API_KEY from env)
    PENUMBRA_REPLAY=runs/demo/<id>.jsonl PENUMBRA_REPLAY_PACE_MS=0 python -m backend.fleet.demo --run-id replay-check

The approval here is the scripted Flight Director of the gate test: it approves the first approval
request with the hash it was shown. In the demo UI a human clicks instead.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from contracts import SAT_IDS, ApprovalRefused
from backend.agent.dispatcher import Dispatcher
from backend.agent.loop import run_agent
from backend.agent.session import model_kind, new_run_id, select_model
from backend.recorder import FlightRecorder
from backend.verify import constants as K

from . import scenarios
from .backend import FleetBackend

DATA_NOTE = {
    "real": ["orbits (cached OMM elements, renamed SAT-01..SAT-24)", "Sun/Earth geometry (DE421)",
             "ground stations and 10 deg contact windows", "battery OCV/R0/efficiency/fade fitted from NASA PCoE"],
    "simulated": ["all EPS telemetry", "the stuck AUX relay fault", "sensor noise"],
}


@dataclass
class DemoResult:
    run_id: str
    path: Path
    status: str
    steps: int
    alert: dict
    approval_id: str | None
    plan_id: str | None
    plan_hash: str | None
    refused: dict | None
    execution: dict | None
    eclipse: tuple[float, float] | None
    min_downlinked_soc_after: float | None
    rendered_text: str | None
    backend: FleetBackend

    def summary(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "backend"}
        d["path"] = str(self.path)
        d["data"] = DATA_NOTE
        return d


def until_alert(backend: FleetBackend, sat_id: str, step_s: float = 60.0) -> tuple[float, list[dict]]:
    """Advance the clock until the detector raises a warning/critical alert for sat_id."""
    t = backend.now()
    end = float(backend.env.t[-1])
    while t < end:
        t = min(t + step_s, end)
        backend.advance_to(t)
        msgs = backend.detector.drain_messages()
        hits = [m for m in msgs if m["type"] == "alert" and m["data"].sat_id == sat_id]
        if hits:
            return t, msgs
    raise RuntimeError(f"no alert for {sat_id} inside the horizon")


def next_eclipse(env, sat_id: str, t: float) -> tuple[float, float] | None:
    """(start, end) of the first shadow (illum < 0.5) that ends after t."""
    i = env.sat_ids.index(sat_id)
    k = int(np.searchsorted(env.t, t))
    dark = env.illum[i, k:] < 0.5
    if not dark.any():
        return None
    a = int(np.argmax(dark))
    rest = dark[a:]
    b = a + (int(np.argmin(rest)) if not rest.all() else len(rest) - 1)
    return float(env.t[k + a]), float(env.t[k + b])


def run(model=None, *, run_id: str | None = None, runs_dir: str | Path = "runs/demo", seed: int = 0,
        sat_ids: tuple[str, ...] = SAT_IDS, approve: bool = True) -> DemoResult:
    sc = scenarios.build("single-stuck-load", seed=seed, sat_ids=sat_ids)
    backend = FleetBackend(sc)
    t_alert, msgs = until_alert(backend, sc.focus_sat)
    model = model or select_model(live=False)
    run_id = run_id or new_run_id(model_kind(model))
    path = Path(runs_dir) / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    dispatcher = Dispatcher(backend)
    alert = next(m["data"] for m in msgs if m["type"] == "alert" and m["data"].sat_id == sc.focus_sat)

    approval_id = plan_id = plan_hash = None
    refused = execution = None
    with FlightRecorder(run_id, path=path) as flight:
        for m in msgs:
            if m["type"] == "alert":
                flight.record("alert", m["data"].model_dump(mode="json"))
        agent = run_agent(model, dispatcher, flight)
        tickets = list(dispatcher.approvals.values())
        if approve and tickets:
            ticket = tickets[-1]
            approval_id, plan_id, plan_hash = ticket.approval_id, ticket.plan_id, ticket.plan_hash
            if ticket.tier == 1:
                execution = {"status": "auto_executed"}
            else:
                outcome = dispatcher.approve(approval_id, plan_hash, flight)
                if isinstance(outcome, ApprovalRefused):
                    refused = outcome.model_dump(mode="json")
                else:
                    execution = dispatcher.execute(approval_id, flight)

    eclipse = min_soc = None
    if execution is not None:
        eclipse = next_eclipse(sc.env, sc.focus_sat, backend.now())
        if eclipse is not None:
            nxt = sc.next_contact(sc.focus_sat, eclipse[1])
            end = (nxt.los if nxt is not None else eclipse[1])
            t0 = backend.now()
            for t in np.arange(t0 + 60.0, end + 60.0, 60.0):
                backend.advance_to(float(min(t, sc.env.t[-1])))
            tm = backend.world.telemetry(sc.focus_sat, t0, end)
            soc = tm["soc"][np.isfinite(tm["soc"])]
            min_soc = float(soc.min()) if soc.size else None

    return DemoResult(
        run_id=run_id, path=path, status=agent.status, steps=agent.steps, alert=alert.model_dump(mode="json"),
        approval_id=approval_id, plan_id=plan_id, plan_hash=plan_hash, refused=refused,
        execution=json.loads(json.dumps(execution, default=str)) if execution is not None else None,
        eclipse=eclipse, min_downlinked_soc_after=min_soc, rendered_text=agent.rendered_text, backend=backend,
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m backend.fleet.demo")
    ap.add_argument("--live", action="store_true", help="Gemini (one live run per feature: quota)")
    ap.add_argument("--run-id")
    ap.add_argument("--runs-dir", default="runs/demo")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--scenario", default="single-stuck-load", choices=["single-stuck-load", "fault-storm"])
    args = ap.parse_args(argv)
    if args.scenario == "fault-storm":
        res = run_storm(select_model(live=args.live), run_id=args.run_id, runs_dir=args.runs_dir, seed=args.seed)
        res.pop("backend")
        res.pop("dispatchers")
        print(json.dumps(res, indent=1, default=str))
        return
    res = run(select_model(live=args.live), run_id=args.run_id, runs_dir=args.runs_dir, seed=args.seed)
    out = res.summary()
    out["soc_min_limit"] = K.SOC_MIN
    print(json.dumps(out, indent=1, default=str))



# ----------------------------------------------------------------------------- fault storm (T27)
STORM_SATS = ("SAT-12", "SAT-07", "SAT-19")


class _Focus:
    """The backend as one agent run sees it: triage shows only the satellite being worked."""

    def __init__(self, backend: FleetBackend, sat_id: str) -> None:
        self._b, self._sat = backend, sat_id

    def __getattr__(self, name):
        return getattr(self._b, name)

    def triage(self):
        return [e for e in self._b.triage() if e.sat_id == self._sat]


class _Tee:
    """Recorder that also hands every event to a callback (for streaming to the UI)."""

    def __init__(self, inner, on_event) -> None:
        self._inner, self._on_event = inner, on_event

    def record(self, kind, payload, **kw):
        event = self._inner.record(kind, payload, **kw)
        self._on_event(event)
        return event


def run_storm(model=None, *, run_id: str | None = None, runs_dir: str | Path = "runs/demo", seed: int = 0,
              sat_ids: tuple[str, ...] = SAT_IDS, step_s: float = 60.0, on_event=None,
              on_backend=None, backend: FleetBackend | None = None, approve: bool = True,
              on_dispatcher=None, on_approval=None) -> dict:
    """fault-storm: each storm satellite is worked when its alert comes, in alert order (the clock stops
    while the agent works). Returns {sat_id: outcome} plus the run id and path.

    Blocking and CPU-bound (about 60 s on 24 satellites in replay at pace 0): run it in a worker thread.
    on_event(RecorderEvent) gets every flight-recorder event as it is written (alerts, model and tool events,
    approval, execution). on_backend(FleetBackend) is called once, before the clock starts, so the caller
    can read fleet_state, matrix and trajectory from the same backend while the run goes on; or pass
    backend= (a FleetBackend on scenarios.build("fault-storm")) built by the caller.
    Tier 2: by default the scripted Flight Director approves. on_approval(sat_id, dispatcher, ticket) instead
    hands the ticket to the caller and BLOCKS until it returns (the sim clock does not move meanwhile), so
    a person can click Approve, which calls dispatcher.approve(...) and dispatcher.execute(...).
    approve=False with no on_approval leaves the ticket open and moves on (its pass may close).
    on_dispatcher(sat_id, Dispatcher) gets each satellite's dispatcher once its agent run is done
    (also returned in "dispatchers")."""
    if backend is None:
        backend = FleetBackend(scenarios.build("fault-storm", seed=seed, sat_ids=sat_ids))
    sc = backend.scenario
    if on_backend is not None:
        on_backend(backend)
    model = model or select_model(live=False)
    run_id = run_id or new_run_id(model_kind(model))
    path = Path(runs_dir) / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    outcomes: dict[str, dict] = {}
    dispatchers: dict[str, Dispatcher] = {}
    end = float(sc.env.t[-1])
    with FlightRecorder(run_id, path=path) as recorder:
        flight = _Tee(recorder, on_event) if on_event is not None else recorder
        t = backend.now()
        while len(outcomes) < len(STORM_SATS) and t < end:
            t = min(t + step_s, end)
            backend.advance_to(t)
            for m in backend.detector.drain_messages():
                if m["type"] == "alert":
                    flight.record("alert", m["data"].model_dump(mode="json"))
            alerted = {e.sat_id: e for e in backend.triage()}
            todo = [s for s in [e.sat_id for e in backend.triage()] if s in STORM_SATS and s not in outcomes]
            for sat in todo:
                if alerted[sat].severity not in ("warning", "critical"):
                    continue
                dispatcher = Dispatcher(_Focus(backend, sat))
                dispatchers[sat] = dispatcher
                agent = run_agent(model, dispatcher, flight)
                if on_dispatcher is not None:
                    on_dispatcher(sat, dispatcher)
                out = {"t": backend.now(), "status": agent.status, "steps": agent.steps,
                       "rendered_text": agent.rendered_text, "tier": None, "flight_rule": None, "executed": False}
                tickets = list(dispatcher.approvals.values())
                if tickets:
                    ticket = tickets[-1]
                    out.update(tier=ticket.tier, plan_id=ticket.plan_id, plan_hash=ticket.plan_hash)
                    if ticket.tier == 1:
                        out.update(executed=True, flight_rule=dispatcher._flight_rules.get(ticket.approval_id))
                    elif on_approval is not None:
                        on_approval(sat, dispatcher, ticket)            # blocks until the Flight Director acts
                        out.update(approval_id=ticket.approval_id,
                                   executed=ticket.approval_id in dispatcher._executed)
                    elif not approve:
                        out.update(approval_id=ticket.approval_id, awaiting_approval=True)
                    else:
                        res = dispatcher.approve(ticket.approval_id, ticket.plan_hash, flight)
                        if not isinstance(res, ApprovalRefused):
                            dispatcher.execute(ticket.approval_id, flight)
                            out["executed"] = True
                rank = backend.rank(sat)
                if rank.status == "infeasible" and not out["executed"]:
                    backend.mark(sat, "safe_mode_recommended")
                out["rank_status"] = rank.status
                out["triage_status"] = next((e.status for e in backend.triage() if e.sat_id == sat), None)
                outcomes[sat] = out
    return {"run_id": run_id, "path": str(path), "outcomes": outcomes, "data": DATA_NOTE, "backend": backend,
            "dispatchers": dispatchers}


if __name__ == "__main__":
    main()
