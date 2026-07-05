from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core.channels import (
    BATTERY_CURRENT_A,
    BATTERY_VOLTAGE_V,
    CHARGE_STATE,
    LOAD_CURRENT_A,
    LOAD_W,
    SOLAR_INPUT_W,
    SOLAR_VOLTAGE_V,
)
from ..core.reading import Reading

_CHARGE_STATE = {
    0: "off",
    2: "fault",
    3: "bulk",
    4: "absorption",
    5: "float",
    7: "equalize",
    245: "starting-up",
    247: "auto-equalize",
    252: "ext-control",
}


class VeDirectDriver:
    channels = (
        BATTERY_VOLTAGE_V,
        BATTERY_CURRENT_A,
        SOLAR_INPUT_W,
        SOLAR_VOLTAGE_V,
        LOAD_CURRENT_A,
        LOAD_W,
        CHARGE_STATE,
    )

    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.baud = int(baud)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "VeDirectDriver":
        expected = {"port", "baud"}
        unknown = sorted(set(config) - expected)
        missing = sorted(expected - set(config))
        if unknown:
            raise ValueError(f"victron-vedirect: unknown key(s): {', '.join(unknown)}")
        if missing:
            raise ValueError(f"victron-vedirect: missing key(s): {', '.join(missing)}")
        return cls(port=str(config["port"]), baud=int(config["baud"]))

    async def run(self, ctx) -> None:
        import serial

        loop = asyncio.get_running_loop()
        ser = serial.Serial(self.port, self.baud, timeout=2)
        ctx.log.info("victron-vedirect: reading %s @ %d", self.port, self.baud)
        frame: dict[str, str] = {}
        try:
            while True:
                raw = await loop.run_in_executor(None, ser.readline)
                if not raw:
                    continue
                line = raw.decode("ascii", "replace").strip()
                key, _, value = line.partition("\t")
                if key == "Checksum":
                    self._emit(ctx, frame)
                    frame = {}
                elif key:
                    frame[key] = value
        finally:
            ser.close()

    def _emit(self, ctx, frame: dict[str, str]) -> None:
        now = time.time()

        def num(key: str) -> int | None:
            try:
                return int(frame[key])
            except (KeyError, ValueError):
                return None

        values = (
            (BATTERY_VOLTAGE_V, num("V"), 1000.0),
            (BATTERY_CURRENT_A, num("I"), 1000.0),
            (SOLAR_INPUT_W, num("PPV"), 1.0),
            (SOLAR_VOLTAGE_V, num("VPV"), 1000.0),
            (LOAD_CURRENT_A, num("IL"), 1000.0),
        )
        for channel, raw, divisor in values:
            if raw is not None:
                ctx.emit(Reading(channel, raw / divisor, now))
        battery_mv = num("V")
        load_ma = num("IL")
        if battery_mv is not None and load_ma is not None:
            ctx.emit(Reading(LOAD_W, round((battery_mv / 1000.0) * (load_ma / 1000.0), 1), now))
        charge_state = num("CS")
        if charge_state is not None:
            ctx.emit(Reading(CHARGE_STATE, _CHARGE_STATE.get(charge_state, str(charge_state)), now))
