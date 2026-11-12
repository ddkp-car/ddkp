from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence


class RollingHistory:
    def __init__(self, names: Sequence[str], *, window_seconds: float) -> None:
        self.names = tuple(str(name) for name in names)
        if not self.names or len(set(self.names)) != len(self.names):
            raise ValueError("history names must be non-empty and unique")
        self.window_seconds = float(window_seconds)
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("window_seconds must be finite and greater than zero")
        self._points = {name: deque() for name in self.names}
        self._last_timestamp: float | None = None

    def append(self, timestamp: float, values: Mapping[str, float]) -> None:
        now = float(timestamp)
        if not math.isfinite(now):
            raise ValueError("timestamp must be finite")
        if self._last_timestamp is not None and now < self._last_timestamp:
            raise ValueError("timestamps must be monotonic")
        if set(values) != set(self.names):
            raise ValueError("values must exactly match configured series names")
        converted = {name: float(values[name]) for name in self.names}
        if not all(math.isfinite(value) for value in converted.values()):
            raise ValueError("history values must be finite")
        for name, value in converted.items():
            self._points[name].append((now, value))
        self._last_timestamp = now
        self._evict(now)

    def series(self, now: float) -> dict[str, list[tuple[float, float]]]:
        timestamp = float(now)
        if not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite")
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise ValueError("series time cannot precede the latest sample")
        self._evict(timestamp)
        return {name: list(points) for name, points in self._points.items()}

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for points in self._points.values():
            while points and points[0][0] < cutoff:
                points.popleft()

