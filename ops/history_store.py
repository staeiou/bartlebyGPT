import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


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
    charging_status INTEGER
);

CREATE INDEX IF NOT EXISTS idx_battery_events_ts ON battery_events(ts);

CREATE TABLE IF NOT EXISTS vllm_samples (
    sample_ts_ms INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    requests_running REAL,
    requests_waiting REAL,
    requests_completed REAL
);

CREATE INDEX IF NOT EXISTS idx_vllm_samples_ts ON vllm_samples(ts);
"""


def _utc_iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _to_millis(ts):
    return int(round(float(ts) * 1000.0))


def _median_or_none(values, digits=None):
    if not values:
        return None
    value = median(values)
    if digits is None:
        return value
    return round(value, digits)


def align_window_end(now_ts, bin_seconds):
    if bin_seconds <= 0:
        raise ValueError("bin_seconds must be positive")
    return math.floor(float(now_ts) / float(bin_seconds)) * float(bin_seconds)


def build_median_binned_window(battery_rows, vllm_rows, start_ts, end_ts, bin_seconds):
    if end_ts <= start_ts:
        return {
            "window_start_ts": int(start_ts),
            "window_end_ts": int(end_ts),
            "bin_seconds": int(bin_seconds),
            "points": [],
        }

    bin_count = max(1, int(round((end_ts - start_ts) / float(bin_seconds))))
    bins = [
        {
            "load_values": [],
            "charge_values": [],
            "soc_values": [],
            "running_values": [],
            "waiting_values": [],
        }
        for _ in range(bin_count)
    ]

    for row in battery_rows:
        ts = row.get("ts")
        if ts is None or ts < start_ts or ts >= end_ts:
            continue
        idx = int((ts - start_ts) // bin_seconds)
        if idx < 0 or idx >= bin_count:
            continue
        item = bins[idx]
        load_w = row.get("load_w")
        if load_w is not None:
            item["load_values"].append(load_w)
        charge_w = row.get("charge_w")
        if charge_w is not None:
            item["charge_values"].append(charge_w)
        soc_pct = row.get("soc_pct")
        if soc_pct is not None:
            item["soc_values"].append(soc_pct)

    for row in vllm_rows:
        ts = row.get("ts")
        if ts is None or ts < start_ts or ts >= end_ts:
            continue
        idx = int((ts - start_ts) // bin_seconds)
        if idx < 0 or idx >= bin_count:
            continue
        item = bins[idx]
        running = row.get("running")
        if running is not None:
            item["running_values"].append(running)
        waiting = row.get("waiting")
        if waiting is not None:
            item["waiting_values"].append(waiting)

    points = []
    for idx, item in enumerate(bins):
        point_end_ts = start_ts + ((idx + 1) * bin_seconds)
        points.append(
            {
                "ts": int(point_end_ts),
                "iso": _utc_iso(point_end_ts),
                "load_w": _median_or_none(item["load_values"], digits=3),
                "charge_w": _median_or_none(item["charge_values"], digits=3),
                "soc_pct": _median_or_none(item["soc_values"], digits=3),
                "avg_concurrent": _median_or_none(item["running_values"], digits=1),
                "avg_waiting": _median_or_none(item["waiting_values"], digits=1),
            }
        )

    return {
        "window_start_ts": int(start_ts),
        "window_end_ts": int(end_ts),
        "bin_seconds": int(bin_seconds),
        "points": points,
    }


class SQLiteHistoryStore:
    def __init__(self, db_path):
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _ensure_shared_permissions(self):
        # The Jetson stack runs telemetry as root and the battery monitor as ubuntu.
        # Make the DB and SQLite sidecars writable by both service users.
        for candidate in (
            self.db_path,
            f"{self.db_path}-wal",
            f"{self.db_path}-shm",
        ):
            try:
                if os.path.exists(candidate):
                    os.chmod(candidate, 0o666)
            except OSError:
                continue

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            # Migrate legacy solix_events table name if present.
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "solix_events" in tables and "battery_events" not in tables:
                conn.execute("ALTER TABLE solix_events RENAME TO battery_events")
            conn.executescript(SCHEMA_SQL)
        self._ensure_shared_permissions()

    def record_battery_event(
        self,
        *,
        reading_ts,
        load_w,
        charge_w,
        soc_pct,
        solar_input_w=None,
        voltage_mv=None,
        temp_c=None,
        charging_status=None,
    ):
        reading_ts = float(reading_ts)
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
                    charging_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _to_millis(reading_ts),
                    reading_ts,
                    load_w,
                    charge_w,
                    soc_pct,
                    solar_input_w,
                    voltage_mv,
                    temp_c,
                    charging_status,
                ),
            )
        self._ensure_shared_permissions()

    # Backwards-compatible alias — remove once all callers are updated.
    def record_solix_event(self, **kwargs):
        return self.record_battery_event(**kwargs)

    def record_vllm_sample(self, *, sample_ts, requests_running, requests_waiting, requests_completed):
        sample_ts = float(sample_ts)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO vllm_samples (
                    sample_ts_ms,
                    ts,
                    requests_running,
                    requests_waiting,
                    requests_completed
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _to_millis(sample_ts),
                    sample_ts,
                    requests_running,
                    requests_waiting,
                    requests_completed,
                ),
            )
        self._ensure_shared_permissions()

    def count_battery_rows(self, start_ts, end_ts):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM battery_events WHERE ts >= ? AND ts < ?",
                (float(start_ts), float(end_ts)),
            ).fetchone()
        return int(row["count"] or 0)

    def count_vllm_rows(self, start_ts, end_ts):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM vllm_samples WHERE ts >= ? AND ts < ?",
                (float(start_ts), float(end_ts)),
            ).fetchone()
        return int(row["count"] or 0)

    def import_battery_rows(self, rows):
        payload = [
            (
                _to_millis(row["ts"]),
                float(row["ts"]),
                row.get("load_w"),
                row.get("charge_w"),
                row.get("soc_pct"),
                None,
                None,
                None,
                None,
            )
            for row in rows
            if row.get("ts") is not None
        ]
        if not payload:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO battery_events (
                    reading_ts_ms,
                    ts,
                    load_w,
                    charge_w,
                    soc_pct,
                    solar_input_w,
                    voltage_mv,
                    temp_c,
                    charging_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        self._ensure_shared_permissions()
        return len(payload)

    # Backwards-compatible alias — remove once all callers are updated.
    def import_solix_rows(self, rows):
        return self.import_battery_rows(rows)

    def import_vllm_rows(self, rows):
        payload = [
            (
                _to_millis(row["ts"]),
                float(row["ts"]),
                row.get("running"),
                row.get("waiting"),
                row.get("completed"),
            )
            for row in rows
            if row.get("ts") is not None
        ]
        if not payload:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO vllm_samples (
                    sample_ts_ms,
                    ts,
                    requests_running,
                    requests_waiting,
                    requests_completed
                ) VALUES (?, ?, ?, ?, ?)
                """,
                payload,
            )
        self._ensure_shared_permissions()
        return len(payload)

    def fetch_battery_rows(self, start_ts, end_ts):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, load_w, charge_w, soc_pct
                FROM battery_events
                WHERE ts >= ? AND ts < ?
                ORDER BY ts ASC
                """,
                (float(start_ts), float(end_ts)),
            ).fetchall()
        return [
            {
                "ts": float(row["ts"]),
                "load_w": row["load_w"],
                "charge_w": row["charge_w"],
                "soc_pct": row["soc_pct"],
            }
            for row in rows
        ]

    # Backwards-compatible alias — remove once all callers are updated.
    def fetch_solix_rows(self, start_ts, end_ts):
        return self.fetch_battery_rows(start_ts, end_ts)

    def fetch_vllm_rows(self, start_ts, end_ts):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, requests_running, requests_waiting, requests_completed
                FROM vllm_samples
                WHERE ts >= ? AND ts < ?
                ORDER BY ts ASC
                """,
                (float(start_ts), float(end_ts)),
            ).fetchall()
        return [
            {
                "ts": float(row["ts"]),
                "running": row["requests_running"],
                "waiting": row["requests_waiting"],
                "completed": row["requests_completed"],
            }
            for row in rows
        ]

    def build_history_payload(self, *, now_ts, lookback_days, bin_24h_seconds, bin_7d_seconds):
        now_ts = float(now_ts)
        end_24h = align_window_end(now_ts, bin_24h_seconds)
        end_7d = align_window_end(now_ts, bin_7d_seconds)
        start_24h = end_24h - 86400.0
        start_7d = end_7d - (float(lookback_days) * 86400.0)
        overall_start = min(start_24h, start_7d)
        overall_end = max(end_24h, end_7d)

        battery_rows = self.fetch_battery_rows(overall_start, overall_end)
        vllm_rows = self.fetch_vllm_rows(overall_start, overall_end)

        return {
            "generated_at_ts": int(now_ts),
            "generated_at_iso": _utc_iso(now_ts),
            "lookback_days": int(lookback_days),
            "source": "sqlite_history",
            "bin_statistic": "median",
            "rows_considered": len(battery_rows) + len(vllm_rows),
            "battery_rows_considered": len(battery_rows),
            "vllm_rows_considered": len(vllm_rows),
            "history_24h": build_median_binned_window(
                battery_rows=battery_rows,
                vllm_rows=vllm_rows,
                start_ts=start_24h,
                end_ts=end_24h,
                bin_seconds=bin_24h_seconds,
            ),
            "history_7d": build_median_binned_window(
                battery_rows=battery_rows,
                vllm_rows=vllm_rows,
                start_ts=start_7d,
                end_ts=end_7d,
                bin_seconds=bin_7d_seconds,
            ),
        }
