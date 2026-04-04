# bartlebyGPT

`bartlebyGPT` is the repo for the Bartleby web app, inference stack, and power telemetry services.

This branch currently includes:

- the web app in [`docs/`](/home/ubuntu/vllm_jetson/bartlebyGPT/docs)
- deployment/bootstrap code in [`ops/`](/home/ubuntu/vllm_jetson/bartlebyGPT/ops)
- a SQLite-backed telemetry history path
- Solix BLE ingest for both plaintext TLV firmware and encrypted ECDH firmware

## Main Pieces

- Web app: [`docs/`](/home/ubuntu/vllm_jetson/bartlebyGPT/docs)
- Telemetry server: [`ops/scripts/power_telemetry.py`](/home/ubuntu/vllm_jetson/bartlebyGPT/ops/scripts/power_telemetry.py)
- Solix monitor: [`ops/services/solix-monitor/solix_monitor.py`](/home/ubuntu/vllm_jetson/bartlebyGPT/ops/services/solix-monitor/solix_monitor.py)
- LFP monitor: [`ops/services/lfp-monitor/lfp_monitor.py`](/home/ubuntu/vllm_jetson/bartlebyGPT/ops/services/lfp-monitor/lfp_monitor.py)
- Shared SQLite history store: [`ops/history_store.py`](/home/ubuntu/vllm_jetson/bartlebyGPT/ops/history_store.py)
- Bootstrap/deploy entrypoint: [`ops/bootstrap/bootstrap_fresh_box.sh`](/home/ubuntu/vllm_jetson/bartlebyGPT/ops/bootstrap/bootstrap_fresh_box.sh)

## Current Deployment Model

Development happens in the repo.

Deployment happens by:

- copying [`docs/`](/home/ubuntu/vllm_jetson/bartlebyGPT/docs) to `/var/www/bartlebygpt/`
- restarting `bartleby-stack.service` for telemetry/nginx changes
- deploying `solix-monitor` through [`bootstrap_fresh_box.sh`](/home/ubuntu/vllm_jetson/bartlebyGPT/ops/bootstrap/bootstrap_fresh_box.sh)

Do not treat `/opt/bartleby/*` or `/var/www/bartlebygpt/` as source of truth.

## Key Docs

- SQLite history handoff: [README_SQL.md](/home/ubuntu/vllm_jetson/bartlebyGPT/README_SQL.md)
- BLE transport/runbook: [solix-ble.md](/home/ubuntu/vllm_jetson/bartlebyGPT/ops/runbooks/solix-ble.md)
- Ops overview: [ops/README.md](/home/ubuntu/vllm_jetson/bartlebyGPT/ops/README.md)
- Full session handoff/work log: [HANDOFF_2026-03-29-8AM.md](/home/ubuntu/vllm_jetson/bartlebyGPT/HANDOFF_2026-03-29-8AM.md)

## Important Current Caveats

For Solix deployments (`api-jetson`, `rpi4-llama-live`), `SOLIX_BLE_ADDR` must be provided per machine. The bootstrap refuses to fall back to a default MAC.

For the LFP deployment (`jetson-solar-lfp`), `SOLIX_BLE_ADDR` (JBD BMS) and `VICTRON_ENCRYPTION_KEY` must be set — the key goes in `/root/bartleby-secrets.env`, not the profile.
