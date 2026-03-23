# Ops Architecture

## Runtime Components

1. `vLLM` serves model inference on local port (`VLLM_PORT`, default `8000`).
   - Raspberry Pi path can use `llama-server` on the same local port.
2. `solix-monitor` (optional) reads Solix BLE and serves `/sensor/power` shim on `127.0.0.1:18082`.
3. `power_telemetry.py` samples power + load and serves `/telemetry/power`.
4. `nginx` fronts public port (`PUBLIC_PORT`, default `18201`) and proxies:
   - `/v1/*` -> vLLM
   - `/health`, `/metrics`, `/load` -> vLLM
   - `/telemetry/power` -> telemetry service
   - `/` -> optional static web app (`docs/` copy)
5. `cloudflared` optionally exposes nginx via named/quick tunnel.

## Backend Variants

- Primary: `vLLM` (AWQ on Jetson, non-Jetson model per profile).
- Alternate/fallback: `llama.cpp` source build on Jetson (see `ops/runbooks/llama-cpp-jetson.md`).

## Power Backends

- `jtop` / `nvidia-smi`: component load power (telemetry derives wall estimate with base + multiplier).
- `esphome`: direct wall-total watts from a smart plug endpoint (for example, `/sensor/power`).
- `solix-monitor`: Anker Solix C300X DC battery read directly over BLE by `solix-monitor.service` (source: `ops/services/solix-monitor/`). Exposes an ESPHome-compatible `/sensor/power` shim at `http://127.0.0.1:18082/sensor/power`. Used on both deployments. Auto-detects plaintext TLV vs ECDH-encrypted firmware. See `ops/runbooks/solix-ble.md`.

## Control Plane

- `run-stack.sh` owns process lifecycle and generated nginx config.
- deployment variance is primarily profile-driven (`ops/config/profiles/*.env`).
- Raspberry Pi also has a systemd-first control path via:
  - `ops/bootstrap/bootstrap_rpi_llama_full.sh`
  - `ops/bootstrap/bootstrap_rpi_llama_fast.sh`
- Full stack boot persistence (nginx + telemetry + tunnel + process orchestration):
  - `ops/bootstrap/bootstrap_stack_service.sh` installs a systemd unit around `run-stack.sh`
- Fresh host one-command setup:
  - `ops/bootstrap/bootstrap_fresh_box.sh` installs dependencies, optional solix-monitor/cloudflared services, and stack service.
- `run-stack.sh` supports:
  - `STACK_MODE=process` for foreground runtime (`vllm` or `llama-server`)
  - `STACK_MODE=systemd` as a dispatcher to bootstrap scripts

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
- `rpi4-llama`: Raspberry Pi 4B llama.cpp/systemd defaults.
- `rpi4-llama-live`: Raspberry Pi live web+tunnel profile in process mode.
- `eco-jetson-systemd`: run-stack systemd dispatcher for Jetson bootstrap.
- `rpi4-llama-systemd`: run-stack systemd dispatcher for Pi bootstrap.
- `rtx-pod-vllm`: pod/container vLLM process-mode profile (no systemd).

Future:

- add additional `pi-*` profiles that keep the same telemetry contract.
