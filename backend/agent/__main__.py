"""Agent CLI on the mock backend (the real backend is wired in T22).

    python -m backend.agent                 # stub model, or the recorded run named by PENUMBRA_REPLAY
    python -m backend.agent --live          # Gemini (GEMINI_MODEL, GEMINI_API_KEY); always writes runs/<id>.jsonl
    python -m backend.agent --record        # also write runs/<id>.jsonl for a stub or replay run
    python -m backend.agent --fleet         # fleet mode on the SIMULATED storm (3 satellites), until T27's scenario
"""
from __future__ import annotations

import argparse
import json

from .dispatcher import Dispatcher
from .interfaces import ListRecorder
from .fleet import run_fleet
from .loop import run_agent
from .mock_backend import MockBackend
from .mock_fleet import MockStormBackend
from .session import model_kind, record_run, select_model


def _print_event(e) -> None:
    body = json.dumps(e.payload, separators=(",", ":"))
    extra = "".join(
        f" {k}={v:.0f}" if isinstance(v, float) else f" {k}={v}"
        for k, v in (("tokens", e.tokens), ("latency_ms", e.latency_ms))
        if v is not None
    )
    print(f"{e.seq:3d} {e.kind:15s} {body[:150]}{'...' if len(body) > 150 else ''}{extra}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m backend.agent")
    ap.add_argument("--live", action="store_true", help="use Gemini; the run is always recorded")
    ap.add_argument("--record", action="store_true", help="write runs/<run_id>.jsonl for a non-live run too")
    ap.add_argument("--run-id", help="flight-recorder run id (default: <kind>-<UTC time>-<random>)")
    ap.add_argument("--runs-dir", default="runs", help="directory for the JSONL flight record")
    ap.add_argument("--fleet", action="store_true", help="work the triage queue of the SIMULATED fault storm")
    args = ap.parse_args(argv)

    model = select_model(live=args.live)
    dispatcher = Dispatcher(MockStormBackend() if args.fleet else MockBackend())
    if args.live or args.record:
        rec = record_run(model, dispatcher, run_id=args.run_id, runs_dir=args.runs_dir, on_event=_print_event,
                         fleet=args.fleet)
        run, where = rec.run, f"\nrecorded: {rec.path}"
    else:
        recorder = ListRecorder(run_id=args.run_id or f"{model_kind(model)}-demo")
        run = run_fleet(model, dispatcher, recorder) if args.fleet else run_agent(model, dispatcher, recorder)
        for e in recorder.events:
            _print_event(e)
        where = ""
    print(f"\nstatus={run.status} steps={run.steps}\nraw:      {run.final_text}\nrendered: {run.rendered_text}{where}")


if __name__ == "__main__":
    main()
