from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .deployment import Deployment, HttpConfig, Source, VALID_ROLES
from ..drivers.ble_placeholders import (
    JbdBmsBleDriver,
    SolixC300xBleDriver,
    VictronMpptBleDriver,
    VictronSmartShuntBleDriver,
)
from ..drivers.ina219 import Ina219Driver
from ..drivers.simulated import SimulatedDriver
from ..drivers.victron_vedirect import VeDirectDriver


DRIVERS = {
    "ina219": Ina219Driver,
    "jbd-bms-ble": JbdBmsBleDriver,
    "simulated": SimulatedDriver,
    "solix-c300x-ble": SolixC300xBleDriver,
    "victron-mppt-ble": VictronMpptBleDriver,
    "victron-smartshunt-ble": VictronSmartShuntBleDriver,
    "victron-vedirect": VeDirectDriver,
}

REQUIRED_DEPLOYMENT_KEYS = {
    "id",
    "label",
    "http",
    "capacity_wh",
    "cost_model",
    "narrative_html",
    "channel_priority",
    "mode_channels",
    "sources",
}
REQUIRED_SOURCE_KEYS = {"id", "kind", "role", "reconnect_delay", "stale_after_s", "capabilities"}


class ConfigError(ValueError):
    pass


def _literal_config(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    config_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CONFIG":
                    config_node = node.value
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        else:
            raise ConfigError(f"{path}: deployment files may only contain a CONFIG literal")
    if config_node is None:
        raise ConfigError(f"{path}: missing CONFIG")
    try:
        config = ast.literal_eval(config_node)
    except (SyntaxError, ValueError) as err:
        raise ConfigError(f"{path}: CONFIG must be a Python literal") from err
    if not isinstance(config, dict):
        raise ConfigError(f"{path}: CONFIG must be a dict")
    return config


def _require_keys(label: str, value: dict[str, Any], required: set[str]) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise ConfigError(f"{label}: missing required key(s): {', '.join(missing)}")


def _number(label: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label}: expected number")
    return float(value)


def _source_from_config(index: int, config: dict[str, Any]) -> Source:
    label = f"source[{index}]"
    if not isinstance(config, dict):
        raise ConfigError(f"{label}: expected dict")
    _require_keys(label, config, REQUIRED_SOURCE_KEYS)

    kind = config["kind"]
    if kind not in DRIVERS:
        raise ConfigError(f"{label}: unknown source kind {kind!r}")
    role = config["role"]
    if role not in VALID_ROLES:
        raise ConfigError(f"{label}: invalid role {role!r}")
    capabilities = config["capabilities"]
    if not isinstance(capabilities, dict):
        raise ConfigError(f"{label}.capabilities: expected dict")

    driver_config = {
        key: value
        for key, value in config.items()
        if key not in {"id", "kind", "role", "reconnect_delay", "stale_after_s", "capabilities"}
    }
    try:
        driver = DRIVERS[kind].from_config(driver_config)
    except ValueError as err:
        raise ConfigError(f"{label}: {err}") from err
    reconnect_delay = _number(f"{label}.reconnect_delay", config["reconnect_delay"])
    if reconnect_delay <= 0:
        raise ConfigError(f"{label}.reconnect_delay: must be > 0")
    stale_after_s = _number(f"{label}.stale_after_s", config["stale_after_s"])
    if stale_after_s <= 0:
        raise ConfigError(f"{label}.stale_after_s: must be > 0")
    return Source(
        id=str(config["id"]),
        kind=kind,
        role=role,
        driver=driver,
        channels=driver.channels,
        capabilities=dict(capabilities),
        reconnect_delay=reconnect_delay,
        stale_after_s=stale_after_s,
    )


def _tuple_map(label: str, config: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(config, dict):
        raise ConfigError(f"{label}: expected dict")
    result: dict[str, tuple[str, ...]] = {}
    for key, values in config.items():
        if not isinstance(values, list):
            raise ConfigError(f"{label}.{key}: expected list")
        result[str(key)] = tuple(str(value) for value in values)
    return result


def deployment_from_config(config: dict[str, Any]) -> Deployment:
    _require_keys("CONFIG", config, REQUIRED_DEPLOYMENT_KEYS)
    http = config["http"]
    if not isinstance(http, dict):
        raise ConfigError("CONFIG.http: expected dict")
    _require_keys("CONFIG.http", http, {"host", "port"})
    sources_config = config["sources"]
    if not isinstance(sources_config, list) or not sources_config:
        raise ConfigError("CONFIG.sources: expected non-empty list")

    sources = tuple(_source_from_config(index, source) for index, source in enumerate(sources_config))
    source_ids = [source.id for source in sources]
    duplicate_ids = sorted({source_id for source_id in source_ids if source_ids.count(source_id) > 1})
    if duplicate_ids:
        raise ConfigError(f"CONFIG.sources: duplicate source id(s): {', '.join(duplicate_ids)}")
    source_id_set = set(source_ids)
    channel_priority = _tuple_map("CONFIG.channel_priority", config["channel_priority"])
    for channel, priorities in channel_priority.items():
        unknown = sorted(set(priorities) - source_id_set)
        if unknown:
            raise ConfigError(f"CONFIG.channel_priority.{channel}: unknown source id(s): {', '.join(unknown)}")

    return Deployment(
        id=str(config["id"]),
        label=str(config["label"]),
        http=HttpConfig(host=str(http["host"]), port=int(http["port"])),
        capacity_wh=_number("CONFIG.capacity_wh", config["capacity_wh"]),
        cost_model=str(config["cost_model"]),
        narrative_html=str(config["narrative_html"]),
        channel_priority=channel_priority,
        mode_channels=_tuple_map("CONFIG.mode_channels", config["mode_channels"]),
        sources=sources,
    )


def load_deployment(path: str | Path) -> Deployment:
    return deployment_from_config(_literal_config(Path(path)))
