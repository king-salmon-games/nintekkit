#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded, asynchronous access to the existing Onboard keyboard; no text capture."""
from __future__ import annotations

import os
import subprocess
import threading
import time


SERVICE = "org.onboard.Onboard"
OBJECT = "/org/onboard/Onboard/Keyboard"
INTERFACE = "org.onboard.Onboard.Keyboard"
DEADLINE = 0.35


class OnboardTransport:
    def __init__(self, stopped, pid=None, valid=lambda: False):
        self.stopped = stopped
        self.pid, self.valid, self.stacking = pid, valid, None
        self.bus = None
        self.interface = None
        self.onboard_pid = None

    def state(self, start=False):
        if start and not self.valid():
            raise RuntimeError("Verified Java process is no longer active")
        import dbus  # Already installed system runtime; imported only on request.
        if self.bus is None:
            self.bus = dbus.bus.BusConnection("unix:path=/run/user/%d/bus" % os.getuid())
        def owner():
            return str(self.bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
                       "org.freedesktop.DBus", "GetNameOwner", "s", (SERVICE,), timeout=DEADLINE))
        launched = False
        try:
            current = owner()
        except dbus.DBusException as error:
            if not start or error.get_dbus_name() != "org.freedesktop.DBus.Error.NameHasNoOwner":
                raise
            subprocess.Popen(["/usr/bin/onboard"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            launched = True
            deadline = time.monotonic() + 2
            while True:
                try:
                    current = owner()
                    break
                except dbus.DBusException:
                    if self.stopped.wait(0.05) or time.monotonic() >= deadline:
                        raise RuntimeError("Onboard service unavailable") from None
        obj = self.bus.get_object(current, OBJECT, introspect=False)
        self.interface = dbus.Interface(obj, INTERFACE)
        props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        visible = bool(props.Get(INTERFACE, "Visible", timeout=DEADLINE))
        if start:
            if not self.valid():
                raise RuntimeError("Verified Java process is no longer active")
            onboard_pid = int(self.bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
                              "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (current,), timeout=DEADLINE))
            self.onboard_pid = onboard_pid
            if self.stacking is None:
                from onboard_stacking import make_stacking
                self.stacking = make_stacking(self.pid)
            self.stacking.prepare(onboard_pid)
        return current, visible, launched

    def show(self):
        if not self.valid():
            raise RuntimeError("Verified Java process is no longer active")
        self.interface.Show(timeout=DEADLINE)
        # A hidden GTK window may be withdrawn from _NET_CLIENT_LIST until Show.
        deadline = time.monotonic() + 1
        while True:
            if not self.valid():
                raise RuntimeError("Verified Java process is no longer active")
            if self.stacking.prepare(self.onboard_pid):
                return
            if self.stopped.wait(0.05) or time.monotonic() >= deadline:
                raise RuntimeError("Onboard stacking could not be verified")

    def hide(self):
        self.interface.Hide(timeout=DEADLINE)

    def close(self):
        if self.stacking:
            self.stacking.close()
            self.stacking = None


class KeyboardLease:
    """Preserve the user's process and preexisting visibility across owner changes."""
    def __init__(self, transport):
        self.transport = transport
        self.owner = None
        self.original_visible = None
        self.own_shown = False

    def toggle(self, allowed=lambda: True):
        owner, visible, launched = self.transport.state(start=True)
        if owner != self.owner:
            self.owner, self.original_visible, self.own_shown = owner, False if launched else visible, False
        if not allowed():
            if launched:
                self.own_shown = True
            self.cleanup()
            return
        if launched or not visible:
            # Track before DBus: a timed-out Show may have reached the service.
            self.own_shown = self.original_visible is False
            self.transport.show()
        else:
            self.transport.hide()
            self.own_shown = False
        if not allowed():
            self.cleanup()

    def cleanup(self):
        if not self.own_shown:
            return
        owner, visible, _ = self.transport.state(start=False)
        if owner == self.owner and visible:
            self.transport.hide()
        self.own_shown = False


class KeyboardWorker:
    def __init__(self, transport=None, report=print, pid=None, valid=lambda: False):
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.allowed = self.pending = self.hide_pending = False
        self.report = report
        self.warned = False
        self.lease = KeyboardLease(transport or OnboardTransport(self.stopped, pid, valid))
        self.thread = threading.Thread(target=self._run, name="nintekkit-keyboard", daemon=True)
        self.thread.start()

    def set_allowed(self, allowed):
        with self.condition:
            self.allowed = bool(allowed)
            if not allowed:
                self.pending = False
                self.hide_pending = True
                self.condition.notify()

    def toggle(self):
        with self.condition:
            if self.allowed and not self.stopped.is_set():
                self.pending = not self.pending  # Bounded coalescing; no unbounded UI queue.
                self.condition.notify()

    def _allowed(self):
        with self.condition:
            return self.allowed and not self.stopped.is_set()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending or self.hide_pending or self.stopped.is_set())
                stop, hide, toggle = self.stopped.is_set(), self.hide_pending, self.pending
                self.pending = self.hide_pending = False
            try:
                if stop or hide:
                    self.lease.cleanup()
                if stop:
                    self._close_transport()
                    return
                if toggle and self._allowed():
                    self.lease.toggle(self._allowed)
                self.warned = False
            except Exception as error:
                try:
                    self.lease.cleanup()
                except Exception:
                    pass  # Retain ownership flag for the next cleanup/close retry.
                if not self.warned:
                    self.report("On-screen keyboard unavailable: " + type(error).__name__)
                    self.warned = True
                if stop:
                    self._close_transport()
                    return

    def _close_transport(self):
        close = getattr(self.lease.transport, "close", None)
        if close:
            try:
                close()
            except Exception as error:
                self.report("On-screen keyboard restoration unavailable: " + type(error).__name__)

    def close(self):
        with self.condition:
            self.allowed = self.pending = False
            self.stopped.set()
            self.condition.notify()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            self.report("On-screen keyboard cleanup deadline exceeded; existing Onboard process preserved")


class DryKeyboard:
    def __init__(self, report=print):
        self.allowed, self.report = False, report
    def set_allowed(self, allowed):
        self.allowed = bool(allowed)
    def toggle(self):
        if self.allowed:
            self.report('{"would_toggle_on_screen_keyboard":true}')
    def close(self):
        self.allowed = False


def make_keyboard(dry_run=False, pid=None, valid=lambda: False):
    return DryKeyboard() if dry_run else KeyboardWorker(pid=pid, valid=valid)
