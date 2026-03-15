# Raspberry Pi 4B 4GB Runbook (llama-server + systemd)

## Host

- Raspberry Pi 4B (4GB RAM)
- Debian/Raspberry Pi OS (aarch64 recommended)

## Profile

- default profile: `ops/config/profiles/rpi4-llama.env`
- default model: `staeiou/bartleby-qwen3-1.7b_v4` GGUF `Q4_K_M`
- default concurrency: `-np 1`

For public web deployment (Bartleby site in `docs/` + API), use:

- `ops/config/profiles/rpi4-llama-live.env`

## Full Bootstrap

Build from source, download model, install and start service:

```bash
sudo PROFILE=rpi4-llama ./ops/bootstrap/bootstrap_rpi_llama_full.sh
```

## Fast Rebootstrap

Reuse existing install, refresh source/build/service, restart:

```bash
sudo PROFILE=rpi4-llama ./ops/bootstrap/bootstrap_rpi_llama_fast.sh
```

Useful toggles:

```bash
# skip git pull
sudo PROFILE=rpi4-llama PULL_LATEST=0 ./ops/bootstrap/bootstrap_rpi_llama_fast.sh

# skip rebuild and only rewrite service/env + restart
sudo PROFILE=rpi4-llama REBUILD=0 ./ops/bootstrap/bootstrap_rpi_llama_fast.sh
```

## Verify

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/v1/models
sudo systemctl status bartleby-llama --no-pager
```

## Public Site Mode (docs + API + tunnel)

```bash
sudo PROFILE=rpi4-llama-live ./ops/scripts/run-stack.sh
```

Important tunnel target:

- point `pi.bartlebygpt.org` to `http://localhost:18201` (nginx)
- do not point it to `http://localhost:8000` (raw llama-server)

## Logs

```bash
sudo journalctl -u bartleby-llama -f
```

## Notes

- This path is systemd-first and does not depend on `run-stack.sh`.
- On Pi 4B 4GB, keep context small (`512` default) and parallelism at `1`.
