# Ops

This directory is the internal deployment/operations source of truth for BartlebyGPT.
Nothing here is served to end users.

## Scope

- bootstrap scripts for hardware-specific bring-up
- runtime stack launcher (`vLLM + telemetry + nginx + cloudflared`)
- systemd-first Raspberry Pi llama.cpp bootstrap path
- deployment profile env files
- internal runbooks and architecture notes

## Layout

- `ops/scripts/run-stack.sh`: canonical runtime launcher
- `ops/scripts/power_telemetry.py`: telemetry HTTP service (`/telemetry/power`)
- `ops/bootstrap/bootstrap_jetson_full.sh`: full Jetson bootstrap (AWQ + BnB path)
- `ops/bootstrap/bootstrap_jetson_fast.sh`: fast Jetson rebootstrap
- `ops/bootstrap/bootstrap_rpi_llama_full.sh`: full Pi bootstrap (`llama-server` + systemd)
- `ops/bootstrap/bootstrap_rpi_llama_fast.sh`: fast Pi rebootstrap (`llama-server` + systemd)
- `ops/config/profiles/*.env`: deployment-specific defaults
- `ops/runbooks/*.md`: operator procedures
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

## Notes

- Caller-provided env vars still work and can override profile values.
- `docs/` remains the frontend bundle source for optional nginx web serving.
- Keep one telemetry schema across all deployments (Jetson, RTX, future Pi).
- `run-stack.sh` process mode is best for pod/container and for live web+tunnel foreground runs.
- Pi systemd scripts are best for persistent inference service management on bare metal.
- `STACK_MODE=systemd` in `run-stack.sh` is a dispatcher for bootstrap scripts, not a full-systemd replacement for every component.
