from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core.reading import Reading


class SimulatedDriver:
    def __init__(self, values: dict[str, float | str], interval: float, emit_for: float | None) -> None:
        self.values = dict(values)
        self.interval = float(interval)
        self.emit_for = emit_for
        self.channels = tuple(self.values.keys())

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SimulatedDriver":
        expected = {"values", "interval", "emit_for"}
        unknown = sorted(set(config) - expected)
        missing = sorted(expected - set(config))
        if unknown:
            raise ValueError(f"simulated: unknown key(s): {', '.join(unknown)}")
        if missing:
            raise ValueError(f"simulated: missing key(s): {', '.join(missing)}")
        values = config["values"]
        if not isinstance(values, dict) or not values:
            raise ValueError("simulated.values: expected non-empty dict")
        emit_for = config["emit_for"]
        if emit_for is not None:
            emit_for = float(emit_for)
        return cls(values=values, interval=float(config["interval"]), emit_for=emit_for)

    async def run(self, ctx) -> None:
        started = time.monotonic()
        while True:
            if self.emit_for is None or time.monotonic() - started < self.emit_for:
                now = time.time()
                for channel, value in self.values.items():
                    ctx.emit(Reading(channel, value, now))
            await asyncio.sleep(self.interval)
