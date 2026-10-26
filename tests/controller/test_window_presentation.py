"""Fake X/EWMH tests only; no native display, focus or input changes."""
import ctypes as C
import re
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/controller"))
import window_presentation as window
import test_play_with_controls as lifecycle


class Backend:
    root = 900
    def __init__(self):
        self.windows = {50: {"owner": 123, "title": "Tekkit 2", "fullscreen": False, "geometry": (1280, 648)}}
        self.desktop, self.sent, self.closed = (1280, 720), [], 0
        self.send_result = True
    def clients(self): return list(self.windows)
    def owner(self, value): return self.windows[value]["owner"]
    def title(self, value): return self.windows[value]["title"]
    def geometry(self, value): return self.desktop if value == self.root else self.windows[value]["geometry"]
    def fullscreen(self, value): return self.windows[value]["fullscreen"]
    def request_fullscreen(self, value, pid, pattern):
        if self.send_result: self.sent.append((value, pid))
        return self.send_result
    def close(self): self.closed += 1
    def apply(self, value=50): self.windows[value].update(fullscreen=True, geometry=(1280, 720))


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.clock, self.messages = Backend(), lifecycle.Clock(), []
        self.presentation = window.WindowPresentation(self.backend, 123, clock=self.clock, report=self.messages.append)
    def test_one_add_request_then_state_and_geometry_are_verified_without_resends(self):
        self.assertFalse(self.presentation.refresh())
        self.assertFalse(self.presentation.refresh())
        self.assertEqual(self.backend.sent, [(50, 123)])
        self.backend.windows[50]["fullscreen"] = True
        self.assertFalse(self.presentation.refresh()) # A WM property alone is insufficient.
        self.backend.apply()
        self.assertTrue(self.presentation.refresh())
        self.assertTrue(self.presentation.refresh())
        self.assertEqual(len(self.backend.sent), 1)
        self.assertEqual(sum("VERIFIED" in value for value in self.messages), 1)
    def test_root_prism_other_pid_unknown_owner_and_wrong_caption_never_receive_requests(self):
        for owner, title, xid in ((999, "Tekkit 2", 50), (None, "Tekkit 2", 50), (123, "Prism Launcher", 50), (123, "Tekkit 2", 900)):
            with self.subTest(owner=owner, title=title, xid=xid):
                self.backend.windows = {xid: {"owner": owner, "title": title, "fullscreen": False, "geometry": (1280, 648)}}
                self.assertFalse(self.presentation.refresh())
        self.assertEqual(self.backend.sent, [])
    def test_ambiguous_owned_windows_wait_without_mutating_either(self):
        self.backend.windows[51] = dict(self.backend.windows[50])
        self.assertFalse(self.presentation.refresh())
        self.assertEqual(self.backend.sent, [])
        del self.backend.windows[51]
        self.presentation.refresh()
        self.assertEqual(self.backend.sent, [(50, 123)])
    def test_recreated_window_is_rediscovered_and_prior_xid_is_not_used(self):
        self.presentation.refresh()
        self.backend.apply()
        self.presentation.refresh()
        old = self.backend.windows.pop(50)
        self.presentation.refresh()
        self.backend.windows[51] = {**old, "fullscreen": False, "geometry": (1280, 648)}
        self.presentation.refresh()
        self.assertEqual(self.backend.sent, [(50, 123), (51, 123)])
        self.assertNotIn(50, self.presentation.verified)
    def test_verified_state_loss_is_reported_and_gets_only_one_bounded_retry(self):
        self.presentation.refresh()
        self.backend.apply()
        self.assertTrue(self.presentation.refresh())
        # Also represents same-XID recreation between polls: current state wins.
        self.backend.windows[50].update(fullscreen=False, geometry=(1280, 648))
        self.assertFalse(self.presentation.refresh())
        self.assertNotIn(50, self.presentation.verified)
        self.assertTrue(any("LOST" in value for value in self.messages))
        self.assertEqual(self.backend.sent, [(50, 123), (50, 123)])
        self.presentation.refresh()
        self.assertEqual(len(self.backend.sent), 2)
        self.backend.apply()
        self.assertTrue(self.presentation.refresh())
        self.backend.windows[50]["fullscreen"] = False
        with self.assertRaisesRegex(window.PresentationError, "bounded reapplication"):
            self.presentation.refresh()
        self.assertEqual(len(self.backend.sent), 2)
    def test_inherited_fullscreen_has_only_one_reapplication_budget(self):
        self.backend.apply()
        self.assertTrue(self.presentation.refresh())
        self.assertEqual(self.backend.sent, [])
        self.backend.windows[50].update(fullscreen=False, geometry=(1280, 648))
        self.assertFalse(self.presentation.refresh())
        self.assertEqual(self.backend.sent, [(50, 123)])
        self.backend.apply()
        self.assertTrue(self.presentation.refresh())
        self.backend.windows[50]["fullscreen"] = False
        with self.assertRaisesRegex(window.PresentationError, "bounded reapplication"):
            self.presentation.refresh()
        self.assertEqual(self.backend.sent, [(50, 123)])
    def test_ignored_request_times_out_without_spamming_or_changing_display_mode(self):
        self.presentation.refresh()
        self.clock.now = 10
        with self.assertRaisesRegex(window.PresentationError, "ten seconds"):
            self.presentation.refresh()
        self.assertEqual(self.backend.sent, [(50, 123)])
        self.backend.desktop = (1920, 1080)
        with self.assertRaisesRegex(window.PresentationError, "not 1280x720"):
            self.presentation.refresh()
    def test_process_identity_loss_prevents_any_request_and_close_never_removes_state(self):
        presentation = window.WindowPresentation(self.backend, 123, identity="original", read_identity=lambda _: "reused")
        self.assertFalse(presentation.refresh())
        self.assertEqual(self.backend.sent, [])
        presentation.close()
        presentation.close()
        self.assertEqual(self.backend.closed, 1)
    def test_unfocused_owned_game_is_targeted_without_reading_or_changing_focus(self):
        # Backend intentionally exposes no focus/activation or input API.
        self.presentation.refresh()
        self.assertEqual(self.backend.sent, [(50, 123)])


class NativeMessageTests(unittest.TestCase):
    def native(self):
        backend = window.NativeEwmh.__new__(window.NativeEwmh)
        backend.display, backend.root = 0x1234, 900
        backend.atoms = {"_NET_WM_STATE": 10, "_NET_WM_STATE_FULLSCREEN": 11}
        backend.clients = lambda: [50]
        backend.owner = lambda _: 123
        backend.title = lambda _: "Tekkit 2"
        return backend
    def test_linux_lp64_event_layout_and_exact_ewmh_add_message(self):
        self.assertEqual(C.sizeof(window.ClientMessage), 96)
        self.assertEqual(C.sizeof(window.XEvent), 192)
        self.assertEqual(window.ClientMessage.data.offset, 56)
        backend, calls = self.native(), []
        class X:
            def XSendEvent(self, display, root, propagate, mask, event):
                message = C.cast(event, C.POINTER(window.XEvent)).contents.client
                calls.append((display, root, propagate, mask, message.type, message.window,
                              message.message_type, message.format, list(message.data.l)))
                return 1
            def XFlush(self, display): calls.append(("flush", display))
        backend.x = X()
        self.assertTrue(backend.request_fullscreen(50, 123, re.compile("^Tekkit 2$")))
        self.assertEqual(calls[0], (0x1234, 900, 0, (1 << 19) | (1 << 20), 33, 50, 10, 32, [1, 11, 0, 1, 0]))
        self.assertEqual(calls[1], ("flush", 0x1234))
    def test_native_rechecks_ownership_before_send_and_does_not_target_root(self):
        backend = self.native()
        backend.x = object() # Any X mutation would fail the test.
        pattern = re.compile("^Tekkit 2$")
        backend.owner = lambda _: 999
        self.assertFalse(backend.request_fullscreen(50, 123, pattern))
        self.assertFalse(backend.request_fullscreen(900, 123, pattern))
    def test_false_send_result_is_an_error_not_verification(self):
        backend = self.native()
        class X:
            def XSendEvent(self, *args): return 0
        backend.x = X()
        with self.assertRaisesRegex(window.PresentationError, "not queued"):
            backend.request_fullscreen(50, 123, re.compile("^Tekkit 2$"))
    def test_format32_property_uses_lp64_elements_and_always_frees_x_data(self):
        backend, freed = self.native(), []
        storage = (window.XID * 2)(0x11223344, 11)
        class X:
            def XGetWindowProperty(self, *args):
                C.cast(args[7], C.POINTER(window.XID))[0] = 4
                C.cast(args[8], C.POINTER(C.c_int))[0] = 32
                C.cast(args[9], C.POINTER(window.XID))[0] = 2
                C.cast(args[10], C.POINTER(window.XID))[0] = 0
                C.cast(args[11], C.POINTER(C.c_void_p))[0] = C.cast(storage, C.c_void_p).value
                return 0
            def XFree(self, data): freed.append(data.value)
        backend.x = X()
        self.assertEqual(backend.array(50, "_NET_WM_STATE", 4), [0x11223344, 11])
        self.assertEqual(freed, [C.addressof(storage)])


class SharedLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.harness = lifecycle.LifecycleTests()
        self.harness.setUp()
    def test_plain_route_keeps_awake_and_presentation_without_mapper_or_java_kill(self):
        calls, game = [], lifecycle.GAME
        class Lease:
            def __init__(self, name): self.name = name
            def refresh(self): calls.append(self.name + " refresh")
            def close(self): calls.append(self.name + " close")
        result = lifecycle.play.supervise(["prism"], None,
            lifecycle.Sequence([[], [game], [game], [game], [game], [game], []]),
            spawn=self.harness.spawn, clock=self.harness.clock, sleep=self.harness.clock.sleep,
            report=lambda _: None, awake_factory=lambda _: Lease("awake"), presentation_factory=lambda _: Lease("window"))
        self.assertEqual(result, 0)
        self.assertEqual(len(self.harness.commands), 1)
        self.assertEqual(self.harness.dispatcher.terminated, 0)
        self.assertEqual(calls, ["awake refresh", "window refresh", "awake refresh", "window refresh", "window close", "awake close"])
    def test_presentation_failure_releases_awake_and_never_kills_game_or_prism(self):
        calls, game = [], lifecycle.GAME
        class Awake:
            def refresh(self): pass
            def close(self): calls.append("awake close")
        class Window:
            def refresh(self): raise window.PresentationError("test")
            def close(self): calls.append("window close")
        with self.assertRaises(window.PresentationError):
            self.harness.run_lifecycle(lifecycle.Sequence([[], [game], [game], [game]]),
                awake_factory=lambda _: Awake(), presentation_factory=lambda _: Window())
        self.assertEqual(calls, ["window close", "awake close"])
        self.assertEqual(len(self.harness.commands), 1)
        self.assertEqual(self.harness.dispatcher.terminated, 0)
    def test_private_bookmark_callback_runs_before_prism_and_refusal_never_dispatches(self):
        calls = []
        def refuse():
            calls.append("bookmark")
            raise ValueError("foreign list preserved")
        with self.assertRaisesRegex(ValueError, "foreign"):
            self.harness.run_lifecycle(lambda: [], before_dispatch=refuse)
        self.assertEqual(calls, ["bookmark"])
        self.assertEqual(self.harness.commands, [])


if __name__ == "__main__": unittest.main()
