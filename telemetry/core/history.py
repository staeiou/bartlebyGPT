from __future__ import annotations

import asyncio
import math
import os
import sqlite3
import time
from pathlib import Path

from . import deployment as dep


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS battery_events (
    reading_ts_ms INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    load_w REAL,
    charge_w REAL,
    soc_pct REAL,
    solar_input_w REAL,
    voltage_mv REAL,
    temp_c REAL,
    charging_status INTEGER,
    victron_model_name TEXT,
    victron_charge_state TEXT,
    victron_charger_error TEXT,
    victron_battery_voltage_v REAL,
    victron_battery_charging_current_a REAL,
    victron_battery_power_w REAL,
    victron_external_device_load_a REAL,
    victron_yield_today_wh REAL,
    victron_manufacturer_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_battery_events_ts ON battery_events(ts);
"""


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


def history_db_path() -> str:
    return _env_first("BATT_HISTORY_DB_PATH", "TELEMETRY_HISTORY_DB_PATH").strip()


def history_interval_seconds() -> float:
    raw = _env_first("BATT_HISTORY_INTERVAL", "BATT_CSV_INTERVAL", default="60")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 60.0
    return max(1.0, value)


def _to_millis(ts: float) -> int:
    return int(round(float(ts) * 1000.0))


def _number(value):
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _value(channels: dict, name: str):
    reading = channels.get(name)
    return reading.value if reading else None


def _ts(channels: dict, *names: str):
    for name in names:
        reading = channels.get(name)
        if reading:
            return reading.ts
    return None


class BatteryHistoryWriter:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA_SQL)
        self._ensure_shared_permissions()

    def _ensure_shared_permissions(self) -> None:
        for candidate in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            try:
                if os.path.exists(candidate):
                    os.chmod(candidate, 0o666)
            except OSError:
                continue

    def record(self, row: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO battery_events (
                    reading_ts_ms,
                    ts,
                    load_w,
                    charge_w,
                    soc_pct,
                    solar_input_w,
                    voltage_mv,
                    temp_c,
                    charging_status,
                    victron_battery_voltage_v,
                    victron_battery_charging_current_a,
                    victron_battery_power_w,
                    victron_external_device_load_a
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _to_millis(row["ts"]),
                    row["ts"],
                    row.get("load_w"),
                    row.get("charge_w"),
                    row.get("soc_pct"),
                    row.get("solar_input_w"),
                    row.get("voltage_mv"),
                    row.get("temp_c"),
                    row.get("charging_status"),
                    row.get("victron_battery_voltage_v"),
                    row.get("victron_battery_charging_current_a"),
                    row.get("victron_battery_power_w"),
                    row.get("victron_external_device_load_a"),
                ),
            )
        self._ensure_shared_permissions()


def build_battery_history_row(deployment, snapshot, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    channels = dep.resolve_channels(deployment, snapshot, now)
    load_w = _number(_value(channels, "load.w"))
    if load_w is None:
        load_w = _number(_value(channels, "wall.total_w"))
    solar_input_w = _number(_value(channels, "solar.input_w"))
    charge_w = _number(_value(channels, "battery.charge_w"))
    soc_pct = _number(_value(channels, "battery.soc_pct"))
    voltage_v = _number(_value(channels, "battery.voltage_v"))
    current_a = _number(_value(channels, "battery.current_a"))
    temp_c = _number(_value(channels, "battery.temp_c"))
    ts = _ts(
        channels,
        "load.w",
        "wall.total_w",
        "solar.input_w",
        "battery.soc_pct",
        "battery.voltage_v",
        "battery.current_a",
    )

    if ts is None or (load_w is None and solar_input_w is None and charge_w is None and soc_pct is None):
        return None

    battery_power_w = None
    if voltage_v is not None and current_a is not None:
        battery_power_w = round(voltage_v * current_a, 3)

    return {
        "ts": float(ts),
        "load_w": load_w,
        "charge_w": charge_w,
        "soc_pct": soc_pct,
        "solar_input_w": solar_input_w,
        "voltage_mv": round(voltage_v * 1000.0, 3) if voltage_v is not None else None,
        "temp_c": temp_c,
        "charging_status": _value(channels, "charge.state"),
        "victron_battery_voltage_v": voltage_v,
        "victron_battery_charging_current_a": current_a,
        "victron_battery_power_w": battery_power_w,
        "victron_external_device_load_a": _number(_value(channels, "load.current_a")),
    }


async def battery_history_loop(deployment, snapshot, log) -> None:
    db_path = history_db_path()
    if not db_path:
        log.info("battery history sqlite disabled: no BATT_HISTORY_DB_PATH")
        return

    writer = BatteryHistoryWriter(db_path)
    interval = history_interval_seconds()
    last_ts = None
    log.info("battery history sqlite: writing %s every %.1fs", db_path, interval)
    while True:
        row = build_battery_history_row(deployment, snapshot)
        if row is not None and row["ts"] != last_ts:
            try:
                writer.record(row)
                last_ts = row["ts"]
            except Exception as err:
                log.warning("battery history sqlite write failed: %s", err)
        await asyncio.sleep(interval)
