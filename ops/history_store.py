import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def _env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


# Capability: solar/PV is measured by a dedicated sensor independent of battery charge
# current (see power_telemetry.BATTERY_SOLAR_MEASURED). When true, prefer the dedicated
# solar_input_w column over charge_w for the solar series. Legacy deployments keyed this
# off DEPLOYMENT_PROFILE == "jetson-solar-lfp"; that fallback default preserves them.
BATTERY_SOLAR_MEASURED = _env_flag(
    "BATTERY_SOLAR_MEASURED",
    default=(os.environ.get("DEPLOYMENT_PROFILE", "").strip() == "jetson-solar-lfp"),
)


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


MAX_HISTORY_WATTS = 1000.0


def _row_value(row, key):
    if hasattr(row, "get"):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _is_plausible_battery_history_row(row):
    for key in ("load_w", "charge_w"):
        value = _row_value(row, key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(numeric) or numeric < 0.0 or numeric > MAX_HISTORY_WATTS:
            return False
    return True


BATTERY_EVENT_COLUMNS = {
    "reading_ts_ms": "INTEGER PRIMARY KEY",
    "ts": "REAL NOT NULL",
    "load_w": "REAL",
    "charge_w": "REAL",
    "soc_pct": "REAL",
    "solar_input_w": "REAL",
    "voltage_mv": "REAL",
    "temp_c": "REAL",
    "charging_status": "INTEGER",
    "victron_model_name": "TEXT",
    "victron_charge_state": "TEXT",
    "victron_charger_error": "TEXT",
    "victron_battery_voltage_v": "REAL",
    "victron_battery_charging_current_a": "REAL",
    "victron_battery_power_w": "REAL",
    "victron_external_device_load_a": "REAL",
    "victron_yield_today_wh": "REAL",
    "victron_manufacturer_id": "INTEGER",
}


def align_window_end(now_ts, bin_seconds):
    if bin_seconds <= 0:
        raise ValueError("bin_seconds must be positive")
    return math.floor(float(now_ts) / float(bin_seconds)) * float(bin_seconds)


def build_history_window(battery_rows, vllm_bins, start_ts, end_ts, bin_seconds):
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
            "avg_concurrent": None,
            "avg_waiting": None,
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

    for row in vllm_bins:
        ts = row.get("ts")
        if ts is None or ts <= start_ts or ts > end_ts:
            continue
        idx = int((ts - start_ts) // bin_seconds) - 1
        if idx < 0 or idx >= bin_count:
            continue
        bins[idx]["avg_concurrent"] = row.get("avg_concurrent")
        bins[idx]["avg_waiting"] = row.get("avg_waiting")

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
                "avg_concurrent": item["avg_concurrent"],
                "avg_waiting": item["avg_waiting"],
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
            self._ensure_columns(conn, "battery_events", BATTERY_EVENT_COLUMNS)
        self._ensure_shared_permissions()

    def _ensure_columns(self, conn, table_name, columns):
        existing = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for name, definition in columns.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")

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
        victron_model_name=None,
        victron_charge_state=None,
        victron_charger_error=None,
        victron_battery_voltage_v=None,
        victron_battery_charging_current_a=None,
        victron_battery_power_w=None,
        victron_external_device_load_a=None,
        victron_yield_today_wh=None,
        victron_manufacturer_id=None,
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
                    charging_status,
                    victron_model_name,
                    victron_charge_state,
                    victron_charger_error,
                    victron_battery_voltage_v,
                    victron_battery_charging_current_a,
                    victron_battery_power_w,
                    victron_external_device_load_a,
                    victron_yield_today_wh,
                    victron_manufacturer_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    victron_model_name,
                    victron_charge_state,
                    victron_charger_error,
                    victron_battery_voltage_v,
                    victron_battery_charging_current_a,
                    victron_battery_power_w,
                    victron_external_device_load_a,
                    victron_yield_today_wh,
                    victron_manufacturer_id,
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
                row.get("solar_input_w"),
                row.get("voltage_mv"),
                row.get("temp_c"),
                row.get("charging_status"),
                row.get("victron_model_name"),
                row.get("victron_charge_state"),
                row.get("victron_charger_error"),
                row.get("victron_battery_voltage_v"),
                row.get("victron_battery_charging_current_a"),
                row.get("victron_battery_power_w"),
                row.get("victron_external_device_load_a"),
                row.get("victron_yield_today_wh"),
                row.get("victron_manufacturer_id"),
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
                    charging_status,
                    victron_model_name,
                    victron_charge_state,
                    victron_charger_error,
                    victron_battery_voltage_v,
                    victron_battery_charging_current_a,
                    victron_battery_power_w,
                    victron_external_device_load_a,
                    victron_yield_today_wh,
                    victron_manufacturer_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        self._ensure_shared_permissions()
        return len(payload)

    # Backwards-compatible alias — remove once all callers are updated.
    def import_solix_rows(self, rows):
        return self.import_battery_rows(rows)

    def import_victron_rows(self, rows):
        payload = [
            (
                _to_millis(row["ts"]),
                float(row["ts"]),
                row.get("load_w"),
                row.get("charge_w"),
                row.get("solar_input_w"),
                row.get("voltage_mv"),
                row.get("charging_status"),
                row.get("victron_model_name"),
                row.get("victron_charge_state"),
                row.get("victron_charger_error"),
                row.get("victron_battery_voltage_v"),
                row.get("victron_battery_charging_current_a"),
                row.get("victron_battery_power_w"),
                row.get("victron_external_device_load_a"),
                row.get("victron_yield_today_wh"),
                row.get("victron_manufacturer_id"),
            )
            for row in rows
            if row.get("ts") is not None
        ]
        if not payload:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO battery_events (
                    reading_ts_ms,
                    ts,
                    load_w,
                    charge_w,
                    solar_input_w,
                    voltage_mv,
                    charging_status,
                    victron_model_name,
                    victron_charge_state,
                    victron_charger_error,
                    victron_battery_voltage_v,
                    victron_battery_charging_current_a,
                    victron_battery_power_w,
                    victron_external_device_load_a,
                    victron_yield_today_wh,
                    victron_manufacturer_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reading_ts_ms) DO UPDATE SET
                    load_w=excluded.load_w,
                    charge_w=excluded.charge_w,
                    solar_input_w=excluded.solar_input_w,
                    voltage_mv=excluded.voltage_mv,
                    charging_status=excluded.charging_status,
                    victron_model_name=excluded.victron_model_name,
                    victron_charge_state=excluded.victron_charge_state,
                    victron_charger_error=excluded.victron_charger_error,
                    victron_battery_voltage_v=excluded.victron_battery_voltage_v,
                    victron_battery_charging_current_a=excluded.victron_battery_charging_current_a,
                    victron_battery_power_w=excluded.victron_battery_power_w,
                    victron_external_device_load_a=excluded.victron_external_device_load_a,
                    victron_yield_today_wh=excluded.victron_yield_today_wh,
                    victron_manufacturer_id=excluded.victron_manufacturer_id
                """,
                payload,
            )
        self._ensure_shared_permissions()
        return len(payload)

    def record_victron_event(self, **kwargs):
        return self.import_victron_rows([kwargs])

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
            if BATTERY_SOLAR_MEASURED:
                rows = conn.execute(
                    """
                    SELECT ts, load_w, COALESCE(solar_input_w, charge_w) AS charge_w, soc_pct
                    FROM battery_events
                    WHERE ts >= ? AND ts < ?
                    ORDER BY ts ASC
                    """,
                    (float(start_ts), float(end_ts)),
                ).fetchall()
            else:
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
            if _is_plausible_battery_history_row(row)
        ]

    # Backwards-compatible alias — remove once all callers are updated.
    def fetch_solix_rows(self, start_ts, end_ts):
        return self.fetch_battery_rows(start_ts, end_ts)

    def fetch_vllm_bins(self, start_ts, end_ts, bin_seconds):
        bin_seconds = int(bin_seconds)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    (CAST(ts / ? AS INTEGER) + 1) * ? AS point_ts,
                    ROUND(AVG(requests_running), 0) AS avg_running,
                    ROUND(AVG(requests_waiting), 0) AS avg_waiting,
                    COUNT(*) AS sample_count
                FROM vllm_samples
                WHERE ts >= ? AND ts < ?
                GROUP BY CAST(ts / ? AS INTEGER)
                ORDER BY point_ts ASC
                """,
                (
                    bin_seconds,
                    bin_seconds,
                    float(start_ts),
                    float(end_ts),
                    bin_seconds,
                ),
            ).fetchall()
        return [
            {
                "ts": float(row["point_ts"]),
                "avg_concurrent": int(row["avg_running"]) if row["avg_running"] is not None else None,
                "avg_waiting": int(row["avg_waiting"]) if row["avg_waiting"] is not None else None,
                "sample_count": int(row["sample_count"] or 0),
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
        vllm_rows_considered = self.count_vllm_rows(overall_start, overall_end)
        vllm_24h_bins = self.fetch_vllm_bins(start_24h, end_24h, bin_24h_seconds)
        vllm_7d_bins = self.fetch_vllm_bins(start_7d, end_7d, bin_7d_seconds)

        return {
            "generated_at_ts": int(now_ts),
            "generated_at_iso": _utc_iso(now_ts),
            "lookback_days": int(lookback_days),
            "deployment_profile": os.environ.get("DEPLOYMENT_PROFILE", "").strip() or None,
            "source": "sqlite_history",
            "bin_statistic": "mixed",
            "battery_bin_statistic": "median",
            "vllm_bin_statistic": "mean",
            "rows_considered": len(battery_rows) + vllm_rows_considered,
            "battery_rows_considered": len(battery_rows),
            "vllm_rows_considered": vllm_rows_considered,
            "history_24h": build_history_window(
                battery_rows=battery_rows,
                vllm_bins=vllm_24h_bins,
                start_ts=start_24h,
                end_ts=end_24h,
                bin_seconds=bin_24h_seconds,
            ),
            "history_7d": build_history_window(
                battery_rows=battery_rows,
                vllm_bins=vllm_7d_bins,
                start_ts=start_7d,
                end_ts=end_7d,
                bin_seconds=bin_7d_seconds,
            ),
        }
