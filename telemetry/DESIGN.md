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
- capacity, cost model, and narrative copy
- source list
- each source id, kind, role, reconnect delay, stale window, capabilities, and
  device parameters
- channel priority when more than one device can report the same quantity
- which fresh channels imply `main` or `ups` power-source mode

Drivers own reusable device mechanics:

- protocol parsing
- register math
- unit conversion
- emitted channel names
- blocking I/O adaptation

Drivers must not choose deployment behavior. If a value may differ by site,
battery, bus, address, port, stale policy, reconnect policy, channel priority,
mode rule, or UI capability, it belongs in the deployment `CONFIG`.

## Device Composition

The system must support both all-in-one devices and split-device deployments:

- Solix C300X BLE: one BLE device reports battery, solar, and load.
- Victron SmartSolar BLE + JBD BMS BLE: MPPT reports PV/load; BMS reports
  battery state.
- Victron SmartSolar BLE + Victron SmartShunt BLE: MPPT reports PV/load;
  SmartShunt reports battery state.
- Victron VE.Direct USB + Victron SmartShunt BLE: serial MPPT reports PV/load;
  SmartShunt reports battery state.

Because multiple devices can emit the same canonical channel, readings are stored
by `(channel, source_id)`. The HTTP payload resolves one value per channel using
the deployment's `channel_priority`; if no priority is configured for a channel,
the freshest non-stale reading wins.

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

The runtime derives `power_source_mode` from configured channel lists:

- `main`: any channel in `mode_channels.main` resolves fresh
- `ups`: no fresh main channel, but any channel in `mode_channels.ups` resolves fresh
- `unknown`: neither path has fresh data

This handles both UPS-only deployments and a normal deployment whose main power
feed went stale.

## Current Slice

Implemented:

- pure-data deployment loader and registry
- channel registry and snapshot
- HTTP payload
- source supervision
- per-source freshness
- source-aware channel resolution
- simulated driver
- VE.Direct driver
- INA219 driver
- pure-data recipes for Solix, Victron MPPT + JBD, Victron MPPT + SmartShunt,
  VE.Direct MPPT + SmartShunt, and simulated transition testing

Not implemented yet:

- history writer
- BLE source drivers (placeholder classes validate config and declare channels,
  but raise if run)
- vLLM/load source driver
- nginx/systemd cutover
- frontend capability-driven rendering
