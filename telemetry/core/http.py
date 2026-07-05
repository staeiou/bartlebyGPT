from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import deployment as dep
from .channels import channel_meta


def build_payload(deployment, snapshot, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    channels = {}
    for name, reading in dep.resolve_channels(deployment, snapshot, now).items():
        meta = channel_meta(name)
        channels[name] = {
            "value": reading.value,
            "ts": reading.ts,
            "source_id": reading.source_id,
            "unit": meta.unit if meta else "",
            "label": meta.label if meta else name,
            "group": meta.group if meta else "",
        }
    return {
        "schema_version": 1,
        "deployment": deployment.descriptor(),
        "capabilities": dep.capabilities(deployment, snapshot, now),
        "channels": channels,
        "timestamp": now,
    }


def _reading(channels: dict, name: str):
    return channels.get(name)


def _value(channels: dict, name: str):
    reading = _reading(channels, name)
    return reading.value if reading else None


def _ts(channels: dict, *names: str):
    for name in names:
        reading = _reading(channels, name)
        if reading:
            return reading.ts
    return None


def build_sensor_power_payload(deployment, snapshot, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    channels = dep.resolve_channels(deployment, snapshot, now)
    load_w = _value(channels, "load.w")
    if load_w is None:
        load_w = _value(channels, "wall.total_w")
    solar_w = _value(channels, "solar.input_w")
    soc = _value(channels, "battery.soc_pct")
    voltage_v = _value(channels, "battery.voltage_v")
    current_a = _value(channels, "battery.current_a")
    temp_c = _value(channels, "battery.temp_c")
    battery_ts = _ts(channels, "battery.soc_pct", "battery.voltage_v", "battery.current_a")
    power_ts = _ts(channels, "load.w", "wall.total_w", "solar.input_w")
    connected = load_w is not None or solar_w is not None or soc is not None or voltage_v is not None
    voltage_mv = round(voltage_v * 1000.0, 1) if isinstance(voltage_v, (int, float)) else None
    current_ma = round(current_a * 1000.0, 1) if isinstance(current_a, (int, float)) else None

    return {
        "value": load_w,
        "ble_connected": connected,
        "last_error": "",
        "battery_soc_pct": soc,
        "battery_solar_input_w": solar_w,
        "battery_total_input_w": solar_w,
        "battery_voltage_mv": voltage_mv,
        "battery_temp_c": temp_c,
        "battery_reading_ts": battery_ts,
        "battery_charging_status": _value(channels, "charge.state"),
        "battery_capacity_wh": deployment.capacity_wh,
        "battery_remaining_ah": _value(channels, "battery.remaining_ah"),
        "battery_nominal_ah": _value(channels, "battery.nominal_ah"),
        "battery_net_current_ma": current_ma,
        "power_reading_ts": power_ts,
        "victron_reading_ts": power_ts,
        "source": deployment.id,
        "deployment_profile": deployment.id,
        "solix_soc_pct": soc,
        "solix_solar_input_w": solar_w,
        "solix_total_input_w": solar_w,
        "solix_voltage_mv": voltage_mv,
        "solix_temp_c": temp_c,
        "solix_reading_ts": battery_ts,
        "solix_charging_status": _value(channels, "charge.state"),
        "timestamp": now,
    }


def make_server(host: str, port: int, deployment, snapshot) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._json(200, {"ok": True})
            elif path in ("/telemetry", "/telemetry/power"):
                self._json(200, build_payload(deployment, snapshot))
            elif path == "/sensor/power":
                self._json(200, build_sensor_power_payload(deployment, snapshot))
            elif path == "/telemetry/history":
                self._json(200, {"schema_version": 1, "deployment_id": deployment.id, "points": []})
            else:
                self._json(404, {"error": "not_found"})

    return ThreadingHTTPServer((host, port), Handler)
