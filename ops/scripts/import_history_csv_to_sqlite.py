#!/usr/bin/env python3
import argparse
import csv
import glob
import gzip
import os
import re
import sys
from pathlib import Path


OPS_DIR = Path(__file__).resolve().parents[1]
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))

from history_store import SQLiteHistoryStore


def parse_iso_timestamp(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        import datetime as _dt
        parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def safe_float(value, lo=None, hi=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if lo is not None and parsed < lo:
        return None
    if hi is not None and parsed > hi:
        return None
    return parsed


def safe_int(value, lo=None, hi=None):
    try:
        parsed = int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return None
    if lo is not None and parsed < lo:
        return None
    if hi is not None and parsed > hi:
        return None
    return parsed


def iter_csv_dict_rows(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader((line.replace("\x00", "") for line in handle))
        for row in reader:
            yield row


def iter_solix_rows(log_dir):
    patterns = [
        os.path.join(log_dir, "solix-*.csv"),
        os.path.join(log_dir, "solix-*.csv.gz"),
    ]
    paths = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern))
    for path in sorted(paths):
        basename = os.path.basename(path)
        if not re.match(r"^solix-\d{4}-\d{2}-\d{2}\.csv(?:\.gz)?$", basename):
            continue
        try:
            for row in iter_csv_dict_rows(path):
                ts = parse_iso_timestamp(row.get("timestamp"))
                if ts is None:
                    continue
                yield {
                    "ts": ts,
                    "load_w": safe_float(row.get("total_output_w"), lo=0.0),
                    "charge_w": safe_float(row.get("total_input_w"), lo=0.0),
                    "soc_pct": safe_float(row.get("soc_pct"), lo=0.0, hi=100.0),
                }
        except OSError:
            continue


def iter_vllm_rows(log_dir):
    patterns = [
        os.path.join(log_dir, "vllm-*.csv"),
        os.path.join(log_dir, "vllm-*.csv.gz"),
    ]
    paths = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern))
    for path in sorted(paths):
        basename = os.path.basename(path)
        if not re.match(r"^vllm-\d{4}-\d{2}-\d{2}\.csv(?:\.gz)?$", basename):
            continue
        try:
            for row in iter_csv_dict_rows(path):
                ts = parse_iso_timestamp(row.get("timestamp"))
                if ts is None:
                    continue
                yield {
                    "ts": ts,
                    "running": safe_float(row.get("requests_running"), lo=0.0),
                    "waiting": safe_float(row.get("requests_waiting"), lo=0.0),
                    "completed": safe_float(row.get("requests_completed"), lo=0.0),
                }
        except OSError:
            continue


def iter_victron_rows(log_dir, start_ts=None, end_ts=None):
    patterns = [
        os.path.join(log_dir, "victron-adv-*.csv"),
        os.path.join(log_dir, "victron-adv-*.csv.gz"),
    ]
    paths = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern))
    for path in sorted(paths):
        basename = os.path.basename(path)
        if not re.match(r"^victron-adv-\d{4}-\d{2}-\d{2}\.csv(?:\.gz)?$", basename):
            continue
        try:
            for row in iter_csv_dict_rows(path):
                ts = safe_float(row.get("reading_ts"))
                if ts is None:
                    ts = parse_iso_timestamp(row.get("timestamp"))
                if ts is None:
                    continue
                if start_ts is not None and ts < start_ts:
                    continue
                if end_ts is not None and ts >= end_ts:
                    continue

                battery_voltage_v = safe_float(row.get("battery_voltage_v"))
                battery_current_a = safe_float(row.get("battery_charging_current_a"))
                battery_power_w = None
                if battery_voltage_v is not None and battery_current_a is not None:
                    battery_power_w = round(battery_voltage_v * battery_current_a, 3)

                yield {
                    "ts": ts,
                    "load_w": safe_float(row.get("load_w"), lo=0.0),
                    "charge_w": max(0.0, battery_power_w) if battery_power_w is not None else None,
                    "solar_input_w": safe_float(row.get("solar_power_w"), lo=0.0),
                    "voltage_mv": round(battery_voltage_v * 1000.0, 3) if battery_voltage_v is not None else None,
                    "victron_model_name": row.get("model_name") or None,
                    "victron_charge_state": row.get("charge_state") or None,
                    "victron_charger_error": row.get("charger_error") or None,
                    "victron_battery_voltage_v": battery_voltage_v,
                    "victron_battery_charging_current_a": battery_current_a,
                    "victron_battery_power_w": battery_power_w,
                    "victron_external_device_load_a": safe_float(row.get("external_device_load_a"), lo=0.0),
                    "victron_yield_today_wh": safe_float(row.get("yield_today_wh"), lo=0.0),
                    "victron_manufacturer_id": safe_int(row.get("manufacturer_id")),
                }
        except OSError:
            continue


def batched(iterable, batch_size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def main():
    parser = argparse.ArgumentParser(description="Import legacy Solix/vLLM CSV history into SQLite.")
    parser.add_argument("--db", required=True, help="Path to the SQLite history DB.")
    parser.add_argument("--solix-log-dir", default="", help="Directory containing solix-YYYY-MM-DD.csv files.")
    parser.add_argument("--victron-log-dir", default="", help="Directory containing victron-adv-YYYY-MM-DD.csv files.")
    parser.add_argument("--vllm-log-dir", default="", help="Directory containing vllm-YYYY-MM-DD.csv files.")
    parser.add_argument("--start", default="", help="Inclusive ISO timestamp bound for Victron backfill rows.")
    parser.add_argument("--end", default="", help="Exclusive ISO timestamp bound for Victron backfill rows.")
    parser.add_argument("--batch-size", type=int, default=5000, help="Rows per bulk insert batch.")
    args = parser.parse_args()

    store = SQLiteHistoryStore(args.db)
    solix_imported = 0
    victron_imported = 0
    vllm_imported = 0
    start_ts = parse_iso_timestamp(args.start) if args.start else None
    end_ts = parse_iso_timestamp(args.end) if args.end else None

    if args.solix_log_dir:
        for rows in batched(iter_solix_rows(args.solix_log_dir), args.batch_size):
            solix_imported += store.import_battery_rows(rows)

    if args.victron_log_dir:
        row_iter = iter_victron_rows(args.victron_log_dir, start_ts=start_ts, end_ts=end_ts)
        for rows in batched(row_iter, args.batch_size):
            victron_imported += store.import_victron_rows(rows)

    if args.vllm_log_dir:
        for rows in batched(iter_vllm_rows(args.vllm_log_dir), args.batch_size):
            vllm_imported += store.import_vllm_rows(rows)

    print(f"solix_rows_seen={solix_imported}")
    print(f"victron_rows_seen={victron_imported}")
    print(f"vllm_rows_seen={vllm_imported}")
    print(f"db={args.db}")


if __name__ == "__main__":
    main()
