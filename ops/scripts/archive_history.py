#!/usr/bin/env python3
"""Archive old history rows into monthly SQLite DBs and prune the production DB.

Production keeps full-resolution rows for the retention window. Archive DBs keep
battery rows at their existing cadence and vLLM rows downsampled to one-minute
mean bins.
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


OPS_DIR = Path(__file__).resolve().parents[1]
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))

from history_store import BATTERY_EVENT_COLUMNS, SCHEMA_SQL


ARCHIVE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS archive.battery_events (
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

CREATE INDEX IF NOT EXISTS archive.idx_archive_battery_events_ts ON battery_events(ts);

CREATE TABLE IF NOT EXISTS archive.vllm_minute_bins (
    minute_ts_ms INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    avg_requests_running REAL,
    avg_requests_waiting REAL,
    sum_requests_completed REAL,
    sample_count INTEGER NOT NULL,
    source_start_ts REAL,
    source_end_ts REAL
);

CREATE INDEX IF NOT EXISTS archive.idx_archive_vllm_minute_bins_ts ON vllm_minute_bins(ts);
"""


BATTERY_COLUMNS = tuple(BATTERY_EVENT_COLUMNS.keys())


def utc_ts(value):
    raw = str(value).strip()
    if not raw:
        raise argparse.ArgumentTypeError("timestamp cannot be empty")
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    if len(raw) == 10:
        raw = f"{raw}T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def month_start_ts(year, month):
    return datetime(year, month, 1, tzinfo=timezone.utc).timestamp()


def next_month(year, month):
    if month == 12:
        return year + 1, 1
    return year, month + 1


def iter_month_ranges(start_ts, end_ts):
    if end_ts <= start_ts:
        return
    dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    year, month = dt.year, dt.month
    while True:
        current_start = month_start_ts(year, month)
        next_year, next_mon = next_month(year, month)
        current_end = month_start_ts(next_year, next_mon)
        range_start = max(start_ts, current_start)
        range_end = min(end_ts, current_end)
        if range_start < range_end:
            yield year, month, range_start, range_end
        if current_end >= end_ts:
            break
        year, month = next_year, next_mon


def iso(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def ensure_production_schema(conn, *, readonly_ok=False):
    if readonly_ok:
        required = {"battery_events", "vllm_samples"}
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing = required - tables
        if missing:
            raise RuntimeError(f"production DB missing required tables: {sorted(missing)}")
        return
    try:
        conn.executescript(SCHEMA_SQL)
    except sqlite3.OperationalError as err:
        raise
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(battery_events)").fetchall()
    }
    for name, definition in BATTERY_EVENT_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE battery_events ADD COLUMN {name} {definition}")


def table_count(conn, table, start_ts, end_ts):
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE ts >= ? AND ts < ?",
        (float(start_ts), float(end_ts)),
    ).fetchone()
    return int(row["count"] or 0)


def table_count_before(conn, table, end_ts):
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE ts < ?",
        (float(end_ts),),
    ).fetchone()
    return int(row["count"] or 0)


def open_conn(path):
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def archive_month(conn, archive_path, start_ts, end_ts, *, dry_run=False, prune=True):
    battery_count = table_count(conn, "battery_events", start_ts, end_ts)
    vllm_count = table_count(conn, "vllm_samples", start_ts, end_ts)
    row = conn.execute(
        """
        SELECT COUNT(*) AS bins
        FROM (
            SELECT CAST(ts / 60 AS INTEGER) AS minute_bin
            FROM vllm_samples
            WHERE ts >= ? AND ts < ?
            GROUP BY CAST(ts / 60 AS INTEGER)
        )
        """,
        (float(start_ts), float(end_ts)),
    ).fetchone()
    source_vllm_bins = int(row["bins"] or 0)
    if battery_count == 0 and vllm_count == 0:
        return {
            "battery_source": 0,
            "vllm_source": 0,
            "vllm_bins": 0,
            "deleted_battery": 0,
            "deleted_vllm": 0,
        }

    if dry_run:
        return {
            "battery_source": battery_count,
            "vllm_source": vllm_count,
            "vllm_bins": source_vllm_bins,
            "deleted_battery": 0,
            "deleted_vllm": 0,
        }

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    column_list = ", ".join(BATTERY_COLUMNS)
    select_list = ", ".join(BATTERY_COLUMNS)
    conn.execute("ATTACH DATABASE ? AS archive", (str(archive_path),))
    conn.executescript(ARCHIVE_SCHEMA_SQL)

    try:
        with conn:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO archive.battery_events ({column_list})
                SELECT {select_list}
                FROM main.battery_events
                WHERE ts >= ? AND ts < ?
                """,
                (float(start_ts), float(end_ts)),
            )
            conn.execute(
                """
                INSERT INTO archive.vllm_minute_bins (
                    minute_ts_ms,
                    ts,
                    avg_requests_running,
                    avg_requests_waiting,
                    sum_requests_completed,
                    sample_count,
                    source_start_ts,
                    source_end_ts
                )
                SELECT
                    CAST(((CAST(ts / 60 AS INTEGER) + 1) * 60) * 1000 AS INTEGER) AS minute_ts_ms,
                    (CAST(ts / 60 AS INTEGER) + 1) * 60 AS point_ts,
                    ROUND(AVG(requests_running), 0) AS avg_requests_running,
                    ROUND(AVG(requests_waiting), 0) AS avg_requests_waiting,
                    SUM(requests_completed) AS sum_requests_completed,
                    COUNT(*) AS sample_count,
                    MIN(ts) AS source_start_ts,
                    MAX(ts) AS source_end_ts
                FROM main.vllm_samples
                WHERE ts >= ? AND ts < ?
                GROUP BY CAST(ts / 60 AS INTEGER)
                ON CONFLICT(minute_ts_ms) DO UPDATE SET
                    ts=excluded.ts,
                    avg_requests_running=excluded.avg_requests_running,
                    avg_requests_waiting=excluded.avg_requests_waiting,
                    sum_requests_completed=excluded.sum_requests_completed,
                    sample_count=excluded.sample_count,
                    source_start_ts=excluded.source_start_ts,
                    source_end_ts=excluded.source_end_ts
                """,
                (float(start_ts), float(end_ts)),
            )
            deleted_battery = 0
            deleted_vllm = 0
            if prune:
                deleted_battery = conn.execute(
                    "DELETE FROM main.battery_events WHERE ts >= ? AND ts < ?",
                    (float(start_ts), float(end_ts)),
                ).rowcount
                deleted_vllm = conn.execute(
                    "DELETE FROM main.vllm_samples WHERE ts >= ? AND ts < ?",
                    (float(start_ts), float(end_ts)),
                ).rowcount
    finally:
        conn.execute("DETACH DATABASE archive")

    for candidate in (archive_path, Path(f"{archive_path}-wal"), Path(f"{archive_path}-shm")):
        try:
            if candidate.exists():
                os.chmod(candidate, 0o666)
        except OSError:
            pass

    return {
        "battery_source": battery_count,
        "vllm_source": vllm_count,
        "vllm_bins": source_vllm_bins,
        "deleted_battery": int(deleted_battery or 0),
        "deleted_vllm": int(deleted_vllm or 0),
    }


def drop_before_start(conn, start_ts, *, dry_run=False):
    battery_count = table_count_before(conn, "battery_events", start_ts)
    vllm_count = table_count_before(conn, "vllm_samples", start_ts)
    if dry_run:
        return {
            "battery_source": battery_count,
            "vllm_source": vllm_count,
            "deleted_battery": 0,
            "deleted_vllm": 0,
        }
    with conn:
        deleted_battery = conn.execute(
            "DELETE FROM battery_events WHERE ts < ?",
            (float(start_ts),),
        ).rowcount
        deleted_vllm = conn.execute(
            "DELETE FROM vllm_samples WHERE ts < ?",
            (float(start_ts),),
        ).rowcount
    return {
        "battery_source": battery_count,
        "vllm_source": vllm_count,
        "deleted_battery": int(deleted_battery or 0),
        "deleted_vllm": int(deleted_vllm or 0),
    }


def archive_db_name(year, month):
    return f"history-{year:04d}-{month:02d}.sqlite3"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Production history.sqlite3 path.")
    parser.add_argument("--archive-dir", required=True, help="Directory for monthly archive DBs.")
    parser.add_argument("--start", required=True, type=utc_ts, help="First UTC date/time to archive, e.g. 2026-04-04.")
    parser.add_argument("--retention-days", type=float, default=14.0, help="Full-resolution production retention window.")
    parser.add_argument("--cutoff", type=utc_ts, default=None, help="Override retention cutoff UTC date/time.")
    parser.add_argument("--dry-run", action="store_true", help="Report work without writing archives or pruning.")
    parser.add_argument("--no-prune", action="store_true", help="Archive but do not delete production rows.")
    parser.add_argument(
        "--drop-before-start",
        action="store_true",
        help="Delete production rows older than --start without archiving them.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    archive_dir = Path(args.archive_dir)
    now_ts = time.time()
    cutoff_ts = float(args.cutoff if args.cutoff is not None else now_ts - (args.retention_days * 86400.0))
    start_ts = float(args.start)
    if cutoff_ts <= start_ts:
        raise SystemExit("Nothing to archive: cutoff is not after start")

    total = {
        "battery_source": 0,
        "vllm_source": 0,
        "vllm_bins": 0,
        "deleted_battery": 0,
        "deleted_vllm": 0,
    }

    with open_conn(db_path) as conn:
        ensure_production_schema(conn, readonly_ok=args.dry_run)
        print(
            f"archive window: {iso(start_ts)} <= ts < {iso(cutoff_ts)} "
            f"({'dry-run, ' if args.dry_run else ''}{'archive-only' if args.no_prune else 'archive+prune'})"
        )
        if args.drop_before_start and not args.no_prune:
            stats = drop_before_start(conn, start_ts, dry_run=args.dry_run)
            total["deleted_battery"] += stats["deleted_battery"]
            total["deleted_vllm"] += stats["deleted_vllm"]
            print(
                f"pre-start < {iso(start_ts)} "
                f"battery={stats['battery_source']} vllm={stats['vllm_source']} "
                f"deleted=({stats['deleted_battery']},{stats['deleted_vllm']})"
            )
        for year, month, range_start, range_end in iter_month_ranges(start_ts, cutoff_ts):
            archive_path = archive_dir / archive_db_name(year, month)
            stats = archive_month(
                conn,
                archive_path,
                range_start,
                range_end,
                dry_run=args.dry_run,
                prune=not args.no_prune,
            )
            for key, value in stats.items():
                total[key] += value
            print(
                f"{year:04d}-{month:02d} {iso(range_start)} -> {iso(range_end)} "
                f"battery={stats['battery_source']} vllm={stats['vllm_source']} "
                f"vllm_bins={stats['vllm_bins']} "
                f"deleted=({stats['deleted_battery']},{stats['deleted_vllm']}) "
                f"archive={archive_path}"
            )

    print(
        "total "
        f"battery={total['battery_source']} vllm={total['vllm_source']} "
        f"vllm_bins={total['vllm_bins']} "
        f"deleted=({total['deleted_battery']},{total['deleted_vllm']})"
    )


if __name__ == "__main__":
    main()
