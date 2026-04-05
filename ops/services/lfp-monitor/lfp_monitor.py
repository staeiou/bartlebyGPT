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
import subprocess
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


class JbdAdapterStuckError(Exception):
    """Raised when JBD has failed enough consecutive times to warrant a BT stack reset."""


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
VICTRON_ADV_TIMEOUT    = float(os.environ.get("VICTRON_ADV_TIMEOUT", "30"))
JBD_POLL_INTERVAL      = max(5.0, float(os.environ.get("BATTERY_JBD_POLL_INTERVAL", "60")))
# After this many consecutive JBD GATT failures (connect succeeds but no notification),
# trigger an automatic bluetooth stack reset so the service heals without human intervention.
JBD_BT_RESET_THRESHOLD = max(1, int(os.environ.get("JBD_BT_RESET_THRESHOLD", "3")))

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
    # Scan most-recent files first (reverse date order) and stop as soon as
    # we find a valid row — avoids reading years of history on long-running deployments.
    for path in sorted(glob.glob(pattern), reverse=True):
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader((line.replace("\x00", "") for line in handle)))
            for row in reversed(rows):  # newest rows are at the end of each file
                reading_ts = _safe_csv_float(row.get("reading_ts"))
                if reading_ts is None:
                    continue
                if latest_ts is None or reading_ts > latest_ts:
                    latest_ts = reading_ts
                    latest_row = row
            if latest_row is not None:
                break  # found a valid row in this file; no need to look further back
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
    request = jbd_request(cmd)

    def on_notify(handle, data):
        log.info("JBD: notify handle=%s len=%s data=%s", handle, len(data), data.hex())
        buf.extend(data)
        raw = bytes(buf)
        if len(raw) >= 4 and raw[0] == 0xDD:
            if raw[2] != 0x00:
                # BMS returned an error frame — signal done immediately so we
                # don't block for the full timeout; parse_jbd_basic will reject it.
                log.warning("JBD: error frame status=0x%02x data=%s", raw[2], raw.hex())
                done.set()
            elif len(raw) >= 4 + raw[3] + 3:
                done.set()

    log.info("JBD: start_notify char=%s", JBD_CHAR_N)
    await client.start_notify(JBD_CHAR_N, on_notify)
    try:
        log.info("JBD: write_gatt_char char=%s req=%s response=%s", JBD_CHAR_W, request.hex(), False)
        await client.write_gatt_char(JBD_CHAR_W, request, response=False)
        log.info("JBD: wait_for response timeout=%ss", timeout)
        await asyncio.wait_for(done.wait(), timeout=timeout)
        log.info("JBD: response event completed")
    finally:
        try:
            log.info("JBD: stop_notify char=%s", JBD_CHAR_N)
            await client.stop_notify(JBD_CHAR_N)
        except Exception:
            pass

    raw = bytes(buf)
    log.info("JBD: raw assembled len=%s data=%s", len(raw), raw.hex())
    if len(raw) >= 4 and raw[0] == 0xDD and raw[2] == 0x00:
        return raw[:4 + raw[3] + 3]
    return raw


# ---------------------------------------------------------------------------
# Shared BLE scanner + per-device dispatch queues
#
# One persistent BleakScanner feeds both devices via a detection callback.
# JBD uses an asyncio.Lock (bleak's recommended pattern for scan+connect
# serialisation) rather than a custom event + sleep-based timing hack.
# Victron is advertisement-only (no connection) so it never needs the lock.
#
# BlueZ caveat: running a scanner concurrently with a GATT connection
# (connect + notify) can cause notifications to never arrive. The JBD loop
# therefore calls scanner.stop() before connecting and scanner.start() after
# disconnecting. This pauses Victron ads for the ~3-5s JBD session window
# (well under VICTRON_ADV_TIMEOUT=30s, so no false timeout warnings).
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


async def jbd_loop(connect_lock: asyncio.Lock, jbd_queue: asyncio.Queue, scanner: BleakScanner):
    """Polling JBD BMS loop — wait for advertisement, connect under lock, query once, disconnect, sleep.

    Tracks consecutive failures. After JBD_BT_RESET_THRESHOLD consecutive failures where
    the device connects but produces no notification (the BMS stuck state), raises
    JbdAdapterStuckError so ble_thread can perform an automatic BT stack reset.
    """
    jbd_addr = JBD_ADDR.upper()
    consecutive_failures = 0

    while True:
        # Drain stale entries left over from the previous poll cycle.
        while not jbd_queue.empty():
            jbd_queue.get_nowait()

        # Wait for the shared scanner to see the JBD device.
        log.info("JBD: waiting for advertisement from %s (timeout=%ss) ...", jbd_addr, SCAN_TIMEOUT)
        try:
            dev = await asyncio.wait_for(jbd_queue.get(), timeout=SCAN_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("JBD: device not seen in scan, retrying in %ss", JBD_POLL_INTERVAL)
            with STATE_LOCK:
                STATE["ble_connected_jbd"] = False
                STATE["last_error_jbd"] = "device not seen in scan"
            await asyncio.sleep(JBD_POLL_INTERVAL)
            continue

        # Serialise the connect phase with the lock (bleak two_devices.py pattern).
        # Stop the shared scanner before connecting: running a BleakScanner
        # concurrently with a GATT notify session prevents notifications from
        # arriving on BlueZ. Scanner restarts immediately after disconnect.
        poll_succeeded = False
        try:
            async with connect_lock:
                log.info("JBD: pausing scanner for GATT session")
                await scanner.stop()
                try:
                    log.info("JBD: connecting...")
                    async with BleakClient(dev, timeout=15.0) as client:
                        log.info("JBD: connected")
                        try:
                            raw = await jbd_query_once(client, 0x03)
                            parsed = parse_jbd_basic(raw)
                            if parsed:
                                update_state_jbd(parsed, raw)
                                poll_succeeded = True
                            else:
                                log.warning("JBD: bad response len=%s data=%s", len(raw), raw.hex())
                                with STATE_LOCK:
                                    STATE["last_error_jbd"] = (
                                        f"bad response (len={len(raw)} status=0x{raw[2]:02x})"
                                        if len(raw) >= 3
                                        else f"bad response (len={len(raw)})"
                                    )
                                    STATE["ble_connected_jbd"] = False
                        except Exception as err:
                            log.warning("JBD: query error type=%s repr=%r str=%s", type(err).__name__, err, err)
                            with STATE_LOCK:
                                STATE["last_error_jbd"] = str(err)
                                STATE["ble_connected_jbd"] = False
                finally:
                    log.info("JBD: resuming scanner")
                    await scanner.start()

            log.info("JBD: disconnected")

        except Exception as exc:
            log.warning("JBD: connection error: %s — retrying in %ss", exc, RECONNECT_DELAY)
            with STATE_LOCK:
                STATE["ble_connected_jbd"] = False
                STATE["last_error_jbd"] = str(exc)
            await asyncio.sleep(RECONNECT_DELAY)

        if poll_succeeded:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            log.warning(
                "JBD: consecutive failures: %d / %d before BT reset",
                consecutive_failures, JBD_BT_RESET_THRESHOLD,
            )
            if consecutive_failures >= JBD_BT_RESET_THRESHOLD:
                raise JbdAdapterStuckError(
                    f"JBD failed {consecutive_failures} consecutive times — triggering BT reset"
                )

        await asyncio.sleep(JBD_POLL_INTERVAL)


async def victron_loop(victron_queue: asyncio.Queue):
    """Consume Victron SmartSolar advertisements from the shared scanner queue."""
    if SolarCharger is None:
        log.warning("victron-ble not available (%s); Victron data will be absent", VICTRON_IMPORT_ERROR)
        return
    if not VICTRON_KEY:
        log.warning("VICTRON_ENCRYPTION_KEY not set; Victron data will be absent")
        return

    log.info("Victron: consuming advertisements from %s", VICTRON_ADDR.upper())
    last_seen = time.monotonic()

    while True:
        try:
            mfr_data = await asyncio.wait_for(victron_queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - last_seen
            if elapsed >= VICTRON_ADV_TIMEOUT:
                log.warning("Victron: no advertisement for %.0fs", elapsed)
                with STATE_LOCK:
                    STATE["ble_connected_victron"] = False
                    STATE["last_error_victron"] = "advertisement timeout"
            continue

        last_seen = time.monotonic()
        for _mfr_id, data in mfr_data.items():
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


async def ble_main():
    """Single persistent BleakScanner feeds per-device queues. One event loop, no restarts.

    The scanner is explicitly stopped/started around each JBD GATT session to avoid
    the BlueZ issue where concurrent scanning blocks GATT notifications.
    """
    jbd_addr = JBD_ADDR.upper()
    victron_addr = VICTRON_ADDR.upper()

    jbd_queue: asyncio.Queue = asyncio.Queue()
    victron_queue: asyncio.Queue = asyncio.Queue()
    connect_lock = asyncio.Lock()

    def on_detection(device, adv):
        addr = device.address.upper()
        if addr == jbd_addr:
            jbd_queue.put_nowait(device)
        elif addr == victron_addr:
            victron_queue.put_nowait(adv.manufacturer_data)

    scanner = BleakScanner(on_detection)
    log.info("BLE: starting shared scanner (JBD=%s Victron=%s)", jbd_addr, victron_addr)
    await scanner.start()

    # Use create_task so we hold references and can cancel them explicitly.
    # asyncio.gather in Python 3.10 does NOT cancel sibling tasks when one
    # raises — they continue as orphaned background tasks on the event loop.
    # With create_task + explicit cancel in finally, every ble_main exit
    # (normal, exception, or BT reset) is guaranteed to clean up both tasks.
    jbd_task = asyncio.create_task(jbd_loop(connect_lock, jbd_queue, scanner), name="jbd")
    vic_task = asyncio.create_task(victron_loop(victron_queue), name="victron")
    try:
        await asyncio.gather(jbd_task, vic_task)
    except BaseException as exc:
        log.error("BLE: task exited (%s: %s)", type(exc).__name__, exc)
        raise
    finally:
        for t in (jbd_task, vic_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(jbd_task, vic_task, return_exceptions=True)
        await scanner.stop()


# ---------------------------------------------------------------------------
# BLE thread
# ---------------------------------------------------------------------------

def _bt_stack_reset():
    """Reset the bluetooth stack to clear stuck BMS notification state.

    Runs synchronously — called from ble_thread between ble_main() restarts.
    Restarts bluetoothd (which resets the HCI adapter and clears all BlueZ
    device state), then waits for the adapter to re-initialize.
    """
    log.warning("BLE: resetting bluetooth stack to recover JBD stuck state ...")
    try:
        subprocess.run(
            ["systemctl", "restart", "bluetooth"],
            check=True, capture_output=True, text=True, timeout=20,
        )
        log.warning("BLE: bluetooth restarted — waiting for adapter to reinitialize")
        time.sleep(6)
        log.warning("BLE: bluetooth reset complete, restarting BLE loop")
    except (OSError, subprocess.SubprocessError) as err:
        log.error("BLE: bluetooth reset failed: %s — continuing anyway", err)


def ble_thread():
    # Single event loop for the lifetime of the process — bleak requires this.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        try:
            loop.run_until_complete(ble_main())
        except JbdAdapterStuckError as exc:
            log.error("BLE: %s", exc)
            with STATE_LOCK:
                STATE["ble_connected_jbd"] = False
                STATE["ble_connected_victron"] = False
            _bt_stack_reset()
        except Exception as exc:
            log.error("BLE: ble_main exited unexpectedly: %s — restarting in %ss", exc, RECONNECT_DELAY)
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
    restore_last_jbd_state_from_logs()
    if not VICTRON_KEY:
        log.warning("VICTRON_ENCRYPTION_KEY not set — solar/load data will be absent")
    threading.Thread(target=ble_thread, daemon=True, name="ble").start()
    threading.Thread(target=csv_logger_loop, daemon=True, name="csv").start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("HTTP listening on %s:%s", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
