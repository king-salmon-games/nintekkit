#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Game-owned EWMH fullscreen presentation; no mode switch, focus or input events.

Keep Minecraft/LWJGL native fullscreen false. This helper asks the existing window
manager to remove the owned game's frame and verifies its X window dimensions.
It does not establish the game's framebuffer/viewport dimensions or performance.
"""
from __future__ import annotations

import ctypes as C
import platform
import re
import time

from nintekkit_controller import DEFAULT_WINDOW_REGEX, XConnection
from screen_awake import read_java


class PresentationError(RuntimeError): pass


# Explicit Linux LP64 ABI also permits meaningful layout checks on Windows hosts.
XID, XLong = C.c_uint64, C.c_int64


class ClientData(C.Union):
    _fields_ = [("b", C.c_char * 20), ("s", C.c_short * 10), ("l", XLong * 5)]


class ClientMessage(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", XID), ("send_event", C.c_int),
                ("display", C.c_void_p), ("window", XID), ("message_type", XID),
                ("format", C.c_int), ("data", ClientData)]


class XEvent(C.Union):
    _fields_ = [("client", ClientMessage), ("pad", XLong * 24)]


class NativeEwmh:
    def __init__(self, connection=None):
        self.connection = connection or XConnection(inject=False)
        self.x, self.display = self.connection.x, self.connection.display
        try:
            if C.sizeof(C.c_void_p) != 8 or not self.connection.xres:
                raise PresentationError("EWMH presentation requires Linux LP64 and XRes 1.2 ownership queries")
            self.x.XDefaultRootWindow.argtypes, self.x.XDefaultRootWindow.restype = [C.c_void_p], XID
            self.x.XSendEvent.argtypes = [C.c_void_p, XID, C.c_int, XLong, C.POINTER(XEvent)]
            self.x.XSendEvent.restype = C.c_int
            self.x.XGetGeometry.argtypes = [C.c_void_p, XID, C.POINTER(XID), C.POINTER(C.c_int), C.POINTER(C.c_int),
                                           C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint)]
            self.x.XGetGeometry.restype = C.c_int
            self.root = int(self.x.XDefaultRootWindow(self.display))
            if self.root <= 1: raise PresentationError("Cannot identify the actual X root window")
            self.atoms = {name: int(self.x.XInternAtom(self.display, name.encode("ascii"), 1))
                          for name in ("_NET_CLIENT_LIST", "_NET_SUPPORTED", "_NET_WM_STATE", "_NET_WM_STATE_FULLSCREEN")}
            supported = self.array(self.root, "_NET_SUPPORTED", 4)
            if any(not self.atoms[name] for name in self.atoms) or any(self.atoms[name] not in supported for name in ("_NET_WM_STATE", "_NET_WM_STATE_FULLSCREEN")):
                raise PresentationError("The current window manager does not advertise EWMH fullscreen support")
        except BaseException:
            self.close()
            raise

    def array(self, window, name, expected_type):
        atom = self.atoms.get(name, 0)
        if not atom: return []
        actual, format_, count, after, data = XID(), C.c_int(), XID(), XID(), C.c_void_p()
        status = self.x.XGetWindowProperty(self.display, window, atom, 0, 4096, 0, expected_type,
            C.byref(actual), C.byref(format_), C.byref(count), C.byref(after), C.byref(data))
        try:
            if status != 0 or actual.value != expected_type or format_.value != 32 or not data:
                return []
            if count.value > 4096 or after.value:
                raise PresentationError("EWMH property exceeds the bounded query size")
            return [int(value) for value in C.cast(data, C.POINTER(XID))[:count.value]]
        finally:
            if data: self.x.XFree(data)

    def clients(self):
        return self.array(self.root, "_NET_CLIENT_LIST", 33) # XA_WINDOW

    def owner(self, window):
        # Authoritative server resource ownership; do not trust a title/PID hint.
        return self.connection._xres_window_pid(window)

    def title(self, window):
        value = C.c_char_p()
        if not self.x.XFetchName(self.display, window, C.byref(value)) or not value:
            return ""
        try: return value.value.decode("utf-8", "replace")
        finally: self.x.XFree(C.cast(value, C.c_void_p))

    def geometry(self, window):
        root, x, y = XID(), C.c_int(), C.c_int()
        width, height, border, depth = C.c_uint(), C.c_uint(), C.c_uint(), C.c_uint()
        if not self.x.XGetGeometry(self.display, window, C.byref(root), C.byref(x), C.byref(y),
                                  C.byref(width), C.byref(height), C.byref(border), C.byref(depth)):
            return None
        return width.value, height.value

    def fullscreen(self, window):
        return self.atoms["_NET_WM_STATE_FULLSCREEN"] in self.array(window, "_NET_WM_STATE", 4)

    def request_fullscreen(self, window, pid, pattern):
        # Recheck immediately before the only mutating X request.
        if window in (0, 1, self.root) or window not in self.clients() or self.owner(window) != pid or not pattern.search(self.title(window)):
            return False
        event = XEvent()
        event.client.type, event.client.send_event = 33, 1 # ClientMessage
        event.client.display, event.client.window = self.display, window
        event.client.message_type, event.client.format = self.atoms["_NET_WM_STATE"], 32
        event.client.data.l[:] = [1, self.atoms["_NET_WM_STATE_FULLSCREEN"], 0, 1, 0] # ADD; application source
        if not self.x.XSendEvent(self.display, self.root, 0, (1 << 19) | (1 << 20), C.byref(event)):
            raise PresentationError("Window manager fullscreen request was not queued")
        self.x.XFlush(self.display)
        return True # Only the later state/geometry query proves application.

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None


class WindowPresentation:
    def __init__(self, backend, pid, *, identity=None, read_identity=read_java,
                 clock=time.monotonic, report=print, pattern=DEFAULT_WINDOW_REGEX):
        self.backend, self.pid, self.identity, self.read_identity = backend, pid, identity, read_identity
        self.clock, self.report, self.pattern = clock, report, re.compile(pattern, re.I)
        self.requested, self.attempts, self.verified, self.lost, self.last_status = {}, {}, set(), set(), None

    def status(self, text):
        if text != self.last_status:
            self.report(text)
            self.last_status = text

    def refresh(self):
        if self.identity is not None and self.read_identity(self.pid) != self.identity:
            return False
        if self.backend.geometry(self.backend.root) != (1280, 720):
            raise PresentationError("Actual X desktop is not 1280x720; presentation made no mode change")
        clients = set(self.backend.clients()) - {0, 1, self.backend.root}
        self.requested = {window: stamp for window, stamp in self.requested.items() if window in clients}
        self.attempts = {window: count for window, count in self.attempts.items() if window in clients}
        self.verified.intersection_update(clients)
        self.lost.intersection_update(clients)
        matches = [window for window in clients if self.backend.owner(window) == self.pid and self.pattern.search(self.backend.title(window))]
        if len(matches) != 1:
            self.status("PRESENTATION_WAITING_FOR_ONE_OWNED_GAME_WINDOW")
            return False
        window = matches[0]
        if self.backend.fullscreen(window) and self.backend.geometry(window) == (1280, 720):
            # An inherited verified state is the baseline too: it gets the same
            # single reapplication budget as a state established by our ADD.
            self.attempts.setdefault(window, 1)
            self.verified.add(window)
            self.lost.discard(window)
            self.status("PRESENTATION_VERIFIED window=" + hex(window) + " geometry=1280x720")
            return True
        if window in self.verified:
            self.verified.discard(window)
            self.lost.add(window)
            self.status("PRESENTATION_LOST window=" + hex(window))
        if window in self.lost and self.attempts.get(window, 0) >= 2:
            raise PresentationError("Owned game window lost fullscreen after its one bounded reapplication")
        if window not in self.requested or window in self.lost:
            if self.identity is not None and self.read_identity(self.pid) != self.identity:
                return False
            if self.backend.request_fullscreen(window, self.pid, self.pattern):
                self.requested[window] = self.clock()
                self.attempts[window] = self.attempts.get(window, 0) + 1
                self.lost.discard(window)
                self.status("PRESENTATION_REQUESTED window=" + hex(window))
        elif window not in self.verified and self.clock() - self.requested[window] >= 10:
            raise PresentationError("Owned window did not reach EWMH fullscreen 1280x720 within ten seconds")
        return False

    def close(self):
        # Stop observing; never send REMOVE, activate another window, or kill Java.
        if self.backend:
            self.backend.close()
            self.backend = None


def make_presentation(pid, report=print):
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise PresentationError("Run presentation in the Switch's existing ARM64 X11 desktop")
    identity = read_java(pid)
    if identity is None:
        raise PresentationError("Presentation requires a live same-user Java process")
    return WindowPresentation(NativeEwmh(), pid, identity=identity, report=report)
