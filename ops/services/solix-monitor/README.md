# solix-monitor

Repo-native Anker Solix C300X DC BLE monitor service.

## Source Of Truth And Deployment

- Source file to edit: `ops/services/solix-monitor/solix_monitor.py` (repo).
- Runtime file used by service: `/opt/bartleby/solix-monitor/solix_monitor.py`.
- Do not edit `/opt/bartleby/solix-monitor/solix_monitor.py` directly.
- Deploy repo changes to runtime via the idempotent bootstrap script:

```bash
cd /path/to/bartlebyGPT
sudo ./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile api-jetson \
  --force-solix-monitor \
  --skip-cloudflared \
  --skip-inference-bootstrap \
  --skip-doctor
```

Provides:

- `/health`
- `/solix/power` (full payload)
- `/telemetry/power` (alias)
- `/sensor/power` (ESPHome-compatible shim for `power_telemetry.py`)

`/sensor/power` fields:

| Field | Description |
|-------|-------------|
| `value` | `total_output_w` — USB-C3 load watts (tag `0xad`) |
| `solix_soc_pct` | State of charge % (tag `0xb7`) |
| `solix_solar_input_w` | Solar input watts (tag `0xab`) — 0 at 100% SOC |
| `solix_total_input_w` | Total input watts (tag `0xac`) |
| `solix_voltage_mv` | Battery voltage mV (tag `0xaf`) |
| `solix_temp_c` | Temperature °C (tag `0xb5`) |
| `solix_charging_status` | Raw uint8 from tag `0xb6` — transitions logged for reverse engineering |
| `solix_reading_ts` | Unix timestamp of the BLE packet |

At 100% SOC the Solix charge controller stops accepting charge and reports `solix_solar_input_w = 0`,
even though solar is still powering the load via pass-through. `power_telemetry.py` corrects for this
by computing `solix_effective_solar_w = estimated_total_watts` when `solix_soc_pct >= 100` and
`solix_solar_input_w == 0`.

Installed by:

- `ops/bootstrap/bootstrap_fresh_box.sh`

Runtime defaults (overridable via environment):

- BLE addr: `F4:9D:8A:83:D3:24`
- HTTP: `127.0.0.1:18082`
- Capacity: `288Wh`
