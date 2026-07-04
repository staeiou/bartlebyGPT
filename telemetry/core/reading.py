from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    channel: str
    value: float | str
    ts: float
    source_id: str = ""


class Snapshot:
    """Latest reading per channel, safe across source tasks and HTTP threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, dict[str, Reading]] = {}

    def update(self, reading: Reading) -> None:
        with self._lock:
            if not reading.source_id:
                raise ValueError(f"reading missing source_id for channel {reading.channel}")
            channel_readings = self._latest.setdefault(reading.channel, {})
            previous = channel_readings.get(reading.source_id)
            if previous is not None and reading.ts < previous.ts:
                return
            channel_readings[reading.source_id] = reading

    def view(self) -> dict[str, dict[str, Reading]]:
        with self._lock:
            return {channel: dict(readings) for channel, readings in self._latest.items()}

    def fresh_by_source(self, source_max_age_s: dict[str, float], now: float | None = None) -> dict[str, dict[str, Reading]]:
        now = time.time() if now is None else now
        with self._lock:
            fresh: dict[str, dict[str, Reading]] = {}
            for channel, readings in self._latest.items():
                for source_id, reading in readings.items():
                    max_age_s = source_max_age_s.get(source_id)
                    if max_age_s is not None and now - reading.ts <= max_age_s:
                        fresh.setdefault(channel, {})[source_id] = reading
            return fresh
