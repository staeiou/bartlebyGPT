# WORK_LOG_SOLIX_2026-03-28-1PM.md

Work log for the Solix BLE instability investigation and stabilization on `sqlite-history-store`.

## Context

- Branch: `sqlite-history-store`
- Repo: `/home/ubuntu/vllm_jetson/bartlebyGPT`
- Live host: Jetson `api-jetson`
- Problem investigated:
  - Solix feed going stale
  - telemetry repeatedly restarting `solix-monitor.service` and `bluetooth.service`
  - need to stabilize live BLE path before pushing SQLite history cutover further

## What Was True At Start

- `bartleby-stack.service` was running repo `power_telemetry.py`
- `solix-monitor.service` was still running the old installed `/opt/bartleby/solix-monitor/solix_monitor.py`
- live `/telemetry/history` was still returning legacy history:
  - `source = "solix_csv"`
  - no `bin_statistic`
- telemetry log showed repeated stale-feed recovery attempts restarting:
  - `solix-monitor.service`
  - and every other attempt `bluetooth.service`

## Live Investigation Findings

### 1. SQLite branch was not the cause of the live stale-feed problem

Verified:

- installed `/opt/bartleby/solix-monitor/` did not yet contain `history_store.py`
- installed `solix-monitor.service` unit did not yet include `SOLIX_HISTORY_DB_PATH`
- therefore the new SQLite Solix writer was not yet running live

### 2. Old TLV path was dropping on its own

Observed in live `journalctl -u solix-monitor`:

- repeated `TLV connection dropped`
- occasional `Device ... not found in scan`

This existed before the new Solix reconnect patch was deployed.

### 3. Telemetry auto-recovery was making recovery worse

The old reconnect path in `solix_monitor.py` was:

- wait `RECONNECT_DELAY=10s`
- run `BleakScanner.discover(timeout=30.0)`
- reconnect if found

Telemetry stale detection was:

- `TELEMETRY_SOLIX_STALE_SECONDS=45`

So the stack was doing this:

1. TLV drops
2. Solix monitor starts its own reconnect cycle
3. telemetry reaches stale threshold right as reconnect is happening
4. telemetry restarts `solix-monitor` and sometimes `bluetooth.service`
5. recovery gets interrupted

This was confirmed from timestamp alignment in live journals.

## Stabilization Changes Applied

### 1. Disabled telemetry-driven Solix auto-recovery

Changed:

- `ops/config/profiles/api-jetson.env`

Change:

- `TELEMETRY_SOLIX_AUTO_RECOVER=0`

Applied live by restarting:

- `bartleby-stack.service`

Reason:

- telemetry should not restart Solix/Bluetooth while BLE recovery is already in progress

### 2. Reworked Solix reconnect path

Changed:

- `ops/services/solix-monitor/solix_monitor.py`

New behavior:

- import and use `bleak-retry-connector` when available
- use `get_device(address)` first on Linux/BlueZ
- then `BleakScanner.find_device_by_address(..., timeout=SCAN_TIMEOUT)`
- only fall back to `BleakScanner.discover(timeout=SCAN_TIMEOUT)` if needed
- use `establish_connection(BleakClientWithServiceCache, ...)` for TLV mode
- explicit cleanup on disconnect

New env var:

- `SOLIX_SCAN_TIMEOUT` defaulting to `10`

Old path:

- blunt `discover(timeout=30.0)` on every reconnect

New path:

- targeted lookup first, much shorter scan fallback

## Deployment Performed

Used the intended deploy path:

```bash
./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile api-jetson \
  --force-solix-monitor \
  --skip-cloudflared \
  --skip-inference-bootstrap \
  --skip-doctor
```

This also reinstalled/restarted `bartleby-stack.service`.

## Live Results After Deploy

### Telemetry

- `/telemetry/power` remained live
- observed `power_reading_age_s` around `2.5s`
- telemetry no longer restarted Solix/Bluetooth during stale periods

### Solix reconnect latency

Observed post-deploy cycles:

- `20:05:16.958` drop -> `20:05:30.739` first `RAW` back
- `20:06:08.388` drop -> `20:06:20.706` first `RAW` back
- `20:07:51.125` drop -> `20:08:03.882` first `RAW` back

So reconnect time improved from roughly `40s+` under the old scan path to roughly `12-14s`.

### Remaining issue

TLV still drops sometimes.

So:

- reconnect architecture is significantly better
- underlying BLE instability is not fully solved

## SQLite / History State After This Work

- live SQLite DB still exists at `/opt/bartleby/solix-monitor/logs/history.sqlite3`
- historical CSV import already happened earlier
- branch code for SQLite history still exists
- live `/telemetry/history` is now verified serving SQLite-backed median history:
  - `source = "sqlite_history"`
  - `bin_statistic = "median"`
- live DB freshness was also verified:
  - fresh `solix_events` timestamps are near current wall time
  - recent 24h bins are minute-aligned and populated from SQLite

So this session ended up doing both:

- stabilizing the Solix transport path
- and confirming the SQLite history cutover is live

## Current Recommendation

1. Keep `TELEMETRY_SOLIX_AUTO_RECOVER=0` for now.
2. Do not let telemetry restart `bluetooth.service`.
3. Continue investigating the root cause of TLV session drops separately from history work.
4. Treat the SQLite history work as live now; remaining work is hardening and cleanup, not initial cutover.

## Remaining Work / Hardening

What is left is no longer basic cutover. It is operational hardening and cleanup.

### 1. Commit and document the stabilization changes cleanly

- commit the Solix reconnect-path changes in `ops/services/solix-monitor/solix_monitor.py`
- commit the operational profile change keeping `TELEMETRY_SOLIX_AUTO_RECOVER=0`
- keep the updated handoff/runbook docs consistent with actual live state

### 2. Continue investigating the root cause of TLV disconnects

The reconnect path is better, but TLV still drops intermittently.

Remaining investigation areas:

- BlueZ / Bleak disconnect reason visibility
- RF/interference or device-advertising behavior on the Jetson host
- whether the short malformed TLV-like packets seen around reconnect boundaries are meaningful protocol state transitions
- whether TLV path longevity changes with different charging / battery states

The key point:

- reconnect is improved
- root cause of the disconnects is still not explained

### 3. Keep telemetry recovery conservative

Current safe policy:

- `TELEMETRY_SOLIX_AUTO_RECOVER=0`
- do not let telemetry restart `bluetooth.service`

If recovery is ever reintroduced, it should be much less aggressive:

- stale threshold significantly higher than the expected reconnect window
- no bluetooth restarts from telemetry
- likely only service-local recovery, not cross-service control

### 4. Validate SQLite history semantics more deeply

The endpoint is live and serving median bins, but more sanity checks are still worth doing:

- compare a few raw Solix event windows against their resulting median bins
- verify 7d hourly medians against direct SQLite row slices
- verify empty bins are genuinely empty rather than artifacts of import/reconnect gaps
- confirm frontend labeling and tooltip copy remain accurate under all fallback states

### 5. Decide whether to keep legacy CSV history indefinitely

Current state:

- SQLite is now the live history source
- CSV logging still exists as a backup/import path

Still undecided:

- keep CSV forever as a human-readable safety log
- or eventually reduce/remove CSV dependence once SQLite confidence is high

That decision should be explicit, because it affects future operational complexity.

### 6. Clean branch state before final merge

Before merging or handing off for a final PR/commit series:

- make sure docs reflect that SQLite history is live
- ensure the work log and README no longer contradict each other
- decide whether this work should be split into:
  - Solix transport stabilization
  - SQLite history architecture / deployment
  - doc updates

### 7. Optional next architecture step

Now that SQLite history is live, the next non-emergency improvement would be:

- reduce request-time raw-row processing further if needed
- consider whether median bin materialization or cached query layers are worth it

This is not urgent right now because the endpoint is already serving from SQLite successfully.

## Files Changed During This Session

- `ops/config/profiles/api-jetson.env`
- `ops/services/solix-monitor/solix_monitor.py`
- `README_SQL.md`
- `ops/runbooks/solix-ble.md`
- `WORK_LOG_SOLIX_2026-03-28-1PM.md`
