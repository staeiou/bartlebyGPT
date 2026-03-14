# Home RTX 4000 Runbook

## Host

- Home server with Quadro RTX 4000
- Uses `nvidia-smi` telemetry backend

## Serve API Stack

```bash
sudo PROFILE=home-rtx4000 ./ops/scripts/run-stack.sh
```

## Typical Mode

- API only (`ENABLE_WEB_APP=0`) because frontend is usually hosted via GitHub Pages.
- Cloudflare hostname defaults to `api.bartlebygpt.org` in this profile.

## Verify

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:18201/health
curl -fsS http://127.0.0.1:18201/telemetry/power
```

## Notes

- Keep telemetry schema aligned with Jetson path.
- Base power assumptions for frontend copy remain managed in frontend deployment profile logic.
