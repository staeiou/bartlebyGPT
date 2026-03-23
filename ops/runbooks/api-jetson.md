# API Jetson Runbook

## Purpose

Deploy full `api.bartlebygpt.org` stack on a fresh Jetson host with:

- `vllm.service` inference
- `solix-monitor.service` BLE power producer
- `bartleby-stack.service` (nginx + telemetry)
- `cloudflared.service` named tunnel to `localhost:18201`

## One-Command Bootstrap

```bash
cd /home/ubuntu/vllm_jetson/bartlebyGPT
cp ops/config/secrets.example.env /root/bartleby-secrets.env
chmod 600 /root/bartleby-secrets.env
# edit /root/bartleby-secrets.env and set CLOUDFLARE_TUNNEL_TOKEN
# and set VLLM_WHEEL_URL + VLLM_WHEEL_SHA256 from the GitHub Release asset.
# Example:
# VLLM_WHEEL_URL=https://github.com/staeiou/bartlebyGPT/releases/download/vllm-wheel-jetson-cu126-20260312/vllm-0.1.dev1%2Bgbdc234345.d20260312.cu126-cp310-cp310-linux_aarch64.whl
# VLLM_WHEEL_SHA256=c6d9a1a06e01df27bf3d4f6db115ba6a971c1f3622fe675f214532205339f89f

sudo ./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile api-jetson \
  --secrets-file /root/bartleby-secrets.env
```

## Verify

```bash
sudo PROFILE=api-jetson ./ops/scripts/doctor.sh
sudo systemctl status bartleby-stack --no-pager -n 80
sudo systemctl status cloudflared --no-pager -n 80
sudo systemctl status solix-monitor --no-pager -n 80
```

## Notes

- `api-jetson` profile uses local Solix shim endpoint:
  - `http://127.0.0.1:18082/sensor/power`
- If the Anker mobile app is connected, BLE may be occupied; disconnect app and restart:

```bash
sudo systemctl restart solix-monitor
```
