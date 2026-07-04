CONFIG = {
    "id": "jetson-solar-lfp",
    "label": "Solar LFP (Jetson Orin Nano)",
    "http": {"host": "127.0.0.1", "port": 18082},
    "capacity_wh": 1280,
    "cost_model": "solar-zero",
    "narrative_html": (
        "<p>Running an open, sovereign, on-the-box AI on off-grid solar. "
        "A Jetson Orin Nano on a Victron SmartSolar MPPT and 12V 100Ah LFP "
        "battery, with a UPS HAT as internal backup.</p>"
    ),
    "channel_priority": {
        "battery.voltage_v": ["mppt-usb"],
        "battery.current_a": ["mppt-usb"],
    },
    "mode_channels": {
        "main": ["load.w", "battery.voltage_v", "solar.input_w"],
        "ups": ["ups.soc_pct", "ups.power_w"],
    },
    "sources": [
        {
            "id": "mppt-usb",
            "kind": "victron-vedirect",
            "role": "main",
            "reconnect_delay": 10,
            "stale_after_s": 20,
            "capabilities": {"solar_measured": True},
            "port": "/dev/ttyUSB0",
            "baud": 19200,
        },
        {
            "id": "ups-hat",
            "kind": "ina219",
            "role": "ups",
            "reconnect_delay": 10,
            "stale_after_s": 30,
            "capabilities": {"solar_measured": False},
            "bus": 7,
            "addr": 0x41,
            "interval": 10,
        },
    ],
}
