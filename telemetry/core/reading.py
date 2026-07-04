from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    channel: str
    value: float | str
    ts: float


class Snapshot:
    """Latest reading per channel, safe across source tasks and HTTP threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Reading] = {}

    def update(self, reading: Reading) -> None:
        with self._lock:
            previous = self._latest.get(reading.channel)
            if previous is not None and reading.ts < previous.ts:
                return
            self._latest[reading.channel] = reading

    def view(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._latest)

    def fresh_channels(self, max_age_s: float, now: float | None = None) -> set[str]:
        now = time.time() if now is None else now
        with self._lock:
            return {name for name, reading in self._latest.items() if now - reading.ts <= max_age_s}
