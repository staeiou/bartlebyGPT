CONFIG = {
    "id": "solix-c300x",
    "label": "Anker Solix C300X DC",
    "http": {"host": "127.0.0.1", "port": 18082},
    "capacity_wh": 288,
    "cost_model": "solar-zero",
    "narrative_html": "<p>Single Solix BLE device reporting battery, solar input, and load.</p>",
    "channel_priority": {},
    "mode_channels": {
        "main": ["load.w"],
        "ups": [],
    },
    "sources": [
        {
            "id": "solix",
            "kind": "solix-c300x-ble",
            "role": "main",
            "reconnect_delay": 10,
            "stale_after_s": 30,
            "capabilities": {"solar_measured": False, "solix_pass_through_solar": True},
            "mac": "SET_IN_LOCAL_CONFIG",
        },
    ],
}

