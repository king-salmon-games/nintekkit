"""Fake window ownership/transient tests; never contact X11 or Onboard."""
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/controller"))
import onboard_stacking as stacking
from window_presentation import PresentationError


class Backend:
    root = 900
    def __init__(self, original=900):
        self.windows = {50: {"owner": 123, "title": "Tekkit 2", "class": ("Minecraft", "Minecraft"), "dock": False, "geometry": (1280, 720)},
                        60: {"owner": 456, "title": "Onboard", "class": ("onboard", "Onboard"), "dock": True, "geometry": (1280, 240), "parent": original},
                        61: {"owner": 456, "title": "Onboard", "class": ("onboard", "Onboard"), "dock": True, "geometry": (20, 20), "parent": None}}
        self.changes, self.closed = [], 0
    def clients(self): return list(self.windows)
    def owner(self, xid): return self.windows.get(xid, {}).get("owner")
    def title(self, xid): return self.windows.get(xid, {}).get("title", "")
    def class_hint(self, xid): return self.windows.get(xid, {}).get("class")
    def dock(self, xid): return self.windows.get(xid, {}).get("dock", False)
    def geometry(self, xid): return self.windows.get(xid, {}).get("geometry")
    def transient(self, xid): return self.windows[xid]["parent"]
    def set_transient(self, xid, parent):
        self.changes.append((xid, parent))
        self.windows[xid]["parent"] = parent
    def close(self): self.closed += 1


class StackingTests(unittest.TestCase):
    def test_only_main_dock_is_parented_and_original_root_is_preserved_for_rollback(self):
        backend = Backend()
        helper = stacking.OnboardStacking(backend, 123)
        self.assertTrue(helper.prepare(456))
        self.assertTrue(helper.prepare(456))
        self.assertEqual(backend.changes, [(60, 50)])
        helper.close()
        helper.close()
        self.assertEqual(backend.changes, [(60, 50), (60, 900)])
        self.assertEqual(backend.closed, 1)
    def test_absent_original_hint_is_restored_as_absent(self):
        backend = Backend(original=None)
        helper = stacking.OnboardStacking(backend, 123)
        self.assertTrue(helper.prepare(456))
        helper.close()
        self.assertEqual(backend.changes[-1], (60, None))
    def test_wrong_pid_class_title_type_or_auxiliary_size_never_changes_property(self):
        for field, value in (("owner", 999), ("class", ("other", "Onboard")), ("title", "Other"), ("dock", False), ("geometry", (20, 20))):
            with self.subTest(field=field):
                backend = Backend()
                backend.windows[60][field] = value
                helper = stacking.OnboardStacking(backend, 123)
                self.assertFalse(helper.prepare(456))
                self.assertEqual(backend.changes, [])
        backend = Backend()
        backend.windows[50]["owner"] = 999
        self.assertFalse(stacking.OnboardStacking(backend, 123).prepare(456))
        self.assertEqual(backend.changes, [])
    def test_ambiguous_main_window_refuses_both(self):
        backend = Backend()
        backend.windows[62] = dict(backend.windows[60])
        self.assertFalse(stacking.OnboardStacking(backend, 123).prepare(456))
        self.assertEqual(backend.changes, [])
    def test_foreign_hint_edit_or_recycled_owner_is_preserved_during_cleanup(self):
        for field, value in (("parent", 777), ("owner", 999)):
            with self.subTest(field=field):
                backend = Backend()
                helper = stacking.OnboardStacking(backend, 123)
                self.assertTrue(helper.prepare(456))
                backend.windows[60][field] = value
                helper.close()
                self.assertEqual(backend.changes, [(60, 50)])
    def test_game_recreation_restores_original_then_parents_to_new_owned_window(self):
        backend = Backend()
        helper = stacking.OnboardStacking(backend, 123)
        self.assertTrue(helper.prepare(456))
        backend.windows[51] = backend.windows.pop(50)
        self.assertTrue(helper.prepare(456))
        self.assertEqual(backend.changes, [(60, 50), (60, 900), (60, 51)])
        helper.close()
        self.assertEqual(backend.changes[-1], (60, 900))
    def test_game_identity_change_prevents_new_mutation_but_cleanup_can_restore(self):
        backend, current = Backend(), ["original"]
        helper = stacking.OnboardStacking(backend, 123, identity="original", read_identity=lambda _: current[0])
        self.assertTrue(helper.prepare(456))
        current[0] = "reused"
        self.assertFalse(helper.prepare(456))
        helper.close()
        self.assertEqual(backend.changes, [(60, 50), (60, 900)])
    def test_cross_thread_use_is_refused_before_any_native_change(self):
        backend, failures = Backend(), []
        helper = stacking.OnboardStacking(backend, 123)
        def wrong_thread():
            try: helper.prepare(456)
            except PresentationError as error: failures.append(str(error))
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(timeout=1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(backend.changes, [])
        helper.close()


if __name__ == "__main__": unittest.main()
