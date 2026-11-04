from __future__ import annotations


class PositionEMA:
    """Smooth WCL coordinates; the runtime lock serializes update/reset calls."""

    def __init__(self, alpha: float = 0.35, max_gap_epochs: int = 3):
        if not 0 < alpha <= 1 or max_gap_epochs < 1:
            raise ValueError("EMA requires 0 < alpha <= 1 and reset gap >= 1")
        self.alpha = alpha
        self.max_gap_epochs = max_gap_epochs
        self.states: dict[tuple[str, str], tuple[int, float, float]] = {}

    def update(self, session_id: str, device_token: str, epoch_id: int,
               x: float, y: float) -> tuple[float, float] | None:
        key = (session_id, device_token)
        previous = self.states.get(key)
        # Ignore late/duplicate positions instead of attaching newer coordinates to an old epoch.
        if previous is not None and epoch_id <= previous[0]:
            return None
        if previous is not None and epoch_id - previous[0] <= self.max_gap_epochs:
            x = self.alpha * x + (1 - self.alpha) * previous[1]
            y = self.alpha * y + (1 - self.alpha) * previous[2]
        self.states[key] = (epoch_id, x, y)
        return x, y

    def reset(self) -> None:
        self.states.clear()
