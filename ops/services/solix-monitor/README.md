# solix-monitor

Repo-native Anker Solix C300X DC BLE monitor service.

Provides:

- `/health`
- `/solix/power` (full payload)
- `/telemetry/power` (alias)
- `/sensor/power` (ESPHome-compatible shim for `power_telemetry.py`)

Installed by:

- `ops/bootstrap/bootstrap_fresh_box.sh`

Runtime defaults (overridable via environment):

- BLE addr: `F4:9D:8A:83:D3:24`
- HTTP: `127.0.0.1:18082`
- Capacity: `288Wh`
