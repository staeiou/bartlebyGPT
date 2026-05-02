#!/usr/bin/env python3
"""Independent Waveshare UPS HAT INA219 logger.

This service is intentionally separate from lfp-monitor and power_telemetry.py.
It records local UPS HAT readings to its own daily CSV files and SQLite database.
"""

import argparse
import csv
import fcntl
import json
import logging
import math
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


LOG = logging.getLogger("ups-hat-monitor")

I2C_SLAVE = 0x0703

REG_CONFIG = 0x00
REG_SHUNT_VOLTAGE = 0x01
REG_BUS_VOLTAGE = 0x02
REG_POWER = 0x03
REG_CURRENT = 0x04
REG_CALIBRATION = 0x05

DEFAULT_CONFIG = 0x0EEF
DEFAULT_CALIBRATION = 0x68F4
CURRENT_LSB_MA = 0.1524
POWER_LSB_W = 0.003048

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ups_hat_samples (
    reading_ts_ms INTEGER PRIMARY KEY,
    reading_ts REAL NOT NULL,
    timestamp TEXT NOT NULL,
    i2c_bus INTEGER NOT NULL,
    i2c_addr TEXT NOT NULL,
    bus_voltage_v REAL,
    shunt_voltage_mv REAL,
    supply_voltage_v REAL,
    current_a REAL,
    power_w REAL,
    soc_pct_est REAL,
    direction TEXT,
    raw_bus_voltage INTEGER,
    raw_shunt_voltage INTEGER,
    raw_current INTEGER,
    raw_power INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ups_hat_samples_reading_ts
ON ups_hat_samples(reading_ts);
"""

CSV_FIELDS = [
    "timestamp",
    "reading_ts",
    "i2c_bus",
    "i2c_addr",
    "bus_voltage_v",
    "shunt_voltage_mv",
    "supply_voltage_v",
    "current_a",
    "power_w",
    "soc_pct_est",
    "direction",
    "raw_bus_voltage",
    "raw_shunt_voltage",
    "raw_current",
    "raw_power",
]


def parse_int(value):
    if isinstance(value, int):
        return value
    return int(str(value), 0)


def utc_iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def to_millis(ts):
    return int(round(float(ts) * 1000.0))


def signed16(value):
    value = int(value) & 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def clamp(value, lo, hi):
    return max(lo, min(value, hi))


class INA219:
    def __init__(self, bus, addr):
        self.bus = int(bus)
        self.addr = int(addr)
        self.path = f"/dev/i2c-{self.bus}"
        self.fd = os.open(self.path, os.O_RDWR)
        fcntl.ioctl(self.fd, I2C_SLAVE, self.addr)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read16(self, register):
        os.write(self.fd, bytes([register & 0xFF]))
        data = os.read(self.fd, 2)
        if len(data) != 2:
            raise OSError(f"short INA219 read from register 0x{register:02x}: {len(data)} bytes")
        return (data[0] << 8) | data[1]

    def write16(self, register, value):
        os.write(self.fd, bytes([register & 0xFF, (value >> 8) & 0xFF, value & 0xFF]))

    def configure(self):
        self.write16(REG_CALIBRATION, DEFAULT_CALIBRATION)
        self.write16(REG_CONFIG, DEFAULT_CONFIG)

    def sample(self):
        self.configure()
        raw_shunt = self.read16(REG_SHUNT_VOLTAGE)
        raw_bus = self.read16(REG_BUS_VOLTAGE)
        raw_current = self.read16(REG_CURRENT)
        raw_power = self.read16(REG_POWER)

        shunt_mv = signed16(raw_shunt) * 0.01
        bus_v = (raw_bus >> 3) * 0.004
        current_a = signed16(raw_current) * CURRENT_LSB_MA / 1000.0
        power_w = signed16(raw_power) * POWER_LSB_W
        supply_v = bus_v + (shunt_mv / 1000.0)
        soc_pct = clamp((bus_v - 9.0) / 3.6 * 100.0, 0.0, 100.0)

        if current_a > 0.05:
            direction = "charging"
        elif current_a < -0.05:
            direction = "discharging"
        else:
            direction = "idle"

        reading_ts = time.time()
        return {
            "timestamp": utc_iso(reading_ts),
            "reading_ts": f"{reading_ts:.6f}",
            "i2c_bus": self.bus,
            "i2c_addr": f"0x{self.addr:02x}",
            "bus_voltage_v": round(bus_v, 6),
            "shunt_voltage_mv": round(shunt_mv, 6),
            "supply_voltage_v": round(supply_v, 6),
            "current_a": round(current_a, 6),
            "power_w": round(power_w, 6),
            "soc_pct_est": round(soc_pct, 3),
            "direction": direction,
            "raw_bus_voltage": raw_bus,
            "raw_shunt_voltage": signed16(raw_shunt),
            "raw_current": signed16(raw_current),
            "raw_power": signed16(raw_power),
        }


class Store:
    def __init__(self, log_dir, db_path):
        self.log_dir = Path(log_dir)
        self.db_path = Path(db_path)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA_SQL)
        self._chmod_outputs()

    def _chmod_outputs(self):
        for candidate in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            try:
                if candidate.exists():
                    candidate.chmod(0o644)
            except OSError:
                pass

    def write_sample(self, sample):
        self._write_csv(sample)
        self._write_sqlite(sample)

    def _write_csv(self, sample):
        reading_ts = float(sample["reading_ts"])
        ts = datetime.fromtimestamp(reading_ts, tz=timezone.utc)
        csv_path = self.log_dir / f"ups-hat-{ts.strftime('%Y-%m-%d')}.csv"
        write_header = not csv_path.exists() or csv_path.stat().st_size == 0
        with open(csv_path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow({field: sample.get(field) for field in CSV_FIELDS})
        try:
            csv_path.chmod(0o644)
        except OSError:
            pass

    def _write_sqlite(self, sample):
        reading_ts = float(sample["reading_ts"])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ups_hat_samples (
                    reading_ts_ms,
                    reading_ts,
                    timestamp,
                    i2c_bus,
                    i2c_addr,
                    bus_voltage_v,
                    shunt_voltage_mv,
                    supply_voltage_v,
                    current_a,
                    power_w,
                    soc_pct_est,
                    direction,
                    raw_bus_voltage,
                    raw_shunt_voltage,
                    raw_current,
                    raw_power
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    to_millis(reading_ts),
                    reading_ts,
                    sample["timestamp"],
                    sample["i2c_bus"],
                    sample["i2c_addr"],
                    sample["bus_voltage_v"],
                    sample["shunt_voltage_mv"],
                    sample["supply_voltage_v"],
                    sample["current_a"],
                    sample["power_w"],
                    sample["soc_pct_est"],
                    sample["direction"],
                    sample["raw_bus_voltage"],
                    sample["raw_shunt_voltage"],
                    sample["raw_current"],
                    sample["raw_power"],
                ),
            )
        self._chmod_outputs()


def env_config():
    log_dir = os.environ.get("UPS_HAT_LOG_DIR", "/opt/bartleby/ups-hat-monitor/logs")
    return {
        "bus": parse_int(os.environ.get("UPS_HAT_I2C_BUS", "7")),
        "addr": parse_int(os.environ.get("UPS_HAT_I2C_ADDR", "0x41")),
        "interval": float(os.environ.get("UPS_HAT_LOG_INTERVAL", "10")),
        "log_dir": log_dir,
        "db_path": os.environ.get("UPS_HAT_SQLITE_PATH", str(Path(log_dir) / "ups_hat.sqlite3")),
    }


def validate_interval(interval):
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("UPS_HAT_LOG_INTERVAL must be a positive number")
    return interval


def run_once(config, write=True):
    sensor = INA219(config["bus"], config["addr"])
    try:
        sample = sensor.sample()
    finally:
        sensor.close()

    if write:
        Store(config["log_dir"], config["db_path"]).write_sample(sample)
    return sample


def run_loop(config):
    interval = validate_interval(config["interval"])
    stop = False

    def handle_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    sensor = INA219(config["bus"], config["addr"])
    store = Store(config["log_dir"], config["db_path"])
    LOG.info(
        "ups-hat-monitor starting: i2c_bus=%s i2c_addr=0x%02x interval=%ss log_dir=%s db=%s",
        config["bus"],
        config["addr"],
        interval,
        config["log_dir"],
        config["db_path"],
    )
    try:
        while not stop:
            started = time.monotonic()
            try:
                sample = sensor.sample()
                store.write_sample(sample)
                LOG.debug(
                    "sample voltage=%.3fV current=%.3fA power=%.3fW soc_est=%.1f%% direction=%s",
                    sample["bus_voltage_v"],
                    sample["current_a"],
                    sample["power_w"],
                    sample["soc_pct_est"],
                    sample["direction"],
                )
            except Exception as err:
                LOG.warning("sample failed: %s", err)

            delay = max(0.1, interval - (time.monotonic() - started))
            deadline = time.monotonic() + delay
            while not stop and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    finally:
        sensor.close()
        LOG.info("ups-hat-monitor stopped")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Log Waveshare UPS HAT INA219 readings.")
    parser.add_argument("--once", action="store_true", help="take one sample and print JSON")
    parser.add_argument("--no-write", action="store_true", help="with --once, do not write logs")
    args = parser.parse_args()

    try:
        config = env_config()
        validate_interval(config["interval"])
        if args.once:
            sample = run_once(config, write=not args.no_write)
            print(json.dumps(sample, sort_keys=True))
        else:
            run_loop(config)
    except Exception as err:
        print(f"ups-hat-monitor: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
