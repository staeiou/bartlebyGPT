# TELEMETRY_REFACTOR_NOTES_20260326

This document is a recovery and refactor note for the BartlebyGPT power telemetry stack as of 2026-03-26.

Its purpose is:

- preserve the actual reconstructed state after a long debugging session
- explain what failed, why the UI looked contradictory, and what was fixed
- record the remaining architectural problems so future work does not restart from zero

This file is intentionally direct and redundant in a few places. It is meant to survive context loss.

## Scope

This note covers:

- `ops/services/solix-monitor/solix_monitor.py`
- `ops/scripts/power_telemetry.py`
- `docs/app/power.js`
- `ops/bootstrap/bootstrap_fresh_box.sh`
- `ops/config/profiles/api-jetson.env`
- deployed runtime at `/opt/bartleby/solix-monitor/`

## Non-Negotiable Deployment Model

Development and edits must happen in repo:

- `/home/ubuntu/vllm_jetson/bartlebyGPT`

Runtime deployment artifacts live outside repo:

- `/opt/bartleby/solix-monitor/`
- `/var/www/bartlebygpt/`

Those runtime locations are deploy outputs, not source of truth.

For Solix monitor deployment, the intended idempotent apply path is:

```bash
cd /home/ubuntu/vllm_jetson/bartlebyGPT
sudo ./ops/bootstrap/bootstrap_fresh_box.sh \
  --profile api-jetson \
  --force-solix-monitor \
  --skip-cloudflared \
  --skip-inference-bootstrap \
  --skip-doctor
```

Important:

- `bartleby-stack.service` runs telemetry from repo.
- `solix-monitor.service` runs the deployed copy in `/opt`.
- Editing only repo `solix_monitor.py` does nothing until deployment copies it to `/opt`.
- Editing `/opt` directly is wrong because bootstrap can overwrite it.

## Runtime Topology

The live stack is:

```text
Solix BLE
  -> solix-monitor.service
     -> HTTP: 127.0.0.1:18082 /sensor/power
     -> CSV: /opt/bartleby/solix-monitor/logs/solix-YYYY-MM-DD.csv
  -> power_telemetry.py
     -> HTTP: 127.0.0.1:18081 /telemetry/power
     -> HTTP: 127.0.0.1:18081 /telemetry/history
     -> polls vLLM /load and /metrics
  -> frontend docs/app/power.js
     -> live display from /telemetry/power
     -> history charts from /telemetry/history
```

Three different truth layers exist:

- current Solix wall reading
- current in-memory telemetry state
- historical power rebuilt from CSV later

Those layers can diverge.

## Protocol State

At runtime on this box, Solix is using the plaintext TLV path, not the encrypted ECDH path.

Evidence:

- deployed monitor logs repeatedly show `Protocol: plaintext TLV`
- raw TLV packets are visible in `journalctl -u solix-monitor`

Repo `solix_monitor.py` now supports optional ECDH via SolixBLE, but current live traffic here is TLV.

## Original Symptom

Observed user symptom:

- on the history graph, charge and load stopped appearing after `2026-03-26 10:15 AM Pacific`
- SoC, concurrent, and queued continued to appear

That symptom was real in backend data, not just a frontend rendering problem.

## Reconstructed Failure Timeline

Absolute times:

- `2026-03-26 10:15 AM Pacific` = `2026-03-26 17:15 UTC`

What happened:

1. `solix-monitor` was receiving live TLV packets normally before the failure window.
2. Around `17:14:41 UTC`, BLE connectivity dropped.
3. The older deployed monitor wrote repeated stale/disconnected rows to CSV.
4. The Solix device then remained undiscoverable for an extended interval.
5. `solix-monitor` kept retrying and logging `Device ... was not found`.
6. Fresh packets resumed later, after service restart/reconnect.

Concrete anomaly found in `/opt/bartleby/solix-monitor/logs/solix-2026-03-26.csv`:

- timestamp `2026-03-26T17:14:41.109530+00:00` appears 34 times
- those rows have `ble_connected=False`
- same stale Solix values are repeated
- next genuinely fresh row is `2026-03-26T17:53:05.529772+00:00`

This means there was a large freshness gap in wall-power history even though queue metrics continued.

## Root Cause Of The History Break

The history builder in `power_telemetry.py` reads Solix CSV rows and bins them by timestamp.

Relevant behavior:

- `read_solix_rows()` dedups rows by exact timestamp
- `build_binned_window()` computes per-bin averages for `load_w` and `charge_w`
- `soc_pct` is carried forward if a bin has no new SoC sample
- vLLM running/waiting metrics are merged from separate logs

That means:

- 34 repeated rows with one identical timestamp collapse to a single point
- after that one point, there are no fresh Solix rows until `17:53 UTC`
- bins in between have no `load_w`
- bins in between have no `charge_w`
- bins still have carried-forward `soc_pct`
- bins still have `avg_concurrent`
- bins still have `avg_waiting`

This exactly explains the user-visible chart behavior:

- load disappears
- charge disappears
- SoC continues
- concurrent continues
- queued continues

## Why The Website Still Said "Live From Wall"

The frontend live dot is controlled by `payload.watts_is_live` from `/telemetry/power`.

`docs/app/power.js`:

- `elements.wattsLiveDot.hidden = !payload.watts_is_live`

The frontend does not independently validate freshness. It trusts telemetry.

`power_telemetry.py` marks the sample as live wall power when:

- backend is Solix / ESPHome shim
- wall reading is accepted
- `power_measurement_kind = "wall-total"`
- `watts_is_live = true`

The key problem is that historical freshness and current live state were separate:

- history comes from CSV
- live state comes from current in-memory telemetry payload

So the site could truthfully show the current payload as wall-live while the history builder still had a gap, or worse, show stale wall-live if telemetry preserved an old wall sample too long.

Important remaining issue:

`power_telemetry.py` still does not fully clear prior wall-live state on total power-read failure. If a read fails, `last_error` is updated, but prior wall state can persist in memory unless replaced by a successful fallback sample. This is a remaining truthfulness bug.

## Repo Changes Already Made During The Prior Session

The prior session modified these repo files:

- `ops/services/solix-monitor/solix_monitor.py`
- `ops/scripts/power_telemetry.py`
- `ops/config/profiles/api-jetson.env`
- `ops/bootstrap/bootstrap_fresh_box.sh`

### 1. `ops/services/solix-monitor/solix_monitor.py`

Changes made:

- optional SolixBLE import so missing package does not hard-fail startup
- TLV raw packet logging restored (`INFO RAW ...`)
- ECDH fallback logic made tolerant when SolixBLE is unavailable
- CSV logger hardened:
  - skip writes if `ble_connected=False`
  - skip duplicate or non-increasing timestamps

Why this matters:

- this directly prevents the exact CSV corruption pattern that produced the 34 duplicated disconnected rows

### 2. `ops/scripts/power_telemetry.py`

Changes made:

- added `TELEMETRY_SOLIX_STALE_SECONDS`
- added `TELEMETRY_SOLIX_AUTO_RECOVER`
- added `TELEMETRY_SOLIX_RECOVERY_COOLDOWN_SECONDS`
- added `TELEMETRY_SOLIX_RECOVERY_ESCALATE_EVERY`
- added stale-read detection based on `solix_reading_ts`
- added auto-recovery attempts:
  - restart `solix-monitor.service`
  - periodically escalate to restarting `bluetooth.service` and `solix-monitor.service`
- propagated backend failure details into `last_error`
- corrected `power_reading_ts` on non-wall fallback path so component-load timestamps use current time

Why this matters:

- telemetry can now detect stale Solix data instead of trusting it forever
- telemetry can now try to recover upstream service failures automatically

### 3. `ops/config/profiles/api-jetson.env`

Changes made:

- explicit resilience env vars for the stale detection / recovery path

### 4. `ops/bootstrap/bootstrap_fresh_box.sh`

Changes made:

- `install_solix_monitor()` updated so deployment is handled through bootstrap
- Solix monitor venv install now tolerates unavailable `SolixBLE`
- bootstrap still installs BLE dependencies needed for TLV mode

Why this matters:

- repo changes to `solix_monitor.py` can be applied idempotently through the intended deployment path

## Deployment Verification Performed

This was explicitly verified during reconstruction:

- repo Solix monitor file and deployed `/opt` file were hash-identical after bootstrap
- `solix-monitor.service` runs `/opt/bartleby/solix-monitor/solix_monitor.py`
- `bartleby-stack.service` runs `run-stack.sh` from repo

This confirms:

- telemetry edits in repo affect runtime directly after stack restart
- Solix monitor edits require deployment into `/opt`

## Current Hardening Status

The stack is improved, but not fully hardened.

### What is now hardened

- duplicate disconnected Solix CSV spam is blocked by the new CSV logger guard
- non-increasing timestamp rows are blocked
- stale Solix readings are detected in telemetry
- telemetry can attempt recovery restarts automatically

### What is not yet fully hardened

- telemetry may still preserve stale wall-live state in memory on full power-read failure
- frontend trusts `watts_is_live` without checking age
- history has no first-class representation of disconnect/gap intervals
- history silently carries SoC forward, which can visually imply continuity even when power data is absent
- the system still has three truth layers instead of one unified freshness contract

## Current Component Interaction Notes

### `solix-monitor`

Responsibilities:

- BLE connection management
- packet parsing
- current HTTP payload on `/sensor/power`
- periodic CSV snapshots

Key risk:

- if BLE disappears, monitor may still have stale in-memory state until refreshed or restarted

### `power_telemetry.py`

Responsibilities:

- polls Solix HTTP shim
- polls vLLM metrics
- derives unified current telemetry state
- builds historical bins from CSV + vLLM logs

Key risk:

- current-state freshness contract is still weaker than it should be

### frontend `power.js`

Responsibilities:

- current live display
- modal debug information
- history chart rendering

Key risk:

- "live" UI is too trusting of backend booleans

## Why This Still Feels Messy

Because it is not one telemetry system. It is three systems glued together:

- live wall-power transport
- current display state
- historical reconstruction

The current patches defend against the specific outage pattern, but they do not yet simplify the model.

## Recommended Refactor Direction

The clean solution is not another ad hoc patch. It is a freshness-first contract.

### A. Make freshness explicit in current telemetry payload

Add and use fields such as:

- `power_source_state`: `live`, `stale`, `fallback`, `unavailable`
- `power_age_sec`
- `solix_age_sec`
- `solix_connected`
- `solix_backend_ok`

Frontend should show the live dot only for `power_source_state=live`.

### B. Clear stale wall state aggressively

On failed Solix read or stale upstream sample:

- do not preserve old wall-live display as if it were fresh
- explicitly downgrade current state to stale or fallback

### C. Record history gaps explicitly

CSV/history contract should expose disconnect windows instead of only null values.

Desired behavior:

- history payload should mark bins as `gap=true` or similar
- frontend should render a visible break or outage annotation

### D. Unify current and historical truth around source timestamps

The source timestamp of the actual Solix packet should drive:

- live state
- stale detection
- history continuity decisions

### E. Recovery should be visible, not only automatic

If auto-recovery triggers, expose that in telemetry payload and UI/debug panel so operators can see:

- stale detected
- recovery attempted
- service restarted
- bluetooth restarted

## Practical Reconstruction Facts To Re-Use Later

These were verified and should not need rediscovery next time:

- The current box is using plaintext TLV, not encrypted ECDH, at runtime.
- The graph symptom after `2026-03-26 10:15 Pacific` was caused by a real history gap, not just chart rendering.
- The exact corruption pattern was repeated disconnected CSV rows with identical timestamp.
- The repo already contains a fix to block that specific CSV failure mode.
- The telemetry repo code already contains stale-read detection and service auto-recovery logic.
- The remaining major issue is truthfulness of current wall-live state under failure, not just CSV logging.

## Files To Inspect First In Any Future Recovery

If this problem reappears, inspect these first:

- `ops/services/solix-monitor/solix_monitor.py`
- `ops/scripts/power_telemetry.py`
- `docs/app/power.js`
- `/opt/bartleby/solix-monitor/logs/solix-YYYY-MM-DD.csv`
- `journalctl -u solix-monitor`
- `journalctl -u bartleby-stack`
- `curl http://127.0.0.1:18082/sensor/power`
- `curl http://127.0.0.1:18081/telemetry/power`
- `curl http://127.0.0.1:18081/telemetry/history?seconds=7200`

## Bottom Line

The stack is not in the same broken state it was when the outage happened.

The deployed monitor now has the specific CSV anti-poisoning guard that was missing when the history hole was created.

But the telemetry/UI contract is still architecturally messy:

- current live truth can still be overstated
- history gap semantics are implicit instead of explicit
- three layers can still disagree

That is why this should be treated as a refactor problem, not just a bugfix problem.
