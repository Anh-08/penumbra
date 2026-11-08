"""Fault-storm director (T35): the recorded storm in paced replay, or Gemini for one run, on the API's backend.

The run follows backend.fleet.demo.run_storm call for call, so the recorded file replays exactly: scenario
`fault-storm` seed 0, the clock steps 60 s at a time, and each storm satellite gets its own agent run (a fresh
Dispatcher whose triage shows only that satellite) when its alert comes. Differences, for the live demo:
- it uses the API provider's backend, so fleet_state, approval and reveal see the same world;
- every recorder event is streamed as `trace_event` (TeeRecorder), and hypotheses / matrix columns are published
  as their tool results arrive;
- a satellite where nothing passes (infeasible, SAFE_MODE recommended) gets a ticket for every plan of its matrix,
  so the judge can press a red column at any later point and see the physics veto (failed_verification with the
  failing hypotheses; no open uplink is needed for that refusal);
- a Tier 2 plan is not approved by the driver: right after that satellite's run the director enters `approval`
  (clock frozen, so its uplink window is still open) with every plan of that satellite ticketed, and waits for the
  judge's Approve, which executes it. Then the storm resumes for the remaining satellites. At the end `fastforward`
  runs the clock at 600x through the approved satellite's next eclipse and next pass, and `reveal` pauses.
  With the recorded run that is: SAT-12 (Tier 1, auto) -> SAT-07 (approval) -> SAT-19 (infeasible, SAFE_MODE
  recommended) -> fastforward -> reveal. Approving right after the run is also what the recording did.

Demo states and clock speeds follow CONTRACTS (DEMO_SPEED). Sim time is frozen while the agent works.
Pacing: model turns are PENUMBRA_REPLAY_PACE_MS apart (default 700); the clock moves at the state's speed.
PENUMBRA_REPLAY_PACE_MS=0 runs everything as fast as possible (tests, benchmarks).
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from contracts import DEMO_SPEED, HYPOTHESIS_LABELS, CalloutMsg, ExecutionMsg, HypothesesMsg, MatrixMsg, SimResult

REPO = Path(__file__).resolve().parents[2]
STORM_RUN = REPO / "runs" / "demo" / "live-fault-storm.jsonl"  # recorded by B1 with run_storm (Gemini, seed 0)
STORM_SEED = 0
STEP_S = 60.0  # same step as run_storm


@dataclass
class Pending:
    sat_id: str
    dispatcher: Any


@dataclass
class StormStatus:
    running: bool = False
    phase: str = "idle"  # idle | storm | agent | approval | fastforward | reveal | failed
    mode: str = "replay"  # replay | live
    run_id: str | None = None
    path: str | None = None
    worked: list[str] = field(default_factory=list)
    outcomes: dict[str, dict] = field(default_factory=dict)
    error: str | None = None
    wall_s: list[tuple[str, float]] = field(default_factory=list)  # (state, wall s since start), in order


def pace_ms() -> int:
    return int(os.environ.get("PENUMBRA_REPLAY_PACE_MS", "700"))


class StormDirector:
    def __init__(self, provider: Any, publish: Callable[[str, Any], None], transition: Callable[[str], Any],
                 runs_dir: str | Path = REPO / "runs", replay_path: str | Path = STORM_RUN) -> None:
        self.provider = provider
        self.publish = publish
        self.transition = transition
        self.runs_dir = Path(runs_dir)
        self.replay_path = Path(replay_path)
        self.status = StormStatus()
        self.pending: Pending | None = None
        self.approved_sat: str | None = None
        self._approved = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._t_start = 0.0
        self._current: str | None = None
        self._sims: dict[str, list[dict]] = {}

    # ---------------------------------------------------------------- control
    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, live: bool) -> None:
        if self.busy:
            raise RuntimeError("a fault storm is already running")
        self.status = StormStatus(running=True, phase="storm")
        self.pending, self.approved_sat = None, None
        self._approved.clear()
        self._cancel.clear()
        self._t_start = time.monotonic()
        self._thread = threading.Thread(target=self._run, args=(live,), name="fault-storm", daemon=True)
        self._thread.start()

    def approved(self, sat_id: str) -> None:
        """The Flight Director's plan for the satellite on screen executed: the storm continues."""
        if self.pending is not None and self.pending.sat_id == sat_id:
            self.approved_sat = sat_id
            self._approved.set()

    def cancel(self, timeout: float = 5.0) -> None:
        """Stop a running storm (a new scenario was requested)."""
        self._cancel.set()
        self._approved.set()
        self.join(timeout)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ---------------------------------------------------------------- the storm
    def choose_model(self, live: bool) -> tuple[Any, str]:
        from backend.agent.gemini import GeminiConfigError
        from backend.agent.session import select_model
        from backend.recorder import ReplayModel

        if live:
            try:
                return select_model(live=True), "live"
            except GeminiConfigError as exc:
                self.publish("callout", CalloutMsg(text=f"Run live unavailable ({exc}); replaying the recorded "
                                                        "storm instead.", priority=2))
        return ReplayModel(self.replay_path, pace_ms=pace_ms()), "replay"

    def _run(self, live: bool) -> None:
        try:
            self._storm(live)
        except Exception as exc:  # noqa: BLE001 - the demo must say what happened, not die silently
            self.status.error = f"{type(exc).__name__}: {exc}"
            self.status.phase = "failed"
            self.publish("callout", CalloutMsg(text=f"Fault storm stopped: {type(exc).__name__}. "
                                                    "Press FAULT STORM to replay the recorded run.", priority=1))
        finally:
            self.status.running = False

    def _storm(self, live: bool) -> None:
        from backend.agent.dispatcher import Dispatcher
        from backend.agent.loop import run_agent
        from backend.agent.session import TeeRecorder, model_kind, new_run_id
        from backend.fleet.demo import STORM_SATS
        from backend.recorder import FlightRecorder

        provider = self.provider
        model, mode = self.choose_model(live)
        self.status.mode = mode
        run_id = new_run_id(f"storm-{model_kind(model)}")
        path = Path(self.runs_dir) / f"{run_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.status.run_id, self.status.path = run_id, str(path)

        backend = provider.locked_backend()
        provider.hold()
        self._phase("storm")
        end = float(provider.backend.env.t[-1])
        step_wall = STEP_S / DEMO_SPEED["storm"] if pace_ms() > 0 else 0.0
        with FlightRecorder(run_id, path=path) as flight:
            recorder = TeeRecorder(flight, self._on_event)
            t = backend.now()
            while len(self.status.outcomes) < len(STORM_SATS) and t < end:
                tick = time.monotonic()
                t = min(t + STEP_S, end)
                with provider.lock:
                    backend.advance_to(t)
                    messages = provider.backend.detector.drain_messages()
                    triage = provider.backend.triage()
                for m in messages:
                    if m["type"] == "alert":
                        flight.record("alert", m["data"].model_dump(mode="json"))
                    self.publish(m["type"], m["data"])
                alerted = {e.sat_id: e for e in triage}
                todo = [s for s in [e.sat_id for e in triage] if s in STORM_SATS and s not in self.status.outcomes]
                for sat in todo:
                    if alerted[sat].severity not in ("warning", "critical"):
                        continue
                    self._phase("agent")
                    dispatcher = Dispatcher(_Focus(backend, sat))
                    self._current, self._sims[sat] = sat, []
                    agent = run_agent(model, dispatcher, recorder)
                    self._current = None
                    self._after_agent(sat, agent, dispatcher)
                    if self.pending is not None and self.pending.sat_id == sat:
                        self._enter_approval(self.pending)
                        self._approved.wait()  # the judge's click (sim time is frozen meanwhile)
                        if self._cancel.is_set():
                            return
                        self.status.outcomes[sat]["executed"] = True
                    if len(self.status.outcomes) < len(STORM_SATS):
                        self._phase("storm")
                provider.snapshot()
                if self._cancel.is_set():
                    return
                if step_wall:
                    time.sleep(max(0.0, step_wall - (time.monotonic() - tick)))

        if self.approved_sat is not None:
            self._fastforward(self.approved_sat)
        self._phase("reveal")

    def _after_agent(self, sat: str, agent: Any, dispatcher: Any) -> None:
        provider, backend = self.provider, self.provider.locked_backend()
        out = {"t": backend.now(), "status": agent.status, "steps": agent.steps,
               "rendered_text": agent.rendered_text, "tier": None, "flight_rule": None, "executed": False}
        matrix = self._publish_matrix(sat, complete=True)
        if dispatcher.last_schedule is not None:
            self.publish("schedule", dispatcher.last_schedule)
        if agent.rendered_text:
            self.publish("callout", CalloutMsg(text=agent.rendered_text, priority=3))
        tickets = list(dispatcher.approvals.values())
        if tickets:
            ticket = tickets[-1]
            out.update(tier=ticket.tier, plan_id=ticket.plan_id, plan_hash=ticket.plan_hash)
            if ticket.tier == 1:
                rule = dispatcher._flight_rules.get(ticket.approval_id)
                out.update(executed=True, flight_rule=rule)
                provider.executed.add(sat)
                self.publish("execution", ExecutionMsg(plan_id=ticket.plan_id, sat_id=sat, status="auto_executed",
                                                       t=backend.now(), flight_rule=rule))
            elif self.pending is None:
                self.pending = Pending(sat, dispatcher)  # the judge approves it next, before time moves on
        with provider.lock:
            rank = provider.backend.rank(sat)
            if rank.status == "infeasible" and not out["executed"]:
                provider.backend.mark(sat, "safe_mode_recommended")
        out["rank_status"] = rank.status
        if rank.status == "infeasible" and not out["executed"] and (self.pending is None or self.pending.sat_id != sat):
            self._ticket_all(sat, dispatcher, matrix)  # red columns stay clickable: the physics veto
        self.status.outcomes[sat] = out
        self.status.worked.append(sat)

    def _enter_approval(self, pending: Pending) -> None:
        provider = self.provider
        provider.activate(pending.dispatcher)
        matrix = self._publish_matrix(pending.sat_id, complete=True)
        results = matrix.results if matrix is not None else []
        self._ticket_all(pending.sat_id, pending.dispatcher, matrix)
        if results:
            best = results[0]
            plausible = [h for h in best.plausible if h in best.per_hyp]
            if plausible:
                worst = min(plausible, key=lambda h: best.per_hyp[h].min_soc_p5)
                with provider.lock:
                    trajectory = provider.backend.verifier.trajectory(best.plan_id, worst)
                self.publish("trajectory", trajectory)
        self._phase("approval")

    def _ticket_all(self, sat: str, dispatcher: Any, matrix: MatrixMsg | None) -> None:
        """An approval ticket for every plan in the satellite's complete matrix (green and red)."""
        for r in (matrix.results if matrix is not None else []):
            self.publish("approval_request", self.provider.ticket(dispatcher, r.plan_id))

    def _fastforward(self, sat: str) -> None:
        """600x through the approved satellite's next eclipse and its next pass (truth comes down in passes)."""
        from backend.fleet.demo import next_eclipse

        provider = self.provider
        backend = provider.locked_backend()
        self._phase("fastforward")
        env = provider.backend.env
        t = backend.now()
        eclipse = next_eclipse(env, sat, t)
        stop = float(env.t[-1])
        if eclipse is not None:
            nxt = provider.backend.next_contact(sat, eclipse[1])
            stop = min(stop, nxt.los if nxt is not None else eclipse[1])
        step_wall = STEP_S / DEMO_SPEED["fastforward"] if pace_ms() > 0 else 0.0
        while t < stop and not self._cancel.is_set():
            tick = time.monotonic()
            t = min(t + STEP_S, stop)
            with provider.lock:
                backend.advance_to(t)
                messages = provider.backend.detector.drain_messages()
            for m in messages:
                self.publish(m["type"], m["data"])
            provider.snapshot()
            if step_wall:
                time.sleep(max(0.0, step_wall - (time.monotonic() - tick)))

    # ---------------------------------------------------------------- streaming
    def _on_event(self, event: Any) -> None:
        self.publish("trace_event", event)
        if event.kind != "tool_result" or self._current is None:
            return
        name, result = event.payload.get("name"), event.payload.get("result") or {}
        sat = self._current
        if name == "score_hypotheses" and "scores" in result:
            self.publish("hypotheses", HypothesesMsg(
                sat_id=result.get("sat_id", sat), scores=result["scores"], plausible=result.get("plausible", []),
                labels=result.get("labels") or {h: HYPOTHESIS_LABELS[h] for h in result["scores"]}))
        elif name == "simulate" and "sim_id" in result:
            sim = {k: v for k, v in result.items() if k in SimResult.model_fields}  # drop hint / rank_status
            sims = self._sims.setdefault(sat, [])
            sims[:] = [s for s in sims if s["plan_id"] != sim["plan_id"]] + [sim]
            self.publish("matrix", MatrixMsg(sat_id=sat, results=[SimResult.model_validate(s) for s in sims],
                                             complete=False))

    def _publish_matrix(self, sat: str, complete: bool) -> MatrixMsg | None:
        with self.provider.lock:
            matrix = self.provider.backend.matrix(sat, complete)
        if matrix.results:
            self.publish("matrix", matrix)
            return matrix
        return None

    def _phase(self, state: str) -> None:
        self.status.phase = state
        self.status.wall_s.append((state, round(time.monotonic() - self._t_start, 2)))
        self.transition(state)


class _Focus:
    """The backend as one agent run sees it: triage shows only the satellite being worked (as in run_storm)."""

    def __init__(self, backend: Any, sat_id: str) -> None:
        self._b, self._sat = backend, sat_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._b, name)

    def triage(self) -> list:
        return [e for e in self._b.triage() if e.sat_id == self._sat]
