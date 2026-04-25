from dataclasses import dataclass, field


@dataclass
class PID:
    kp: float
    ki: float
    kd: float
    out_min: float = -float("inf")
    out_max: float = float("inf")
    # Low-pass time constant on the derivative term, in seconds. Bounding-box
    # centers from a detector are noisy, and a raw d/dt amplifies that noise.
    d_tau: float = 0.05

    _integral: float = field(default=0.0, init=False, repr=False)
    _prev_error: float | None = field(default=None, init=False, repr=False)
    _d_filtered: float = field(default=0.0, init=False, repr=False)

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_error = None
        self._d_filtered = 0.0

    def step(self, error: float, dt: float) -> float:
        if dt <= 0:
            return self._clamp(self.kp * error + self.ki * self._integral + self.kd * self._d_filtered)

        if self._prev_error is None:
            d_raw = 0.0
        else:
            d_raw = (error - self._prev_error) / dt

        alpha = dt / (self.d_tau + dt) if self.d_tau > 0 else 1.0
        self._d_filtered += alpha * (d_raw - self._d_filtered)

        # Anti-windup: don't accumulate integral when the unsaturated output
        # would already exceed the clamp in the same direction as `error`.
        unsaturated = self.kp * error + self.ki * self._integral + self.kd * self._d_filtered
        if not (unsaturated >= self.out_max and error > 0) and not (unsaturated <= self.out_min and error < 0):
            self._integral += error * dt

        self._prev_error = error
        return self._clamp(
            self.kp * error + self.ki * self._integral + self.kd * self._d_filtered
        )

    def _clamp(self, v: float) -> float:
        return max(self.out_min, min(self.out_max, v))
