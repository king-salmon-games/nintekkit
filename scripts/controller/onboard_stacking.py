#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Temporarily parent only a verified Onboard dock to the owned Java window."""
import ctypes as C
import re
import threading

from nintekkit_controller import DEFAULT_WINDOW_REGEX
from screen_awake import read_java
from window_presentation import NativeEwmh, PresentationError, XID


class ClassHint(C.Structure):
    _fields_ = [("name", C.c_void_p), ("class_", C.c_void_p)]


class NativeStacking:
    def __init__(self):
        self.ewmh = NativeEwmh()
        self.x, self.display, self.root = self.ewmh.x, self.ewmh.display, self.ewmh.root
        self.x.XGetClassHint.argtypes, self.x.XGetClassHint.restype = [C.c_void_p, XID, C.POINTER(ClassHint)], C.c_int
        self.x.XGetTransientForHint.argtypes, self.x.XGetTransientForHint.restype = [C.c_void_p, XID, C.POINTER(XID)], C.c_int
        self.x.XSetTransientForHint.argtypes, self.x.XSetTransientForHint.restype = [C.c_void_p, XID, XID], C.c_int
        self.x.XDeleteProperty.argtypes, self.x.XDeleteProperty.restype = [C.c_void_p, XID, XID], C.c_int
        for name in ("_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DOCK", "WM_TRANSIENT_FOR"):
            self.ewmh.atoms[name] = int(self.x.XInternAtom(self.display, name.encode("ascii"), 1))
    def clients(self): return self.ewmh.clients()
    def owner(self, xid): return self.ewmh.owner(xid)
    def title(self, xid): return self.ewmh.title(xid)
    def geometry(self, xid): return self.ewmh.geometry(xid)
    def class_hint(self, xid):
        hint = ClassHint()
        if not self.x.XGetClassHint(self.display, xid, C.byref(hint)): return None
        try:
            return tuple(C.string_at(value).decode("utf-8", "replace") if value else "" for value in (hint.name, hint.class_))
        finally:
            for value in (hint.name, hint.class_):
                if value: self.x.XFree(value)
    def dock(self, xid):
        atom = self.ewmh.atoms["_NET_WM_WINDOW_TYPE_DOCK"]
        return bool(atom and atom in self.ewmh.array(xid, "_NET_WM_WINDOW_TYPE", 4))
    def transient(self, xid):
        parent = XID()
        return int(parent.value) if self.x.XGetTransientForHint(self.display, xid, C.byref(parent)) else None
    def set_transient(self, xid, parent):
        if parent is None:
            # XSetTransientForHint may have created the atom after our first query.
            atom = int(self.x.XInternAtom(self.display, b"WM_TRANSIENT_FOR", 1))
            if atom: self.x.XDeleteProperty(self.display, xid, atom)
        else:
            self.x.XSetTransientForHint(self.display, xid, parent)
        self.x.XSync(self.display, 0)
    def close(self): self.ewmh.close()


class OnboardStacking:
    def __init__(self, backend, pid, *, identity=None, read_identity=read_java):
        self.backend, self.pid, self.identity, self.read_identity = backend, pid, identity, read_identity
        self.pattern, self.saved = re.compile(DEFAULT_WINDOW_REGEX, re.I), None
        self.thread = threading.get_ident()
    def check_thread(self):
        if threading.get_ident() != self.thread:
            raise PresentationError("Onboard stacking must stay on its creating worker thread")
    def onboard_matches(self, xid, owner):
        size = self.backend.geometry(xid)
        return (xid not in (0, 1, self.backend.root) and self.backend.owner(xid) == owner
                and self.backend.title(xid) == "Onboard" and self.backend.class_hint(xid) == ("onboard", "Onboard")
                and self.backend.dock(xid) and size is not None and size[0] > 100 and size[1] > 100)
    def restore(self):
        self.check_thread()
        if self.saved is None: return
        xid, owner, parent, original = self.saved
        self.saved = None
        if self.onboard_matches(xid, owner) and self.backend.transient(xid) == parent:
            self.backend.set_transient(xid, original)
            if self.backend.transient(xid) != original:
                raise PresentationError("Onboard transient-parent restoration was not confirmed")
    def prepare(self, onboard_owner_pid):
        self.check_thread()
        if onboard_owner_pid <= 1 or onboard_owner_pid == self.pid:
            return False
        if self.identity is not None and self.read_identity(self.pid) != self.identity:
            return False
        clients = set(self.backend.clients()) - {0, 1, self.backend.root}
        games = [xid for xid in clients if self.backend.owner(xid) == self.pid and self.pattern.search(self.backend.title(xid))]
        keyboards = [xid for xid in clients if self.onboard_matches(xid, onboard_owner_pid)]
        if len(games) != 1 or len(keyboards) != 1:
            return False
        game, keyboard = games[0], keyboards[0]
        if self.saved is not None and self.saved[:3] == (keyboard, onboard_owner_pid, game) and self.backend.transient(keyboard) == game:
            return True
        self.restore()
        # Recheck both resource owners and game lifetime immediately before change.
        if (self.backend.owner(game) != self.pid or not self.pattern.search(self.backend.title(game))
                or not self.onboard_matches(keyboard, onboard_owner_pid)
                or (self.identity is not None and self.read_identity(self.pid) != self.identity)):
            return False
        original = self.backend.transient(keyboard)
        self.saved = (keyboard, onboard_owner_pid, game, original)
        self.backend.set_transient(keyboard, game)
        if self.backend.transient(keyboard) != game:
            self.restore()
            raise PresentationError("Onboard game-parent request was not confirmed")
        return True
    def close(self):
        self.check_thread()
        if self.backend:
            try: self.restore()
            finally:
                self.backend.close()
                self.backend = None


def make_stacking(pid):
    identity = read_java(pid)
    if identity is None: raise PresentationError("Onboard stacking requires the actual same-user Java process")
    return OnboardStacking(NativeStacking(), pid, identity=identity)
