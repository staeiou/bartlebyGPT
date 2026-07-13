from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from telemetry.core.deployment import power_source_mode
from telemetry.core.history import BatteryHistoryWriter, build_battery_history_row
from telemetry.core.http import build_payload, build_sensor_power_payload
from telemetry.core.reading import Reading, Snapshot
from telemetry.core.registry import ConfigError, deployment_from_config, load_deployment
from telemetry.drivers.victron_smartshunt_ble import VictronSmartShuntBleDriver
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

    def test_smartshunt_deployment_resets_bluetooth_after_repeated_failures(self):
        deployment = load_deployment("telemetry/deployments/victron-mppt-usb-smartshunt.py")
        smartshunt = next(source for source in deployment.sources if source.id == "smartshunt")
        self.assertEqual(smartshunt.reset_after_failures, 3)

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
        snapshot.update(Reading("battery.charge_w", 28, now, "main_sim"))
        snapshot.update(Reading("battery.soc_pct", 87, now, "main_sim"))
        snapshot.update(Reading("battery.voltage_v", 12.8, now, "main_sim"))
        snapshot.update(Reading("battery.current_a", -0.9, now, "main_sim"))
        payload = build_sensor_power_payload(deployment, snapshot, now=now)
        self.assertEqual(payload["value"], 12)
        self.assertEqual(payload["battery_soc_pct"], 87)
        self.assertEqual(payload["battery_solar_input_w"], 40)
        self.assertEqual(payload["battery_total_input_w"], 28)
        self.assertEqual(payload["battery_voltage_mv"], 12800.0)
        self.assertEqual(payload["battery_net_current_ma"], -900.0)
        self.assertEqual(payload["solix_soc_pct"], 87)
        self.assertEqual(payload["victron_reading_ts"], now)

    def test_battery_history_writer_preserves_existing_sqlite_contract(self):
        deployment = load_deployment("telemetry/deployments/sim-demo.py")
        snapshot = Snapshot()
        now = time.time()
        snapshot.update(Reading("load.w", 12, now, "main_sim"))
        snapshot.update(Reading("solar.input_w", 40, now, "main_sim"))
        snapshot.update(Reading("battery.charge_w", 28, now, "main_sim"))
        snapshot.update(Reading("battery.soc_pct", 87, now, "main_sim"))
        snapshot.update(Reading("battery.voltage_v", 12.8, now, "main_sim"))
        snapshot.update(Reading("battery.current_a", -0.9, now, "main_sim"))

        row = build_battery_history_row(deployment, snapshot, now=now)
        self.assertIsNotNone(row)
        self.assertEqual(row["load_w"], 12)
        self.assertEqual(row["solar_input_w"], 40)
        self.assertEqual(row["charge_w"], 28)
        self.assertEqual(row["soc_pct"], 87)

        with TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "history.sqlite3")
            writer = BatteryHistoryWriter(db_path)
            writer.record(row)

            import sqlite3

            with sqlite3.connect(db_path) as conn:
                saved = conn.execute(
                    """
                    SELECT load_w, charge_w, solar_input_w, soc_pct, voltage_mv
                    FROM battery_events
                    WHERE reading_ts_ms = ?
                    """,
                    (int(round(now * 1000.0)),),
                ).fetchone()
        self.assertEqual(saved, (12.0, 28.0, 40.0, 87.0, 12800.0))

    def test_vedirect_emits_load_w_from_voltage_and_load_current(self):
        emitted = []

        class Ctx:
            emit = emitted.append

        driver = VeDirectDriver(port="/dev/null", baud=19200)
        driver._emit(Ctx(), {"V": "12800", "I": "1500", "IL": "750"})
        by_channel = {reading.channel: reading.value for reading in emitted}
        self.assertEqual(by_channel["battery.charge_w"], 19.2)
        self.assertEqual(by_channel["load.current_a"], 0.75)
        self.assertEqual(by_channel["load.w"], 9.6)

    def test_smartshunt_ignores_non_instant_readout_advertisements(self):
        emitted = []

        class Ctx:
            emit = emitted.append

            class log:
                @staticmethod
                def debug(*_args):
                    pass

        class Parsed:
            def get_soc(self):
                return 97.8

            def get_voltage(self):
                return 13.59

            def get_current(self):
                return 4.939

            def get_temperature(self):
                return None

            def get_model_name(self):
                return "SmartShunt"

        class Parser:
            def parse(self, payload):
                if not payload.startswith(b"\x10"):
                    raise AssertionError("non-Instant-Readout payload should not be parsed")
                return Parsed()

        driver = VictronSmartShuntBleDriver(
            mac="ff8b0d49c0c0",
            mac_env=None,
            encryption_key="00" * 16,
            encryption_key_env=None,
            nominal_ah=100,
            advertisement_timeout=30,
        )
        ignored = driver._parse_and_emit(
            Ctx(),
            Parser(),
            {737: bytes.fromhex("02f6a54bde9e33eb0d03d5555f348f32448ad66025a2")},
        )
        parsed = driver._parse_and_emit(
            Ctx(),
            Parser(),
            {737: bytes.fromhex("100239c002d64fe9e2b0390d0ab33a44aa7a2c24534ce9")},
        )
        by_channel = {reading.channel: reading.value for reading in emitted}
        self.assertFalse(ignored)
        self.assertTrue(parsed)
        self.assertEqual(by_channel["battery.soc_pct"], 97.8)
        self.assertEqual(by_channel["battery.voltage_v"], 13.59)
        self.assertEqual(by_channel["battery.current_a"], 4.939)


if __name__ == "__main__":
    unittest.main()
