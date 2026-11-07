"""Monotonic wall-clock to UTC POSIX simulation time."""
from __future__ import annotations

import time


class SimClock:
    def __init__(self, t0: float, speed: float = 120.0):
        self._sim_anchor = float(t0)
        self._wall_anchor = time.monotonic()
        self._speed = float(speed)

    @property
    def speed(self) -> float:
        return self._speed

    def now(self) -> float:
        return self._sim_anchor + (time.monotonic() - self._wall_anchor) * self._speed

    def set_speed(self, speed: float) -> float:
        if not 0 <= speed <= 600:
            raise ValueError("simulation speed must be between 0 and 600")
        self._sim_anchor = self.now()
        self._wall_anchor = time.monotonic()
        self._speed = float(speed)
        return self._sim_anchor

    def reset(self, t0: float) -> None:
        self._sim_anchor = float(t0)
        self._wall_anchor = time.monotonic()
