"""T22 gate (02:00): single-stuck-load reaches approval -> execution in replay of the recorded live run, and
after the approved plan the truth keeps SOC above the limit through the next eclipse.

Lives in backend/eps because only eps may look at the truth (world.reveal()). The live run itself
(Gemini, B's key) is recorded once in runs/demo/live-single-stuck-load.jsonl; this test replays it offline.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from backend.agent.session import select_model
from backend.fleet import demo
from backend.verify import constants as K

RUN = Path(__file__).resolve().parents[3] / "runs" / "demo" / "live-single-stuck-load.jsonl"


@pytest.mark.skipif(not RUN.exists(), reason="recorded demo run not present")
def test_replay_reaches_execution_and_truth_survives_next_eclipse(tmp_path, monkeypatch):
    monkeypatch.setenv("PENUMBRA_REPLAY", str(RUN))
    monkeypatch.setenv("PENUMBRA_REPLAY_PACE_MS", "0")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    recorded = [json.loads(line) for line in RUN.read_text(encoding="utf-8").splitlines()]
    rec_exec = next(e for e in recorded if e["kind"] == "execution")

    res = demo.run(select_model(live=False), run_id="replay", runs_dir=tmp_path)
    assert res.status == "done" and res.refused is None
    assert res.approval_id == rec_exec["payload"]["approval_id"]
    assert res.plan_hash == rec_exec["plan_hash"]                 # deterministic tools, same plan
    assert res.execution is not None and res.execution["plan_id"] == rec_exec["payload"]["plan_id"]
    kinds = [json.loads(line)["kind"] for line in (tmp_path / "replay.jsonl").read_text().splitlines()]
    assert kinds.count("approval") == 1 and kinds.count("execution") == 1 and "approval_refused" not in kinds

    world = res.backend.world
    sat = res.backend.scenario.focus_sat
    t_exec = min(c.t_exec for c in world.executed(sat) if c.t_exec > world.t_grid[0])
    a, b = res.eclipse
    truth = world.reveal(sat)["truth_soc"]
    t, soc = np.array(truth["t"]), np.array(truth["soc"])
    window = (t >= t_exec) & (t <= b)
    assert t[window].max() >= b - 10                              # the clock really went through the eclipse
    assert soc[window].min() >= K.SOC_MIN
