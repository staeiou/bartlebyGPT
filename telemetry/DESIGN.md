# Telemetry Runtime

This directory is the rebuild path for the power and battery telemetry stack.

The hard rule is:

> One deployment is defined by one pure-data `CONFIG` file.

Deployment files live in `telemetry/deployments/`. They may contain comments,
literal values, and one top-level `CONFIG = {...}` assignment. They may not
import classes, call helpers, read environment variables, or rely on runtime
defaults for deployment behavior. `telemetry/core/registry.py` parses them with
`ast.literal_eval` to enforce that boundary.

## Ownership

Deployment config owns all choices for a deployment:

- deployment id and label
- HTTP host and port
- capacity, cost model, stale window, and narrative copy
- source list
- each source kind, role, reconnect delay, capabilities, and device parameters

Drivers own reusable device mechanics:

- protocol parsing
- register math
- unit conversion
- emitted channel names
- blocking I/O adaptation

Drivers must not choose deployment behavior. If a value may differ by site,
battery, bus, address, port, stale policy, reconnect policy, or UI capability,
it belongs in the deployment `CONFIG`.

## Runtime Shape

Every driver emits `Reading(channel, value, ts)` objects. The runtime stores the
latest reading per channel in a `Snapshot`, derives capabilities from fresh
channels and source roles, and serves one JSON payload:

```json
{
  "schema_version": 1,
  "deployment": {"id": "...", "label": "..."},
  "capabilities": {"power_source_mode": "main"},
  "channels": {"battery.soc_pct": {"value": 87, "unit": "%"}}
}
```

The browser should eventually render from `deployment`, `capabilities`, and
`channels`, not from a hardcoded profile id table.

## Roles

Sources have one configured role:

- `main`: primary battery, PV, or wall-meter feed for the deployment
- `ups`: internal backup battery for the compute device
- `meter`: compute-side or auxiliary meter that does not determine power source

The runtime derives `power_source_mode`:

- `main`: any `main` source has a fresh reading
- `ups`: no fresh `main` source, but an `ups` source is fresh
- `unknown`: neither path has fresh data

This handles both UPS-only deployments and a normal deployment whose main power
feed went stale.

## Current Slice

Implemented:

- pure-data deployment loader and registry
- channel registry and snapshot
- HTTP payload
- source supervision
- simulated driver
- VE.Direct driver
- INA219 driver
- simulated deployment proving `main -> ups` stale transition

Not implemented yet:

- history writer
- BLE source drivers
- vLLM/load source driver
- nginx/systemd cutover
- frontend capability-driven rendering

