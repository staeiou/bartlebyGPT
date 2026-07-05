from __future__ import annotations

import time
import unittest
from pathlib import Path

from telemetry.core.deployment import power_source_mode
from telemetry.core.http import build_payload, build_sensor_power_payload
from telemetry.core.reading import Reading, Snapshot
from telemetry.core.registry import ConfigError, deployment_from_config, load_deployment
from telemetry.drivers.victron_vedirect import VeDirectDriver


class TelemetryConfigTests(unittest.TestCase):
    def test_loads_pure_data_sim_deployment(self):
        deployment = load_deployment("telemetry/deployments/sim-demo.py")
        self.assertEqual(deployment.id, "sim-demo")
        self.assertEqual(deployment.http.host, "127.0.0.1")
        self.assertEqual(deployment.http.port, 18083)
        self.assertEqual([source.kind for source in deployment.sources], ["simulated", "simulated"])

    def test_all_deployment_recipes_load(self):
        paths = sorted(Path("telemetry/deployments").glob("*.py"))
        self.assertGreaterEqual(len(paths), 1)
        for path in paths:
            with self.subTest(path=str(path)):
                deployment = load_deployment(path)
                self.assertTrue(deployment.id)
                self.assertGreater(len(deployment.sources), 0)

    def test_rejects_missing_explicit_deployment_key(self):
        with self.assertRaises(ConfigError):
            deployment_from_config(
                {
                    "id": "bad",
                    "label": "Bad",
                    "http": {"host": "127.0.0.1", "port": 1},
                    "capacity_wh": 1,
                    "cost_model": "none",
                    "narrative_html": "",
                    "channel_priority": {},
                    "sources": [],
                }
            )

    def test_main_to_ups_mode_uses_fresh_roles(self):
        deployment = load_deployment("telemetry/deployments/sim-demo.py")
        snapshot = Snapshot()
        now = time.time()
        snapshot.update(Reading("battery.soc_pct", 80, now - 10, "main_sim"))
        snapshot.update(Reading("ups.soc_pct", 95, now, "ups_sim"))
        self.assertEqual(power_source_mode(deployment, snapshot, now), "ups")

        snapshot.update(Reading("load.w", 12, now, "main_sim"))
        self.assertEqual(power_source_mode(deployment, snapshot, now), "main")

    def test_payload_is_capability_driven(self):
        deployment = load_deployment("telemetry/deployments/sim-demo.py")
        snapshot = Snapshot()
        now = time.time()
        snapshot.update(Reading("ups.soc_pct", 95, now, "ups_sim"))
        payload = build_payload(deployment, snapshot, now=now)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["deployment"]["id"], "sim-demo")
        self.assertEqual(payload["capabilities"]["power_source_mode"], "ups")
        self.assertIn("ups.soc_pct", payload["channels"])
        self.assertEqual(payload["channels"]["ups.soc_pct"]["source_id"], "ups_sim")

    def test_sensor_power_payload_preserves_legacy_contract(self):
        deployment = load_deployment("telemetry/deployments/sim-demo.py")
        snapshot = Snapshot()
        now = time.time()
        snapshot.update(Reading("load.w", 12, now, "main_sim"))
        snapshot.update(Reading("solar.input_w", 40, now, "main_sim"))
        snapshot.update(Reading("battery.soc_pct", 87, now, "main_sim"))
        snapshot.update(Reading("battery.voltage_v", 12.8, now, "main_sim"))
        snapshot.update(Reading("battery.current_a", -0.9, now, "main_sim"))
        payload = build_sensor_power_payload(deployment, snapshot, now=now)
        self.assertEqual(payload["value"], 12)
        self.assertEqual(payload["battery_soc_pct"], 87)
        self.assertEqual(payload["battery_solar_input_w"], 40)
        self.assertEqual(payload["battery_voltage_mv"], 12800.0)
        self.assertEqual(payload["battery_net_current_ma"], -900.0)
        self.assertEqual(payload["solix_soc_pct"], 87)
        self.assertEqual(payload["victron_reading_ts"], now)

    def test_vedirect_emits_load_w_from_voltage_and_load_current(self):
        emitted = []

        class Ctx:
            emit = emitted.append

        driver = VeDirectDriver(port="/dev/null", baud=19200)
        driver._emit(Ctx(), {"V": "12800", "IL": "750"})
        by_channel = {reading.channel: reading.value for reading in emitted}
        self.assertEqual(by_channel["load.current_a"], 0.75)
        self.assertEqual(by_channel["load.w"], 9.6)


if __name__ == "__main__":
    unittest.main()
