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
| `battery_reading_ts` | System | Unix timestamp of last JBD reading |
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

## Notes

- Victron data arrives via passive BLE advertisement scanning — no connection required.
- JBD BMS is polled every 5s via GATT request (command 0x03).
- CSV files are written as `battery-YYYY-MM-DD.csv` (not `solix-*.csv`).
- `SOLIX_LOG_DIR` in `power_telemetry.py` must point to the lfp-monitor logs dir when using this monitor, and `BATTERY_CSV_PREFIX` should be set to `battery-` so the history reader finds the right files.
