CONFIG = {
    "id": "victron-mppt-ble-jbd-bms",
    "label": "Victron SmartSolar BLE + JBD BMS",
    "http": {"host": "127.0.0.1", "port": 18082},
    "capacity_wh": 1280,
    "cost_model": "solar-zero",
    "narrative_html": "<p>Victron MPPT BLE provides solar/load; JBD BMS BLE provides battery state.</p>",
    "channel_priority": {
        "battery.voltage_v": ["jbd-bms", "mppt-ble"],
        "battery.current_a": ["jbd-bms", "mppt-ble"],
    },
    "mode_channels": {
        "main": ["load.w", "solar.input_w", "battery.voltage_v"],
        "ups": [],
    },
    "sources": [
        {
            "id": "mppt-ble",
            "kind": "victron-mppt-ble",
            "role": "main",
            "reconnect_delay": 10,
            "stale_after_s": 30,
            "capabilities": {"solar_measured": True},
            "mac": "SET_IN_LOCAL_CONFIG",
            "encryption_key": "SET_IN_LOCAL_CONFIG",
        },
        {
            "id": "jbd-bms",
            "kind": "jbd-bms-ble",
            "role": "main",
            "reconnect_delay": 10,
            "stale_after_s": 90,
            "capabilities": {"battery_soc_measured": True},
            "mac": "SET_IN_LOCAL_CONFIG",
            "poll_interval": 60,
        },
    ],
}

