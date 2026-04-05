# Jetson Runbook

## Host

- Jetson Orin Nano Super 8GB
- JetPack/L4T 36.4.x, CUDA 12.6

## Bootstrap

Full setup:

```bash
sudo ./ops/bootstrap/bootstrap_jetson_full.sh
```

Fast rebootstrap after wheel/service updates:

```bash
sudo ./ops/bootstrap/bootstrap_jetson_fast.sh
```

## Serve Stack

For fresh `api.bartlebygpt.org` hosts (managed cloudflared + local Solix BLE):

```bash
sudo ./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile api-jetson \
  --secrets-file /root/bartleby-secrets.env
```

For the current off-grid Jetson LFP deployment:

```bash
sudo ./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile jetson-solar-lfp \
  --secrets-file /root/bartleby-secrets.env
```

## Key Jetson Defaults

- model: `staeiou/bartleby-qwen3-1.7b_v4-awq`
- quantization: `awq_marlin`
- telemetry backend: `esphome` via local battery monitor on power-aware deployments, otherwise `jtop` then `nvidia-smi`
- fixed base overhead: `5.5W`

## Current Jetson Solar LFP State

- Battery monitor service: `lfp-monitor` (port 18082)
- Power path:
  - Victron SmartSolar BLE advertisements provide live `value` / load watts and solar input
  - JBD BMS BLE provides battery-side fields: SOC, Ah, voltage, current
- JBD is polled one-shot every `60s` and disconnected immediately after read so the phone app can still use the BMS
- Telemetry stale detection follows `victron_reading_ts`, not `battery_reading_ts` (JBD freshness)
- Single shared `BleakScanner` feeds both Victron and JBD queues; scanner is stopped during JBD GATT sessions to prevent BlueZ notification failures

### JBD BLE Recovery

If `journalctl -u lfp-monitor` shows repeated `TimeoutError` on JBD queries:

```bash
sudo systemctl stop lfp-monitor
sudo systemctl restart bluetooth
sleep 5
sudo systemctl start lfp-monitor
```

## Verify

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:18201/health
curl -fsS http://127.0.0.1:18201/telemetry/power
```

For `jetson-solar-lfp`, also verify the monitor directly:

```bash
curl -fsS http://127.0.0.1:18082/sensor/power
sudo systemctl status lfp-monitor --no-pager -n 80
```
