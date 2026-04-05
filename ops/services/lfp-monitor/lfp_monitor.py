#!/usr/bin/env python3
"""LFP battery BLE monitor — JBD BMS + Victron SmartSolar — CSV logger + HTTP telemetry.

Two BLE sources:
  JBD BMS (0xFF00 service):  SOC (from Ah), voltage, temperature, net current, cell voltages.
  Victron SmartSolar (VE.Direct BLE, encrypted): solar_w, load_w (external_device_load).

Topology: Jetson powered from Victron load output.
  load_w  = victron.external_device_load (amps) × victron.battery_voltage (V)
  solar_w = victron.solar_power
  SOC     = jbd.remaining_ah / jbd.nominal_ah * 100  (BMS-reported SOC% is unreliable; use Ah)

HTTP contract matches solix_monitor.py /sensor/power shape so power_telemetry.py is unaffected.
"""

import asyncio
import csv
import json
import logging
import os
import glob
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bleak import BleakClient, BleakScanner
try:
    from victron_ble.devices.solar_charger import SolarCharger
    VICTRON_IMPORT_ERROR = ""
except Exception as err:
    SolarCharger = None
    VICTRON_IMPORT_ERROR = str(err)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lfp-monitor")

SCRIPT_DIR = Path(__file__).resolve().parent
OPS_DIR = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else SCRIPT_DIR
for candidate in (SCRIPT_DIR, OPS_DIR):
    s = str(candidate)
    if s not in sys.path:
        sys.path.insert(0, s)

from history_store import SQLiteHistoryStore

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

JBD_ADDR            = os.environ.get("BLE_ADDR", "A5:C2:39:1A:5D:29")
VICTRON_ADDR        = os.environ.get("VICTRON_BLE_ADDR", "CD:4C:1F:A1:BF:EF")
VICTRON_KEY         = os.environ.get("VICTRON_ENCRYPTION_KEY", "")
HOST                = os.environ.get("SOLIX_HOST", "127.0.0.1")
PORT                = int(os.environ.get("SOLIX_PORT", "18082"))
CSV_DIR             = Path(os.environ.get("SOLIX_CSV_DIR", str(SCRIPT_DIR / "logs")))
CSV_INTERVAL        = float(os.environ.get("SOLIX_CSV_INTERVAL", "60"))
CAPACITY_WH         = float(os.environ.get("SOLIX_CAPACITY_WH", "1280"))
NOMINAL_AH          = float(os.environ.get("LFP_NOMINAL_AH", "100"))
RECONNECT_DELAY     = float(os.environ.get("SOLIX_RECONNECT_DELAY", "10"))
SCAN_TIMEOUT        = max(2.0, float(os.environ.get("SOLIX_SCAN_TIMEOUT", "10")))
HISTORY_DB_PATH     = os.environ.get("SOLIX_HISTORY_DB_PATH", "").strip()
VICTRON_ADV_TIMEOUT = float(os.environ.get("VICTRON_ADV_TIMEOUT", "30"))
JBD_POLL_INTERVAL   = max(5.0, float(os.environ.get("BATTERY_JBD_POLL_INTERVAL", "60")))

# JBD GATT
JBD_CHAR_W = "0000ff02-0000-1000-8000-00805f9b34fb"
JBD_CHAR_N = "0000ff01-0000-1000-8000-00805f9b34fb"

CSV_FIELDS = [
    "timestamp", "soc_pct", "temp_c", "voltage_mv",
    "solar_input_w", "load_w", "remaining_ah", "nominal_ah",
    "net_current_ma", "charge_state", "ble_connected_jbd", "ble_connected_victron",
]
JBD_DEBUG_FIELDS = [
    "timestamp",
    "reading_ts",
    "raw_hex",
    "payload_len",
    "voltage_mv",
    "net_current_ma",
    "remaining_ah",
    "nominal_ah",
    "soc_pct_derived",
    "temp_c",
] + [f"b{i:02d}" for i in range(64)]

STATE_LOCK = threading.Lock()
STATE = {
    # Battery (JBD)
    "timestamp": None,
    "soc_pct": None,
    "temp_c": None,
    "voltage_mv": None,
    "remaining_ah": None,
    "nominal_ah": NOMINAL_AH,
    "net_current_ma": None,
    "charge_state": None,
    "ble_connected_jbd": False,
    "ble_connected_victron": False,
    "jbd_reading_ts": None,
    "victron_reading_ts": None,
    # Solar / load (Victron)
    "solar_input_w": None,
    "load_w": None,
    "yield_today_wh": None,
    # Derived
    "hours_remaining": None,
    # Meta
    "source": "lfp-ble",
    "last_error": "",
    "last_error_jbd": "",
    "last_error_victron": "",
}

try:
    HISTORY_DB = SQLiteHistoryStore(HISTORY_DB_PATH) if HISTORY_DB_PATH else None
except Exception as err:
    HISTORY_DB = None
    log.warning("history sqlite disabled: %s", err)


# ---------------------------------------------------------------------------
# JBD BMS protocol
# ---------------------------------------------------------------------------

def jbd_request(cmd):
    cs = (0x10000 - cmd) & 0xFFFF
    return bytes([0xDD, 0xA5, cmd, 0x00, cs >> 8, cs & 0xFF, 0x77])


def parse_jbd_basic(data: bytes):
    """Parse JBD command 0x03 response. Returns dict or None."""
    if len(data) < 7 or data[0] != 0xDD or data[2] != 0x00:
        return None
    length = data[3]
    payload = data[4:4 + length]
    if len(payload) < length or length < 23:
        return None

    total_mv        = struct.unpack_from(">H", payload, 0)[0] * 10
    net_current_raw = struct.unpack_from(">h", payload, 2)[0]
    net_current_ma  = net_current_raw * 10
    remaining_mah   = struct.unpack_from(">H", payload, 4)[0] * 10
    nominal_mah     = struct.unpack_from(">H", payload, 6)[0] * 10
    soc_from_ah     = round((remaining_mah / nominal_mah * 100) if nominal_mah > 0 else 0, 1)
    num_temps       = payload[26] if length > 26 else 0
    temps_c = []
    for i in range(num_temps):
        if 27 + i * 2 + 2 > len(payload):
            break
        raw = struct.unpack_from(">H", payload, 27 + i * 2)[0]
        temps_c.append(round(raw / 10.0 - 273.15, 1))

    return {
        "voltage_mv": total_mv,
        "net_current_ma": net_current_ma,
        "remaining_ah": round(remaining_mah / 1000, 3),
        "nominal_ah": round(nominal_mah / 1000, 3),
        "soc_pct": soc_from_ah,
        "temp_c": temps_c[0] if temps_c else None,
    }


def append_jbd_debug_row(reading_ts: float, raw: bytes, parsed: dict):
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.fromtimestamp(reading_ts, tz=timezone.utc)
    payload = raw[4:4 + raw[3]] if len(raw) >= 4 and raw[0] == 0xDD else b""
    row = {
        "timestamp": ts.isoformat(),
        "reading_ts": f"{reading_ts:.6f}",
        "raw_hex": raw.hex(),
        "payload_len": len(payload),
        "voltage_mv": parsed.get("voltage_mv"),
        "net_current_ma": parsed.get("net_current_ma"),
        "remaining_ah": parsed.get("remaining_ah"),
        "nominal_ah": parsed.get("nominal_ah"),
        "soc_pct_derived": parsed.get("soc_pct"),
        "temp_c": parsed.get("temp_c"),
    }
    for idx in range(64):
        row[f"b{idx:02d}"] = payload[idx] if idx < len(payload) else ""
    csv_path = CSV_DIR / f"jbd-basic-{ts.strftime('%Y-%m-%d')}.csv"
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=JBD_DEBUG_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _safe_csv_float(value):
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed
    except (TypeError, ValueError):
        return None


def restore_last_jbd_state_from_logs():
    pattern = str(CSV_DIR / "jbd-basic-*.csv")
    latest_row = None
    latest_ts = None
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader((line.replace("\x00", "") for line in handle))
                for row in reader:
                    reading_ts = _safe_csv_float(row.get("reading_ts"))
                    if reading_ts is None:
                        continue
                    if latest_ts is None or reading_ts > latest_ts:
                        latest_ts = reading_ts
                        latest_row = row
        except OSError as err:
            log.warning("JBD startup restore read failed for %s: %s", path, err)

    if latest_row is None or latest_ts is None:
        return

    restored = {
        "timestamp": latest_ts,
        "jbd_reading_ts": latest_ts,
        "soc_pct": _safe_csv_float(latest_row.get("soc_pct_derived")),
        "voltage_mv": _safe_csv_float(latest_row.get("voltage_mv")),
        "net_current_ma": _safe_csv_float(latest_row.get("net_current_ma")),
        "remaining_ah": _safe_csv_float(latest_row.get("remaining_ah")),
        "nominal_ah": _safe_csv_float(latest_row.get("nominal_ah")) or NOMINAL_AH,
        "temp_c": _safe_csv_float(latest_row.get("temp_c")),
        "ble_connected_jbd": False,
    }
    with STATE_LOCK:
        STATE.update(restored)
    log.info("JBD: restored last-good reading from logs at ts=%.3f", latest_ts)


def update_state_jbd(parsed: dict, raw: bytes):
    soc = parsed["soc_pct"]
    voltage_mv = parsed["voltage_mv"]
    hours_remaining = None

    # Use BMS-reported nominal capacity to derive Wh if available; fall back to config.
    nominal_ah = parsed.get("nominal_ah") or NOMINAL_AH
    nominal_v = voltage_mv / 1000.0 if voltage_mv else 12.8
    capacity_wh = nominal_ah * nominal_v if nominal_ah > 0 else CAPACITY_WH

    net_w = (parsed["net_current_ma"] / 1000.0) * nominal_v
    if net_w < -0.5:
        hours_remaining = round((soc / 100.0) * capacity_wh / abs(net_w), 2)

    now = time.time()
    with STATE_LOCK:
        STATE.update({
            "timestamp": now,
            "jbd_reading_ts": now,
            "soc_pct": soc,
            "voltage_mv": parsed["voltage_mv"],
            "net_current_ma": parsed["net_current_ma"],
            "remaining_ah": parsed["remaining_ah"],
            "nominal_ah": parsed["nominal_ah"],
            "temp_c": parsed["temp_c"],
            "hours_remaining": hours_remaining,
            "ble_connected_jbd": True,
            "last_error_jbd": "",
        })

    try:
        append_jbd_debug_row(now, raw, parsed)
    except OSError as err:
        log.warning("JBD debug CSV write failed: %s", err)

    if HISTORY_DB is not None:
        with STATE_LOCK:
            _load_w = STATE.get("load_w")
            _solar_w = STATE.get("solar_input_w")
            _charge_w = max(0.0, (parsed["net_current_ma"] / 1000.0) * (parsed["voltage_mv"] / 1000.0))
        try:
            HISTORY_DB.record_battery_event(
                reading_ts=now,
                load_w=_load_w,
                charge_w=_charge_w,
                soc_pct=soc,
                solar_input_w=_solar_w,
                voltage_mv=parsed["voltage_mv"],
                temp_c=parsed["temp_c"],
            )
        except Exception as err:
            log.warning("history sqlite write failed: %s", err)


async def jbd_query_once(client: BleakClient, cmd: int, timeout: float = 8.0) -> bytes:
    """Send a JBD request and collect the full response packet."""
    buf = bytearray()
    done = asyncio.Event()

    def on_notify(handle, data):
        buf.extend(data)
        raw = bytes(buf)
        if len(raw) >= 4 and raw[0] == 0xDD and raw[2] == 0x00:
            expected = 4 + raw[3] + 3
            if len(raw) >= expected:
                done.set()

    await client.start_notify(JBD_CHAR_N, on_notify)
    try:
        await client.write_gatt_char(JBD_CHAR_W, jbd_request(cmd), response=False)
        await asyncio.wait_for(done.wait(), timeout=timeout)
    finally:
        try:
            await client.stop_notify(JBD_CHAR_N)
        except Exception:
            pass

    raw = bytes(buf)
    if len(raw) >= 4 and raw[0] == 0xDD and raw[2] == 0x00:
        return raw[:4 + raw[3] + 3]
    return raw


# Set when JBD needs the BLE adapter (connecting/reconnecting).
# victron_loop watches this and stops its scanner to yield the adapter.
_JBD_NEEDS_ADAPTER: asyncio.Event | None = None


def _jbd_adapter_event() -> asyncio.Event:
    global _JBD_NEEDS_ADAPTER
    if _JBD_NEEDS_ADAPTER is None:
        _JBD_NEEDS_ADAPTER = asyncio.Event()
    return _JBD_NEEDS_ADAPTER


async def jbd_loop():
    """Polling JBD BMS loop — connect, query once, disconnect, then sleep."""
    ev = _jbd_adapter_event()
    while True:
        try:
            log.info("JBD: scanning for %s ...", JBD_ADDR)
            ev.set()  # ask Victron scanner to pause
            await asyncio.sleep(2.0)  # give Victron time to exit its scanner context
            dev = await BleakScanner.find_device_by_address(JBD_ADDR, timeout=SCAN_TIMEOUT)
            if dev is None:
                log.warning("JBD: device not found, retrying in %ss", RECONNECT_DELAY)
                with STATE_LOCK:
                    STATE["ble_connected_jbd"] = False
                    STATE["last_error_jbd"] = "device not found"
                ev.clear()
                await asyncio.sleep(RECONNECT_DELAY)
                continue
            log.info("JBD: connecting...")
            async with BleakClient(dev, timeout=15.0) as client:
                ev.clear()  # connected — Victron may resume scanning
                log.info("JBD: connected")
                try:
                    raw = await jbd_query_once(client, 0x03)
                    parsed = parse_jbd_basic(raw)
                    if parsed:
                        update_state_jbd(parsed, raw)
                    else:
                        log.warning("JBD: bad response: %s", raw.hex())
                except Exception as err:
                    log.warning("JBD: query error: %s", err)
                    with STATE_LOCK:
                        STATE["last_error_jbd"] = str(err)

            log.info("JBD: disconnected")

        except Exception as exc:
            log.warning("JBD: error: %s — retrying in %ss", exc, RECONNECT_DELAY)
            with STATE_LOCK:
                STATE["ble_connected_jbd"] = False
                STATE["last_error_jbd"] = str(exc)

        ev.clear()
        await asyncio.sleep(JBD_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Victron SmartSolar (passive BLE advertisement scanning)
# ---------------------------------------------------------------------------

def update_state_victron(solar_w, load_w, battery_voltage_v, charge_state, yield_today_wh):
    now = time.time()
    with STATE_LOCK:
        STATE["timestamp"] = now
        STATE["victron_reading_ts"] = now
        STATE["solar_input_w"] = solar_w
        STATE["load_w"] = load_w
        STATE["yield_today_wh"] = yield_today_wh
        STATE["charge_state"] = str(charge_state) if charge_state is not None else None
        STATE["ble_connected_victron"] = True
        STATE["last_error_victron"] = ""


async def victron_loop():
    """Passive Victron advertisement scanner — no connection needed."""
    if SolarCharger is None:
        log.warning("victron-ble not available (%s); Victron data will be absent", VICTRON_IMPORT_ERROR)
        return
    if not VICTRON_KEY:
        log.warning("VICTRON_ENCRYPTION_KEY not set; Victron data will be absent")
        return

    addr = VICTRON_ADDR.upper()
    log.info("Victron: scanning advertisements from %s", addr)
    ev = _jbd_adapter_event()

    while True:
        # Yield adapter to JBD if it needs to connect
        while ev.is_set():
            await asyncio.sleep(0.5)

        last_seen = time.monotonic()
        try:
            queue: asyncio.Queue = asyncio.Queue()

            def cb(device, adv):
                if device.address.upper() != addr:
                    return
                queue.put_nowait(adv.manufacturer_data)

            async with BleakScanner(cb):
                while True:
                    # Poll the queue with a short timeout so we can check for JBD need
                    if ev.is_set():
                        log.info("Victron: pausing scanner for JBD connection")
                        break  # exit scanner context — JBD will connect, then we restart

                    try:
                        mfr_data = await asyncio.wait_for(queue.get(), timeout=2.0)
                    except asyncio.TimeoutError:
                        elapsed = time.monotonic() - last_seen
                        if elapsed >= VICTRON_ADV_TIMEOUT:
                            log.warning("Victron: no advertisement for %ss", elapsed)
                            with STATE_LOCK:
                                STATE["ble_connected_victron"] = False
                                STATE["last_error_victron"] = "advertisement timeout"
                        continue

                    last_seen = time.monotonic()
                    for mfr_id, data in mfr_data.items():
                        try:
                            result = SolarCharger(VICTRON_KEY).parse(data)
                            solar_w = result.get_solar_power()
                            load_a = result.get_external_device_load()
                            batt_v = result.get_battery_voltage()
                            load_w = round(load_a * batt_v, 1) if (load_a is not None and batt_v is not None) else None
                            charge_state = result.get_charge_state()
                            yield_today = result.get_yield_today()
                            update_state_victron(
                                solar_w=solar_w,
                                load_w=load_w,
                                battery_voltage_v=batt_v,
                                charge_state=charge_state,
                                yield_today_wh=yield_today,
                            )
                        except Exception as err:
                            log.warning("Victron: parse error: %s", err)
                            with STATE_LOCK:
                                STATE["last_error_victron"] = str(err)

        except Exception as exc:
            log.warning("Victron: scanner error: %s — retrying in %ss", exc, RECONNECT_DELAY)
            with STATE_LOCK:
                STATE["ble_connected_victron"] = False
                STATE["last_error_victron"] = str(exc)
            await asyncio.sleep(RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# BLE thread (runs both loops concurrently)
# ---------------------------------------------------------------------------

def ble_thread():
    while True:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(asyncio.gather(jbd_loop(), victron_loop()))
        except Exception as exc:
            log.error("BLE event loop crashed: %s — restarting in %ss", exc, RECONNECT_DELAY)
            with STATE_LOCK:
                STATE["ble_connected_jbd"] = False
                STATE["ble_connected_victron"] = False
            time.sleep(RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# CSV logger
# ---------------------------------------------------------------------------

def csv_logger_loop():
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    last_logged_ts = None
    while True:
        time.sleep(CSV_INTERVAL)
        with STATE_LOCK:
            if STATE["timestamp"] is None:
                continue
            if not (STATE.get("ble_connected_jbd") or STATE.get("ble_connected_victron")):
                continue
            if last_logged_ts is not None and STATE["timestamp"] <= last_logged_ts:
                continue
            reading_ts = float(STATE["timestamp"])
            row = {k: STATE.get(k) for k in CSV_FIELDS}
        ts = datetime.fromtimestamp(reading_ts, tz=timezone.utc)
        row["timestamp"] = ts.isoformat()
        csv_path = CSV_DIR / f"battery-{ts.strftime('%Y-%m-%d')}.csv"
        write_header = not csv_path.exists() or csv_path.stat().st_size == 0
        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        last_logged_ts = reading_ts
        log.debug("CSV: wrote row to %s", csv_path)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send_json(self, code, body_bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, b'{"ok":true}')
            return

        if self.path in ("/sensor/power", "/telemetry/power", "/solix/power"):
            with STATE_LOCK:
                s = dict(STATE)
            now = time.time()
            reading_ts = s.get("timestamp")
            jbd_reading_ts = s.get("jbd_reading_ts")
            victron_reading_ts = s.get("victron_reading_ts")
            ble_connected = bool(s.get("ble_connected_victron"))
            load_w = s.get("load_w")
            solar_w = s.get("solar_input_w")

            body = json.dumps({
                # /sensor/power contract (matches solix_monitor.py shape)
                "value": load_w,
                "ble_connected": ble_connected,
                "last_error": s.get("last_error") or s.get("last_error_jbd") or s.get("last_error_victron") or "",
                # battery_* fields (canonical)
                "battery_soc_pct": s.get("soc_pct"),
                "battery_solar_input_w": solar_w,
                "battery_total_input_w": solar_w,
                "battery_voltage_mv": s.get("voltage_mv"),
                "battery_temp_c": s.get("temp_c"),
                "battery_reading_ts": jbd_reading_ts,
                "battery_charging_status": s.get("charge_state"),
                "battery_capacity_wh": round((s.get("nominal_ah") or NOMINAL_AH) * ((s.get("voltage_mv") or 12800) / 1000.0), 1),
                "battery_remaining_ah": s.get("remaining_ah"),
                "battery_nominal_ah": s.get("nominal_ah"),
                "battery_net_current_ma": s.get("net_current_ma"),
                "battery_yield_today_wh": s.get("yield_today_wh"),
                # solix_* aliases for power_telemetry.py compat (remove after Step 5)
                "solix_soc_pct": s.get("soc_pct"),
                "solix_solar_input_w": solar_w,
                "solix_total_input_w": solar_w,
                "solix_voltage_mv": s.get("voltage_mv"),
                "solix_temp_c": s.get("temp_c"),
                "solix_reading_ts": jbd_reading_ts,
                "solix_charging_status": s.get("charge_state"),
                # status
                "source": "lfp-ble",
                "ble_connected_jbd": s.get("ble_connected_jbd"),
                "ble_connected_victron": s.get("ble_connected_victron"),
                "victron_reading_ts": victron_reading_ts,
                "last_error_jbd": s.get("last_error_jbd"),
                "last_error_victron": s.get("last_error_victron"),
                "hours_remaining": s.get("hours_remaining"),
            }, separators=(",", ":")).encode()
            self._send_json(200, body)
            return

        self._send_json(404, b'{"error":"not_found"}')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info(
        "lfp-monitor starting: jbd=%s victron=%s http=%s:%s csv=%s capacity=%.0fWh",
        JBD_ADDR, VICTRON_ADDR, HOST, PORT, CSV_DIR, CAPACITY_WH,
    )
    if not VICTRON_KEY:
        log.warning("VICTRON_ENCRYPTION_KEY not set — solar/load data will be absent")
    threading.Thread(target=ble_thread, daemon=True, name="ble").start()
    threading.Thread(target=csv_logger_loop, daemon=True, name="csv").start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("HTTP listening on %s:%s", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
