# README_SQL.md

This document is a handoff for the SQLite-backed telemetry history rearchitecture on branch `sqlite-history-store`.

It is written for a fresh coding agent. Do not assume this work is deployed just because the code exists in this branch.

## Current Status

- Branch: `sqlite-history-store`
- Repo: `/home/ubuntu/vllm_jetson/bartlebyGPT`
- This branch contains the SQLite-backed history path now serving live history.
- Current active Jetson deployment is the LFP/Victron profile (`jetson-solar-lfp`), not Solix.
- A live SQLite DB **has** already been populated from existing CSV history at:
  - LFP/Victron active path: `/opt/bartleby/lfp-monitor/logs/history.sqlite3`
  - Solix reference path: `/opt/bartleby/solix-monitor/logs/history.sqlite3`
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
  - `/telemetry/history` now returns `bin_statistic = "mixed"`
  - `/telemetry/history` now returns `battery_bin_statistic = "median"`
  - `/telemetry/history` now returns `vllm_bin_statistic = "mean"`
- The current BLE transport remains an active investigation; the history stack is live, but transport burn-in is still required.

## Current Retention And Archive State (2026-07-14)

The active LFP/Victron production DB keeps full-resolution history for roughly 14 days:

- `/opt/bartleby/lfp-monitor/logs/history.sqlite3`
- `battery_events` are minute-scale on this deployment
- `vllm_samples` are second-scale in production

Older history is archived by month under:

- `/opt/bartleby/lfp-monitor/logs/history-archive/history-YYYY-MM.sqlite3`

Archive DBs contain:

- `battery_events` at existing resolution
- `vllm_minute_bins`, downsampled from raw vLLM samples to 1-minute rounded mean
  `avg_requests_running` / `avg_requests_waiting`, plus `sum_requests_completed`
  and `sample_count`

Initial backfill was run from `2026-04-04`, creating:

- `history-2026-04.sqlite3`
- `history-2026-05.sqlite3`
- `history-2026-06.sqlite3`

Manual archive/prune command:

```bash
sudo python3 /home/ubuntu/vllm_jetson/bartlebyGPT/ops/scripts/archive_history.py \
  --db /opt/bartleby/lfp-monitor/logs/history.sqlite3 \
  --archive-dir /opt/bartleby/lfp-monitor/logs/history-archive \
  --start 2026-04-04 \
  --retention-days 14 \
  --drop-before-start
```

Use `--dry-run` before changing ranges. The command is idempotent for archive DBs.
It does not run automatically yet. Deleting rows does not shrink `history.sqlite3`
until `VACUUM`; do not run `VACUUM` casually on the Jetson.

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

The current target architecture is:

- the active battery monitor (`lfp-monitor` on `jetson-solar-lfp`, `solix-monitor` on Solix profiles) writes SQLite battery rows
- `power_telemetry.py` writes one SQLite row per vLLM telemetry sample
- `/telemetry/history` reads from SQLite and builds aligned history bins:
  - battery/load/SOC series use median bins
  - vLLM concurrent/queued series use SQL-side one-bin means rounded to integers
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
- emit explicit aggregation metadata:
  - `bin_statistic: "mixed"` for SQLite history
  - `battery_bin_statistic: "median"`
  - `vllm_bin_statistic: "mean"`

Tables:

- `battery_events` (migrated from `solix_events` — auto-renamed on first connect)
  - primary key: `reading_ts_ms`
  - stores `ts`, `load_w`, `charge_w`, `soc_pct`, plus extra battery fields
  - on LFP/Victron deployments, also stores Victron fields:
    `victron_model_name`, `victron_charge_state`, `victron_charger_error`,
    `victron_battery_voltage_v`, `victron_battery_charging_current_a`,
    signed `victron_battery_power_w`, `victron_external_device_load_a`,
    `victron_yield_today_wh`, and `victron_manufacturer_id`
- `vllm_samples`
  - primary key: `sample_ts_ms`
  - stores `ts`, `requests_running`, `requests_waiting`, `requests_completed`
- monthly archive DBs also contain `vllm_minute_bins`
  - primary key: `minute_ts_ms`
  - stores rounded one-minute mean running/waiting request counts
  - stores `sum_requests_completed`, `sample_count`, and source time bounds

## Files Changed On This Branch

Core code:

- `ops/history_store.py`
- `ops/scripts/power_telemetry.py`
- `ops/services/solix-monitor/solix_monitor.py`
- `docs/app/power.js`

Deployment/bootstrap/config:

- `ops/scripts/run-stack.sh`
- `ops/bootstrap/bootstrap_fresh_box.sh`
- `ops/templates/systemd.battery-monitor.service.tmpl`
- `ops/config/profiles/api-jetson.env`
- `ops/config/profiles/rpi4-llama-live.env`

Utility script:

- `ops/scripts/import_history_csv_to_sqlite.py`
  - can import `victron-adv-YYYY-MM-DD.csv` rows with `--victron-log-dir`
  - Victron backfill is discrete-only: it writes actual advertisement timestamps and
    does not interpolate missing current, voltage, load, or solar samples
- `ops/scripts/archive_history.py`
  - manual production history archive/prune tool
  - keeps production DB at roughly 14 days full resolution
  - archives old vLLM samples into monthly one-minute bins

## Behavior Of The New Code

### power_telemetry.py

Key changes:

- reads `TELEMETRY_HISTORY_DB_PATH`
- instantiates `SQLiteHistoryStore` when configured
- writes vLLM sample rows into SQLite in `log_vllm_metrics()`
- builds history from SQLite in `compute_history_payload()`
- falls back to legacy CSV history if SQLite has no Solix rows yet
- emits aggregation fields:
  - SQLite path: `bin_statistic = "mixed"`, `battery_bin_statistic = "median"`,
    `vllm_bin_statistic = "mean"`
  - legacy CSV fallback: all three statistic fields are `"mean"`
- `?refresh=1` on `/telemetry/history` now rebuilds synchronously unless another
  refresh is already in progress; normal requests still serve the cache
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
- concurrent/queued label text is derived from `state.powerHistory.vllm_bin_statistic`
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
- branch code is now deployed enough that the live history endpoint is serving SQLite-backed mixed aggregation history

What is live now:

- the active LFP/Victron SQLite DB is `/opt/bartleby/lfp-monitor/logs/history.sqlite3`
- Solix reference deployments use `/opt/bartleby/solix-monitor/logs/history.sqlite3`
- the LFP/Victron production DB has been pruned to roughly 14 days full resolution
- older LFP/Victron history has been archived under `/opt/bartleby/lfp-monitor/logs/history-archive/`
- the live Solix service has since been redeployed for BLE stabilization work:
  - reconnect path now uses targeted lookup and `bleak-retry-connector`
  - telemetry auto-recovery is currently disabled in the active Jetson profile
- the live history endpoint is now verified serving SQLite history
- the deployed frontend already understands `vllm_bin_statistic` labels and matches the repo version

Live DB paths:

- active LFP/Victron: `/opt/bartleby/lfp-monitor/logs/history.sqlite3`
- Solix reference: `/opt/bartleby/solix-monitor/logs/history.sqlite3`

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
  - `bin_statistic = "mixed"`
  - `battery_bin_statistic = "median"`
  - `vllm_bin_statistic = "mean"`
- the SQLite DB contains fresh battery/vLLM rows with timestamps near current wall time
- recent 24h bins are aligned to wall-clock minutes such as:
  - `2026-03-28T20:06:00+00:00`
  - `2026-03-28T20:07:00+00:00`
  - `2026-03-28T20:08:00+00:00`

So the branch is no longer just a partial SQLite prototype; the mixed aggregation
SQLite history path is live.

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
- the battery monitor service may run as another user on Solix profiles
- both telemetry and the battery monitor need to write the same SQLite DB

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
  - `ops/scripts/archive_history.py`
- shell syntax checks for:
  - `ops/scripts/run-stack.sh`
  - `ops/bootstrap/bootstrap_fresh_box.sh`
- throwaway SQLite tests for:
  - raw inserts
  - CSV bootstrap
  - median payload generation
- `/opt`-style packaging test for `solix_monitor.py` + `history_store.py`
- live import into `/opt/bartleby/solix-monitor/logs/history.sqlite3`
- archive/prune rehearsal on a copied production DB
- production archive/prune backfill from `2026-04-04` on `2026-07-14`

Validated end-to-end enough to claim cutover:

- deployed battery monitor service is writing fresh rows into SQLite
- deployed `bartleby-stack.service` is serving `/telemetry/history` from SQLite
- deployed frontend JS matches repo and supports `vllm_bin_statistic`

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
sudo systemctl status lfp-monitor --no-pager -n 80
sudo systemctl status solix-monitor --no-pager -n 80
sudo systemctl status bartleby-stack.service --no-pager -n 80
```

Inspect live DB row counts:

```bash
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('/opt/bartleby/lfp-monitor/logs/history.sqlite3')
for table in ('battery_events', 'vllm_samples'):
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
- `battery_bin_statistic`
- `vllm_bin_statistic`
- `battery_rows_considered`
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
