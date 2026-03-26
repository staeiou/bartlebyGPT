# Ops

This directory is the internal deployment/operations source of truth for BartlebyGPT.
Nothing here is served to end users.

## Source Of Truth And Deploy Rules

- Edit code in repo paths only (under `bartlebyGPT/`).
- Treat `/opt/bartleby/*` and `/var/www/bartlebygpt` as deploy outputs.
- Apply changes with idempotent scripts, not manual file copies into `/opt`.
- For Solix monitor updates, rerun `ops/bootstrap/bootstrap_fresh_box.sh` so `/opt` is regenerated from repo.

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
- `ops/scripts/doctor.sh`: health + telemetry verification checks
- `ops/bootstrap/bootstrap_fresh_box.sh`: one-command fresh host setup
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
sudo PROFILE=eco-jetson ./ops/scripts/run-stack.sh
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

Fast idempotent apply for Solix monitor code/config changes:

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
  - `eco-jetson`
  - `api-jetson`
  - `rpi4-llama-live`
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
sudo PROFILE=eco-jetson-systemd ./ops/scripts/run-stack.sh
```

Boot-persistent full stack examples (run once, then auto-start on boot):

```bash
# Jetson full stack at boot (reuses vllm.service)
sudo PROFILE=eco-jetson ./ops/bootstrap/bootstrap_stack_service.sh

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
sudo PROFILE_FILE=./ops/config/profiles/eco-jetson.env ./ops/scripts/run-stack.sh
```

## Solix BLE Power Telemetry

Both deployments run on an Anker Solix C300X DC battery (288Wh LFP).
Power is read directly over BLE by `solix-monitor.service` (repo source in `ops/services/solix-monitor/`).

The service auto-detects firmware protocol on first connect (plaintext TLV vs ECDH-encrypted) and caches the result to `firmware_type.txt`. Same code runs on both machines.

- Logs per-day CSVs (default) to `/opt/bartleby/solix-monitor/logs/solix-YYYY-MM-DD.csv` every 60s
- Serves live JSON at `http://127.0.0.1:18082/solix/power`
- ESPHome shim at `http://127.0.0.1:18082/sensor/power` feeds `power_telemetry.py`

Manage: `sudo systemctl {start,stop,restart,status} solix-monitor`

See `ops/runbooks/solix-ble.md` for setup, troubleshooting, and firmware details.

## Jetson Power Optimization

On headless Jetson deployments, unused audio and camera kernel modules waste significant power (powertop shows audio codec at ~80% active time). `ops/config/modprobe.d/disable-av.conf` contains a deny list targeting audio, camera, HDMI CEC, and display modules.

**This did not work safely.** Every combination we tried that blocked the AV modules also broke CUDA — either directly or via missing `nvidia_modeset` (which `nvidia_drm` normally pulls in as a dependency). We could not find a deny list that reduced module load without killing inference. The config file is retained for reference but is not deployed.

## Notes

- Caller-provided env vars still work and can override profile values.
- `docs/` remains the frontend bundle source for optional nginx web serving.
- Keep one telemetry schema across all deployments (Jetson, RTX, future Pi).
- Power telemetry backend options:
  - `TELEMETRY_POWER_BACKEND=esphome` + `TELEMETRY_ESPHOME_POWER_URL=http://<plug-ip>/sensor/power` for smart plug (WiFi)
  - On Solix BLE deployments (`api-jetson`, `rpi4-llama-live`), `solix-monitor.service` exposes an ESPHome-compatible `/sensor/power` shim at `http://127.0.0.1:18082/sensor/power`. `total_output_w` from Solix becomes `estimated_total_watts` in the telemetry contract.
- `run-stack.sh` process mode is best for pod/container and for live web+tunnel foreground runs.
- Pi systemd scripts are best for persistent inference service management on bare metal.
- `STACK_MODE=systemd` in `run-stack.sh` is a dispatcher for bootstrap scripts, not a full-systemd replacement for every component.
