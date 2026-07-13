from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from ..core.channels import (
    BATTERY_CURRENT_A,
    BATTERY_NOMINAL_AH,
    BATTERY_REMAINING_AH,
    BATTERY_SOC_PCT,
    BATTERY_TEMP_C,
    BATTERY_VOLTAGE_V,
)
from ..core.reading import Reading


def _normalize_mac(value: str) -> str:
    compact = "".join(ch for ch in value.strip().upper() if ch != ":")
    if len(compact) != 12 or any(ch not in "0123456789ABCDEF" for ch in compact):
        raise ValueError(f"invalid BLE MAC address: {value!r}")
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2))


class VictronSmartShuntBleDriver:
    channels = (
        BATTERY_SOC_PCT,
        BATTERY_VOLTAGE_V,
        BATTERY_CURRENT_A,
        BATTERY_REMAINING_AH,
        BATTERY_NOMINAL_AH,
        BATTERY_TEMP_C,
    )

    def __init__(
        self,
        mac: str | None,
        mac_env: str | None,
        encryption_key: str | None,
        encryption_key_env: str | None,
        nominal_ah: float,
        advertisement_timeout: float,
    ) -> None:
        self.mac = _normalize_mac(mac) if mac else ""
        self.mac_env = mac_env.strip() if mac_env else ""
        self.encryption_key = encryption_key.strip() if encryption_key else ""
        self.encryption_key_env = encryption_key_env.strip() if encryption_key_env else ""
        self.nominal_ah = float(nominal_ah)
        self.advertisement_timeout = float(advertisement_timeout)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "VictronSmartShuntBleDriver":
        expected = {"mac", "mac_env", "encryption_key", "encryption_key_env", "nominal_ah", "advertisement_timeout"}
        unknown = sorted(set(config) - expected)
        missing = sorted({"nominal_ah", "advertisement_timeout"} - set(config))
        if unknown:
            raise ValueError(f"victron-smartshunt-ble: unknown key(s): {', '.join(unknown)}")
        if missing:
            raise ValueError(f"victron-smartshunt-ble: missing key(s): {', '.join(missing)}")
        if ("mac" in config) == ("mac_env" in config):
            raise ValueError("victron-smartshunt-ble: set exactly one of mac or mac_env")
        if ("encryption_key" in config) == ("encryption_key_env" in config):
            raise ValueError("victron-smartshunt-ble: set exactly one of encryption_key or encryption_key_env")
        nominal_ah = float(config["nominal_ah"])
        if nominal_ah <= 0:
            raise ValueError("victron-smartshunt-ble.nominal_ah: must be > 0")
        advertisement_timeout = float(config["advertisement_timeout"])
        if advertisement_timeout <= 0:
            raise ValueError("victron-smartshunt-ble.advertisement_timeout: must be > 0")
        mac = str(config.get("mac") or "").strip()
        mac_env = str(config.get("mac_env") or "").strip()
        if "mac" in config and not mac:
            raise ValueError("victron-smartshunt-ble.mac: must not be empty")
        if "mac_env" in config and not mac_env:
            raise ValueError("victron-smartshunt-ble.mac_env: must not be empty")
        encryption_key = str(config.get("encryption_key") or "").strip()
        encryption_key_env = str(config.get("encryption_key_env") or "").strip()
        if "encryption_key" in config and not encryption_key:
            raise ValueError("victron-smartshunt-ble.encryption_key: must not be empty")
        if "encryption_key_env" in config and not encryption_key_env:
            raise ValueError("victron-smartshunt-ble.encryption_key_env: must not be empty")
        return cls(
            mac=mac,
            mac_env=mac_env,
            encryption_key=encryption_key,
            encryption_key_env=encryption_key_env,
            nominal_ah=nominal_ah,
            advertisement_timeout=advertisement_timeout,
        )

    async def run(self, ctx) -> None:
        from bleak import BleakScanner
        from victron_ble.devices.battery_monitor import BatteryMonitor

        encryption_key = self.encryption_key
        if self.encryption_key_env:
            encryption_key = os.environ.get(self.encryption_key_env, "").strip()
        if not encryption_key:
            raise RuntimeError(
                f"SmartShunt advertisement key missing; set {self.encryption_key_env}"
                if self.encryption_key_env
                else "SmartShunt advertisement key missing"
            )
        mac = self.mac
        if self.mac_env:
            raw_mac = os.environ.get(self.mac_env, "").strip()
            mac = _normalize_mac(raw_mac) if raw_mac else ""
        if not mac:
            raise RuntimeError(
                f"SmartShunt BLE MAC missing; set {self.mac_env}"
                if self.mac_env
                else "SmartShunt BLE MAC missing"
            )
        queue: asyncio.Queue[dict[int, bytes]] = asyncio.Queue(maxsize=8)
        parser = BatteryMonitor(encryption_key)
        last_decoded = time.monotonic()

        def on_detection(device, adv) -> None:
            if device.address.upper() != mac:
                return
            try:
                queue.put_nowait(dict(adv.manufacturer_data))
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(dict(adv.manufacturer_data))

        scanner = BleakScanner(on_detection)
        ctx.log.info("victron-smartshunt-ble: scanning for %s", mac)
        await scanner.start()
        try:
            while True:
                try:
                    manufacturer_data = await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - last_decoded
                    if elapsed >= self.advertisement_timeout:
                        raise TimeoutError(
                            f"no decodable SmartShunt Instant Readout from {mac} for {elapsed:.1f}s"
                        )
                    continue

                if self._parse_and_emit(ctx, parser, manufacturer_data):
                    last_decoded = time.monotonic()
        finally:
            await scanner.stop()

    def _parse_and_emit(self, ctx, parser, manufacturer_data: dict[int, bytes]) -> bool:
        last_error: Exception | None = None
        for _manufacturer_id, payload in manufacturer_data.items():
            if not payload.startswith(b"\x10"):
                ctx.log.debug("victron-smartshunt-ble: ignoring non-Instant-Readout payload %s", payload.hex())
                continue
            try:
                result = parser.parse(payload)
            except Exception as err:  # keep trying other manufacturer-data records
                last_error = err
                continue

            now = time.time()
            soc = result.get_soc()
            voltage = result.get_voltage()
            current = result.get_current()
            temperature = result.get_temperature()

            if soc is not None:
                ctx.emit(Reading(BATTERY_SOC_PCT, soc, now))
                ctx.emit(Reading(BATTERY_REMAINING_AH, round((soc / 100.0) * self.nominal_ah, 3), now))
            if voltage is not None:
                ctx.emit(Reading(BATTERY_VOLTAGE_V, voltage, now))
            if current is not None:
                ctx.emit(Reading(BATTERY_CURRENT_A, current, now))
            if temperature is not None:
                ctx.emit(Reading(BATTERY_TEMP_C, temperature, now))
            ctx.emit(Reading(BATTERY_NOMINAL_AH, self.nominal_ah, now))
            ctx.log.debug(
                "victron-smartshunt-ble: model=%s soc=%s voltage=%s current=%s",
                result.get_model_name(),
                soc,
                voltage,
                current,
            )
            return True
        if last_error is not None:
            raise last_error
        return False
