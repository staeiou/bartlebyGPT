from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .deployment import Deployment, HttpConfig, Source, VALID_ROLES
from ..drivers.ina219 import Ina219Driver
from ..drivers.simulated import SimulatedDriver
from ..drivers.victron_vedirect import VeDirectDriver


DRIVERS = {
    "ina219": Ina219Driver,
    "simulated": SimulatedDriver,
    "victron-vedirect": VeDirectDriver,
}

REQUIRED_DEPLOYMENT_KEYS = {
    "id",
    "label",
    "http",
    "capacity_wh",
    "cost_model",
    "stale_after_s",
    "narrative_html",
    "sources",
}
REQUIRED_SOURCE_KEYS = {"kind", "role", "reconnect_delay", "capabilities"}


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
        if key not in {"kind", "role", "reconnect_delay", "capabilities"}
    }
    driver = DRIVERS[kind].from_config(driver_config)
    reconnect_delay = _number(f"{label}.reconnect_delay", config["reconnect_delay"])
    if reconnect_delay <= 0:
        raise ConfigError(f"{label}.reconnect_delay: must be > 0")
    return Source(
        kind=kind,
        role=role,
        driver=driver,
        channels=driver.channels,
        capabilities=dict(capabilities),
        reconnect_delay=reconnect_delay,
    )


def deployment_from_config(config: dict[str, Any]) -> Deployment:
    _require_keys("CONFIG", config, REQUIRED_DEPLOYMENT_KEYS)
    http = config["http"]
    if not isinstance(http, dict):
        raise ConfigError("CONFIG.http: expected dict")
    _require_keys("CONFIG.http", http, {"host", "port"})
    sources_config = config["sources"]
    if not isinstance(sources_config, list) or not sources_config:
        raise ConfigError("CONFIG.sources: expected non-empty list")

    return Deployment(
        id=str(config["id"]),
        label=str(config["label"]),
        http=HttpConfig(host=str(http["host"]), port=int(http["port"])),
        capacity_wh=_number("CONFIG.capacity_wh", config["capacity_wh"]),
        cost_model=str(config["cost_model"]),
        stale_after_s=_number("CONFIG.stale_after_s", config["stale_after_s"]),
        narrative_html=str(config["narrative_html"]),
        sources=tuple(_source_from_config(index, source) for index, source in enumerate(sources_config)),
    )


def load_deployment(path: str | Path) -> Deployment:
    return deployment_from_config(_literal_config(Path(path)))
