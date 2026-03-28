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
    parser.add_argument("--vllm-log-dir", default="", help="Directory containing vllm-YYYY-MM-DD.csv files.")
    parser.add_argument("--batch-size", type=int, default=5000, help="Rows per bulk insert batch.")
    args = parser.parse_args()

    store = SQLiteHistoryStore(args.db)
    solix_imported = 0
    vllm_imported = 0

    if args.solix_log_dir:
        for rows in batched(iter_solix_rows(args.solix_log_dir), args.batch_size):
            solix_imported += store.import_solix_rows(rows)

    if args.vllm_log_dir:
        for rows in batched(iter_vllm_rows(args.vllm_log_dir), args.batch_size):
            vllm_imported += store.import_vllm_rows(rows)

    print(f"solix_rows_seen={solix_imported}")
    print(f"vllm_rows_seen={vllm_imported}")
    print(f"db={args.db}")


if __name__ == "__main__":
    main()
