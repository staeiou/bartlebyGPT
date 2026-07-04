CONFIG = {
    "id": "jetson-solar-lfp",
    "label": "Solar LFP (Jetson Orin Nano)",
    "http": {"host": "127.0.0.1", "port": 18082},
    "capacity_wh": 1280,
    "cost_model": "solar-zero",
    "stale_after_s": 90,
    "narrative_html": (
        "<p>Running an open, sovereign, on-the-box AI on off-grid solar. "
        "A Jetson Orin Nano on a Victron SmartSolar MPPT and 12V 100Ah LFP "
        "battery, with a UPS HAT as internal backup.</p>"
    ),
    "sources": [
        {
            "kind": "victron-vedirect",
            "role": "main",
            "reconnect_delay": 10,
            "capabilities": {"solar_measured": True},
            "port": "/dev/ttyUSB0",
            "baud": 19200,
        },
        {
            "kind": "ina219",
            "role": "ups",
            "reconnect_delay": 10,
            "capabilities": {"solar_measured": False},
            "bus": 7,
            "addr": 0x41,
            "interval": 10,
        },
    ],
}
