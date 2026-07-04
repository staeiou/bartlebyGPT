from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Protocol, runtime_checkable

from .reading import Reading, Snapshot

ROLE_MAIN = "main"
ROLE_UPS = "ups"
ROLE_METER = "meter"
VALID_ROLES = {ROLE_MAIN, ROLE_UPS, ROLE_METER}


class SourceContext:
    def __init__(self, emit: Callable[[Reading], None], log, ble=None) -> None:
        self.emit = emit
        self.log = log
        self.ble = ble


@runtime_checkable
class Driver(Protocol):
    channels: tuple[str, ...]

    async def run(self, ctx: SourceContext) -> None:
        ...


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    role: str
    driver: Driver
    channels: tuple[str, ...]
    capabilities: dict
    reconnect_delay: float
    stale_after_s: float

    async def run(self, ctx: SourceContext) -> None:
        source_id = self.id

        def emit(reading: Reading) -> None:
            ctx.emit(replace(reading, source_id=source_id))

        await self.driver.run(SourceContext(emit=emit, log=ctx.log, ble=ctx.ble))


@dataclass(frozen=True)
class HttpConfig:
    host: str
    port: int


@dataclass(frozen=True)
class Deployment:
    id: str
    label: str
    http: HttpConfig
    sources: tuple[Source, ...]
    capacity_wh: float
    cost_model: str
    narrative_html: str
    channel_priority: dict[str, tuple[str, ...]]
    mode_channels: dict[str, tuple[str, ...]]

    def descriptor(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "capacity_wh": self.capacity_wh,
            "cost_model": self.cost_model,
            "narrative_html": self.narrative_html,
        }


def source_stale_windows(deployment: Deployment) -> dict[str, float]:
    return {source.id: source.stale_after_s for source in deployment.sources}


def resolve_channels(deployment: Deployment, snapshot: Snapshot, now: float) -> dict[str, Reading]:
    fresh = snapshot.fresh_by_source(source_stale_windows(deployment), now)
    resolved: dict[str, Reading] = {}
    for channel, readings in fresh.items():
        for source_id in deployment.channel_priority.get(channel, ()):
            if source_id in readings:
                resolved[channel] = readings[source_id]
                break
        if channel not in resolved:
            resolved[channel] = max(readings.values(), key=lambda reading: reading.ts)
    return resolved


def power_source_mode(deployment: Deployment, snapshot: Snapshot, now: float) -> str:
    resolved = resolve_channels(deployment, snapshot, now)
    if any(channel in resolved for channel in deployment.mode_channels.get("main", ())):
        return "main"
    if any(channel in resolved for channel in deployment.mode_channels.get("ups", ())):
        return "ups"
    return "unknown"


def capabilities(deployment: Deployment, snapshot: Snapshot, now: float) -> dict:
    resolved = resolve_channels(deployment, snapshot, now)
    source_caps: dict = {}
    for source in deployment.sources:
        for key, value in source.capabilities.items():
            source_caps[key] = source_caps.get(key, False) or value
    return {
        "has_battery": "battery.soc_pct" in resolved,
        "has_ups": "ups.soc_pct" in resolved,
        "has_solar": "solar.input_w" in resolved,
        "has_load": "load.w" in resolved or "wall.total_w" in resolved,
        "solar_measured": bool(source_caps.get("solar_measured")),
        "power_source_mode": power_source_mode(deployment, snapshot, now),
    }
