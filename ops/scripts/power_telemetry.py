#!/usr/bin/env python3
import csv
import glob
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import urlopen


OPS_DIR = Path(__file__).resolve().parents[1]
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))

from history_store import SQLiteHistoryStore


class EsphomeFeedError(RuntimeError):
    def __init__(self, message, extra=None):
        super().__init__(message)
        self.extra = dict(extra or {})


def env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return default


HOST = os.environ.get("TELEMETRY_HOST", "127.0.0.1")
PORT = int(os.environ.get("TELEMETRY_PORT", "18081"))
SAMPLE_INTERVAL = float(os.environ.get("TELEMETRY_SAMPLE_INTERVAL", "1.0"))
VLLM_BASE_URL = os.environ.get("TELEMETRY_VLLM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
POWER_BACKEND = os.environ.get("TELEMETRY_POWER_BACKEND", "auto").strip().lower()
NVIDIA_SMI_BIN = os.environ.get("NVIDIA_SMI_BIN", "nvidia-smi")
REQUEST_TIMEOUT = float(os.environ.get("TELEMETRY_REQUEST_TIMEOUT", "1.5"))
ESPHOME_POWER_URL = os.environ.get("TELEMETRY_ESPHOME_POWER_URL", "").strip()
ESPHOME_BASE_URL = os.environ.get("TELEMETRY_ESPHOME_BASE_URL", "").strip().rstrip("/")
ESPHOME_POWER_PATH = os.environ.get("TELEMETRY_ESPHOME_POWER_PATH", "/sensor/power").strip()
SOLIX_STALE_SECONDS = max(0.0, float(os.environ.get("TELEMETRY_SOLIX_STALE_SECONDS", "90")))
SOLIX_AUTO_RECOVER = env_bool("TELEMETRY_SOLIX_AUTO_RECOVER", default=True)
SOLIX_RECOVERY_COOLDOWN_SECONDS = max(
    30.0, float(os.environ.get("TELEMETRY_SOLIX_RECOVERY_COOLDOWN_SECONDS", "180"))
)
SOLIX_RECOVERY_ESCALATE_EVERY = max(
    1, int(os.environ.get("TELEMETRY_SOLIX_RECOVERY_ESCALATE_EVERY", "2"))
)
BATTERY_MONITOR_SERVICE_NAME = os.environ.get("BATTERY_MONITOR_SERVICE_NAME", "solix-monitor").strip()
SOLIX_LOG_DIR = os.environ.get("TELEMETRY_SOLIX_LOG_DIR", "/opt/bartleby/solix-monitor/logs").strip()
BATTERY_CSV_DIR = os.environ.get("TELEMETRY_BATTERY_CSV_DIR", SOLIX_LOG_DIR).strip()
BATTERY_CSV_PREFIX = os.environ.get("TELEMETRY_BATTERY_CSV_PREFIX", "solix").strip().rstrip("-")
DEPLOYMENT_PROFILE = os.environ.get("DEPLOYMENT_PROFILE", "").strip()
BATTERY_CAPACITY_WH = float(os.environ.get("BATTERY_CAPACITY_WH", "0"))
VLLM_LOG_DIR = os.environ.get("TELEMETRY_VLLM_LOG_DIR", "").strip()
HISTORY_DB_PATH = os.environ.get("TELEMETRY_HISTORY_DB_PATH", "").strip()
HISTORY_CACHE_TTL_SECONDS = max(
    10.0, float(os.environ.get("TELEMETRY_HISTORY_CACHE_TTL_SECONDS", "600"))
)
HISTORY_LOOKBACK_DAYS = max(1, int(os.environ.get("TELEMETRY_HISTORY_LOOKBACK_DAYS", "7")))
HISTORY_24H_BIN_SECONDS = max(60, int(os.environ.get("TELEMETRY_HISTORY_24H_BIN_SECONDS", "300")))
HISTORY_7D_BIN_SECONDS = max(300, int(os.environ.get("TELEMETRY_HISTORY_7D_BIN_SECONDS", "3600")))
IS_JETSON = os.path.isfile("/etc/nv_tegra_release")
try:
    with open("/proc/device-tree/model", "rb") as _f:
        IS_RPI = b"Raspberry Pi" in _f.read()
except OSError:
    IS_RPI = False
DEFAULT_IDLE_GPU_WATTS = float(
    os.environ.get("TELEMETRY_IDLE_GPU_WATTS", "2" if IS_JETSON else "35")
)
BASE_SYSTEM_WATTS = float(
    os.environ.get("TELEMETRY_BASE_SYSTEM_WATTS", "5.5" if IS_JETSON else "300")
)
GPU_COOLING_MULTIPLIER = float(
    os.environ.get("TELEMETRY_GPU_COOLING_MULTIPLIER", "1.00" if IS_JETSON else "1.35")
)
CLAMP_MIN_WATTS = float(os.environ.get("TELEMETRY_CLAMP_MIN_WATTS", "0"))
CLAMP_MAX_WATTS = float(os.environ.get("TELEMETRY_CLAMP_MAX_WATTS", "0"))  # 0 = disabled


RUNNING_RE = re.compile(r"^vllm:num_requests_running\{.*\}\s+([0-9.eE+-]+)$", re.MULTILINE)
WAITING_RE = re.compile(r"^vllm:num_requests_waiting\{.*\}\s+([0-9.eE+-]+)$", re.MULTILINE)
SUCCESS_RE = re.compile(r"^vllm:request_success_total\{.*\}\s+([0-9.eE+-]+)$", re.MULTILINE)

STATE_LOCK = threading.Lock()
STATE = {
    "timestamp": time.time(),
    "measured_gpu_watts": None,
    "measured_server_watts": None,
    "idle_gpu_watts": DEFAULT_IDLE_GPU_WATTS,
    "attributed_gpu_watts": DEFAULT_IDLE_GPU_WATTS,
    "base_system_watts": BASE_SYSTEM_WATTS,
    "estimated_total_watts": BASE_SYSTEM_WATTS + (DEFAULT_IDLE_GPU_WATTS * GPU_COOLING_MULTIPLIER),
    "estimated_total_server_watts": BASE_SYSTEM_WATTS + (DEFAULT_IDLE_GPU_WATTS * GPU_COOLING_MULTIPLIER),
    "cost_share_fraction": 1.0,
    "requests_running": 0,
    "requests_waiting": 0,
    "request_success_total": 0,
    "requests_completed_interval": 0,
    "server_load": None,
    "is_active": False,
    "source": "fallback",
    "power_backend": "none",
    "power_measurement_kind": "none",
    "watts_is_live": False,
    "clamp_min_watts": CLAMP_MIN_WATTS,
    "clamp_max_watts": CLAMP_MAX_WATTS,
    "power_reading_ts": None,
    "power_rails_watts": {},
    "last_error": "",
    "battery_soc_pct": None,
    "battery_solar_input_w": None,
    "battery_total_input_w": None,
    "battery_voltage_mv": None,
    "battery_temp_c": None,
    "battery_reading_ts": None,
    "battery_charging_status": None,
    "battery_effective_solar_w": None,
    "battery_capacity_wh": BATTERY_CAPACITY_WH if BATTERY_CAPACITY_WH > 0 else None,
    # solix_* compat aliases — populated alongside battery_* while old monitors are still deployed
    "solix_soc_pct": None,
    "solix_solar_input_w": None,
    "solix_total_input_w": None,
    "solix_voltage_mv": None,
    "solix_temp_c": None,
    "solix_reading_ts": None,
    "solix_charging_status": None,
    "solix_effective_solar_w": None,
    "deployment_profile": DEPLOYMENT_PROFILE or None,
}

JTOP_POWER_SERVICE = None
JTOP_IMPORT_ERROR = ""
HISTORY_LOCK = threading.Lock()
HISTORY_CACHE = {"generated_at": 0.0, "payload": None, "refreshing": False, "last_error": ""}
SOLIX_RECOVERY_LOCK = threading.Lock()
SOLIX_RECOVERY_STATE = {"last_attempt_ts": 0.0, "attempt_count": 0}
try:
    HISTORY_DB = SQLiteHistoryStore(HISTORY_DB_PATH) if HISTORY_DB_PATH else None
except Exception as err:  # pragma: no cover - startup environment guard
    HISTORY_DB = None
    logging.warning("history sqlite disabled: %s", err)

try:
    from jtop.core.power import PowerService

    # jtop's power module emits warnings for skipped rails by default.
    logging.getLogger("jtop.core.power").disabled = True
except Exception as err:  # pragma: no cover - import availability is environment-specific
    PowerService = None
    JTOP_IMPORT_ERROR = str(err)


def fetch_text(url):
    with urlopen(url, timeout=REQUEST_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def read_nvidia_smi_watts():
    completed = subprocess.run(
        [
            NVIDIA_SMI_BIN,
            "--query-gpu=power.draw",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=REQUEST_TIMEOUT,
    )
    values = []
    for line in completed.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        values.append(float(raw))
    if not values:
        raise RuntimeError("nvidia-smi returned no power values")
    return sum(values)


def get_jtop_power_service():
    global JTOP_POWER_SERVICE
    if PowerService is None:
        if JTOP_IMPORT_ERROR:
            raise RuntimeError(f"jtop import failed: {JTOP_IMPORT_ERROR}")
        raise RuntimeError("jtop is not installed")
    if JTOP_POWER_SERVICE is None:
        JTOP_POWER_SERVICE = PowerService()
    return JTOP_POWER_SERVICE


def read_jtop_power_watts():
    service = get_jtop_power_service()
    status = service.get_status() or {}
    rails = status.get("rail", {})
    total = status.get("tot", {})

    rails_watts = {}
    for rail_name, rail_data in rails.items():
        raw_mw = rail_data.get("power")
        if raw_mw is None:
            continue
        rails_watts[rail_name] = float(raw_mw) / 1000.0

    total_mw = total.get("power")
    if total_mw is not None and float(total_mw) > 0:
        return float(total_mw) / 1000.0, rails_watts, "jtop-total"

    if rails_watts:
        return sum(rails_watts.values()), rails_watts, "jtop-rails"

    raise RuntimeError("jtop power status had no usable rails")


def log_vllm_metrics(ts, requests_running, requests_waiting, requests_completed):
    if HISTORY_DB is not None:
        try:
            HISTORY_DB.record_vllm_sample(
                sample_ts=ts,
                requests_running=requests_running,
                requests_waiting=requests_waiting,
                requests_completed=requests_completed,
            )
        except Exception as err:
            logging.warning("vllm metrics sqlite write failed: %s", err)
    if not VLLM_LOG_DIR:
        return
    date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(VLLM_LOG_DIR, f"vllm-{date_str}.csv")
    write_header = not os.path.exists(path)
    try:
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["timestamp", "requests_running", "requests_waiting", "requests_completed"])
            writer.writerow([
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                requests_running,
                requests_waiting,
                requests_completed,
            ])
    except OSError as err:
        logging.warning("vllm metrics log write failed: %s", err)


def read_vllm_rows(now_ts):
    if not VLLM_LOG_DIR:
        return []
    cutoff_ts = now_ts - (HISTORY_LOOKBACK_DAYS * 86400.0)
    now_date = datetime.fromtimestamp(now_ts, tz=timezone.utc).date()
    min_date = now_date - timedelta(days=HISTORY_LOOKBACK_DAYS)
    pattern = os.path.join(VLLM_LOG_DIR, "vllm-*.csv")
    files = sorted(glob.glob(pattern))
    dedup = {}
    for path in files:
        basename = os.path.basename(path)
        match = re.match(r"^vllm-(\d{4}-\d{2}-\d{2})\.csv$", basename)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < min_date:
            continue
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader((line.replace("\x00", "") for line in handle))
                for row in reader:
                    ts = parse_iso_timestamp(row.get("timestamp"))
                    if ts is None or ts < cutoff_ts or ts > (now_ts + 60.0):
                        continue
                    dedup[ts] = {
                        "ts": ts,
                        "running": safe_float(row.get("requests_running"), lo=0.0),
                        "waiting": safe_float(row.get("requests_waiting"), lo=0.0),
                        "completed": safe_float(row.get("requests_completed"), lo=0.0),
                    }
        except OSError:
            continue
    return [dedup[key] for key in sorted(dedup.keys())]


def parse_metrics(metrics_text):
    running = sum(float(value) for value in RUNNING_RE.findall(metrics_text))
    waiting = sum(float(value) for value in WAITING_RE.findall(metrics_text))
    total_success = sum(float(value) for value in SUCCESS_RE.findall(metrics_text))
    return int(round(running)), int(round(waiting)), int(round(total_success))


def resolve_esphome_power_url():
    if ESPHOME_POWER_URL:
        return ESPHOME_POWER_URL
    if not ESPHOME_BASE_URL:
        raise RuntimeError(
            "esphome backend requires TELEMETRY_ESPHOME_POWER_URL or TELEMETRY_ESPHOME_BASE_URL"
        )
    path = ESPHOME_POWER_PATH if ESPHOME_POWER_PATH else "/sensor/power"
    if not path.startswith("/"):
        path = f"/{path}"
    return urljoin(f"{ESPHOME_BASE_URL}/", path.lstrip("/"))


def read_esphome_power_watts():
    payload = json.loads(fetch_text(resolve_esphome_power_url()))

    # Collect battery_* fields (canonical); fall back to solix_* for older monitors.
    def _bfield(battery_key, solix_key):
        return payload.get(battery_key) if battery_key in payload else payload.get(solix_key)

    reading_ts = _bfield("battery_reading_ts", "solix_reading_ts")
    power_feed_ts = payload.get("victron_reading_ts")
    if power_feed_ts is None:
        power_feed_ts = payload.get("power_reading_ts")
    if power_feed_ts is None:
        power_feed_ts = reading_ts
    extra = {
        # canonical battery_* fields
        "battery_soc_pct":        _bfield("battery_soc_pct", "solix_soc_pct"),
        "battery_solar_input_w":  _bfield("battery_solar_input_w", "solix_solar_input_w"),
        "battery_total_input_w":  _bfield("battery_total_input_w", "solix_total_input_w"),
        "battery_voltage_mv":     _bfield("battery_voltage_mv", "solix_voltage_mv"),
        "battery_temp_c":         _bfield("battery_temp_c", "solix_temp_c"),
        "battery_reading_ts":     reading_ts,
        "battery_charging_status": _bfield("battery_charging_status", "solix_charging_status"),
        "battery_capacity_wh":    payload.get("battery_capacity_wh"),
        # solix_* compat aliases (remove after all monitors emit battery_* natively)
        "solix_soc_pct":          _bfield("battery_soc_pct", "solix_soc_pct"),
        "solix_solar_input_w":    _bfield("battery_solar_input_w", "solix_solar_input_w"),
        "solix_total_input_w":    _bfield("battery_total_input_w", "solix_total_input_w"),
        "solix_voltage_mv":       _bfield("battery_voltage_mv", "solix_voltage_mv"),
        "solix_temp_c":           _bfield("battery_temp_c", "solix_temp_c"),
        "solix_reading_ts":       reading_ts,
        "solix_charging_status":  _bfield("battery_charging_status", "solix_charging_status"),
        "power_feed_reading_ts":  power_feed_ts,
    }
    ble_connected = bool(payload.get("ble_connected"))
    extra["ble_connected"] = ble_connected
    if "last_error" in payload:
        extra["solix_last_error"] = str(payload.get("last_error") or "")

    if not ble_connected:
        raise EsphomeFeedError("esphome battery feed disconnected", extra=extra)

    power_feed_ts_f = safe_float(power_feed_ts, lo=0.0)
    if SOLIX_STALE_SECONDS > 0 and power_feed_ts_f is not None:
        age_seconds = time.time() - power_feed_ts_f
        if age_seconds > SOLIX_STALE_SECONDS:
            recovery_note = maybe_recover_battery_feed(
                reason=f"stale power feed reading ({age_seconds:.1f}s old)",
                age_seconds=age_seconds,
            )
            raise EsphomeFeedError(
                f"esphome power feed reading stale by {age_seconds:.1f}s (>{SOLIX_STALE_SECONDS:.1f}s); {recovery_note}",
                extra=extra,
            )

    raw_value = payload.get("value")
    if raw_value is None and "value" not in payload:
        state_value = str(payload.get("state", ""))
        match = re.search(r"([-+]?[0-9]*\.?[0-9]+)", state_value)
        if match:
            raw_value = match.group(1)
    if raw_value is None:
        raise RuntimeError("esphome power payload missing value/state")
    watts = float(raw_value)
    if watts < 0:
        raise RuntimeError(f"esphome power payload had invalid negative watts: {watts}")
    return watts, {}, "esphome", True, extra


def maybe_recover_battery_feed(reason, age_seconds=None):
    if not SOLIX_AUTO_RECOVER:
        return "auto-recovery disabled"

    now_ts = time.time()
    with SOLIX_RECOVERY_LOCK:
        last_attempt_ts = float(SOLIX_RECOVERY_STATE.get("last_attempt_ts") or 0.0)
        if (now_ts - last_attempt_ts) < SOLIX_RECOVERY_COOLDOWN_SECONDS:
            wait_seconds = SOLIX_RECOVERY_COOLDOWN_SECONDS - (now_ts - last_attempt_ts)
            return f"recovery cooldown active ({wait_seconds:.0f}s remaining)"
        SOLIX_RECOVERY_STATE["last_attempt_ts"] = now_ts
        SOLIX_RECOVERY_STATE["attempt_count"] = int(SOLIX_RECOVERY_STATE.get("attempt_count") or 0) + 1
        attempt_count = SOLIX_RECOVERY_STATE["attempt_count"]

    monitor_unit = BATTERY_MONITOR_SERVICE_NAME
    if attempt_count % SOLIX_RECOVERY_ESCALATE_EVERY == 0:
        restart_units = ["bluetooth.service", monitor_unit]
        mode_label = f"restart bluetooth.service + {monitor_unit}"
    else:
        restart_units = [monitor_unit]
        mode_label = f"restart {monitor_unit}"

    logging.warning("Battery feed recovery attempt #%d: %s (reason=%s)", attempt_count, mode_label, reason)

    def _do_recovery(units, label, age_s):
        try:
            for unit in units:
                subprocess.run(
                    ["systemctl", "restart", unit],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            logging.warning("Battery feed recovery succeeded: %s", label)
        except (OSError, subprocess.SubprocessError) as err:
            age_label = "n/a" if age_s is None else f"{age_s:.1f}s"
            logging.warning("Battery feed auto-recovery failed (age=%s, mode=%s): %s", age_label, label, err)

    threading.Thread(target=_do_recovery, args=(restart_units, mode_label, age_seconds), daemon=True).start()
    return f"auto-recovery dispatched ({mode_label})"


def read_rpi_power_watts():
    """Raspberry Pi power via PMIC hwmon (Pi 5+). Raises if unavailable."""
    for hwmon_path in sorted(glob.glob("/sys/class/hwmon/hwmon*/power1_input")):
        try:
            with open(hwmon_path) as f:
                uw = int(f.read().strip())
            if uw > 0:
                return float(uw) / 1_000_000.0, {}, "rpi-hwmon", True, {}
        except (OSError, ValueError):
            continue
    raise RuntimeError("no Pi power meter found (hwmon power1_input not available)")


def parse_iso_timestamp(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def safe_float(value, lo=None, hi=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if lo is not None and parsed < lo:
        return None
    if hi is not None and parsed > hi:
        return None
    return parsed


def read_battery_csv_rows(now_ts):
    """Read battery telemetry CSV rows. Supports both solix-monitor and lfp-monitor CSV formats."""
    cutoff_ts = now_ts - (HISTORY_LOOKBACK_DAYS * 86400.0)
    now_date = datetime.fromtimestamp(now_ts, tz=timezone.utc).date()
    min_date = now_date - timedelta(days=HISTORY_LOOKBACK_DAYS)
    prefix = BATTERY_CSV_PREFIX  # e.g. "solix" or "battery"
    pattern = os.path.join(BATTERY_CSV_DIR, f"{prefix}-*.csv")
    files = sorted(glob.glob(pattern))
    dedup = {}
    date_re = re.compile(rf"^{re.escape(prefix)}-(\d{{4}}-\d{{2}}-\d{{2}})\.csv$")
    for path in files:
        basename = os.path.basename(path)
        match = date_re.match(basename)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < min_date:
            continue
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader((line.replace("\x00", "") for line in handle))
                for row in reader:
                    ts = parse_iso_timestamp(row.get("timestamp"))
                    if ts is None or ts < cutoff_ts or ts > (now_ts + 60.0):
                        continue
                    # solix-monitor uses total_output_w/total_input_w
                    # lfp-monitor uses load_w/solar_input_w directly
                    load_w = safe_float(row.get("load_w") or row.get("total_output_w"), lo=0.0)
                    charge_w = safe_float(row.get("solar_input_w") or row.get("total_input_w"), lo=0.0)
                    dedup[ts] = {
                        "ts": ts,
                        "load_w": load_w,
                        "charge_w": charge_w,
                        "soc_pct": safe_float(row.get("soc_pct"), lo=0.0, hi=100.0),
                    }
        except OSError:
            continue
    return [dedup[key] for key in sorted(dedup.keys())]


# Keep old name as alias for any external callers.
def read_solix_rows(now_ts):
    return read_battery_csv_rows(now_ts)


def build_binned_window(rows, start_ts, end_ts, bin_seconds, vllm_rows=None):
    if end_ts <= start_ts:
        return {
            "window_start_ts": start_ts,
            "window_end_ts": end_ts,
            "bin_seconds": bin_seconds,
            "points": [],
        }

    bin_count = max(1, int(math.ceil((end_ts - start_ts) / float(bin_seconds))))
    bins = [
        {
            "sum_load": 0.0,
            "count_load": 0,
            "sum_charge": 0.0,
            "count_charge": 0,
            "soc_pct": None,
            "soc_ts": None,
            "sum_completed": 0.0,
            "sum_running": 0.0,
            "count_running": 0,
            "sum_waiting": 0.0,
            "count_waiting": 0,
        }
        for _ in range(bin_count)
    ]

    for row in rows:
        ts = row.get("ts")
        if ts is None or ts < start_ts or ts > end_ts:
            continue
        idx = int((ts - start_ts) // bin_seconds)
        if idx < 0:
            continue
        if idx >= bin_count:
            idx = bin_count - 1
        item = bins[idx]

        load_w = row.get("load_w")
        if load_w is not None:
            item["sum_load"] += load_w
            item["count_load"] += 1

        charge_w = row.get("charge_w")
        if charge_w is not None:
            item["sum_charge"] += charge_w
            item["count_charge"] += 1

        soc_pct = row.get("soc_pct")
        if soc_pct is not None and (item["soc_ts"] is None or ts >= item["soc_ts"]):
            item["soc_pct"] = soc_pct
            item["soc_ts"] = ts

    for row in (vllm_rows or []):
        ts = row.get("ts")
        if ts is None or ts < start_ts or ts > end_ts:
            continue
        idx = int((ts - start_ts) // bin_seconds)
        if idx < 0:
            continue
        if idx >= bin_count:
            idx = bin_count - 1
        completed = row.get("completed")
        if completed is not None:
            bins[idx]["sum_completed"] += completed
        running = row.get("running")
        if running is not None:
            bins[idx]["sum_running"] += running
            bins[idx]["count_running"] += 1
        waiting = row.get("waiting")
        if waiting is not None:
            bins[idx]["sum_waiting"] += waiting
            bins[idx]["count_waiting"] += 1

    points = []
    carry_soc = None
    for idx, item in enumerate(bins):
        load_w = (
            round(item["sum_load"] / item["count_load"], 3)
            if item["count_load"] > 0
            else None
        )
        charge_w = (
            round(item["sum_charge"] / item["count_charge"], 3)
            if item["count_charge"] > 0
            else None
        )

        soc_pct = item["soc_pct"]
        if soc_pct is None:
            soc_pct = carry_soc
        else:
            carry_soc = soc_pct

        avg_concurrent = (
            round(item["sum_running"] / item["count_running"], 1)
            if item["count_running"] > 0
            else None
        )
        avg_waiting = (
            round(item["sum_waiting"] / item["count_waiting"], 1)
            if item["count_waiting"] > 0
            else None
        )
        point_ts = min(end_ts, start_ts + ((idx + 1) * bin_seconds))
        points.append(
            {
                "ts": int(point_ts),
                "iso": datetime.fromtimestamp(point_ts, tz=timezone.utc).isoformat(),
                "load_w": load_w,
                "charge_w": charge_w,
                "soc_pct": round(soc_pct, 3) if soc_pct is not None else None,
                "avg_concurrent": avg_concurrent,
                "avg_waiting": avg_waiting,
            }
        )

    return {
        "window_start_ts": int(start_ts),
        "window_end_ts": int(end_ts),
        "bin_seconds": bin_seconds,
        "points": points,
    }


def compute_history_payload(now_ts=None):
    now_ts = float(now_ts if now_ts is not None else time.time())
    if HISTORY_DB is not None:
        payload = HISTORY_DB.build_history_payload(
            now_ts=now_ts,
            lookback_days=HISTORY_LOOKBACK_DAYS,
            bin_24h_seconds=HISTORY_24H_BIN_SECONDS,
            bin_7d_seconds=HISTORY_7D_BIN_SECONDS,
        )
        if int(payload.get("solix_rows_considered") or 0) > 0:
            payload["cache_ttl_seconds"] = int(HISTORY_CACHE_TTL_SECONDS)
            return payload

    rows = read_solix_rows(now_ts)
    vllm_rows = read_vllm_rows(now_ts)

    window_24h = build_binned_window(
        rows=rows,
        start_ts=now_ts - 86400.0,
        end_ts=now_ts,
        bin_seconds=HISTORY_24H_BIN_SECONDS,
        vllm_rows=vllm_rows,
    )
    window_7d = build_binned_window(
        rows=rows,
        start_ts=now_ts - (HISTORY_LOOKBACK_DAYS * 86400.0),
        end_ts=now_ts,
        bin_seconds=HISTORY_7D_BIN_SECONDS,
        vllm_rows=vllm_rows,
    )

    return {
        "generated_at_ts": int(now_ts),
        "generated_at_iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "cache_ttl_seconds": int(HISTORY_CACHE_TTL_SECONDS),
        "lookback_days": HISTORY_LOOKBACK_DAYS,
        "source": "solix_csv",
        "bin_statistic": "mean",
        "rows_considered": len(rows),
        "history_24h": window_24h,
        "history_7d": window_7d,
    }


def bootstrap_history_db(now_ts=None):
    if HISTORY_DB is None:
        return

    now_ts = float(now_ts if now_ts is not None else time.time())
    start_ts = now_ts - (HISTORY_LOOKBACK_DAYS * 86400.0)
    end_ts = now_ts + 60.0

    try:
        solix_count = HISTORY_DB.count_battery_rows(start_ts, end_ts)
        if solix_count <= 0:
            rows = read_solix_rows(now_ts)
            imported = HISTORY_DB.import_battery_rows(rows)
            logging.info("bootstrapped sqlite history with %d battery CSV rows", imported)

        vllm_count = HISTORY_DB.count_vllm_rows(start_ts, end_ts)
        if vllm_count <= 0 and VLLM_LOG_DIR:
            rows = read_vllm_rows(now_ts)
            imported = HISTORY_DB.import_vllm_rows(rows)
            logging.info("bootstrapped sqlite history with %d vLLM CSV rows", imported)
    except Exception as err:  # pragma: no cover - startup environment guard
        logging.warning("history sqlite bootstrap failed: %s", err)


def refresh_history_payload(now_ts=None):
    current_ts = float(now_ts if now_ts is not None else time.time())
    try:
        payload = compute_history_payload(now_ts=current_ts)
    except Exception as err:
        with HISTORY_LOCK:
            HISTORY_CACHE["refreshing"] = False
            HISTORY_CACHE["last_error"] = str(err)
        logging.warning("history refresh failed: %s", err)
        raise

    with HISTORY_LOCK:
        HISTORY_CACHE["generated_at"] = current_ts
        HISTORY_CACHE["payload"] = payload
        HISTORY_CACHE["refreshing"] = False
        HISTORY_CACHE["last_error"] = ""
    return payload


def _history_refresh_worker():
    try:
        refresh_history_payload()
    except Exception:
        return


def start_history_refresh(force=False):
    now_ts = time.time()
    with HISTORY_LOCK:
        cached = HISTORY_CACHE.get("payload")
        generated_at = float(HISTORY_CACHE.get("generated_at") or 0.0)
        if HISTORY_CACHE.get("refreshing"):
            return False
        if (
            not force
            and cached is not None
            and (now_ts - generated_at) < HISTORY_CACHE_TTL_SECONDS
        ):
            return False
        HISTORY_CACHE["refreshing"] = True

    thread = threading.Thread(target=_history_refresh_worker, daemon=True)
    thread.start()
    return True


def get_history_payload(force_refresh=False):
    now_ts = time.time()
    with HISTORY_LOCK:
        cached = HISTORY_CACHE.get("payload")
        generated_at = float(HISTORY_CACHE.get("generated_at") or 0.0)
        is_fresh = (
            cached is not None
            and (now_ts - generated_at) < HISTORY_CACHE_TTL_SECONDS
        )
        if not force_refresh and is_fresh:
            return cached

    if cached is not None:
        start_history_refresh(force=True)
        return cached

    return refresh_history_payload(now_ts=now_ts)


def power_backends_in_order():
    if POWER_BACKEND == "jtop":
        return ["jtop"]
    if POWER_BACKEND == "nvidia-smi":
        return ["nvidia-smi"]
    if POWER_BACKEND == "esphome":
        return ["esphome"]
    if POWER_BACKEND == "rpi":
        return ["rpi"]
    if POWER_BACKEND == "auto":
        order = []
        if ESPHOME_POWER_URL or ESPHOME_BASE_URL:
            order.append("esphome")
        if IS_JETSON:
            order.extend(["jtop", "nvidia-smi"])
        elif IS_RPI:
            order.append("rpi")
        else:
            order.extend(["nvidia-smi", "jtop"])
        return order
    return [POWER_BACKEND]


def read_power_watts():
    errors = []
    sticky_extra = {}
    for backend in power_backends_in_order():
        try:
            if backend == "jtop":
                watts, rails_watts, source = read_jtop_power_watts()
                return watts, rails_watts, source, False, sticky_extra, errors
            if backend == "nvidia-smi":
                watts = read_nvidia_smi_watts()
                return watts, {}, "nvidia-smi", False, sticky_extra, errors
            if backend == "esphome":
                watts, rails, source, is_live, extra = read_esphome_power_watts()
                return watts, rails, source, is_live, extra, errors
            if backend == "rpi":
                watts, rails, source, is_live, extra = read_rpi_power_watts()
                return watts, rails, source, is_live, extra, errors
            errors.append(f"{backend}: unsupported backend")
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as err:
            error_detail = f"{backend}: {err}"
            if backend == "esphome":
                if isinstance(err, EsphomeFeedError):
                    sticky_extra.update(err.extra)
                err_text = str(err)
                if "esphome power feed reading stale by" not in err_text:
                    recovery_note = maybe_recover_battery_feed(reason=f"esphome read failure: {err_text}")
                    error_detail = f"{error_detail} ({recovery_note})"
            errors.append(error_detail)

    raise RuntimeError(" | ".join(errors) if errors else "no telemetry backend available")


def sample_once():
    next_state = {}
    error_parts = []

    measured_server_watts = None
    power_is_wall_total = False
    power_source = "fallback"

    try:
        measured_server_watts, rails_watts, power_source, power_is_wall_total, extra, backend_errors = read_power_watts()
        next_state["measured_server_watts"] = measured_server_watts
        next_state["measured_gpu_watts"] = measured_server_watts
        next_state["power_rails_watts"] = rails_watts
        next_state["power_backend"] = power_source
        next_state.update(extra)
        if backend_errors:
            error_parts.append(f"power-backends: {' | '.join(backend_errors)}")
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as err:
        error_parts.append(f"power: {err}")

    try:
        load_payload = json.loads(fetch_text(f"{VLLM_BASE_URL}/load"))
        next_state["server_load"] = load_payload.get("server_load")
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as err:
        error_parts.append(f"/load: {err}")

    try:
        metrics_text = fetch_text(f"{VLLM_BASE_URL}/metrics")
        requests_running, requests_waiting, total_success = parse_metrics(metrics_text)
        next_state["requests_running"] = requests_running
        next_state["requests_waiting"] = requests_waiting
        next_state["request_success_total"] = total_success
    except (URLError, TimeoutError, ValueError) as err:
        error_parts.append(f"/metrics: {err}")

    with STATE_LOCK:
        current = dict(STATE)

        def clear_solix_live_fields():
            for key in (
                "battery_solar_input_w", "battery_total_input_w", "battery_voltage_mv",
                "battery_temp_c", "battery_reading_ts", "battery_charging_status",
                "battery_effective_solar_w",
                "solix_solar_input_w", "solix_total_input_w", "solix_voltage_mv",
                "solix_temp_c", "solix_reading_ts", "solix_charging_status",
                "solix_effective_solar_w",
                "power_reading_ts",
            ):
                current[key] = None

        if "server_load" in next_state:
            current["server_load"] = next_state["server_load"]
        if "requests_running" in next_state:
            current["requests_running"] = next_state["requests_running"]
        if "requests_waiting" in next_state:
            current["requests_waiting"] = next_state["requests_waiting"]
        if "request_success_total" in next_state:
            new_total = next_state["request_success_total"]
            last_total = current.get("request_success_total") or 0
            delta = max(0, new_total - last_total) if new_total >= last_total else 0
            current["requests_completed_interval"] = delta
            current["request_success_total"] = new_total
            log_vllm_metrics(time.time(), current["requests_running"], current["requests_waiting"], delta)
        if "power_rails_watts" in next_state:
            current["power_rails_watts"] = next_state["power_rails_watts"]
        if "power_backend" in next_state:
            current["power_backend"] = next_state["power_backend"]
        for k in (
            "battery_soc_pct", "battery_solar_input_w", "battery_total_input_w",
            "battery_voltage_mv", "battery_temp_c", "battery_reading_ts",
            "battery_charging_status", "battery_capacity_wh",
            "solix_soc_pct", "solix_solar_input_w", "solix_total_input_w",
            "solix_voltage_mv", "solix_temp_c", "solix_reading_ts", "solix_charging_status",
            "power_feed_reading_ts",
        ):
            if k in next_state:
                current[k] = next_state[k]

        # At 100% SOC some charge controllers report 0W solar input (charge stopped)
        # but solar is still powering the load via pass-through. Effective solar = load.
        _soc = current.get("battery_soc_pct") or current.get("solix_soc_pct")
        _solar = current.get("battery_solar_input_w") or current.get("solix_solar_input_w")
        _load = current.get("estimated_total_watts")
        _effective = _load if (_soc is not None and _soc >= 100 and _solar == 0 and _load is not None) else _solar
        current["battery_effective_solar_w"] = _effective
        current["solix_effective_solar_w"] = _effective

        if measured_server_watts is not None:
            if power_is_wall_total:
                current["power_reading_ts"] = current.get("power_feed_reading_ts") or current.get("battery_reading_ts") or current.get("solix_reading_ts") or time.time()
                wall_total_watts = measured_server_watts
                current["measured_server_watts"] = None
                current["measured_gpu_watts"] = None
                current["base_system_watts"] = 0.0

                if current["requests_running"] <= 0:
                    if current["idle_gpu_watts"] > 0:
                        current["idle_gpu_watts"] = (current["idle_gpu_watts"] * 0.8) + (wall_total_watts * 0.2)
                    else:
                        current["idle_gpu_watts"] = wall_total_watts

                if current["requests_running"] > 0:
                    current["attributed_gpu_watts"] = wall_total_watts / current["requests_running"]
                else:
                    current["attributed_gpu_watts"] = wall_total_watts

                current["estimated_total_watts"] = wall_total_watts
                current["estimated_total_server_watts"] = wall_total_watts
                current["cost_share_fraction"] = 1.0
                current["power_measurement_kind"] = "wall-total"
                current["watts_is_live"] = True
            else:
                current["power_reading_ts"] = time.time()
                current["measured_server_watts"] = measured_server_watts
                current["measured_gpu_watts"] = measured_server_watts

                if current["requests_running"] <= 0:
                    if current["idle_gpu_watts"] > 0:
                        current["idle_gpu_watts"] = (current["idle_gpu_watts"] * 0.8) + (measured_server_watts * 0.2)
                    else:
                        current["idle_gpu_watts"] = measured_server_watts

                if current["requests_running"] > 0:
                    current["attributed_gpu_watts"] = measured_server_watts / current["requests_running"]
                else:
                    current["attributed_gpu_watts"] = measured_server_watts

                # jtop/nvidia-smi reports component rails; add fixed base overhead.
                current["base_system_watts"] = BASE_SYSTEM_WATTS
                total_server_watts = (BASE_SYSTEM_WATTS + measured_server_watts) * GPU_COOLING_MULTIPLIER
                if CLAMP_MIN_WATTS > 0:
                    total_server_watts = max(CLAMP_MIN_WATTS, total_server_watts)
                if CLAMP_MAX_WATTS > 0:
                    total_server_watts = min(CLAMP_MAX_WATTS, total_server_watts)
                current["estimated_total_watts"] = total_server_watts
                current["estimated_total_server_watts"] = total_server_watts
                current["cost_share_fraction"] = 1.0
                current["power_measurement_kind"] = "component-load"
                current["watts_is_live"] = False
                clear_solix_live_fields()
        else:
            current["watts_is_live"] = False
            current["power_measurement_kind"] = "unavailable"
            clear_solix_live_fields()

        current["is_active"] = current["requests_running"] > 0
        current["timestamp"] = time.time()
        current["source"] = f"{power_source}+vllm" if measured_server_watts is not None else "fallback"
        current["last_error"] = " | ".join(error_parts)

        STATE.update(current)


def sampler_loop():
    while True:
        next_wake = time.time() + SAMPLE_INTERVAL
        sample_once()
        time.sleep(max(0.0, next_wake - time.time()))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if path == "/telemetry/history":
            force_refresh = str(query.get("refresh", ["0"])[0]).strip().lower() in (
                "1",
                "true",
                "yes",
            )
            try:
                payload = get_history_payload(force_refresh=force_refresh)
            except Exception as err:  # pragma: no cover - defensive runtime guard
                body = json.dumps(
                    {"error": "history_unavailable", "detail": str(err)},
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path != "/telemetry/power":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not_found"}')
            return

        with STATE_LOCK:
            payload = dict(STATE)

        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    thread = threading.Thread(target=sampler_loop, daemon=True)
    thread.start()
    bootstrap_history_db()
    try:
        refresh_history_payload()
    except Exception:
        pass

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
