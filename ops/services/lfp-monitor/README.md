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

## Notes

- Victron data arrives via passive BLE advertisement scanning — no connection required.
- JBD BMS is polled with a one-shot GATT request (`0x03`), then disconnected to avoid holding the BMS open and blocking the phone app.
- JBD polling interval is `BATTERY_JBD_POLL_INTERVAL`; the Jetson LFP profile currently sets it to `60` seconds.
- Feed-health semantics matter:
  - `ble_connected` on `/sensor/power` now means Victron/power-feed health, not "JBD is connected right this instant"
  - `battery_reading_ts` is JBD freshness only
  - `victron_reading_ts` is the live power-feed freshness clock
- `power_telemetry.py` must use Victron/power-feed freshness for stale detection and `power_reading_ts`.
  Using JBD `battery_reading_ts` for whole-feed staleness is wrong and causes the website to fall back to `jtop` between delayed JBD polls even while Victron watts are still live.
- The combined telemetry CSV is written as `battery-YYYY-MM-DD.csv`.
- Every successful JBD read also appends a raw packet log to `jbd-basic-YYYY-MM-DD.csv` with:
  - full raw packet hex
  - decoded core values
  - every payload byte in `b00...`
- The raw JBD CSV is the best local forensic record short of a live `btmon` capture.
- `SOLIX_LOG_DIR` in `power_telemetry.py` must point to the lfp-monitor logs dir when using this monitor, and `BATTERY_CSV_PREFIX` should be set to `battery-` so the history reader finds the right files.
