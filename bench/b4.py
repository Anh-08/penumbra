"""T24 planning-scale probe (work in progress; scheduler case awaits decision)."""

from __future__ import annotations

import resource

import numpy as np

from backend.eps import planning


def planning_case(
    satellites: int,
    *,
    step_s: int = 60,
    hours: float = 3.0,
    monte_carlo: int = 20,
    dtype: type = np.float32,
) -> dict[str, float | int | str]:
    """Measure a synthetic, no-command EPS batch at a stated scale."""
    t = np.arange(0, hours * 3600, step_s, dtype=np.float64)
    phase = np.sin(2 * np.pi * t / 5700)
    illumination = np.broadcast_to((phase > -0.25).astype(dtype), (satellites, len(t)))
    beta = np.zeros((satellites, len(t)), dtype=dtype)
    switch = np.broadcast_to(
        np.array([1, 1, 1, 1, 1, 0], dtype=bool), (satellites, 6)
    )
    initial = planning.InitialState(
        soc=np.full(satellites, 0.75, dtype=dtype),
        t_bat_c=np.full(satellites, 15, dtype=dtype),
        switch=switch,
        payload_duty=np.full(satellites, 0.5, dtype=dtype),
        heater_setpoint_c=np.full(satellites, 5, dtype=dtype),
    )
    result = planning.simulate_batch(
        t, illumination, beta, [[[]] for _ in range(satellites)], initial,
        perturb=planning.Perturbation(n_mc=monte_carlo), dtype=dtype,
    )
    return {
        "satellites": satellites,
        "step_s": step_s,
        "hours": hours,
        "monte_carlo": monte_carlo,
        "dtype": np.dtype(dtype).name,
        "evals_per_s": result.evals_per_s,
        "peak_ram_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
