"""Controller safety and behavior tests; these never inject real input."""
from __future__ import annotations

from pathlib import Path
import io
import re
import struct
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "controller"))
import nintekkit_controller as mapper  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.events = []
        self.keys = set()
        self.buttons = set()

    def key(self, name, down):
        self.events.append(("key", name, down))
        if down:
            self.keys.add(name)
        else:
            self.keys.discard(name)

    def button(self, number, down):
        self.events.append(("button", number, down))
        if down:
            self.buttons.add(number)
        else:
            self.buttons.discard(number)

    def motion(self, dx, dy):
        self.events.append(("motion", dx, dy))


def event(kind, number, value, initial=False):
    return mapper.JsEvent.parse(struct.pack("=IhBB", 123, value,
                                            kind | (mapper.INITIAL if initial else 0), number))


def initialize(state, held_buttons=(), axis_values=None):
    values = axis_values or [0] * 4
    for number, value in enumerate(values):
        state.handle(event(mapper.AXIS, number, value, True))
    for number in range(18):
        state.handle(event(mapper.BUTTON, number, int(number in held_buttons), True))


class EventTests(unittest.TestCase):
    def test_signed_axis_initial_flag_and_unsigned_timestamp(self):
        parsed = mapper.JsEvent.parse(struct.pack("=IhBB", 0xFFFFFFFF, -32768, 0x82, 3))
        self.assertEqual((parsed.time_ms, parsed.value, parsed.event_type, parsed.number,
                          parsed.initial), (0xFFFFFFFF, -32768, mapper.AXIS, 3, True))

    def test_invalid_length_type_and_button_value_rejected(self):
        for data in (b"", b"\x00" * 7, b"\x00" * 9,
                     struct.pack("=IhBB", 0, 0, 3, 0),
                     struct.pack("=IhBB", 0, 0, 0x80, 0),
                     struct.pack("=IhBB", 0, 2, mapper.BUTTON, 0)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                mapper.JsEvent.parse(data)

    def test_partial_reads_keep_event_boundaries(self):
        buffer = mapper.EventBuffer()
        first = struct.pack("=IhBB", 1, -23000, 0x82, 0)
        second = struct.pack("=IhBB", 2, 1, mapper.BUTTON, 8)
        self.assertEqual(buffer.feed(first[:3]), [])
        parsed = buffer.feed(first[3:] + second[:6])
        self.assertEqual(parsed, [mapper.JsEvent.parse(first)])
        self.assertEqual(buffer.feed(b""), [])
        self.assertEqual(buffer.feed(second[6:]), [mapper.JsEvent.parse(second)])


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.state = mapper.MappingState(self.backend, context="world")

    def ready(self):
        initialize(self.state)
        self.assertTrue(self.state.complete)
        self.assertTrue(self.state.neutral)
        self.assertTrue(self.state.armed)
        self.assertEqual(self.backend.events, [])

    def test_all_initial_observations_required_before_arming(self):
        for number in range(4):
            self.state.handle(event(mapper.AXIS, number, 0, True))
        for number in range(17):
            self.state.handle(event(mapper.BUTTON, number, 0, True))
        self.assertFalse(self.state.complete)
        self.assertFalse(self.state.armed)
        self.state.tick(1)
        self.assertEqual(self.backend.events, [])
        self.state.handle(event(mapper.BUTTON, 17, 0, True))
        self.assertTrue(self.state.armed)
        self.assertEqual(self.backend.events, [])

    def test_initially_held_controls_require_neutral_without_injection(self):
        initialize(self.state, held_buttons={0, 5, 8}, axis_values=[23000, 0, 32767, 0])
        self.assertTrue(self.state.complete)
        self.assertFalse(self.state.armed)
        self.state.tick(1)
        for number in (0, 5, 8):
            self.state.handle(event(mapper.BUTTON, number, 0))
        for number in (0, 2):
            self.state.handle(event(mapper.AXIS, number, 0))
        self.assertTrue(self.state.armed)
        self.assertEqual(self.backend.events, [])
        self.state.handle(event(mapper.BUTTON, 1, 1))
        self.assertEqual(self.backend.keys, {"space"})

    def test_diagonal_movement_holds_between_ticks_and_releases_at_deadzone(self):
        self.ready()
        self.state.handle(event(mapper.AXIS, 0, 23170))
        self.state.handle(event(mapper.AXIS, 1, -23170))
        self.assertEqual(self.backend.keys, {"d", "w"})
        calls = self.backend.events[:]
        for _ in range(100):
            self.state.tick(1 / 120)
        self.assertEqual(self.backend.events, calls)
        self.state.handle(event(mapper.AXIS, 0, 8000))
        self.state.handle(event(mapper.AXIS, 1, -8000))
        self.assertEqual(self.backend.keys, set())

    def test_all_physical_button_actions(self):
        self.ready()
        keys = {0: {"Shift_L"}, 1: {"space"}, 2: {"e"}, 3: {"e"},
                9: {"Tab"}, 10: {"Escape"}, 12: {"Control_L"}, 16: {"1"}, 17: {"9"}}
        for number, expected in keys.items():
            with self.subTest(button=number):
                self.state.handle(event(mapper.BUTTON, number, 1))
                self.assertEqual(self.backend.keys, expected)
                self.state.handle(event(mapper.BUTTON, number, 0))
                self.assertEqual(self.backend.keys, set())
        for number, mouse in ((7, 3), (8, 1), (13, 2)):
            self.state.handle(event(mapper.BUTTON, number, 1))
            self.assertEqual(self.backend.buttons, {mouse})
            self.state.handle(event(mapper.BUTTON, number, 0))
            self.assertEqual(self.backend.buttons, set())

    def test_wheel_emits_one_pulse_per_press_edge(self):
        self.ready()
        for number, wheel in ((5, 4), (6, 5)):
            self.backend.events.clear()
            self.state.handle(event(mapper.BUTTON, number, 1))
            self.state.handle(event(mapper.BUTTON, number, 1))
            self.state.tick(0.05)
            self.state.handle(event(mapper.BUTTON, number, 0))
            self.assertEqual(self.backend.events, [("button", wheel, True),
                                                   ("button", wheel, False)])
        self.assertEqual(self.backend.buttons, set())

    def test_overlapping_bindings_keep_shared_keys_held(self):
        self.ready()
        self.state.set_context("gui", "GuiInventory")
        for number in (0, 10):
            self.state.handle(event(mapper.BUTTON, number, 1))
        self.state.handle(event(mapper.BUTTON, 0, 0))
        self.assertEqual(self.backend.keys, {"Escape"})
        self.state.handle(event(mapper.BUTTON, 10, 0))
        self.assertEqual(self.backend.events, [("key", "Escape", True),
                                               ("key", "Escape", False)])

    def test_modifier_chord_order_and_independent_gui_transfer_modifier(self):
        self.ready()
        self.state.set_context("gui", "GuiInventory")
        self.state.handle(event(mapper.BUTTON, 12, 1))
        self.state.handle(event(mapper.BUTTON, 9, 1))
        self.state.handle(event(mapper.BUTTON, 9, 0))
        self.assertEqual(self.backend.keys, {"Shift_L"})
        self.state.handle(event(mapper.BUTTON, 12, 0))
        self.assertEqual(self.backend.events, [("key", "Shift_L", True), ("key", "Control_L", True),
                                               ("key", "f", True), ("key", "f", False),
                                               ("key", "Control_L", False), ("key", "Shift_L", False)])

    def test_camera_speed_is_time_based_and_stalls_are_bounded(self):
        self.ready()
        self.state.handle(event(mapper.AXIS, 2, 32767))
        for _ in range(10):
            self.state.tick(0.01)
        self.assertEqual(sum(e[1] for e in self.backend.events if e[0] == "motion"), 90)
        self.backend.events.clear()
        self.state.tick(10)
        self.assertEqual(self.backend.events, [("motion", 45, 0)])
        self.state.handle(event(mapper.AXIS, 2, -32767))
        self.state.tick(0.01)
        self.assertEqual(self.backend.events[-1], ("motion", -9, 0))

    def test_camera_neutral_discards_fractional_motion_and_does_not_drift(self):
        self.ready()
        self.state.handle(event(mapper.AXIS, 2, 32767))
        self.state.tick(0.00075)
        self.state.handle(event(mapper.AXIS, 2, 0))
        self.state.tick(0.01)
        self.state.handle(event(mapper.AXIS, 2, 32767))
        self.state.tick(0.00075)
        self.assertEqual(self.backend.events, [])
        self.state.handle(event(mapper.AXIS, 2, 6500))
        for _ in range(10):
            self.state.tick(0.05)
        self.assertEqual(self.backend.events, [])

    def test_focus_and_pause_release_then_require_neutral(self):
        for setter in (self.state.set_focus, self.state.set_active):
            initialize(self.state)
            self.state.handle(event(mapper.AXIS, 1, -23000))
            self.state.handle(event(mapper.BUTTON, 8, 1))
            setter(False)
            self.assertFalse(self.state.armed)
            self.assertEqual(self.backend.keys, set())
            self.assertEqual(self.backend.buttons, set())
            setter(True)
            self.assertFalse(self.state.armed)
            calls = self.backend.events[:]
            self.state.tick(1)
            self.assertEqual(self.backend.events, calls)
            self.state.handle(event(mapper.AXIS, 1, 0))
            self.state.handle(event(mapper.BUTTON, 8, 0))
            self.assertTrue(self.state.armed)

    def test_queue_resync_releases_then_rebuilds_complete_state(self):
        self.ready()
        self.state.handle(event(mapper.BUTTON, 8, 1))
        self.state.handle(event(mapper.AXIS, 1, -23000))
        self.state.handle(event(mapper.AXIS, 0, 0, True))
        self.assertFalse(self.state.armed)
        self.assertFalse(self.state.complete)
        self.assertEqual(self.backend.keys, set())
        self.assertEqual(self.backend.buttons, set())
        for number in range(1, 4):
            self.state.handle(event(mapper.AXIS, number, 0, True))
        for number in range(18):
            self.state.handle(event(mapper.BUTTON, number, 0, True))
        self.assertTrue(self.state.armed)
        self.assertTrue(self.state.complete)

    def test_second_init_batch_while_waiting_neutral_cannot_arm_from_stale_state(self):
        initialize(self.state, held_buttons={0})
        self.assertTrue(self.state.complete)
        self.assertFalse(self.state.armed)
        # Simulate overflow resync before the initially held B was released.
        # A new held ZR arrives after the old B's release in the new batch.
        initialize(self.state, held_buttons={8})
        self.assertTrue(self.state.complete)
        self.assertFalse(self.state.armed)
        self.assertEqual(self.backend.events, [])
        self.state.handle(event(mapper.BUTTON, 8, 0))
        self.assertTrue(self.state.armed)
        self.assertEqual(self.backend.events, [])

    def test_release_all_is_idempotent_and_releases_chord_backwards(self):
        self.ready()
        self.state.set_context("gui", "GuiInventory")
        self.state.handle(event(mapper.BUTTON, 9, 1))
        self.state.handle(event(mapper.BUTTON, 8, 1))
        self.backend.events.clear()
        self.state.release_all()
        expected = [("button", 1, False), ("key", "f", False),
                    ("key", "Control_L", False)]
        self.assertEqual(self.backend.events, expected)
        self.state.release_all()
        self.assertEqual(self.backend.events, expected)

    def test_changed_capabilities_fail_closed(self):
        self.ready()
        self.state.handle(event(mapper.BUTTON, 0, 1))
        with self.assertRaisesRegex(ValueError, "capabilities"):
            self.state.handle(mapper.JsEvent(123, 1, mapper.BUTTON, 18))
        self.assertFalse(self.state.armed)
        self.assertEqual(self.backend.keys, set())

    def test_failed_output_press_remains_tracked_for_cleanup(self):
        class FailingPress(FakeBackend):
            def key(self, name, down):
                if down:
                    raise RuntimeError("uncertain key press")
                super().key(name, down)
        backend = FailingPress()
        state = mapper.MappingState(backend, context="world")
        initialize(state)
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            state.handle(event(mapper.BUTTON, 1, 1))
        state.release_all()
        self.assertEqual(backend.events, [("key", "space", False)])

    def test_cleanup_retries_failed_release_and_still_releases_other_holds(self):
        class FailingRelease(FakeBackend):
            fail_once = True

            def key(self, name, down):
                if name == "f" and not down and self.fail_once:
                    self.fail_once = False
                    raise RuntimeError("uncertain key release")
                super().key(name, down)
        backend = FailingRelease()
        state = mapper.MappingState(backend, context="gui")
        initialize(state)
        state.handle(event(mapper.BUTTON, 9, 1))
        with self.assertRaisesRegex(mapper.ControllerError, "release"):
            state.release_all()
        self.assertEqual(backend.keys, {"f"})
        state.release_all()
        self.assertEqual(backend.keys, set())


class FocusTests(unittest.TestCase):
    def connection(self, titles=None, pids=None, focus=500):
        class FakeX:
            parents = {500: 400, 400: 1}

            def XGetInputFocus(self, display, window, revert):
                window._obj.value = focus
                return 1

            def XFetchName(self, display, window, title):
                value = (titles or {400: b"Minecraft 1.12.2"}).get(window)
                if value is None:
                    return 0
                title._obj.value = value
                return 1

            def XQueryTree(self, display, window, root, parent, children, count):
                root._obj.value = 1
                parent._obj.value = self.parents.get(window, 1)
                count._obj.value = 0
                return 1

            def XFree(self, data):
                return 0

        connection = object.__new__(mapper.XConnection)
        connection.x, connection.display = FakeX(), None
        connection.window_pid = lambda window: (pids or {}).get(window)
        return connection

    def test_matching_ancestor_title_and_pid_accepts_child_focus(self):
        connection = self.connection(pids={400: 123})
        self.assertTrue(connection.focused(re.compile(r"^Minecraft\b"), pid=123))

    def test_mismatched_or_unknown_pid_fails_closed(self):
        for pids in ({400: 456}, {}):
            with self.subTest(pids=pids):
                connection = self.connection(pids=pids)
                self.assertFalse(connection.focused(re.compile(r"^Minecraft\b"), pid=123))

    def test_optional_window_id_must_be_on_focused_ancestor_chain(self):
        connection = self.connection(pids={400: 123})
        pattern = re.compile(r"^Minecraft\b")
        self.assertTrue(connection.focused(pattern, pid=123, window_id=400))
        self.assertTrue(connection.focused(pattern, pid=123, window_id=500))
        self.assertFalse(connection.focused(pattern, pid=123, window_id=999))

    def test_pid_and_window_id_do_not_bypass_game_title_requirement(self):
        connection = self.connection(titles={400: b"Terminal"}, pids={400: 123})
        self.assertFalse(connection.focused(re.compile(r"^Minecraft\b"), pid=123, window_id=400))

    def test_title_only_read_mode_and_pseudo_focus(self):
        pattern = re.compile(r"^Minecraft\b")
        self.assertTrue(self.connection().focused(pattern))
        for focus in (0, 1):
            self.assertFalse(self.connection(focus=focus).focused(pattern))

    def test_pid_mode_handles_fullscreen_window_recreation_without_stale_id(self):
        titles, pids = {400: b"Tekkit 2"}, {400: 123}
        connection = self.connection(titles=titles, pids=pids)
        pattern = re.compile(mapper.DEFAULT_WINDOW_REGEX)
        self.assertTrue(connection.focused(pattern, pid=123))
        titles.clear()
        pids.clear()
        titles[600], pids[600] = b"Tekkit 2", 123
        connection.x.parents = {700: 600, 600: 1}
        connection.x.XGetInputFocus = lambda display, window, revert: setattr(window._obj, "value", 700) or 1
        self.assertTrue(connection.focused(pattern, pid=123))
        self.assertFalse(connection.focused(pattern, pid=123, window_id=400))


class CliTests(unittest.TestCase):
    def test_dry_run_rejects_command_without_launching_anything(self):
        with patch.object(sys, "platform", "linux"), \
                patch.object(sys, "argv", ["mapper", "--dry-run", "--command", "unwanted"]), \
                patch.object(mapper, "run") as run, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                mapper.main()
            self.assertEqual(error.exception.code, 2)
            run.assert_not_called()

    def test_live_input_requires_explicit_game_pid_or_observed_window(self):
        with patch.object(sys, "platform", "linux"), \
                patch.object(sys, "argv", ["mapper", "--run"]), \
                patch.object(mapper, "run") as run, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                mapper.main()
            self.assertEqual(error.exception.code, 2)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
