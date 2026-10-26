"""Synthetic Onboard leases and asynchronous UI work; no DBus, launches or input."""
import sys
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/controller"))
import on_screen_keyboard as keyboard


class Transport:
    def __init__(self, visible=False):
        self.owner, self.visible, self.launched = ":1.2", visible, False
        self.events = []
        self.shown = threading.Event()
        self.hidden = threading.Event()
    def state(self, start=False):
        self.events.append(("state", start))
        return self.owner, self.visible, self.launched
    def show(self):
        self.visible = True
        self.events.append("show")
        self.shown.set()
    def hide(self):
        self.visible = False
        self.events.append("hide")
        self.hidden.set()


class KeyboardTests(unittest.TestCase):
    def test_own_show_hidden_on_cleanup_existing_visible_preserved(self):
        transport = Transport()
        lease = keyboard.KeyboardLease(transport)
        lease.toggle()
        self.assertTrue(transport.visible)
        lease.cleanup()
        self.assertFalse(transport.visible)
        existing = Transport(True)
        lease = keyboard.KeyboardLease(existing)
        lease.toggle()  # User explicitly hides their existing keyboard.
        lease.toggle()  # User explicitly shows it again.
        lease.cleanup()
        self.assertTrue(existing.visible)
        self.assertEqual(existing.events.count("hide"), 1)

    def test_owner_change_never_hides_new_users_visible_keyboard(self):
        transport = Transport()
        lease = keyboard.KeyboardLease(transport)
        lease.toggle()
        transport.owner = ":1.3"
        lease.cleanup()
        self.assertTrue(transport.visible)
        self.assertNotIn("hide", transport.events)

    def test_focus_loss_during_request_prevents_show_and_hides_owned_launch(self):
        transport = Transport()
        transport.launched = True
        transport.visible = True
        lease = keyboard.KeyboardLease(transport)
        lease.toggle(lambda: False)
        self.assertNotIn("show", transport.events)
        self.assertFalse(transport.visible)

    def test_worker_returns_immediately_and_focus_loss_cleans_up(self):
        transport = Transport()
        worker = keyboard.KeyboardWorker(transport, report=lambda _: None)
        try:
            worker.set_allowed(True)
            start = time.monotonic()
            worker.toggle()
            self.assertLess(time.monotonic() - start, 0.05)
            self.assertTrue(transport.shown.wait(1))
            worker.set_allowed(False)
            self.assertTrue(transport.hidden.wait(1))
            self.assertFalse(transport.visible)
        finally:
            worker.close()
        self.assertFalse(worker.thread.is_alive())

    def test_dry_run_never_launches_or_constructs_transport(self):
        messages = []
        with patch.object(keyboard.subprocess, "Popen", side_effect=AssertionError("No process allowed")):
            dry = keyboard.DryKeyboard(messages.append)
            dry.toggle()
            self.assertEqual(messages, [])
            dry.set_allowed(True)
            dry.toggle()
            dry.close()
        self.assertEqual(messages, ['{"would_toggle_on_screen_keyboard":true}'])

    def test_slow_service_never_blocks_mapper_and_cancels_show_after_focus_loss(self):
        transport = Transport()
        entered, release = threading.Event(), threading.Event()
        ordinary_state = transport.state
        def blocked_state(start=False):
            if start:
                entered.set()
                release.wait(2)
            return ordinary_state(start)
        transport.state = blocked_state
        worker = keyboard.KeyboardWorker(transport, report=lambda _: None)
        try:
            worker.set_allowed(True)
            worker.toggle()
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            worker.set_allowed(False)
            worker.toggle()
            self.assertLess(time.monotonic() - start, 0.05)
            release.set()
        finally:
            release.set()
            worker.close()
        self.assertNotIn("show", transport.events)

    def test_missing_live_java_refuses_before_dbus_or_launch(self):
        transport = keyboard.OnboardTransport(threading.Event(), pid=123, valid=lambda: False)
        with patch.object(keyboard.subprocess, "Popen", side_effect=AssertionError("No launch allowed")):
            with self.assertRaisesRegex(RuntimeError, "Java process"):
                transport.state(start=True)


if __name__ == "__main__":
    unittest.main()
