#!/usr/bin/env python3
import json
import logging
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import urlopen


HOST = os.environ.get("TELEMETRY_HOST", "127.0.0.1")
PORT = int(os.environ.get("TELEMETRY_PORT", "18081"))
SAMPLE_INTERVAL = float(os.environ.get("TELEMETRY_SAMPLE_INTERVAL", "1.0"))
VLLM_BASE_URL = os.environ.get("TELEMETRY_VLLM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
POWER_BACKEND = os.environ.get("TELEMETRY_POWER_BACKEND", "auto").strip().lower()
NVIDIA_SMI_BIN = os.environ.get("NVIDIA_SMI_BIN", "nvidia-smi")
REQUEST_TIMEOUT = float(os.environ.get("TELEMETRY_REQUEST_TIMEOUT", "1.5"))
IS_JETSON = os.path.isfile("/etc/nv_tegra_release")
DEFAULT_IDLE_GPU_WATTS = float(
    os.environ.get("TELEMETRY_IDLE_GPU_WATTS", "2" if IS_JETSON else "35")
)
BASE_SYSTEM_WATTS = float(
    os.environ.get("TELEMETRY_BASE_SYSTEM_WATTS", "5.5" if IS_JETSON else "300")
)
GPU_COOLING_MULTIPLIER = float(
    os.environ.get("TELEMETRY_GPU_COOLING_MULTIPLIER", "1.00" if IS_JETSON else "1.35")
)


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
    "power_rails_watts": {},
    "last_error": "",
}

JTOP_POWER_SERVICE = None
JTOP_IMPORT_ERROR = ""

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


def power_backends_in_order():
    if POWER_BACKEND == "jtop":
        return ["jtop"]
    if POWER_BACKEND == "nvidia-smi":
        return ["nvidia-smi"]
    if POWER_BACKEND == "auto":
        if IS_JETSON:
            return ["jtop", "nvidia-smi"]
        return ["nvidia-smi", "jtop"]
    return [POWER_BACKEND]


def read_power_watts():
    errors = []
    for backend in power_backends_in_order():
        try:
            if backend == "jtop":
                watts, rails_watts, source = read_jtop_power_watts()
                return watts, rails_watts, source
            if backend == "nvidia-smi":
                watts = read_nvidia_smi_watts()
                return watts, {}, "nvidia-smi"
            errors.append(f"{backend}: unsupported backend")
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as err:
            errors.append(f"{backend}: {err}")

    raise RuntimeError(" | ".join(errors) if errors else "no telemetry backend available")


def sample_once():
    next_state = {}
    error_parts = []

    measured_server_watts = None
    power_source = "fallback"

    try:
        measured_server_watts, rails_watts, power_source = read_power_watts()
        next_state["measured_server_watts"] = measured_server_watts
        next_state["measured_gpu_watts"] = measured_server_watts
        next_state["power_rails_watts"] = rails_watts
        next_state["power_backend"] = power_source
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as err:
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

        if measured_server_watts is not None:
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

            # jtop reports board rails; add fixed wall-measured base overhead.
            current["base_system_watts"] = BASE_SYSTEM_WATTS
            total_server_watts = (BASE_SYSTEM_WATTS + measured_server_watts) * GPU_COOLING_MULTIPLIER

            current["estimated_total_watts"] = total_server_watts
            current["estimated_total_server_watts"] = total_server_watts
            current["cost_share_fraction"] = 1.0

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
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if self.path != "/telemetry/power":
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
