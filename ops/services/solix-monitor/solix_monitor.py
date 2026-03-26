#!/usr/bin/env python3
"""Anker Solix C300X DC BLE monitor — CSV logger + HTTP telemetry service.

Auto-detects firmware protocol on first connection:
  - Old firmware: plaintext TLV pushed passively every ~2s
  - New firmware: ECDH+AES negotiation required (SolixBLE C300DC)

Detected protocol is persisted to SOLIX_CSV_DIR/firmware_type.txt so
subsequent starts skip the probe entirely.
"""

import asyncio
import csv
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bleak import BleakClient, BleakScanner
try:
    from SolixBLE.devices.c300dc import C300DC
    SOLIXBLE_IMPORT_ERROR = ""
except Exception as err:
    C300DC = None
    SOLIXBLE_IMPORT_ERROR = str(err)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("solix-monitor")

BLE_ADDR      = os.environ.get("SOLIX_BLE_ADDR", "F4:9D:8A:83:D3:24")
HOST          = os.environ.get("SOLIX_HOST", "127.0.0.1")
PORT          = int(os.environ.get("SOLIX_PORT", "18082"))
CSV_DIR       = Path(os.environ.get("SOLIX_CSV_DIR", str(Path(__file__).parent / "logs")))
CSV_INTERVAL  = float(os.environ.get("SOLIX_CSV_INTERVAL", "60"))
CAPACITY_WH   = float(os.environ.get("SOLIX_CAPACITY_WH", "288"))
RECONNECT_DELAY = float(os.environ.get("SOLIX_RECONNECT_DELAY", "10"))

NOTIFY_CHAR   = "8c850003-0302-41c5-b46e-cf057c562025"
PROBE_TIMEOUT = 8.0   # seconds to wait for plaintext TLV before assuming ECDH
FIRMWARE_FILE = CSV_DIR / "firmware_type.txt"

CSV_FIELDS = [
    "timestamp", "soc_pct", "temp_c", "temp_f", "voltage_mv",
    "solar_input_w", "total_input_w", "usbc1_w", "usbc2_w", "usbc3_w",
    "total_output_w", "charge_limit_pct", "hours_remaining", "ble_connected",
]

STATE_LOCK = threading.Lock()
STATE = {
    "timestamp": None, "soc_pct": None, "temp_c": None, "temp_f": None,
    "voltage_mv": None, "solar_input_w": None, "total_input_w": None,
    "usbc1_w": None, "usbc2_w": None, "usbc3_w": None,
    "total_output_w": None, "charge_limit_pct": None,
    "hours_remaining": None, "ble_connected": False,
    "source": "solix-ble", "last_error": "", "firmware": None,
    "charging_status": None,
}

_SOLIX_DEFAULT = -1


def _v(val):
    return None if val == _SOLIX_DEFAULT else val


# ---------------------------------------------------------------------------
# Shared state updater — both protocol paths call this
# ---------------------------------------------------------------------------

def update_state(*, soc, temp_c, voltage_mv, solar_w, total_in,
                 c1_w, c2_w, c3_w, total_out, charge_limit, charging_status=None):
    temp_f = round(temp_c * 9 / 5 + 32, 1) if temp_c is not None else None
    hours_remaining = None
    if soc is not None and total_out is not None and total_out > 0:
        hours_remaining = round((soc / 100.0) * CAPACITY_WH / total_out, 2)
    with STATE_LOCK:
        prev_b6 = STATE.get("charging_status")
        STATE.update({
            "timestamp": time.time(),
            "soc_pct": soc, "temp_c": temp_c, "temp_f": temp_f,
            "voltage_mv": voltage_mv,
            "solar_input_w": solar_w, "total_input_w": total_in,
            "usbc1_w": c1_w, "usbc2_w": c2_w, "usbc3_w": c3_w,
            "total_output_w": total_out, "charge_limit_pct": charge_limit,
            "hours_remaining": hours_remaining,
            "ble_connected": True, "last_error": "",
            "charging_status": charging_status,
        })
    if charging_status != prev_b6:
        log.info("0xb6 charging_status changed: %s -> %s (soc=%s solar_w=%s total_out=%s)",
                 prev_b6, charging_status, soc, solar_w, total_out)


# ---------------------------------------------------------------------------
# TLV protocol (old plaintext firmware)
# ---------------------------------------------------------------------------

def parse_tlv(data: bytes) -> dict:
    entries = {}
    i = 9
    while i < len(data) - 1:
        tag = data[i]
        if i + 1 >= len(data):
            break
        length = data[i + 1]
        if i + 2 + length > len(data):
            break
        payload = data[i + 2: i + 2 + length]
        i += 2 + length
        if not payload:
            continue
        subtype = payload[0]
        vb = payload[1:]
        if subtype == 0x01 and len(vb) == 1:
            entries[tag] = vb[0]
        elif subtype == 0x02 and len(vb) == 2:
            entries[tag] = int.from_bytes(vb, "little")
        elif subtype == 0x03 and len(vb) == 4:
            entries[tag] = int.from_bytes(vb, "little")
    return entries


def on_tlv_notify(sender, data: bytes):
    log.info("RAW %s", data.hex())
    e = parse_tlv(data)
    update_state(
        soc=e.get(0xB7), temp_c=e.get(0xB5), voltage_mv=e.get(0xAF),
        solar_w=e.get(0xAB), total_in=e.get(0xAC),
        c1_w=e.get(0xA4), c2_w=e.get(0xA5), c3_w=e.get(0xA6),
        total_out=e.get(0xAD), charge_limit=e.get(0xB8),
        charging_status=e.get(0xB6),
    )


async def run_tlv(dev):
    log.info("Protocol: plaintext TLV")
    async with BleakClient(dev, timeout=15.0) as client:
        with STATE_LOCK:
            STATE["ble_connected"] = True
            STATE["last_error"] = ""
        await client.start_notify(NOTIFY_CHAR, on_tlv_notify)
        while client.is_connected:
            await asyncio.sleep(1.0)
    log.warning("TLV connection dropped.")


# ---------------------------------------------------------------------------
# ECDH protocol (new encrypted firmware)
# ---------------------------------------------------------------------------

def make_ecdh_callback(solix):
    def on_update():
        if not solix.available:
            return
        update_state(
            soc=_v(solix.battery_percentage), temp_c=_v(solix.temperature),
            voltage_mv=None,
            solar_w=_v(solix.solar_power_in), total_in=_v(solix.power_in),
            c1_w=_v(solix.usb_c1_power), c2_w=_v(solix.usb_c2_power),
            c3_w=_v(solix.usb_c3_power), total_out=_v(solix.power_out),
            charge_limit=_v(solix.battery_health),
        )
    return on_update


async def run_ecdh(dev):
    if C300DC is None:
        log.warning("ECDH path unavailable: SolixBLE import failed (%s)", SOLIXBLE_IMPORT_ERROR)
        return
    log.info("Protocol: ECDH encrypted")
    solix = C300DC(dev)
    solix.add_callback(make_ecdh_callback(solix))
    if not await solix.connect():
        log.warning("ECDH connect/negotiate failed")
        await solix.disconnect()
        return
    log.info("Connected and negotiated.")
    with STATE_LOCK:
        STATE["ble_connected"] = True
        STATE["last_error"] = ""
    await solix._disconnect_event.wait()
    log.warning("ECDH connection lost.")
    with STATE_LOCK:
        STATE["ble_connected"] = False
    await solix.disconnect()


# ---------------------------------------------------------------------------
# Firmware detection (probe + disk cache)
# ---------------------------------------------------------------------------

def load_firmware_type():
    try:
        val = FIRMWARE_FILE.read_text().strip()
        if val in ("tlv", "ecdh"):
            log.info("Firmware type from cache: %s", val)
            return val
    except FileNotFoundError:
        pass
    return None


def save_firmware_type(fwtype):
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    FIRMWARE_FILE.write_text(fwtype + "\n")
    log.info("Firmware type detected and cached: %s", fwtype)


async def probe_firmware(dev) -> str:
    """Connect raw, wait PROBE_TIMEOUT seconds for TLV packets. Returns 'tlv' or 'ecdh'."""
    log.info("Probing firmware type (waiting up to %ss for plaintext TLV)...", PROBE_TIMEOUT)
    got_packet = asyncio.Event()

    def _probe_cb(sender, data):
        if not got_packet.is_set():
            got_packet.set()

    try:
        async with BleakClient(dev, timeout=15.0) as client:
            await client.start_notify(NOTIFY_CHAR, _probe_cb)
            try:
                await asyncio.wait_for(got_packet.wait(), timeout=PROBE_TIMEOUT)
                return "tlv"
            except asyncio.TimeoutError:
                if C300DC is None:
                    log.warning(
                        "ECDH probe timed out but SolixBLE is unavailable (%s); falling back to TLV",
                        SOLIXBLE_IMPORT_ERROR,
                    )
                    return "tlv"
                return "ecdh"
    except Exception as exc:
        log.warning("Probe connection failed (%s), assuming ecdh", exc)
        return "ecdh"


# ---------------------------------------------------------------------------
# Main BLE loop
# ---------------------------------------------------------------------------

async def ble_loop():
    firmware_type = load_firmware_type()
    with STATE_LOCK:
        STATE["firmware"] = firmware_type

    while True:
        try:
            log.info("Scanning for Solix %s ...", BLE_ADDR)
            devices = await BleakScanner.discover(timeout=30.0)
            dev = next((d for d in devices if d.address.upper() == BLE_ADDR.upper()), None)
            if dev is None:
                log.warning("Device %s not found in scan, retrying in %ss", BLE_ADDR, RECONNECT_DELAY)
                with STATE_LOCK:
                    STATE["ble_connected"] = False
                    STATE["last_error"] = "device not found in scan"
                await asyncio.sleep(RECONNECT_DELAY)
                continue

            if firmware_type is None:
                firmware_type = await probe_firmware(dev)
                save_firmware_type(firmware_type)
                with STATE_LOCK:
                    STATE["firmware"] = firmware_type

            if firmware_type == "tlv":
                await run_tlv(dev)
            else:
                await run_ecdh(dev)

        except Exception as exc:
            log.warning("BLE error: %s — retrying in %ss", exc, RECONNECT_DELAY)
            with STATE_LOCK:
                STATE["ble_connected"] = False
                STATE["last_error"] = str(exc)

        with STATE_LOCK:
            STATE["ble_connected"] = False
        await asyncio.sleep(RECONNECT_DELAY)


def ble_thread():
    while True:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(ble_loop())
        except Exception as exc:
            log.error("BLE event loop crashed: %s — restarting in %ss", exc, RECONNECT_DELAY)
            with STATE_LOCK:
                STATE["ble_connected"] = False
                STATE["last_error"] = str(exc)
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
            if not STATE.get("ble_connected", False):
                continue
            if last_logged_ts is not None and STATE["timestamp"] <= last_logged_ts:
                continue
            reading_ts = float(STATE["timestamp"])
            row = {k: STATE.get(k) for k in CSV_FIELDS}
        ts = datetime.fromtimestamp(reading_ts, tz=timezone.utc)
        row["timestamp"] = ts.isoformat()
        csv_path = CSV_DIR / f"solix-{ts.strftime('%Y-%m-%d')}.csv"
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

    def do_GET(self):
        if self.path == "/health":
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/sensor/power":
            with STATE_LOCK:
                body = json.dumps({
                    "value": STATE.get("total_output_w"),
                    "solix_reading_ts": STATE.get("timestamp"),
                    "solix_soc_pct": STATE.get("soc_pct"),
                    "solix_solar_input_w": STATE.get("solar_input_w"),
                    "solix_total_input_w": STATE.get("total_input_w"),
                    "solix_voltage_mv": STATE.get("voltage_mv"),
                    "solix_temp_c": STATE.get("temp_c"),
                    "solix_charging_status": STATE.get("charging_status"),
                }, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path in ("/solix/power", "/telemetry/power"):
            with STATE_LOCK:
                payload = dict(STATE)
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = b'{"error":"not_found"}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info(
        "solix-monitor starting: ble=%s http=%s:%s csv=%s interval=%ss capacity=%sWh",
        BLE_ADDR, HOST, PORT, CSV_DIR, CSV_INTERVAL, CAPACITY_WH,
    )
    threading.Thread(target=ble_thread, daemon=True, name="ble").start()
    threading.Thread(target=csv_logger_loop, daemon=True, name="csv").start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("HTTP listening on %s:%s", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
