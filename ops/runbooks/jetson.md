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

```bash
sudo PROFILE=eco-jetson ./ops/scripts/run-stack.sh
```

## Key Jetson Defaults

- model: `staeiou/bartleby-qwen3-1.7b_v4-awq`
- quantization: `awq_marlin`
- telemetry backend: `jtop` then `nvidia-smi`
- fixed base overhead: `5.5W`

## Verify

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:18201/health
curl -fsS http://127.0.0.1:18201/telemetry/power
```
