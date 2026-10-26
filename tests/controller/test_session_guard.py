"""Transactional XInput restoration tests using an in-memory server; no real input."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "controller"))
import nintekkit_controller as mapper  # noqa: E402


class FakeXInput:
    def __init__(self):
        self.devices = {
            2: {"name": "Virtual core pointer", "role": "pointer", "master": True, "attachment": 3},
            3: {"name": "Virtual core keyboard", "role": "keyboard", "master": True, "attachment": 2},
            12: {"name": mapper.NAME, "role": "pointer", "master": False, "attachment": 2},
            11: {"name": mapper.NAME + " (keys)", "role": "keyboard", "master": False, "attachment": 3},
        }
        # Deliberately include an originally disabled output flag, so restoration
        # cannot pass by blindly enabling the desktop defaults.
        self.flags = {"Generate Mouse Events": 1, "Generate Key Events": 0}
        self.buttons, self.axes, self.enabled = 18, 4, 1
        self.calls = []
        self.fail_after_float = None
        self.fail_flag_once = None
        self.ignore_flag = None

    def __call__(self, *arguments):
        args = tuple(str(value) for value in arguments)
        self.calls.append(args)
        if args[:2] == ("list", "--id-only"):
            return "\n".join(str(number) for number, device in self.devices.items()
                             if device["name"] == args[2])
        if args[:2] == ("list", "--name-only"):
            return self.device(args[2])["name"]
        if args[:2] == ("list", "--short"):
            device = self.device(args[2])
            attachment = device["attachment"]
            if device["master"]:
                role = "master " + device["role"] + " (" + str(attachment) + ")"
            elif attachment is None:
                role = "floating slave"
            else:
                role = "slave " + device["role"] + " (" + str(attachment) + ")"
            return device["name"] + "\tid=" + args[2] + "\t[" + role + "]"
        if args[0] == "list-props":
            self.device(args[1])
            values = {"Device Enabled": self.enabled, "Buttons": self.buttons,
                      "Axes": self.axes, **self.flags}
            return "\n".join("\t" + name + " (" + str(200 + number) + "):\t" + str(value)
                             for number, (name, value) in enumerate(values.items()))
        if args[0] == "float":
            number = int(args[1])
            self.device(number)["attachment"] = None
            if number == self.fail_after_float:
                self.fail_after_float = None
                raise mapper.ControllerError("simulated uncertain float failure")
            return ""
        if args[0] == "set-prop":
            self.device(args[3])
            name, value = args[4], int(args[5])
            if name == self.fail_flag_once:
                self.fail_flag_once = None
                raise mapper.ControllerError("simulated flag restoration failure")
            if name != self.ignore_flag:
                self.flags[name] = value
            return ""
        if args[0] == "reattach":
            self.device(args[2])
            self.device(args[1])["attachment"] = int(args[2])
            return ""
        raise AssertionError("Unexpected fake XInput command: " + repr(args))

    def device(self, number):
        try:
            return self.devices[int(number)]
        except KeyError as error:
            raise mapper.ControllerError("device disappeared") from error

    def mutations(self):
        return [call for call in self.calls if call[0] in ("float", "set-prop", "reattach")]


class SessionGuardTests(unittest.TestCase):
    def setUp(self):
        self.xinput = FakeXInput()
        self.guard = mapper.SessionGuard(self.xinput)

    def assert_restored(self):
        self.assertEqual(self.xinput.devices[12]["attachment"], 2)
        self.assertEqual(self.xinput.devices[11]["attachment"], 3)
        self.assertEqual(self.xinput.flags, {"Generate Mouse Events": 1, "Generate Key Events": 0})
        self.assertEqual(self.guard.touched, [])
        self.assertFalse(self.guard.flags_dirty)

    def test_discovery_is_read_only_and_snapshots_exact_original_state(self):
        self.assertEqual(self.xinput.mutations(), [])
        self.assertEqual([(d.id, d.role, d.master, d.master_name) for d in self.guard.devices],
                         [(12, "pointer", 2, "Virtual core pointer"),
                          (11, "keyboard", 3, "Virtual core keyboard")])
        self.assertEqual(self.guard.flags, {"Generate Mouse Events": 1, "Generate Key Events": 0})

    def test_success_restores_both_attachments_and_changed_capture_flags(self):
        self.guard.float_all()
        self.assertIsNone(self.xinput.devices[12]["attachment"])
        self.assertIsNone(self.xinput.devices[11]["attachment"])
        self.guard.check()
        self.xinput.flags.update({"Generate Mouse Events": 0, "Generate Key Events": 1})
        self.guard.restore()
        self.assert_restored()
        mutations = self.xinput.mutations()[:]
        self.guard.restore()
        self.assertEqual(self.xinput.mutations(), mutations)

    def test_uncertain_second_float_failure_rolls_back_both_devices(self):
        self.xinput.fail_after_float = 11
        with self.assertRaisesRegex(mapper.ControllerError, "uncertain float"):
            self.guard.float_all()
        self.assert_restored()
        self.assertIn(("reattach", "11", "3"), self.xinput.mutations())
        self.assertIn(("reattach", "12", "2"), self.xinput.mutations())

    def test_recycled_unrelated_id_is_never_modified_during_restore(self):
        self.guard.float_all()
        self.xinput.devices[11]["name"] = "Unrelated keyboard"
        self.xinput.calls.clear()
        self.guard.restore()
        self.assertNotIn(("reattach", "11", "3"), self.xinput.mutations())
        self.assertEqual(self.xinput.devices[12]["attachment"], 2)
        self.assertIsNone(self.xinput.devices[11]["attachment"])

    def test_external_reattachment_fails_closed(self):
        self.guard.float_all()
        self.xinput.devices[12]["attachment"] = 2
        with self.assertRaisesRegex(mapper.ControllerError, "reattached"):
            self.guard.check()
        self.guard.restore()
        self.assert_restored()

    def test_attachment_changed_before_acquisition_is_not_overwritten(self):
        self.xinput.devices[4] = {"name": "Other master pointer", "role": "pointer",
                                  "master": True, "attachment": 3}
        self.xinput.devices[12]["attachment"] = 4
        with self.assertRaises(mapper.ControllerError):
            self.guard.float_all()
        self.assertEqual(self.xinput.devices[12]["attachment"], 4)
        self.assertEqual(self.xinput.devices[11]["attachment"], 3)
        self.assertEqual(self.xinput.mutations(), [])

    def test_flag_failure_remains_retryable_after_attachments_are_restored(self):
        self.guard.float_all()
        self.xinput.flags["Generate Key Events"] = 1
        self.xinput.fail_flag_once = "Generate Key Events"
        with self.assertRaisesRegex(mapper.ControllerError, "flag restoration"):
            self.guard.restore()
        self.assertTrue(self.guard.flags_dirty)
        self.assertEqual(self.guard.touched, [])
        self.guard.restore()
        self.assert_restored()

    def test_silent_flag_write_failure_is_detected_by_readback(self):
        self.guard.float_all()
        self.xinput.flags["Generate Key Events"] = 1
        self.xinput.ignore_flag = "Generate Key Events"
        with self.assertRaisesRegex(mapper.ControllerError, "not confirmed"):
            self.guard.restore()
        self.assertTrue(self.guard.flags_dirty)
        self.xinput.ignore_flag = None
        self.guard.restore()
        self.assert_restored()

    def test_changed_master_identity_is_not_used_and_can_be_retried(self):
        self.guard.float_all()
        self.xinput.devices[2]["name"] = "Unrelated master pointer"
        self.xinput.calls.clear()
        with self.assertRaisesRegex(mapper.ControllerError, "master identity"):
            self.guard.restore()
        self.assertNotIn(("reattach", "12", "2"), self.xinput.mutations())
        self.assertEqual([device.id for device in self.guard.touched], [12])
        self.xinput.devices[2]["name"] = "Virtual core pointer"
        self.guard.restore()
        self.assert_restored()

    def test_ambiguous_controller_identity_and_wrong_capabilities_are_rejected(self):
        self.xinput.devices[13] = dict(self.xinput.devices[12])
        with self.assertRaisesRegex(mapper.ControllerError, "exactly one"):
            mapper.SessionGuard(self.xinput)
        del self.xinput.devices[13]
        self.xinput.buttons = 19
        with self.assertRaisesRegex(mapper.ControllerError, "capabilities"):
            mapper.SessionGuard(self.xinput)
        self.assertEqual(self.xinput.mutations(), [])


if __name__ == "__main__":
    unittest.main()
