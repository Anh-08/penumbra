"""T31: the recorded live conjunction run replays offline with the same plan, hash, window and execution."""
import json
from pathlib import Path

import pytest

from backend.agent.session import select_model
from backend.fleet import demo
from backend.verify import constants as K

RUN = Path(__file__).resolve().parents[3] / "runs" / "demo" / "live-conjunction.jsonl"


@pytest.mark.skipif(not RUN.exists(), reason="recorded conjunction run not present")
def test_replay_reproduces_the_burn(tmp_path, monkeypatch):
    monkeypatch.setenv("PENUMBRA_REPLAY", str(RUN))
    monkeypatch.setenv("PENUMBRA_REPLAY_PACE_MS", "0")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    recorded = [json.loads(line) for line in RUN.read_text(encoding="utf-8").splitlines()]
    rec_exec = next(e for e in recorded if e["kind"] == "execution")

    res = demo.run_conjunction(select_model(live=False), run_id="replay", runs_dir=tmp_path)
    assert res.status == "done" and res.refused is None
    assert res.plan_hash == rec_exec["plan_hash"]                     # deterministic tools, same plan
    assert res.execution is not None and res.execution["kind"] == "maneuver"
    assert res.window_id is not None and res.burn_t is not None
    assert res.burn_t <= res.tca - 1800.0                               # CAM_LEAD_S before closest approach
    assert res.alert_cleared
    assert res.min_downlinked_soc_after is not None and res.min_downlinked_soc_after >= K.SOC_MIN
    kinds = [json.loads(line)["kind"] for line in (tmp_path / "replay.jsonl").read_text().splitlines()]
    assert kinds.count("approval") == 1 and kinds.count("execution") == 1
