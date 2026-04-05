# Ops

This directory is the internal deployment/operations source of truth for BartlebyGPT.
Nothing here is served to end users.

## Source Of Truth And Deploy Rules

- Edit code in repo paths only (under `bartlebyGPT/`).
- Treat `/opt/bartleby/*` and `/var/www/bartlebygpt` as deploy outputs.
- Apply changes with idempotent scripts, not manual file copies into `/opt`.
- For battery monitor updates, rerun `ops/bootstrap/bootstrap_fresh_box.sh` so `/opt` is regenerated from repo.

## Scope

- bootstrap scripts for hardware-specific bring-up
- runtime stack launcher (`vLLM + telemetry + nginx + cloudflared`)
- fresh-box bootstrap orchestration for one-shot deployment
- systemd-first Raspberry Pi llama.cpp bootstrap path
- deployment profile env files
- internal runbooks and architecture notes

## Layout

- `ops/scripts/run-stack.sh`: canonical runtime launcher
- `ops/scripts/power_telemetry.py`: telemetry HTTP service (`/telemetry/power`)
- `ops/services/solix-monitor/solix_monitor.py`: Anker Solix C300X DC BLE monitor service source
- `ops/services/lfp-monitor/lfp_monitor.py`: LFP12100EK (JBD BMS) + Victron SmartSolar BLE monitor service source
- `ops/scripts/doctor.sh`: health + telemetry verification checks
- `ops/bootstrap/bootstrap_fresh_box.sh`: one-command fresh host setup; installs battery monitor via `--force-solix-monitor` (accepts `BATTERY_MONITOR_SCRIPT` to select which monitor)
- `ops/bootstrap/bootstrap_jetson_full.sh`: full Jetson bootstrap (AWQ + BnB path)
- `ops/bootstrap/bootstrap_jetson_fast.sh`: fast Jetson rebootstrap
- `ops/bootstrap/bootstrap_rpi_llama_full.sh`: full Pi bootstrap (`llama-server` + systemd)
- `ops/bootstrap/bootstrap_rpi_llama_fast.sh`: fast Pi rebootstrap (`llama-server` + systemd)
- `ops/bootstrap/bootstrap_stack_service.sh`: install boot-persistent full stack service (`run-stack.sh`)
- `ops/config/profiles/*.env`: deployment-specific defaults
- `ops/config/modprobe.d/disable-av.conf`: kernel module deny list for headless Jetson (blocks audio + camera + nvidia_drm; safe for CUDA)
- `ops/runbooks/*.md`: operator procedures
  - `ops/runbooks/llama-cpp-jetson.md`: Jetson llama.cpp source-build notes
- `ops/architecture.md`: stack and telemetry contract

## Quick Start

1. Choose a profile.
2. Launch stack from repo root.

```bash
cd /path/to/bartlebyGPT
sudo PROFILE=api-jetson ./ops/scripts/run-stack.sh
```

Or for home RTX API:

```bash
cd /path/to/bartlebyGPT
sudo PROFILE=home-rtx4000 ./ops/scripts/run-stack.sh
```

Fresh host one-shot bootstrap (recommended):

```bash
cd /path/to/bartlebyGPT
sudo ./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile api-jetson \
  --secrets-file /root/bartleby-secrets.env
```

Fast idempotent apply for battery monitor code/config changes:

```bash
cd /path/to/bartlebyGPT
sudo ./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile api-jetson \
  --force-solix-monitor \
  --skip-cloudflared \
  --skip-inference-bootstrap \
  --skip-doctor
```

Example secrets file:

```bash
# /root/bartleby-secrets.env
CLOUDFLARE_TUNNEL_TOKEN=...
```

Template:

```bash
cp ops/config/secrets.example.env /root/bartleby-secrets.env
chmod 600 /root/bartleby-secrets.env
```

For Raspberry Pi (`llama-server` via systemd):

```bash
cd /path/to/bartlebyGPT
sudo PROFILE=rpi4-llama ./ops/bootstrap/bootstrap_rpi_llama_full.sh
```

Fast rebootstrap on Pi:

```bash
cd /path/to/bartlebyGPT
sudo PROFILE=rpi4-llama ./ops/bootstrap/bootstrap_rpi_llama_fast.sh
```

For Raspberry Pi public site + API (serves `docs/` via nginx + tunnel):

```bash
cd /path/to/bartlebyGPT
sudo PROFILE=rpi4-llama-live ./ops/scripts/run-stack.sh
```

## Deployment Split

- Web + API + tunnel hosts:
  - `api-jetson` — Jetson + Anker Solix C300X DC (288Wh)
  - `jetson-solar-lfp` — Jetson + LFP12100EK (1200Wh) + Victron SmartSolar MPPT 100/20
  - `rpi4-llama-live` — Raspberry Pi + Anker Solix C300X DC
- API-only hosts (frontend on GitHub Pages):
  - `home-rtx4000`
  - `rtx-pod-vllm`

## run-stack Modes

`run-stack.sh` now has two explicit modes:

- `STACK_MODE=process` (default): foreground stack (`inference + telemetry + nginx + cloudflared`).
  - supports `INFERENCE_BACKEND=vllm` and `INFERENCE_BACKEND=llama-server`
- `STACK_MODE=systemd`: dispatches to host bootstrap scripts for persistent services.

Systemd dispatch examples:

```bash
sudo PROFILE=rpi4-llama-systemd ./ops/scripts/run-stack.sh
```

Boot-persistent full stack examples (run once, then auto-start on boot):

```bash
# Jetson full stack at boot (reuses vllm.service)
sudo PROFILE=api-jetson ./ops/bootstrap/bootstrap_stack_service.sh

# Raspberry Pi full stack at boot (reuses bartleby-llama.service)
sudo PROFILE=rpi4-llama-live ./ops/bootstrap/bootstrap_stack_service.sh

# If using a named Cloudflare tunnel/domain, include token on install:
sudo PROFILE=rpi4-llama-live CLOUDFLARE_TUNNEL_TOKEN=... ./ops/bootstrap/bootstrap_stack_service.sh
```

Pod/container (no systemd) example:

```bash
sudo PROFILE=rtx-pod-vllm ./ops/scripts/run-stack.sh
```

Pi live web+tunnel example:

```bash
sudo PROFILE=rpi4-llama-live ./ops/scripts/run-stack.sh
```

Expected routing for `rpi4-llama-live`:

- Cloudflare hostname (default `pi.bartlebygpt.org`) should point to `http://localhost:18201` (nginx)
- do not point the hostname directly to `http://localhost:8000` (raw llama-server UI/API)

You can also point directly to any profile file:

```bash
sudo PROFILE_FILE=./ops/config/profiles/api-jetson.env ./ops/scripts/run-stack.sh
```

## Battery BLE Power Telemetry

Battery monitor services read power data over BLE and expose a common HTTP contract at `http://127.0.0.1:18082/sensor/power` consumed by `power_telemetry.py`. Two monitors exist:

### solix-monitor (`api-jetson`, `rpi4-llama-live`)

Source: `ops/services/solix-monitor/solix_monitor.py`

Reads the Anker Solix C300X DC (288Wh) via BLE. Auto-detects firmware protocol (plaintext TLV vs ECDH-encrypted) on first connect and caches to `firmware_type.txt`.

- Logs to `/opt/bartleby/solix-monitor/logs/solix-YYYY-MM-DD.csv` every 60s
- Installed by `bootstrap_fresh_box.sh` with `ENABLE_BATTERY_MONITOR=1`

Manage: `sudo systemctl {start,stop,restart,status} solix-monitor`

See `ops/runbooks/solix-ble.md` for setup and firmware details.

### lfp-monitor (`jetson-solar-lfp`)

Source: `ops/services/lfp-monitor/lfp_monitor.py`

Two BLE sources:
- **JBD BMS** (`A5:C2:39:1A:5D:29`): LFP12100EK — SOC (from Ah ratio), voltage, temperature, net current.
- **Victron SmartSolar MPPT 100/20** (`CD:4C:1F:A1:BF:EF`, passive advertisement): solar_w, load_w via `external_device_load`.

Topology: Jetson powered from Victron load output. `load_w = external_device_load`. `solar_w = solar_power`.

SOC is derived from `remaining_ah / nominal_ah` — the BMS-reported SOC% is unreliable on a new battery until several charge cycles complete.

- Logs to `/opt/bartleby/lfp-monitor/logs/battery-YYYY-MM-DD.csv` every 60s
- Logs raw JBD packets to `/opt/bartleby/lfp-monitor/logs/jbd-basic-YYYY-MM-DD.csv`
- Requires `VICTRON_ENCRYPTION_KEY` in `/root/bartleby-secrets.env`
- Installed by `bootstrap_fresh_box.sh` with `ENABLE_BATTERY_MONITOR=1` and `BATTERY_MONITOR_SCRIPT=./ops/services/lfp-monitor/lfp_monitor.py`

Current LFP semantics:

- Victron is the live power source of truth:
  - `value`
  - `battery_solar_input_w`
  - `battery_total_input_w`
  - `battery_yield_today_wh`
- JBD is polled for battery-side fields only:
  - `battery_soc_pct`
  - `battery_remaining_ah`
  - `battery_voltage_mv`
  - `battery_net_current_ma`
- `ble_connected` on `/sensor/power` means the Victron/power feed is live enough for telemetry, not “JBD is connected right now”
- `battery_reading_ts` is JBD freshness only
- `victron_reading_ts` is the power-feed freshness clock
- `power_telemetry.py` must use Victron/power-feed freshness for stale detection; using JBD timestamps for whole-feed staleness is wrong and causes false fallback to `jtop`

Manage: `sudo systemctl {start,stop,restart,status} lfp-monitor`

## Jetson Power Optimization

On headless Jetson deployments, unused audio and camera kernel modules waste significant power (powertop shows audio codec at ~80% active time). `ops/config/modprobe.d/disable-av.conf` contains a deny list targeting audio, camera, HDMI CEC, and display modules.

**This did not work safely.** Every combination we tried that blocked the AV modules also broke CUDA — either directly or via missing `nvidia_modeset` (which `nvidia_drm` normally pulls in as a dependency). We could not find a deny list that reduced module load without killing inference. The config file is retained for reference but is not deployed.

## Notes

- Caller-provided env vars still work and can override profile values.
- `docs/` remains the frontend bundle source for optional nginx web serving.
- Keep one telemetry schema across all deployments (Jetson, RTX, future Pi).
- Power telemetry backend options:
  - `TELEMETRY_POWER_BACKEND=esphome` + `TELEMETRY_ESPHOME_POWER_URL=http://<plug-ip>/sensor/power` for smart plug (WiFi)
  - Battery monitor deployments (`api-jetson`, `rpi4-llama-live`, `jetson-solar-lfp`) expose an ESPHome-compatible `/sensor/power` shim at `http://127.0.0.1:18082/sensor/power` consumed by `power_telemetry.py`. All monitors emit both `battery_*` (canonical) and `solix_*` (compat) field names.
  - For `jetson-solar-lfp`, wall-total power comes from Victron, not JBD. Treat JBD fields as battery metadata, not the live watt-feed clock.
- `run-stack.sh` process mode is best for pod/container and for live web+tunnel foreground runs.
- Pi systemd scripts are best for persistent inference service management on bare metal.
- `STACK_MODE=systemd` in `run-stack.sh` is a dispatcher for bootstrap scripts, not a full-systemd replacement for every component.
