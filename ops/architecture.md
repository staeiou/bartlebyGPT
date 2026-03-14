# Ops Architecture

## Runtime Components

1. `vLLM` serves model inference on local port (`VLLM_PORT`, default `8000`).
2. `power_telemetry.py` samples power + load and serves `/telemetry/power`.
3. `nginx` fronts public port (`PUBLIC_PORT`, default `18201`) and proxies:
   - `/v1/*` -> vLLM
   - `/health`, `/metrics`, `/load` -> vLLM
   - `/telemetry/power` -> telemetry service
   - `/` -> optional static web app (`docs/` copy)
4. `cloudflared` optionally exposes nginx via named/quick tunnel.

## Control Plane

- `run-stack.sh` owns process lifecycle and generated nginx config.
- deployment variance is primarily profile-driven (`ops/config/profiles/*.env`).

## Telemetry Contract (Stable)

All deployments should emit the same JSON shape from `/telemetry/power`:

- `timestamp`
- `measured_server_watts`
- `measured_gpu_watts`
- `base_system_watts`
- `estimated_total_watts`
- `estimated_total_server_watts`
- `idle_gpu_watts`
- `attributed_gpu_watts`
- `requests_running`
- `requests_waiting`
- `server_load`
- `is_active`
- `power_backend`
- `power_rails_watts`
- `source`
- `last_error`

This avoids frontend branching by hardware type.

## Deployment Profiles

Current profiles:

- `eco-jetson`: Jetson Orin Nano Super 8GB stack and eco hostname defaults.
- `home-rtx4000`: home RTX API defaults and no web app serving.

Future:

- add `pi-*` profiles that keep the same telemetry contract.
