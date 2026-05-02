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

## Network: USB 4G Primary, Wi-Fi Fallback

The off-grid Jetson uses a TCL/T&A Mobile USB 4G dongle as the primary WAN and Wi-Fi as
fallback. The dongle currently presents as USB Ethernet (`rndis_host`), not as a
ModemManager modem; `mmcli -L` may report no modems even when the data path is healthy.

Expected interfaces and routes:

- USB 4G: `enxc0fef37fbf4d`, DHCP from `192.168.0.1`, default route metric `50`
- Wi-Fi: `wlP1p1s0`, default route metric `200`

Persistent host config is in `/etc/netplan`, not in this repo. The 4G config should match
by USB network driver so it survives USB port changes and modem RNDIS/CDC mode flips:

```yaml
# /etc/netplan/91-4g-usb.yaml
network:
  version: 2
  ethernets:
    usb4g:
      match:
        driver: ["rndis_host", "cdc_ether"]
      dhcp4: true
      optional: true
      dhcp4-overrides:
        route-metric: 50
      dhcp6: false
```

Wi-Fi should remain lower priority:

```yaml
# /etc/netplan/90-wifi.yaml
dhcp4-overrides:
  route-metric: 200
dhcp6-overrides:
  route-metric: 200
```

Do not pin the 4G link to a USB topology path or a single MAC address. A previous
configuration matched `Path=platform-3610000.usb-usb-0:1:1.0` and MAC
`20:19:3c:9f:ac:e4`; after the same physical modem re-enumerated as
`1bbb:0643`/`rndis_host` with MAC `c0:fe:f3:7f:bf:4d`, networkd left the live interface
unmanaged and Wi-Fi became the default route.

Apply and verify:

```bash
sudo netplan generate
sudo netplan apply
networkctl status enxc0fef37fbf4d --no-pager
ip -br addr
ip route
ip route get 1.1.1.1
ping -c 3 -W 3 -I enxc0fef37fbf4d 1.1.1.1
```

Healthy state:

```text
enxc0fef37fbf4d routable configured
default via 192.168.0.1 dev enxc0fef37fbf4d ... metric 50
default via 192.168.1.254 dev wlP1p1s0 ... metric 200
```

Avoid `sudo netplan status --all` as the primary check on this host. Ubuntu FAN/VXLAN
interface `ftun0` can make `ip -d -j addr` emit invalid JSON containing `fan-map ...`,
which netplan reports as `Cannot query iproute2 interface data: Expecting ',' delimiter`.
Use `networkctl`, `ip -br addr`, and `ip route` instead.

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
