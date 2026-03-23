#!/usr/bin/env python3
import csv
import glob
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import urlopen


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
SOLIX_LOG_DIR = os.environ.get("TELEMETRY_SOLIX_LOG_DIR", "/opt/bartleby/solix-monitor/logs").strip()
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
    "solix_soc_pct": None,
    "solix_solar_input_w": None,
    "solix_total_input_w": None,
    "solix_voltage_mv": None,
    "solix_temp_c": None,
    "solix_reading_ts": None,
}

JTOP_POWER_SERVICE = None
JTOP_IMPORT_ERROR = ""
HISTORY_LOCK = threading.Lock()
HISTORY_CACHE = {"generated_at": 0.0, "payload": None}

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


def parse_metrics(metrics_text):
    running = sum(float(value) for value in RUNNING_RE.findall(metrics_text))
    waiting = sum(float(value) for value in WAITING_RE.findall(metrics_text))
    return int(round(running)), int(round(waiting))


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
    extra = {k: payload[k] for k in (
        "solix_soc_pct", "solix_solar_input_w", "solix_total_input_w",
        "solix_voltage_mv", "solix_temp_c", "solix_reading_ts",
    ) if k in payload}
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


def read_solix_rows(now_ts):
    cutoff_ts = now_ts - (HISTORY_LOOKBACK_DAYS * 86400.0)
    now_date = datetime.fromtimestamp(now_ts, tz=timezone.utc).date()
    min_date = now_date - timedelta(days=HISTORY_LOOKBACK_DAYS)

    pattern = os.path.join(SOLIX_LOG_DIR, "solix-*.csv")
    files = sorted(glob.glob(pattern))
    dedup = {}
    for path in files:
        basename = os.path.basename(path)
        match = re.match(r"^solix-(\d{4}-\d{2}-\d{2})\.csv$", basename)
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
                        "load_w": safe_float(row.get("total_output_w"), lo=0.0),
                        "charge_w": safe_float(row.get("total_input_w"), lo=0.0),
                        "soc_pct": safe_float(row.get("soc_pct"), lo=0.0, hi=100.0),
                    }
        except OSError:
            continue
    return [dedup[key] for key in sorted(dedup.keys())]


def build_binned_window(rows, start_ts, end_ts, bin_seconds):
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

        point_ts = min(end_ts, start_ts + ((idx + 1) * bin_seconds))
        points.append(
            {
                "ts": int(point_ts),
                "iso": datetime.fromtimestamp(point_ts, tz=timezone.utc).isoformat(),
                "load_w": load_w,
                "charge_w": charge_w,
                "soc_pct": round(soc_pct, 3) if soc_pct is not None else None,
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
    rows = read_solix_rows(now_ts)

    window_24h = build_binned_window(
        rows=rows,
        start_ts=now_ts - 86400.0,
        end_ts=now_ts,
        bin_seconds=HISTORY_24H_BIN_SECONDS,
    )
    window_7d = build_binned_window(
        rows=rows,
        start_ts=now_ts - (HISTORY_LOOKBACK_DAYS * 86400.0),
        end_ts=now_ts,
        bin_seconds=HISTORY_7D_BIN_SECONDS,
    )

    return {
        "generated_at_ts": int(now_ts),
        "generated_at_iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "cache_ttl_seconds": int(HISTORY_CACHE_TTL_SECONDS),
        "lookback_days": HISTORY_LOOKBACK_DAYS,
        "source": "solix_csv",
        "rows_considered": len(rows),
        "history_24h": window_24h,
        "history_7d": window_7d,
    }


def get_history_payload(force_refresh=False):
    now_ts = time.time()
    with HISTORY_LOCK:
        cached = HISTORY_CACHE.get("payload")
        generated_at = float(HISTORY_CACHE.get("generated_at") or 0.0)
        if (
            not force_refresh
            and cached is not None
            and (now_ts - generated_at) < HISTORY_CACHE_TTL_SECONDS
        ):
            return cached

    payload = compute_history_payload(now_ts=now_ts)
    with HISTORY_LOCK:
        HISTORY_CACHE["generated_at"] = now_ts
        HISTORY_CACHE["payload"] = payload
    return payload


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
    for backend in power_backends_in_order():
        try:
            if backend == "jtop":
                watts, rails_watts, source = read_jtop_power_watts()
                return watts, rails_watts, source, False, {}
            if backend == "nvidia-smi":
                watts = read_nvidia_smi_watts()
                return watts, {}, "nvidia-smi", False, {}
            if backend == "esphome":
                return read_esphome_power_watts()
            if backend == "rpi":
                return read_rpi_power_watts()
            errors.append(f"{backend}: unsupported backend")
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as err:
            errors.append(f"{backend}: {err}")

    raise RuntimeError(" | ".join(errors) if errors else "no telemetry backend available")


def sample_once():
    next_state = {}
    error_parts = []

    measured_server_watts = None
    power_is_wall_total = False
    power_source = "fallback"

    try:
        measured_server_watts, rails_watts, power_source, power_is_wall_total, extra = read_power_watts()
        next_state["measured_server_watts"] = measured_server_watts
        next_state["measured_gpu_watts"] = measured_server_watts
        next_state["power_rails_watts"] = rails_watts
        next_state["power_backend"] = power_source
        next_state.update(extra)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as err:
        error_parts.append(f"power: {err}")

    try:
        load_payload = json.loads(fetch_text(f"{VLLM_BASE_URL}/load"))
        next_state["server_load"] = load_payload.get("server_load")
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as err:
        error_parts.append(f"/load: {err}")

    try:
        metrics_text = fetch_text(f"{VLLM_BASE_URL}/metrics")
        requests_running, requests_waiting = parse_metrics(metrics_text)
        next_state["requests_running"] = requests_running
        next_state["requests_waiting"] = requests_waiting
    except (URLError, TimeoutError, ValueError) as err:
        error_parts.append(f"/metrics: {err}")

    with STATE_LOCK:
        current = dict(STATE)

        if "server_load" in next_state:
            current["server_load"] = next_state["server_load"]
        if "requests_running" in next_state:
            current["requests_running"] = next_state["requests_running"]
        if "requests_waiting" in next_state:
            current["requests_waiting"] = next_state["requests_waiting"]
        if "power_rails_watts" in next_state:
            current["power_rails_watts"] = next_state["power_rails_watts"]
        if "power_backend" in next_state:
            current["power_backend"] = next_state["power_backend"]
        for k in ("solix_soc_pct", "solix_solar_input_w", "solix_total_input_w", "solix_voltage_mv", "solix_temp_c", "solix_reading_ts"):
            if k in next_state:
                current[k] = next_state[k]

        if measured_server_watts is not None:
            current["power_reading_ts"] = current.get("solix_reading_ts") or time.time()
            if power_is_wall_total:
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

        current["is_active"] = current["requests_running"] > 0
        current["timestamp"] = time.time()
        current["source"] = f"{power_source}+vllm" if measured_server_watts is not None else "fallback"
        current["last_error"] = " | ".join(error_parts)

        STATE.update(current)


def sampler_loop():
    while True:
        sample_once()
        time.sleep(SAMPLE_INTERVAL)


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

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
