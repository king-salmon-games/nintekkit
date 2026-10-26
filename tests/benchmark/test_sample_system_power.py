"""Bounded, privacy-preserving power telemetry using synthetic sysfs/proc trees."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("sample_system", Path(__file__).resolve().parents[2] / "scripts/benchmark/sample_system.py")
sampler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sampler)


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


class PowerSamplerTests(unittest.TestCase):
    def test_literal_unknown_negative_current_and_zero_online_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field, value in {"type": "Battery", "status": "Unknown", "capacity": "87",
                                 "current_now": "-812448", "power_now": "-3171339", "temp": "330"}.items():
                write(root, "battery/" + field, value)
            write(root, "usb/type", "USB")
            write(root, "usb/online", "0")
            result = sampler.power_supplies(root)
            battery, usb = result["supplies"]
            self.assertEqual(battery["fields"], {"type": "Battery", "status": "Unknown", "capacity": 87,
                                                "current_now": -812448, "power_now": -3171339, "temp": 330})
            self.assertEqual(usb["fields"], {"type": "USB", "online": 0})
            self.assertNotIn("online", battery["fields"])
            self.assertFalse(result["truncated"])

    def test_missing_and_unreadable_values_are_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(sampler.power_supplies(root / "missing")["available"])
            write(root, "BAT0/type", "Battery")
            write(root, "BAT0/status", "Charging" * 100)
            write(root, "BAT0/current_now", "not a number")
            write(root, "BAT0/health", "private-value")
            write(root, "BAT0/energy_now", "1234567890123456789")
            self.assertEqual(sampler.power_supplies(root)["supplies"][0]["fields"], {"type": "Battery"})

    def test_identifying_names_and_unlisted_fields_never_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("joycon-aa-bb-cc-dd-ee-ff", "controller-AABBCCDDEEFF"):
                for field, value in {"type": "Battery", "capacity_level": "Full", "serial_number": "SERIAL-SECRET",
                                     "manufacturer": "PRIVATE-MANUFACTURER", "model_name": "PRIVATE-MODEL"}.items():
                    write(root, name + "/" + field, value)
            result = sampler.power_supplies(root)
            self.assertTrue(all(item["supply"] == "other" for item in result["supplies"]))
            output = json.dumps(result)
            for secret in ("aa-bb", "AABBCC", "SERIAL", "MANUFACTURER", "MODEL"):
                self.assertNotIn(secret, output)

    def test_supply_count_and_file_reads_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(sampler.MAX_POWER_SUPPLIES + 4):
                write(root, "BAT" + str(number) + "/type", "Battery")
            result = sampler.power_supplies(root)
            self.assertEqual(len(result["supplies"]), sampler.MAX_POWER_SUPPLIES)
            self.assertTrue(result["truncated"])
            path = root / "huge"
            path.write_bytes(b"x" * 100000)
            self.assertIsNone(sampler.bounded_text(path))

    def test_boot_state_is_validated_and_integrated_with_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc, system = root / "proc", root / "sys"
            boot = "837ea8e0-4147-42fd-8183-738183f8fded"
            write(proc, "sys/kernel/random/boot_id", boot)
            write(proc, "uptime", "298.37 900.50\n")
            write(proc, "meminfo", "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\n")
            write(proc, "vmstat", "pswpin 0\npswpout 1\n")
            write(system, "class/power_supply/usb/online", "0")
            result = sampler.snapshot(None, proc, system)
            self.assertEqual(result["boot"], {"boot_id": boot, "uptime_seconds": 298.37})
            self.assertEqual(result["power_supplies"]["supplies"][0]["fields"]["online"], 0)
            write(proc, "sys/kernel/random/boot_id", "private-invalid-id")
            write(proc, "uptime", "not-uptime")
            self.assertEqual(sampler.boot_state(proc), {"boot_id": None, "uptime_seconds": None})


if __name__ == "__main__":
    unittest.main()
