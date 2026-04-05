# Ops Architecture

## Runtime Components

1. `vLLM` serves model inference on local port (`VLLM_PORT`, default `8000`).
   - Raspberry Pi path can use `llama-server` on the same local port.
2. Battery monitor service (optional) serves `/sensor/power` shim on `127.0.0.1:18082`.
   - `solix-monitor` for Anker Solix deployments
   - `lfp-monitor` for JBD BMS + Victron deployments
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
- `lfp-monitor`: split-source battery monitor for `jetson-solar-lfp`.
  - Victron SmartSolar BLE advertisements provide live wall/load and solar input
  - JBD BMS BLE GATT polling provides battery-side fields (SOC, Ah, voltage, current)
  - telemetry must treat Victron freshness as the wall-power freshness clock, not JBD freshness

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

All deployments emit the same JSON shape from `/telemetry/power`:

**Core power fields**

| Field | Description |
|-------|-------------|
| `estimated_total_watts` | Display value. Wall watts (Solix path) or `(base + measured) × multiplier` (jtop/smi path). |
| `estimated_total_server_watts` | Same as above (legacy alias). |
| `measured_server_watts` | Raw component load from jtop/nvidia-smi; `null` on wall-total path. |
| `measured_gpu_watts` | Same as `measured_server_watts` (legacy alias). |
| `base_system_watts` | Fixed base overhead added on component-load path; `0` on wall-total path. |
| `idle_gpu_watts` | EMA of watts when no requests are running. |
| `attributed_gpu_watts` | Watts per active request. |
| `power_measurement_kind` | `"wall-total"` (Solix/ESPHome) or `"component-load"` (jtop/smi). |
| `watts_is_live` | `true` when source is a wall meter, `false` when estimated. |
| `power_reading_ts` | Unix timestamp of the underlying power reading. |
| `power_backend` | Active backend name (e.g. `"esphome"`, `"jtop"`). |
| `power_rails_watts` | Dict of per-rail watts from jtop; empty on other paths. |
| `clamp_min_watts` | Configured floor (0 = off). |
| `clamp_max_watts` | Configured ceiling (0 = off). |

**vLLM load fields**

| Field | Description |
|-------|-------------|
| `requests_running` | Requests actively being processed. |
| `requests_waiting` | Requests queued. |
| `request_success_total` | Cumulative completed requests counter. |
| `requests_completed_interval` | Delta completed since last sample. |
| `server_load` | `/load` payload from vLLM. |
| `is_active` | `true` when `requests_running > 0`. |
| `cost_share_fraction` | Reserved for multi-tenant cost attribution (always `1.0` currently). |

**Battery monitor fields** (`battery_*` canonical; `solix_*` kept as compat aliases)

On Solix deployments these come from one device. On `jetson-solar-lfp` they are split:

- live power/load/solar from Victron
- battery-side state from JBD

| Field | Description |
|-------|-------------|
| `battery_soc_pct` | SOC %. On LFP/JBD deployments this is Ah-derived from the BMS packet. |
| `battery_solar_input_w` | Solar input watts. On LFP deployment this is Victron live data. |
| `battery_effective_solar_w` | Corrected solar for pass-through/full-charge cases. |
| `battery_total_input_w` | Total battery input watts. |
| `battery_voltage_mv` | Battery voltage mV. |
| `battery_temp_c` | Battery temperature °C. |
| `battery_charging_status` | Charging mode/status. On LFP deployment this is from Victron charge state. |
| `battery_reading_ts` | Timestamp of last successful battery-side reading. On LFP deployment this is JBD freshness only. |
| `solix_*` | Compatibility aliases for older frontend/telemetry code paths. |

**Solix BLE fields** (legacy/compat naming, still populated)

| Field | Description |
|-------|-------------|
| `solix_soc_pct` | State of charge %. |
| `solix_solar_input_w` | Raw solar input watts (tag `0xab`). Zero at 100% SOC. |
| `solix_effective_solar_w` | Corrected solar: equals `estimated_total_watts` when `soc >= 100` and `solar_input == 0` (pass-through mode); otherwise equals `solix_solar_input_w`. |
| `solix_total_input_w` | Total input watts (tag `0xac`). |
| `solix_voltage_mv` | Battery voltage mV. |
| `solix_temp_c` | Temperature °C. |
| `solix_charging_status` | Raw uint8 from tag `0xb6` — transitions logged to journalctl. Meaning under reverse engineering. |
| `solix_reading_ts` | Unix timestamp of the BLE packet. |

On `jetson-solar-lfp`, `solix_*` aliases are filled from the `battery_*` fields for compatibility only.

**Metadata**

| Field | Description |
|-------|-------------|
| `timestamp` | Unix timestamp of the last sample. |
| `source` | Human-readable source string (e.g. `"esphome+vllm"`). |
| `last_error` | Most recent sampling error string; empty on success. |

The frontend uses `battery_effective_solar_w` / `solix_effective_solar_w` so that solar display,
battery time-remaining calculation, and history charts remain correct at 100% SOC.

Important for `jetson-solar-lfp`:

- `power_reading_ts` must follow the live Victron/power feed timestamp
- JBD `battery_reading_ts` must remain separate
- if telemetry uses JBD freshness for whole-feed stale detection, it will incorrectly fall back to `jtop` while Victron is still live

## Deployment Profiles

Current profiles:

- `api-jetson`: Jetson Orin Nano Super 8GB — vLLM AWQ, Anker Solix C300X DC BLE telemetry (288Wh), web app, managed cloudflared.
- `jetson-solar-lfp`: Jetson Orin Nano Super 8GB — vLLM AWQ, LFP12100EK BMS + Victron SmartSolar MPPT BLE telemetry (1200Wh), web app.
- `home-rtx4000`: home RTX API defaults and no web app serving.
- `rpi4-llama`: Raspberry Pi 4B llama.cpp/systemd build and install defaults.
- `rpi4-llama-live`: Raspberry Pi live web+tunnel profile in process mode, Solix BLE telemetry.
- `rpi4-llama-systemd`: run-stack systemd dispatcher for Pi bootstrap.
- `rtx-pod-vllm`: pod/container vLLM process-mode profile (no systemd).
