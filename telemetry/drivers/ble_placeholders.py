from __future__ import annotations

from typing import Any

from ..core.channels import (
    BATTERY_CURRENT_A,
    BATTERY_NOMINAL_AH,
    BATTERY_REMAINING_AH,
    BATTERY_SOC_PCT,
    BATTERY_TEMP_C,
    BATTERY_VOLTAGE_V,
    CHARGE_STATE,
    LOAD_W,
    SOLAR_INPUT_W,
    SOLAR_VOLTAGE_V,
)


def _require_exact(kind: str, config: dict[str, Any], required: set[str]) -> dict[str, Any]:
    unknown = sorted(set(config) - required)
    missing = sorted(required - set(config))
    if unknown:
        raise ValueError(f"{kind}: unknown key(s): {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{kind}: missing key(s): {', '.join(missing)}")
    return dict(config)


class _PendingBleDriver:
    kind = "pending-ble"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config)

    async def run(self, _ctx) -> None:
        raise NotImplementedError(f"{self.kind} driver is not implemented yet")


class SolixC300xBleDriver(_PendingBleDriver):
    kind = "solix-c300x-ble"
    channels = (
        BATTERY_SOC_PCT,
        BATTERY_VOLTAGE_V,
        BATTERY_TEMP_C,
        SOLAR_INPUT_W,
        LOAD_W,
    )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SolixC300xBleDriver":
        return cls(_require_exact(cls.kind, config, {"mac"}))


class VictronMpptBleDriver(_PendingBleDriver):
    kind = "victron-mppt-ble"
    channels = (
        BATTERY_VOLTAGE_V,
        BATTERY_CURRENT_A,
        SOLAR_INPUT_W,
        SOLAR_VOLTAGE_V,
        LOAD_W,
        CHARGE_STATE,
    )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "VictronMpptBleDriver":
        return cls(_require_exact(cls.kind, config, {"mac", "encryption_key"}))


class JbdBmsBleDriver(_PendingBleDriver):
    kind = "jbd-bms-ble"
    channels = (
        BATTERY_SOC_PCT,
        BATTERY_VOLTAGE_V,
        BATTERY_CURRENT_A,
        BATTERY_REMAINING_AH,
        BATTERY_NOMINAL_AH,
        BATTERY_TEMP_C,
    )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "JbdBmsBleDriver":
        return cls(_require_exact(cls.kind, config, {"mac", "poll_interval"}))
