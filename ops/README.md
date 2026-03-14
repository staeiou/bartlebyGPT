# Ops

This directory is the internal deployment/operations source of truth for BartlebyGPT.
Nothing here is served to end users.

## Scope

- bootstrap scripts for hardware-specific bring-up
- runtime stack launcher (`vLLM + telemetry + nginx + cloudflared`)
- deployment profile env files
- internal runbooks and architecture notes

## Layout

- `ops/scripts/run-stack.sh`: canonical runtime launcher
- `ops/scripts/power_telemetry.py`: telemetry HTTP service (`/telemetry/power`)
- `ops/bootstrap/bootstrap_jetson_full.sh`: full Jetson bootstrap (AWQ + BnB path)
- `ops/bootstrap/bootstrap_jetson_fast.sh`: fast Jetson rebootstrap
- `ops/config/profiles/*.env`: deployment-specific defaults
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

You can also point directly to any profile file:

```bash
sudo PROFILE_FILE=./ops/config/profiles/eco-jetson.env ./ops/scripts/run-stack.sh
```

## Notes

- Caller-provided env vars still work and can override profile values.
- `docs/` remains the frontend bundle source for optional nginx web serving.
- Keep one telemetry schema across all deployments (Jetson, RTX, future Pi).
