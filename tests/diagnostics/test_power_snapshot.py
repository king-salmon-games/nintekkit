"""Power evidence filtering and I/O bounds without a real journal or game."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("power_snapshot", Path(__file__).resolve().parents[2] / "scripts/diagnostics/power_snapshot.py")
power = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(power)


class PowerTests(unittest.TestCase):
    def test_journal_categorizes_without_private_messages(self):
        records = [
            {"MESSAGE": "PM: suspend entry (deep)", "_TRANSPORT": "kernel", "__REALTIME_TIMESTAMP": "1700000000000000"},
            {"MESSAGE": "Out of memory: Killed process 123 (private-player) address=198.51.100.10 token=secret", "_SYSTEMD_UNIT": "private-name.service"},
            {"MESSAGE": "battery is critically low for private-user", "PRIORITY": "3"},
            {"MESSAGE": "ordinary private text", "_SYSTEMD_UNIT": "private-name.service"},
        ]
        result = power.parse_journal("\n".join(json.dumps(record) for record in records))
        text = json.dumps(result)
        self.assertEqual(result["categoryCounts"], {"suspend_entry": 1, "out_of_memory": 1, "battery_critical": 1})
        self.assertEqual(result["lastCategorizedEvents"][1]["source"], "other")
        for private in ("private", "198.51.100.10", "token", "secret", "Killed process"):
            self.assertNotIn(private, text)

    def test_journal_is_bounded_and_rejects_non_string_messages(self):
        records = [json.dumps({"MESSAGE": ["thermal critical"]}), "{broken", json.dumps({"MESSAGE": "x" * 66000})]
        records += [json.dumps({"MESSAGE": "PM: suspend exit"})] * 500
        result = power.parse_journal("\n".join(records))
        self.assertEqual(result["invalidRecords"], 3)
        self.assertEqual(result["categoryCounts"]["resume"], power.JOURNAL_LINES - 3)
        self.assertEqual(len(result["lastCategorizedEvents"]), 40)

    def test_application_exit_does_not_claim_system_shutdown(self):
        records = [{"MESSAGE": "Shutting down", "_SYSTEMD_UNIT": "sddm.service", "_PID": "400"},
                   {"MESSAGE": "Shutting down", "_SYSTEMD_UNIT": ["untrusted"]},
                   {"MESSAGE": "Reached target System Power Off", "_PID": "1"}]
        result = power.parse_journal("\n".join(json.dumps(record) for record in records))
        self.assertEqual(result["categoryCounts"], {"shutdown": 1})

    def test_no_persistent_history_is_explicitly_unavailable(self):
        metadata = {"available": True, "returnCode": 1, "diagnosticCategories": ["no_persistent_journal", "boot_history_unavailable"]}
        with mock.patch.object(power, "command", return_value=(metadata, "")):
            result = power.journals()["previousBoot"]
        self.assertTrue(result["commandAvailable"])
        self.assertFalse(result["available"])
        self.assertFalse(result["historyAvailable"])

    def test_sysfs_reads_only_allowlisted_fields_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            battery = root / "BAT0"
            battery.mkdir()
            for name, value in {"type": "Battery", "capacity": "15", "status": "Discharging", "serial_number": "PRIVATE", "health": "secret-user", "temp": "310"}.items():
                (battery / name).write_text(value)
            result = power.power_supplies(root)
            self.assertEqual(result["supplies"][0]["fields"], {"capacity": 15, "status": "Discharging", "temp": 310, "type": "Battery"})
            self.assertNotIn("PRIVATE", json.dumps(result))

    @unittest.skipUnless(sys.platform == "linux", "selectors on pipe descriptors require Linux")
    def test_command_output_and_deadline_are_bounded(self):
        meta, text = power.command([sys.executable, "-c", "print('x'*100000)"], limit=128)
        self.assertEqual(len(text), 128)
        self.assertTrue(meta["outputTruncated"])
        meta, text = power.command([sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.05)
        self.assertTrue(meta["timedOut"])
        meta, text = power.command([sys.executable, "-c", "import sys;sys.stdout.write('x'*128)"], limit=128)
        self.assertEqual(len(text), 128)
        self.assertFalse(meta["outputTruncated"])
        meta, text = power.command([sys.executable, "-c", "import sys;sys.stderr.write('PRIVATE'*100000)"], limit=128)
        self.assertTrue(meta["outputTruncated"])
        self.assertTrue(meta["stderrPresent"])
        self.assertNotIn("PRIVATE", json.dumps(meta))


if __name__ == "__main__":
    unittest.main()
