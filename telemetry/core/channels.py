from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    name: str
    unit: str
    label: str
    group: str


_REGISTRY: dict[str, Channel] = {}


def register(name: str, unit: str, label: str, group: str) -> str:
    if name in _REGISTRY:
        raise ValueError(f"channel already registered: {name}")
    _REGISTRY[name] = Channel(name=name, unit=unit, label=label, group=group)
    return name


def channel_meta(name: str) -> Channel | None:
    return _REGISTRY.get(name)


def all_channels() -> dict[str, Channel]:
    return dict(_REGISTRY)


BATTERY_SOC_PCT = register("battery.soc_pct", "%", "State of charge", "battery")
BATTERY_VOLTAGE_V = register("battery.voltage_v", "V", "Battery voltage", "battery")
BATTERY_CURRENT_A = register("battery.current_a", "A", "Battery current", "battery")
BATTERY_REMAINING_AH = register("battery.remaining_ah", "Ah", "Remaining Ah", "battery")
BATTERY_NOMINAL_AH = register("battery.nominal_ah", "Ah", "Nominal Ah", "battery")
BATTERY_TEMP_C = register("battery.temp_c", "degC", "Battery temp", "battery")
CHARGE_STATE = register("charge.state", "enum", "Charge state", "battery")

SOLAR_INPUT_W = register("solar.input_w", "W", "Solar input", "solar")
SOLAR_VOLTAGE_V = register("solar.voltage_v", "V", "PV voltage", "solar")

LOAD_W = register("load.w", "W", "Load", "load")
LOAD_CURRENT_A = register("load.current_a", "A", "Load current", "load")

UPS_SOC_PCT = register("ups.soc_pct", "%", "UPS charge", "ups")
UPS_VOLTAGE_V = register("ups.voltage_v", "V", "UPS voltage", "ups")
UPS_CURRENT_A = register("ups.current_a", "A", "UPS current", "ups")
UPS_POWER_W = register("ups.power_w", "W", "UPS power", "ups")

SERVER_GPU_W = register("server.gpu_w", "W", "GPU load power", "server")
WALL_TOTAL_W = register("wall.total_w", "W", "Wall total", "server")

VLLM_REQ_RUNNING = register("vllm.requests_running", "", "Requests running", "vllm")
VLLM_REQ_WAITING = register("vllm.requests_waiting", "", "Requests waiting", "vllm")
