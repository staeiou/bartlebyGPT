from __future__ import annotations

from dataclasses import dataclass
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
    kind: str
    role: str
    driver: Driver
    channels: tuple[str, ...]
    capabilities: dict
    reconnect_delay: float

    async def run(self, ctx: SourceContext) -> None:
        await self.driver.run(ctx)


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
    stale_after_s: float
    narrative_html: str

    def descriptor(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "capacity_wh": self.capacity_wh,
            "cost_model": self.cost_model,
            "narrative_html": self.narrative_html,
        }


def roles_with_fresh_data(deployment: Deployment, snapshot: Snapshot, now: float) -> set[str]:
    fresh = snapshot.fresh_channels(deployment.stale_after_s, now)
    roles: set[str] = set()
    for source in deployment.sources:
        if any(channel in fresh for channel in source.channels):
            roles.add(source.role)
    return roles


def power_source_mode(deployment: Deployment, snapshot: Snapshot, now: float) -> str:
    roles = roles_with_fresh_data(deployment, snapshot, now)
    if ROLE_MAIN in roles:
        return "main"
    if ROLE_UPS in roles:
        return "ups"
    return "unknown"


def capabilities(deployment: Deployment, snapshot: Snapshot, now: float) -> dict:
    fresh = snapshot.fresh_channels(deployment.stale_after_s, now)
    source_caps: dict = {}
    for source in deployment.sources:
        for key, value in source.capabilities.items():
            source_caps[key] = source_caps.get(key, False) or value
    return {
        "has_battery": "battery.soc_pct" in fresh,
        "has_ups": "ups.soc_pct" in fresh,
        "has_solar": "solar.input_w" in fresh,
        "solar_measured": bool(source_caps.get("solar_measured")),
        "power_source_mode": power_source_mode(deployment, snapshot, now),
    }
