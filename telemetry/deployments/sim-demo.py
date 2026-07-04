CONFIG = {
    "id": "sim-demo",
    "label": "Simulated demo",
    "http": {"host": "127.0.0.1", "port": 18083},
    "capacity_wh": 1280,
    "cost_model": "solar-zero",
    "stale_after_s": 3,
    "narrative_html": "<p>Hardware-free telemetry demo.</p>",
    "sources": [
        {
            "kind": "simulated",
            "role": "main",
            "reconnect_delay": 5,
            "capabilities": {"solar_measured": True},
            "values": {"battery.soc_pct": 87, "solar.input_w": 40, "load.w": 12},
            "interval": 1,
            "emit_for": 5,
        },
        {
            "kind": "simulated",
            "role": "ups",
            "reconnect_delay": 5,
            "capabilities": {"solar_measured": False},
            "values": {"ups.soc_pct": 95, "ups.voltage_v": 12.2},
            "interval": 1,
            "emit_for": None,
        },
    ],
}
