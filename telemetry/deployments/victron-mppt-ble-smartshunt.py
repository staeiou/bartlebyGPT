CONFIG = {
    "id": "victron-mppt-ble-smartshunt",
    "label": "Victron SmartSolar BLE + SmartShunt BLE",
    "http": {"host": "127.0.0.1", "port": 18082},
    "capacity_wh": 1280,
    "cost_model": "solar-zero",
    "narrative_html": "<p>Victron MPPT BLE provides solar/load; SmartShunt BLE provides battery state.</p>",
    "channel_priority": {
        "battery.voltage_v": ["smartshunt", "mppt-ble"],
        "battery.current_a": ["smartshunt", "mppt-ble"],
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
            "id": "smartshunt",
            "kind": "victron-smartshunt-ble",
            "role": "main",
            "reconnect_delay": 10,
            "stale_after_s": 30,
            "capabilities": {"battery_soc_measured": True},
            "mac": "SET_IN_LOCAL_CONFIG",
            "encryption_key": "SET_IN_LOCAL_CONFIG",
        },
    ],
}

