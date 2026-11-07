"""Offline demo: `python -m backend.agent` runs the stub model on the mock backend and prints the trace."""
from __future__ import annotations

import json

from .dispatcher import Dispatcher
from .interfaces import ListRecorder
from .loop import run_agent
from .mock_backend import MockBackend
from .stub import StubModel


def main() -> None:
    recorder = ListRecorder(run_id="stub-demo")
    dispatcher = Dispatcher(MockBackend())
    run = run_agent(StubModel(), dispatcher, recorder)
    for e in recorder.events:
        body = json.dumps(e.payload, separators=(",", ":"))
        print(f"{e.seq:3d} {e.kind:15s} {body[:150]}{'...' if len(body) > 150 else ''}")
    print(f"\nstatus={run.status} steps={run.steps}\n{run.final_text}")


if __name__ == "__main__":
    main()
