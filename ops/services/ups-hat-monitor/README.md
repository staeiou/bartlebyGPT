# ups-hat-monitor

Independent logger for the Waveshare UPS Power Module (C) / INA219 battery HAT.

This service is deliberately separate from `lfp-monitor`, `power_telemetry.py`, and the
web history database. It does not own `/sensor/power`, does not write the shared
`battery_events` table, and does not affect the public telemetry path.

## Hardware

- I2C bus: `7`
- I2C address: `0x41`
- Sensor: `INA219`
- Calibration/config match Waveshare's UPS Power Module (C) demo:
  - calibration register: `0x68f4`
  - config register: `0x0eef`

## Logs

Runtime defaults:

- Workdir: `/opt/bartleby/ups-hat-monitor`
- Logs: `/opt/bartleby/ups-hat-monitor/logs`
- CSV: `/opt/bartleby/ups-hat-monitor/logs/ups-hat-YYYY-MM-DD.csv`
- SQLite: `/opt/bartleby/ups-hat-monitor/logs/ups_hat.sqlite3`

CSV and SQLite fields:

- `timestamp`
- `reading_ts`
- `i2c_bus`
- `i2c_addr`
- `bus_voltage_v`
- `shunt_voltage_mv`
- `supply_voltage_v`
- `current_a`
- `power_w`
- `soc_pct_est`
- `direction`
- `raw_bus_voltage`
- `raw_shunt_voltage`
- `raw_current`
- `raw_power`

`soc_pct_est` is Waveshare's simple voltage estimate:

```text
clamp((bus_voltage_v - 9.0) / 3.6 * 100, 0, 100)
```

It is useful for trend logging, not an authoritative coulomb-counted SOC.

`direction` is based on the signed current register:

- `charging`: `current_a > 0.05`
- `discharging`: `current_a < -0.05`
- `idle`: otherwise

## Deployment

Edit source in the repo:

```bash
ops/services/ups-hat-monitor/ups_hat_monitor.py
```

Deploy only this logger:

```bash
cd /home/ubuntu/vllm_jetson/bartlebyGPT
sudo PROFILE_FILE=ops/config/profiles/jetson-solar-lfp.env \
  ./ops/bootstrap/bootstrap_ups_hat_monitor.sh
```

Check status:

```bash
sudo systemctl status ups-hat-monitor --no-pager -n 80
tail -20 /opt/bartleby/ups-hat-monitor/logs/ups-hat-$(date -u +%F).csv
sqlite3 /opt/bartleby/ups-hat-monitor/logs/ups_hat.sqlite3 \
  'select timestamp,bus_voltage_v,current_a,power_w,soc_pct_est,direction from ups_hat_samples order by reading_ts desc limit 5;'
```

Take one live reading without writing logs:

```bash
sudo /usr/bin/python3 /opt/bartleby/ups-hat-monitor/ups_hat_monitor.py --once --no-write
```

## Environment

| Var | Default |
|-----|---------|
| `UPS_HAT_I2C_BUS` | `7` |
| `UPS_HAT_I2C_ADDR` | `0x41` |
| `UPS_HAT_LOG_INTERVAL` | `10` |
| `UPS_HAT_LOG_DIR` | `/opt/bartleby/ups-hat-monitor/logs` |
| `UPS_HAT_SQLITE_PATH` | `$UPS_HAT_LOG_DIR/ups_hat.sqlite3` |
