# README_SQL.md

This document is a handoff for the SQLite-backed telemetry history rearchitecture on branch `sqlite-history-store`.

It is written for a fresh coding agent. Do not assume this work is deployed just because the code exists in this branch.

## Current Status

- Branch: `sqlite-history-store`
- Repo: `/home/ubuntu/vllm_jetson/bartlebyGPT`
- This branch contains the SQLite-backed history path now serving live history.
- A live SQLite DB **has** already been populated from existing CSV history at:
  - `/opt/bartleby/solix-monitor/logs/history.sqlite3`
- Since the first version of this document was written, the live Solix BLE path was changed multiple times:
  - telemetry-driven Solix auto-recovery was disabled
  - a retry-connector reconnect path was deployed and later implicated in a long outage
  - the current live path uses raw `BleakClient`, explicit packet-health timeouts, and forced `StartNotify`
- Since then, telemetry truthfulness was also tightened:
  - active Solix stale threshold is now `90s`
  - telemetry preserves last-known `solix_soc_pct` across Solix disconnect/stale fallback
  - telemetry clears stale live Solix wall/solar fields during fallback instead of carrying them forward
- Since then, the live history endpoint has also been verified serving from SQLite:
  - `/telemetry/history` now returns `source = "sqlite_history"`
  - `/telemetry/history` now returns `bin_statistic = "median"`
- The current BLE transport remains an active investigation; the history stack is live, but transport burn-in is still required.

## What Is Wrong With The Old History System

The old history path is lossy for Solix history:

- `solix-monitor` receives BLE packets every ~3.5s.
- The old CSV logger in `ops/services/solix-monitor/solix_monitor.py` only wrote one snapshot every `SOLIX_CSV_INTERVAL` seconds, usually 60s.
- That means most real Solix readings were never persisted.
- The old `/telemetry/history` path in `ops/scripts/power_telemetry.py` rebuilt charts by rereading CSV files and averaging rows into bins.

So:

- old Solix CSV history is incomplete
- old chart bins were mean-based
- old bins were also built from sparse snapshot data, not true packet-level Solix history

## New Architecture In This Branch

The new target architecture is:

- `solix-monitor` writes one SQLite row per BLE reading event
- `power_telemetry.py` writes one SQLite row per vLLM telemetry sample
- `/telemetry/history` reads from SQLite and builds aligned **median** bins
- old CSVs are used only for one-time bootstrap/import so old on-disk history is not lost

### New Shared Module

File:

- `ops/history_store.py`

Responsibilities:

- create SQLite schema
- insert Solix events
- insert vLLM samples
- bulk import old CSV rows
- build 24h and 7d history payloads from SQLite
- emit `bin_statistic: "median"`

Tables:

- `solix_events`
  - primary key: `reading_ts_ms`
  - stores `ts`, `load_w`, `charge_w`, `soc_pct`, plus extra Solix fields
- `vllm_samples`
  - primary key: `sample_ts_ms`
  - stores `ts`, `requests_running`, `requests_waiting`, `requests_completed`

## Files Changed On This Branch

Core code:

- `ops/history_store.py`
- `ops/scripts/power_telemetry.py`
- `ops/services/solix-monitor/solix_monitor.py`
- `docs/app/power.js`

Deployment/bootstrap/config:

- `ops/scripts/run-stack.sh`
- `ops/bootstrap/bootstrap_fresh_box.sh`
- `ops/bootstrap/bootstrap_solix_monitor.sh`
- `ops/templates/systemd.solix-monitor.service.tmpl`
- `ops/config/profiles/api-jetson.env`
- `ops/config/profiles/rpi4-llama-live.env`

Utility script:

- `ops/scripts/import_history_csv_to_sqlite.py`

## Behavior Of The New Code

### power_telemetry.py

Key changes:

- reads `TELEMETRY_HISTORY_DB_PATH`
- instantiates `SQLiteHistoryStore` when configured
- writes vLLM sample rows into SQLite in `log_vllm_metrics()`
- builds history from SQLite in `compute_history_payload()`
- falls back to legacy CSV history if SQLite has no Solix rows yet
- emits `bin_statistic`
  - `"median"` for SQLite path
  - `"mean"` for legacy CSV fallback
- `bootstrap_history_db()` imports legacy CSV rows into SQLite once if the DB is empty
- active Solix stale threshold on the Jetson profile is now `90s`
- Solix fallback behavior is now split deliberately:
  - preserve last-known battery SOC (`solix_soc_pct`)
  - clear stale live wall/solar fields such as `solix_solar_input_w`, `solix_total_input_w`, `solix_reading_ts`, and `power_reading_ts`
- this means Solix deployments should continue to show battery state during reconnects without falsely implying live wall-power telemetry

### solix_monitor.py

Key changes:

- reads `SOLIX_HISTORY_DB_PATH`
- imports `history_store.py`
- on every `update_state(...)`, writes one Solix event to SQLite
- this is the fix for the old lossy history design

Important:

- the old 60s CSV logger still exists
- it remains useful as a legacy backup/import source
- it is no longer intended to be the authoritative history source once deployed

### power.js

Key changes:

- history labels are no longer hardcoded to “median”
- label text is derived from `state.powerHistory.bin_statistic`
- when payload says `median`, UI shows:
  - `Median Concurrent`
  - `Median Queued`
- when payload says `mean`, UI falls back to:
  - `Avg Concurrent`
  - `Queued`

This was added because partial deployment or fallback must not falsely label mean data as median.

## Deployment State Right Now

This is the important part:

- branch code exists
- branch code has been reviewed and locally validated
- branch code is now deployed enough that the live history endpoint is serving SQLite-backed median history

What is live now:

- the SQLite DB file has already been created and populated with imported legacy CSV history
- the live Solix service has since been redeployed for BLE stabilization work:
  - reconnect path now uses targeted lookup and `bleak-retry-connector`
  - telemetry auto-recovery is currently disabled in the active Jetson profile
- the live history endpoint is now verified serving SQLite history
- the deployed frontend already understands `bin_statistic` labels and matches the repo version

Live imported DB path:

- `/opt/bartleby/solix-monitor/logs/history.sqlite3`

### Important Current Operational State

The old Solix BLE/TLV path was dropping on its own, and telemetry auto-recovery was making it worse by restarting `solix-monitor.service` and `bluetooth.service` near the `45s` stale threshold.

That interaction was stabilized by:

- setting `TELEMETRY_SOLIX_AUTO_RECOVER=0`
- deploying a narrower reconnect path in `solix_monitor.py`

Observed reconnect improvement after that deploy:

- old reconnect path: often `40s+`
- stabilized reconnect path: about `12-14s` in observed live cycles

But:

- TLV drops still happen
- the transport problem is improved, not solved
- do not re-enable aggressive telemetry-driven recovery blindly
- stale threshold is now `90s`, not `45s`

### Current Verified Live History State

Verified directly on the host:

- `/telemetry/history` returns:
  - `source = "sqlite_history"`
  - `bin_statistic = "median"`
- the SQLite DB contains fresh Solix rows with timestamps near current wall time
- recent 24h bins are aligned to wall-clock minutes such as:
  - `2026-03-28T20:06:00+00:00`
  - `2026-03-28T20:07:00+00:00`
  - `2026-03-28T20:08:00+00:00`

So the branch is no longer just a partial SQLite prototype; the median history path is live.

## Current BLE Transport State

The current deployed Solix TLV transport is:

- raw `BleakClient`
- targeted `find_device_by_address(...)` scan path
- explicit first-packet timeout
- explicit packet-idle timeout
- bounded process reset after repeated failures
- forced BlueZ `StartNotify` for the Solix notify characteristic by default

Why `StartNotify` is now forced:

- upstream Bleak switched Linux to prefer `AcquireNotify`
- the live Solix characteristic supports `AcquireNotify`
- upstream Bleak issue `#1885` documents a BlueZ failure mode where `AcquireNotify` can end in `Unexpected EOF` and immediate disconnect
- that failure class is close enough to the observed Solix outage to justify forcing `StartNotify`

Current state of that change:

- deployed through the bootstrap script
- journal confirms `notify_mode=StartNotify`
- packet flow is currently healthy
- not yet burn-in validated

## Current Truthfulness Contract

The live Solix deployment should now behave like this:

- when Solix is live:
  - `watts_is_live = true`
  - `power_measurement_kind = "wall-total"`
  - live Solix wall/solar fields are present
- when Solix is stale or disconnected:
  - `watts_is_live = false`
  - telemetry falls back to component-load estimation
  - last-known `solix_soc_pct` is still preserved
  - stale live wall/solar fields are cleared

This split is intentional:

- battery SOC is stable enough to keep during short reconnect windows
- live wall-power and solar input must not be shown once the Solix packet stream is stale

## Importer Status

Importer file:

- `ops/scripts/import_history_csv_to_sqlite.py`

Purpose:

- one-time or repeatable import of legacy history files into SQLite
- supports both:
  - `*.csv`
  - `*.csv.gz`

The importer was already run against the live host paths:

- Solix source dir: `/opt/bartleby/solix-monitor/logs`
- vLLM source dir: `/opt/bartleby/vllm-metrics`
- DB target: `/opt/bartleby/solix-monitor/logs/history.sqlite3`

Observed final counts after import:

- `solix_events = 10695`
- `vllm_samples = 456066`

Observed importer output:

- `solix_rows_seen=10728`
- `vllm_rows_seen=456066`

The Solix mismatch is expected because rows are deduped by timestamp key.

## Important Host Fact: No gzip Rotation Currently Exists

The host currently has more than 24h of history but no `.gz` files in:

- `/opt/bartleby/solix-monitor/logs`
- `/opt/bartleby/vllm-metrics`

Reason:

- these are app-generated daily CSV files
- there is no active logrotate rule compressing them
- `/etc/logrotate.conf` has global `compress` commented out
- there is no relevant `logrotate.d` config for these app history dirs

Even so, the importer now supports `.csv.gz` if another host or future archive uses it.

## Deployment Risks That Were Found And Fixed In Code

### 1. solix-monitor import failure

Problem:

- deployed `solix_monitor.py` imported `history_store`
- but bootstrap originally copied only `solix_monitor.py`

Fix in branch:

- bootstrap scripts now also copy `ops/history_store.py` into the installed `solix-monitor` workdir

### 2. Shared DB permissions

Problem:

- `bartleby-stack.service` runs as `root`
- `solix-monitor.service` runs as `ubuntu`
- both need to write the same SQLite DB

Fix in branch:

- `ops/history_store.py` forces DB, `-wal`, and `-shm` files to mode `0666`

This is pragmatic, not elegant. It was added specifically to avoid cross-user write failures on this host layout.

## What Has Been Validated

Validated locally in this branch:

- `py_compile` for:
  - `ops/history_store.py`
  - `ops/scripts/power_telemetry.py`
  - `ops/services/solix-monitor/solix_monitor.py`
  - `ops/scripts/import_history_csv_to_sqlite.py`
- shell syntax checks for:
  - `ops/scripts/run-stack.sh`
  - `ops/bootstrap/bootstrap_fresh_box.sh`
  - `ops/bootstrap/bootstrap_solix_monitor.sh`
- throwaway SQLite tests for:
  - raw inserts
  - CSV bootstrap
  - median payload generation
- `/opt`-style packaging test for `solix_monitor.py` + `history_store.py`
- live import into `/opt/bartleby/solix-monitor/logs/history.sqlite3`

Validated end-to-end enough to claim cutover:

- deployed `solix-monitor.service` is writing fresh rows into SQLite
- deployed `bartleby-stack.service` is serving `/telemetry/history` from SQLite
- deployed frontend JS matches repo and supports `bin_statistic`

## What A Fresh Agent Should Do Next

If continuing this work, do these in order:

1. Read [WORK_LOG_SOLIX_2026-03-28-1PM.md](/home/ubuntu/vllm_jetson/bartlebyGPT/WORK_LOG_SOLIX_2026-03-28-1PM.md).
2. Confirm the live Solix BLE path is still stable enough after the reconnect patch.
3. Check for regressions:
   - service startup/import failures
   - SQLite permission issues
   - history endpoint latency
   - missing or obviously wrong bins
4. If the endpoint ever falls back to legacy CSV again, diagnose that before making further architecture claims.

## Suggested Verification Commands

Check service status:

```bash
sudo systemctl status solix-monitor --no-pager -n 80
sudo systemctl status bartleby-stack.service --no-pager -n 80
```

Inspect live DB row counts:

```bash
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('/opt/bartleby/solix-monitor/logs/history.sqlite3')
for table in ('solix_events', 'vllm_samples'):
    print(table, conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
PY
```

Check live history payload:

```bash
curl -s http://127.0.0.1:18081/telemetry/history | python3 -m json.tool
```

Important fields to inspect:

- `source`
- `bin_statistic`
- `solix_rows_considered`
- `vllm_rows_considered`
- `history_24h.bin_seconds`
- `history_7d.bin_seconds`

## Important Limitations

Do not overclaim what the import recovered.

True statements:

- all currently available on-disk CSV history has been imported into SQLite
- future deployed Solix SQLite writes can be packet-level and no longer lossy

False statement:

- that we recovered old packet-level Solix history

We did not. Old missing BLE packets were discarded by the old 60s snapshot logger and are gone forever.

## If You Need To Re-run The Import

Command:

```bash
python3 /home/ubuntu/vllm_jetson/bartlebyGPT/ops/scripts/import_history_csv_to_sqlite.py \
  --db /opt/bartleby/solix-monitor/logs/history.sqlite3 \
  --solix-log-dir /opt/bartleby/solix-monitor/logs \
  --vllm-log-dir /opt/bartleby/vllm-metrics
```

It is safe to rerun because inserts are deduped by timestamp keys.
