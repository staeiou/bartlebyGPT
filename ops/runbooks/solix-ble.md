# Solix BLE Power Monitor Runbook

## Overview

Each Pi deployment is powered by an Anker Solix C300X DC (288Wh LFP).
`solix-monitor.service` reads telemetry directly over BLE and exposes it via HTTP.
The same `solix_monitor.py` runs on both machines — it auto-detects firmware protocol on first connect and caches the result.

## Batteries

| Host | BLE MAC | Serial |
|------|---------|--------|
| `api.bartlebygpt.org` | `F4:9D:8A:83:D3:24` | `AZVZF80E51400468` |
| `pi.bartlebygpt.org` (pipi4b) | `F4:9D:8A:75:CA:D0` | — |

## Service Location

- Script: `~/solix-monitor/solix_monitor.py`
- Systemd unit: `solix-monitor.service`
- Logs/CSVs: `~/solix-monitor/logs/`
- Firmware cache: `~/solix-monitor/logs/firmware_type.txt`

## Firmware Protocol Detection

Anker Solix C300X DC units ship with one of two BLE firmware variants:

- **Plaintext TLV** (older firmware): device pushes 252-byte TLV packets every ~2s on subscribe, no auth required.
- **ECDH encrypted** (newer firmware): requires a 6-step ECDH key exchange + AES session before telemetry flows.

On first connect, the service probes for up to 8 seconds for plaintext packets. If none arrive it assumes ECDH. The detected type is written to `firmware_type.txt` and reused on all subsequent starts.

To force re-detection (e.g. after a firmware upgrade):
```bash
rm ~/solix-monitor/logs/firmware_type.txt
sudo systemctl restart solix-monitor
```

## Service Config (env vars in unit file)

| Var | Default | Description |
|-----|---------|-------------|
| `SOLIX_BLE_ADDR` | `F4:9D:8A:83:D3:24` | BLE MAC of the battery |
| `SOLIX_PORT` | `18082` | HTTP listen port |
| `SOLIX_HOST` | `127.0.0.1` | HTTP listen address |
| `SOLIX_CSV_DIR` | `~/solix-monitor/logs` | CSV + firmware cache dir |
| `SOLIX_CSV_INTERVAL` | `60` | Seconds between CSV rows |
| `SOLIX_CAPACITY_WH` | `288` | Battery capacity for hours-remaining calc |

## HTTP Endpoints

| Endpoint | Description |
|----------|-------------|
| `/solix/power` | Full JSON telemetry state |
| `/telemetry/power` | Alias for `/solix/power` |
| `/sensor/power` | ESPHome-compat shim: `{"value": total_output_w, ...}` — consumed by `power_telemetry.py` |
| `/health` | `{"ok":true}` |

## Common Operations

```bash
# Status
sudo systemctl status solix-monitor

# Restart (e.g. after BLE drops or phone app grabbed connection)
sudo systemctl restart solix-monitor

# Live logs
sudo journalctl -u solix-monitor -f

# Check current telemetry
curl -s http://127.0.0.1:18082/solix/power | python3 -m json.tool

# Check firmware type in use
cat ~/solix-monitor/logs/firmware_type.txt
```

## Troubleshooting

**`[org.bluez.Error.NotPermitted] Notify acquired` in logs**
Another BLE client (e.g. Anker app on a phone) holds the notify slot. Disconnect the phone app and restart the service.

**`Device not found in scan` repeating**
The battery may not be advertising. Check BT stack:
```bash
sudo systemctl restart bluetooth
sudo systemctl restart solix-monitor
```
If the battery was just power-cycled it can take ~30s to reappear in scans.

**Connected but `ble_connected: true`, all telemetry null**
Likely a firmware mismatch — cached firmware type may be wrong. Delete `firmware_type.txt` and restart to re-probe.

**Service connects but immediately drops (ECDH path)**
SolixBLE negotiation timed out. Usually a transient BLE stack issue. The service will auto-retry. If persistent, restart bluetooth then the service.

## Dependencies

Installed on both Pi machines under the system Python:
- `bleak` — BLE client
- `bleak-retry-connector` — robust connection handling
- `SolixBLE` (pip: `SolixBLE`, source: `flip-dots/SolixBLE`) — ECDH protocol for new firmware
- `pycryptodome`, `cryptography` — pulled in by SolixBLE

Install (if setting up from scratch):
```bash
pip install bleak bleak-retry-connector SolixBLE --break-system-packages
```
