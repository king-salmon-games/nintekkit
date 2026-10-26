"""Context tests use private fixtures and fake outputs, never actual input."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/controller"))
import nintekkit_controller as mapper
from test_mapper import FakeBackend, event, initialize


class ContextMappingTests(unittest.TestCase):
    def ready(self, context="unknown", gui_class=None):
        backend = FakeBackend()
        state = mapper.MappingState(backend, context=context)
        if gui_class:
            state.set_context(context, gui_class)
        initialize(state)
        return state, backend

    def test_unknown_context_never_moves_types_jumps_or_mines(self):
        state, backend = self.ready()
        self.assertFalse(state.armed)
        for button in (0, 1, 2, 3, 7, 8, 9, 10, 14):
            state.handle(event(mapper.BUTTON, button, 1))
        for axis in range(4):
            state.handle(event(mapper.AXIS, axis, 32767))
        state.tick(0.05)
        self.assertEqual(backend.events, [])

    def test_a_jumps_in_world_and_selects_in_main_menu(self):
        state, backend = self.ready("world")
        state.handle(event(mapper.BUTTON, 1, 1))
        self.assertEqual(backend.keys, {"space"})
        self.assertEqual(backend.buttons, set())
        state.set_context("gui", "net.minecraft.client.gui.GuiMainMenu")
        self.assertFalse(state.armed)
        self.assertEqual(backend.keys, set())
        self.assertEqual(backend.buttons, set())
        # Held A must not become a GUI click across the transition.
        state.tick(0.05)
        self.assertEqual(backend.buttons, set())
        state.handle(event(mapper.BUTTON, 1, 0))
        self.assertTrue(state.armed)
        state.handle(event(mapper.BUTTON, 1, 1))
        self.assertEqual(backend.buttons, {1})
        self.assertEqual(backend.keys, set())

    def test_b_sneaks_in_world_and_backs_out_of_gui(self):
        state, backend = self.ready("world")
        state.handle(event(mapper.BUTTON, 0, 1))
        self.assertEqual(backend.keys, {"Shift_L"})
        state.handle(event(mapper.BUTTON, 0, 0))
        state.set_context("gui", "GuiInventory")
        state.handle(event(mapper.BUTTON, 0, 1))
        self.assertEqual(backend.keys, {"Escape"})

    def test_y_inventory_x_crafting_share_java_inventory_binding(self):
        state, backend = self.ready("world")
        for button in (2, 3):
            state.handle(event(mapper.BUTTON, button, 1))
            self.assertEqual(backend.keys, {"e"})
            state.handle(event(mapper.BUTTON, button, 0))

    def test_gui_x_types_nothing_y_closes_and_capture_is_gui_edge_only(self):
        from on_screen_keyboard import DryKeyboard
        messages = []
        backend = FakeBackend()
        keyboard = DryKeyboard(messages.append)
        state = mapper.MappingState(backend, context="gui", keyboard=keyboard)
        initialize(state)
        state.handle(event(mapper.BUTTON, 2, 1))
        self.assertEqual(backend.keys, set())
        state.handle(event(mapper.BUTTON, 2, 0))
        state.handle(event(mapper.BUTTON, 3, 1))
        self.assertEqual(backend.keys, {"Escape"})
        state.handle(event(mapper.BUTTON, 3, 0))
        state.handle(event(mapper.BUTTON, 4, 1))
        state.handle(event(mapper.BUTTON, 4, 1))
        self.assertEqual(len(messages), 1)
        state.handle(event(mapper.BUTTON, 4, 0))
        state.set_focus(False)
        state.handle(event(mapper.BUTTON, 4, 1))
        self.assertEqual(len(messages), 1)
        state.handle(event(mapper.BUTTON, 4, 0))
        state.set_focus(True)
        state.set_context("world")
        state.handle(event(mapper.BUTTON, 4, 1))
        self.assertEqual(len(messages), 1)
        state.handle(event(mapper.BUTTON, 4, 0))
        state.set_context("unknown")
        state.handle(event(mapper.BUTTON, 4, 1))
        self.assertEqual(len(messages), 1)

    def test_gui_left_stick_is_coarse_pointer_and_right_is_fine_without_wasd(self):
        state, backend = self.ready("gui", "GuiContainer")
        state.handle(event(mapper.AXIS, 0, 32767))
        state.tick(0.01)
        self.assertEqual(backend.events, [("motion", 7, 0)])
        self.assertEqual(backend.keys, set())
        state.handle(event(mapper.AXIS, 0, 0))
        state.handle(event(mapper.AXIS, 2, 32767))
        state.tick(0.01)
        self.assertEqual(backend.events[-1], ("motion", 2, 0))

    def test_gui_a_and_zr_share_mouse_hold_without_premature_release(self):
        state, backend = self.ready("gui", "GuiInventory")
        for button in (1, 8):
            state.handle(event(mapper.BUTTON, button, 1))
        state.handle(event(mapper.BUTTON, 1, 0))
        self.assertEqual(backend.buttons, {1})
        state.handle(event(mapper.BUTTON, 8, 0))
        self.assertEqual(backend.events, [("button", 1, True), ("button", 1, False)])

    def test_world_transition_cannot_turn_gui_held_a_or_trigger_into_mining(self):
        state, backend = self.ready("gui", "GuiInventory")
        state.handle(event(mapper.BUTTON, 1, 1))
        state.handle(event(mapper.BUTTON, 8, 1))
        state.set_context("world")
        self.assertEqual(backend.buttons, set())
        self.assertFalse(state.armed)
        state.tick(0.05)
        self.assertEqual(backend.buttons, set())
        state.handle(event(mapper.BUTTON, 1, 0))
        state.handle(event(mapper.BUTTON, 8, 0))
        self.assertTrue(state.armed)

    def test_gui_class_change_releases_drag_and_waits_for_neutral(self):
        state, backend = self.ready("gui", "GuiInventory")
        state.handle(event(mapper.BUTTON, 1, 1))
        state.set_context("gui", "GuiMachine")
        self.assertEqual(backend.buttons, set())
        self.assertFalse(state.armed)

    def test_stale_context_releases_all_and_recovery_requires_neutral(self):
        state, backend = self.ready("world")
        state.handle(event(mapper.BUTTON, 8, 1))
        state.handle(event(mapper.AXIS, 1, -32767))
        state.set_context("unknown")
        self.assertEqual((backend.keys, backend.buttons), (set(), set()))
        state.set_context("world")
        self.assertFalse(state.armed)

    def test_gui_jei_and_quick_transfer_extensions_do_not_leak_to_world(self):
        state, backend = self.ready("gui", "GuiInventory")
        for button, key in ((14, "r"), (15, "u"), (12, "Shift_L")):
            state.handle(event(mapper.BUTTON, button, 1))
            self.assertEqual(backend.keys, {key})
            state.handle(event(mapper.BUTTON, button, 0))
        state.handle(event(mapper.BUTTON, 9, 1))
        self.assertEqual(backend.keys, {"Control_L", "f"})
        state.handle(event(mapper.BUTTON, 9, 0))
        state.set_context("world")
        state.handle(event(mapper.BUTTON, 9, 1))
        self.assertEqual(backend.keys, {"Tab"})


class ContextReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "context.json"
        self.data = {"schema": 1, "pid": 123, "sequence": 1, "updated_epoch_ms": 100000,
                     "world_loaded": True, "gui_open": False, "gui_class": None}
        self.write()
        uid = self.path.stat().st_uid
        self.reader = mapper.ContextReader(self.path, 123, clock_ms=lambda: 100000, uid=uid, check_private=False)
    def tearDown(self): self.temporary.cleanup()
    def write(self): self.path.write_text(json.dumps(self.data), encoding="utf-8")
    def test_valid_world_main_menu_and_loading_are_distinct(self):
        self.assertEqual(self.reader.read().mode, "world")
        self.data.update(gui_open=True, gui_class="net.minecraft.client.gui.GuiMainMenu", world_loaded=False, sequence=2)
        self.write()
        self.assertEqual(self.reader.read(), mapper.ControlContext("gui", "net.minecraft.client.gui.GuiMainMenu"))
        self.data.update(gui_open=False, gui_class=None, sequence=3)
        self.write()
        self.assertEqual(self.reader.read().mode, "unknown")
    def test_missing_wrong_owner_schema_pid_and_nonboolean_flags_fail_closed(self):
        for key, value in (("schema", True), ("schema", 2), ("pid", 124), ("gui_open", 0), ("world_loaded", "true")):
            with self.subTest(key=key, value=value):
                old = self.data[key]
                self.data[key] = value
                self.write()
                self.assertEqual(self.reader.read().mode, "unknown")
                self.data[key] = old
        self.reader.uid += 1
        self.write()
        self.assertEqual(self.reader.read().mode, "unknown")
        self.path.unlink()
        self.assertEqual(self.reader.read().mode, "unknown")
    def test_stale_future_malformed_and_rolled_back_states_fail_closed(self):
        for stamp in (97499, 100251):
            self.data["updated_epoch_ms"] = stamp
            self.write()
            self.assertEqual(self.reader.read().mode, "unknown")
        self.data.update(updated_epoch_ms=100000, sequence=8)
        self.write()
        self.assertEqual(self.reader.read().mode, "world")
        self.data["sequence"] = 7
        self.write()
        self.assertEqual(self.reader.read().mode, "unknown")
        self.path.write_text('{"schema":')
        self.assertEqual(self.reader.read().mode, "unknown")
    def test_repeated_unchanged_sequence_is_allowed_until_heartbeat_expires(self):
        self.assertEqual(self.reader.read().mode, "world")
        self.assertEqual(self.reader.read().mode, "world")
        self.reader.clock_ms = lambda: 102501
        self.assertEqual(self.reader.read().mode, "unknown")
    def test_gui_class_validation_prevents_inconsistent_world_context(self):
        for gui, name in ((True, None), (True, ""), (True, "screen name"), (False, "GuiInventory")):
            self.data.update(gui_open=gui, gui_class=name)
            self.write()
            self.assertEqual(self.reader.read().mode, "unknown")


if __name__ == "__main__": unittest.main()
