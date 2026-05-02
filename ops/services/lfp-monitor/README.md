# lfp-monitor

BLE monitor for the LFP12100EK battery + Victron SmartSolar MPPT 100/20.

## Source Of Truth And Deployment

- Source file to edit: `ops/services/lfp-monitor/lfp_monitor.py` (repo).
- Runtime file used by service: `/opt/bartleby/lfp-monitor/lfp_monitor.py`.
- Do not edit the runtime file directly.
- Deploy repo changes via the idempotent bootstrap script:

```bash
cd /path/to/bartlebyGPT
sudo ./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile jetson-solar-lfp \
  --secrets-file /root/bartleby-secrets.env \
  --force-solix-monitor \
  --skip-cloudflared \
  --skip-inference-bootstrap \
  --skip-doctor
```

## Hardware

| Device | BLE Address | Role |
|--------|------------|------|
| LFP12100EK (JBD BMS) | `A5:C2:39:1A:5D:29` | Battery SOC, voltage, temperature, net current |
| Victron SmartSolar MPPT 100/20 48V | `CD:4C:1F:A1:BF:EF` | Solar input watts, load output watts |

## Topology

Jetson is powered from the **Victron load output** (not battery terminals directly).

```
Solar panel → Victron MPPT → [load output → Jetson]
                           → [battery terminals → LFP12100EK]
```

- `load_w` = `victron.external_device_load` (direct measurement)
- `solar_w` = `victron.solar_power`
- `soc_pct` = `jbd.remaining_ah / jbd.nominal_ah × 100` (BMS SOC% is unreliable until several cycles)
- JBD is queried only for battery-side fields: SOC, voltage, temperature, net current, remaining Ah.
- Victron remains the source for live solar/load telemetry.

## Provides

- `/health`
- `/sensor/power` (ESPHome-compatible shim for `power_telemetry.py`)
- `/telemetry/power` (alias)
- `/solix/power` (alias)

### `/sensor/power` fields

| Field | Source | Description |
|-------|--------|-------------|
| `value` | Victron | Load output watts (Jetson draw) |
| `battery_soc_pct` | JBD (Ah-derived) | State of charge % |
| `battery_solar_input_w` | Victron | Solar panel input watts |
| `battery_voltage_mv` | JBD | Battery voltage mV |
| `battery_temp_c` | JBD | Temperature °C (first sensor) |
| `battery_reading_ts` | JBD | Unix timestamp of last successful JBD reading |
| `victron_reading_ts` | Victron | Unix timestamp of last successful Victron advertisement parse |
| `battery_capacity_wh` | Config | Nominal capacity (1200Wh) |
| `battery_remaining_ah` | JBD | Remaining capacity Ah |
| `battery_net_current_ma` | JBD | Net current mA (+ = charging, - = discharging) |
| `battery_yield_today_wh` | Victron | Solar yield today Wh |
| `victron_model_name` | Victron | Parsed device model name |
| `victron_charge_state` | Victron | Parsed charge-state enum |
| `victron_charger_error` | Victron | Parsed charger-error enum |
| `victron_battery_voltage_v` | Victron | Parsed battery voltage V |
| `victron_battery_charging_current_a` | Victron | Parsed charging current A |
| `victron_external_device_load_a` | Victron | Parsed external load current A |
| `victron_manufacturer_id` | Victron | BLE manufacturer-data key for the parsed payload |
| `solix_*` | — | Compat aliases for `power_telemetry.py` — remove after Step 5 cleanup |

## Required secrets

`VICTRON_ENCRYPTION_KEY` must be set in `/root/bartleby-secrets.env`:

```bash
VICTRON_ENCRYPTION_KEY=<hex key from VictronConnect → Product info → Encryption key>
```

## Runtime defaults (overridable via environment)

| Var | Default |
|-----|---------|
| `BLE_ADDR` | `A5:C2:39:1A:5D:29` |
| `VICTRON_BLE_ADDR` | `CD:4C:1F:A1:BF:EF` |
| `VICTRON_ENCRYPTION_KEY` | *(required)* |
| `SOLIX_HOST` | `127.0.0.1` |
| `SOLIX_PORT` | `18082` |
| `SOLIX_CAPACITY_WH` | `1200` |
| `LFP_NOMINAL_AH` | `100` |
| `SOLIX_CSV_DIR` | `./logs` |
| `SOLIX_CSV_INTERVAL` | `60` |
| `SOLIX_RECONNECT_DELAY` | `10` |
| `BATTERY_JBD_POLL_INTERVAL` | `60` |

## BLE Architecture

`lfp_monitor.py` runs a **single shared `BleakScanner`** that dispatches advertisements
to per-device asyncio queues via a detection callback:

- Victron advertisements → `victron_queue` → processed inline (no connection)
- JBD advertisements → `jbd_queue` → connect + query + disconnect under `asyncio.Lock`

**Why one scanner, not two:** running two concurrent `BleakScanner` instances on the
same adapter causes BlueZ conflicts and missed packets. This is the pattern documented
in bleak's own `two_devices.py` example.

**Why stop the scanner during JBD GATT sessions:** BlueZ does not reliably deliver
GATT notifications when a scanner is running concurrently on the same adapter.
`lfp_monitor.py` calls `scanner.stop()` before connecting to JBD and `scanner.start()`
(in a `finally` block) after disconnecting. The scanner gap is ~3-5s per 60s cycle,
well under `VICTRON_ADV_TIMEOUT=30s`, so no false timeout warnings occur.

**Single event loop:** bleak requires a single asyncio event loop for the process
lifetime. `ble_thread()` creates one loop and reuses it across `ble_main()` restarts.

**Why `create_task` instead of bare `gather`:** In Python 3.10, `asyncio.gather` does
NOT cancel sibling tasks when one raises — they continue as orphaned coroutines on the
event loop. If `jbd_loop` raises `JbdAdapterStuckError` and `ble_main` restarts, the
old `victron_loop` would keep running alongside the new one, both writing to shared
STATE. The fix: `create_task` gives explicit references; `ble_main`'s `finally` block
cancels and awaits both tasks on every exit path, guaranteed.

## BLE Recovery

If JBD disappears or GATT notifications stop coming (service reports TimeoutError
repeatedly), the BMS likely needs a Bluetooth stack reset:

```bash
sudo systemctl stop lfp-monitor
sudo systemctl restart bluetooth
sleep 5
sudo systemctl start lfp-monitor
```

This is a BMS hardware/BlueZ state issue, not a code bug. The JBD BMS can enter a
stuck state after rapid service restarts or after being held in a connection too long.

## Notes

- Victron data arrives via passive BLE advertisement scanning — no connection required.
- JBD BMS is polled with a one-shot GATT request (`0x03`), then disconnected to avoid
  holding the BMS open and blocking the phone app.
- JBD polling interval is `BATTERY_JBD_POLL_INTERVAL`; the Jetson LFP profile sets it
  to `60` seconds.
- Feed-health semantics:
  - `ble_connected` on `/sensor/power` reflects Victron/power-feed health, not "JBD
    is connected right this instant"
  - `ble_connected_jbd=true` after a successful poll means "last read succeeded" — the
    BMS is not currently connected (one-shot model). Use `jbd_reading_ts` age for staleness.
  - `battery_reading_ts` is JBD freshness only
  - `victron_reading_ts` is the live power-feed freshness clock
- `power_telemetry.py` uses `victron_reading_ts` for whole-feed stale detection and
  `power_reading_ts`. **Never use `battery_reading_ts` for whole-feed staleness** — JBD
  may be 60s stale while Victron watts are perfectly live, causing false jtop fallback.
- JBD "device not seen in scan" retries at `JBD_POLL_INTERVAL` (60s), not
  `RECONNECT_DELAY` (10s). This prevents flaky JBD from starving Victron scan time.
- The combined telemetry CSV is written as `battery-YYYY-MM-DD.csv`.
- Every successful Victron advertisement parse appends a raw packet log to `victron-adv-YYYY-MM-DD.csv`:
  - raw manufacturer payload hex plus every parsed field exposed by `victron_ble`
  - `model_name`, `charge_state`, `charger_error`, `battery_voltage_v`,
    `battery_charging_current_a`, `yield_today_wh`, `solar_power_w`,
    `external_device_load_a`, and derived `load_w`
- Every successful JBD read appends a raw packet log to `jbd-basic-YYYY-MM-DD.csv`:
  - full raw packet hex, decoded core values, every payload byte in `b00...` columns
  - this is the best local forensic record short of a live `btmon` capture
- `SOLIX_LOG_DIR` in `power_telemetry.py` must point to the lfp-monitor logs dir, and
  `BATTERY_CSV_PREFIX` should be `battery` so the history reader finds the right files.
