"""T16 acceptance on a small sample: one seed per fault kind ranks the true fault first after one
diagnostic. The full 20-seed number comes from `python -m backend.eps.eval_diagnosis` and is recorded
in docs/results/T16-diagnosis.json."""
import pytest

from backend.eps import eval_diagnosis as E

# seeds whose seeded fault is (in order) capacity_fade, array_degradation, stuck_on_load, sensor_bias, beta_season
SEEDS = {0: "capacity_fade", 1: "array_degradation", 3: "stuck_on_load", 6: "sensor_bias", 7: "beta_season"}


@pytest.mark.parametrize("seed", sorted(SEEDS))
def test_true_fault_ranks_first_after_one_diagnostic(seed):
    r = E.run_seed(seed)
    assert r["kind"] == SEEDS[seed]
    assert r["correct_post"], r
    assert r["truth"] in r["post_plausible"]


def test_healthy_fleet_has_no_fault():
    h = E.healthy_seed(0, 20.0)
    assert set(h["tops"].values()) == {"H0"}, h
