from __future__ import annotations

import asyncio
import fcntl
import os
import time
from typing import Any

from ..core.channels import UPS_CURRENT_A, UPS_POWER_W, UPS_SOC_PCT, UPS_VOLTAGE_V
from ..core.reading import Reading

I2C_SLAVE = 0x0703
REG_CONFIG = 0x00
REG_SHUNT = 0x01
REG_BUS = 0x02
REG_POWER = 0x03
REG_CURRENT = 0x04
REG_CAL = 0x05
DEFAULT_CONFIG = 0x0EEF
DEFAULT_CALIBRATION = 0x68F4
CURRENT_LSB_MA = 0.1524
POWER_LSB_W = 0.003048


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


class Ina219Driver:
    channels = (UPS_VOLTAGE_V, UPS_CURRENT_A, UPS_POWER_W, UPS_SOC_PCT)

    def __init__(self, bus: int, addr: int, interval: float) -> None:
        self.bus = int(bus)
        self.addr = int(addr)
        self.interval = float(interval)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Ina219Driver":
        expected = {"bus", "addr", "interval"}
        unknown = sorted(set(config) - expected)
        missing = sorted(expected - set(config))
        if unknown:
            raise ValueError(f"ina219: unknown key(s): {', '.join(unknown)}")
        if missing:
            raise ValueError(f"ina219: missing key(s): {', '.join(missing)}")
        return cls(bus=int(config["bus"]), addr=int(config["addr"]), interval=float(config["interval"]))

    async def run(self, ctx) -> None:
        loop = asyncio.get_running_loop()
        fd = os.open(f"/dev/i2c-{self.bus}", os.O_RDWR)
        fcntl.ioctl(fd, I2C_SLAVE, self.addr)
        ctx.log.info("ina219: i2c bus=%d addr=0x%02x", self.bus, self.addr)
        try:
            while True:
                await loop.run_in_executor(None, self._sample_and_emit, fd, ctx)
                await asyncio.sleep(self.interval)
        finally:
            os.close(fd)

    def _read16(self, fd: int, reg: int) -> int:
        os.write(fd, bytes([reg & 0xFF]))
        data = os.read(fd, 2)
        if len(data) != 2:
            raise OSError(f"short INA219 read from 0x{reg:02x}")
        return (data[0] << 8) | data[1]

    def _write16(self, fd: int, reg: int, value: int) -> None:
        os.write(fd, bytes([reg & 0xFF, (value >> 8) & 0xFF, value & 0xFF]))

    def _sample_and_emit(self, fd: int, ctx) -> None:
        self._write16(fd, REG_CAL, DEFAULT_CALIBRATION)
        self._write16(fd, REG_CONFIG, DEFAULT_CONFIG)
        raw_shunt = self._read16(fd, REG_SHUNT)
        raw_bus = self._read16(fd, REG_BUS)
        raw_current = self._read16(fd, REG_CURRENT)
        raw_power = self._read16(fd, REG_POWER)

        shunt_mv = _signed16(raw_shunt) * 0.01
        bus_v = (raw_bus >> 3) * 0.004
        current_a = _signed16(raw_current) * CURRENT_LSB_MA / 1000.0
        power_w = _signed16(raw_power) * POWER_LSB_W
        soc_pct = max(0.0, min((bus_v - 9.0) / 3.6 * 100.0, 100.0))

        now = time.time()
        ctx.emit(Reading(UPS_VOLTAGE_V, round(bus_v + shunt_mv / 1000.0, 3), now))
        ctx.emit(Reading(UPS_CURRENT_A, round(current_a, 3), now))
        ctx.emit(Reading(UPS_POWER_W, round(power_w, 3), now))
        ctx.emit(Reading(UPS_SOC_PCT, round(soc_pct, 1), now))
