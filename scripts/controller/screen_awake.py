#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inhibit automatic locking and display blanking while one Java process lives."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import sys
import threading
import time


SERVICE = "org.freedesktop.ScreenSaver"
OBJECT = "/ScreenSaver"
POLICY_SERVICE = "org.kde.Solid.PowerManagement"
POLICY_INTERFACE = "org.kde.Solid.PowerManagement.PolicyAgent"
POLICY_OBJECT = "/org/kde/Solid/PowerManagement/PolicyAgent"
DISPLAY_POLICY = 4 # Plasma 5.27 ChangeScreenSettings; also implies InterruptSession.


@dataclass(frozen=True)
class JavaIdentity:
    pid: int
    uid: int
    executable: str
    starttime: str


def read_java(pid, *, proc=Path("/proc"), uid=None):
    """Read only UID, executable and start time; no command lines or environments."""
    uid = os.getuid() if uid is None else uid
    process = proc / str(pid)
    try:
        if process.stat().st_uid != uid:
            return None
        executable = (process / "exe").resolve(strict=True)
        if executable.name != "java":
            return None
        text = (process / "stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        if len(fields) < 20 or not fields[19].isdigit() or fields[0] == "Z":
            return None
        return JavaIdentity(pid, uid, str(executable), fields[19])
    except (OSError, ValueError):
        return None


class ScreenInhibitor:
    """Each cookie stays bound to the service's unique owner, never a new owner."""
    def __init__(self, bus, interface, pid, *, errors=(OSError,), report=print, owner_for=None):
        self.bus, self.interface_for, self.errors, self.report = bus, interface, errors, report
        self.owner_for = owner_for or bus.get_name_owner
        self.application = "Nintekkit Java " + str(pid)
        self.reason = "Automatic locking inhibited while the local Tekkit client runs"
        self.owner = self.cookie = self.interface = None
        self.warned = False

    def close(self):
        interface, cookie = self.interface, self.cookie
        self.owner = self.cookie = self.interface = None
        if interface is not None and cookie is not None:
            try:
                interface.UnInhibit(cookie, timeout=3)
                self.report("SCREENSAVER_INHIBITOR_RELEASED")
            except self.errors:
                pass  # A vanished unique owner cannot retain a live cookie.

    def refresh(self):
        try:
            owner = str(self.owner_for(SERVICE))
            if owner == self.owner and self.cookie is not None:
                return True
            self.close()
            interface = self.interface_for(owner)
            cookie = interface.Inhibit(self.application, self.reason, timeout=3)
            self.owner, self.interface, self.cookie = owner, interface, cookie
            self.warned = False
            self.report("SCREENSAVER_INHIBITOR_ACTIVE owner=" + owner + " cookie=" + str(int(cookie)))
            return True
        except self.errors:
            self.close()
            if not self.warned:
                self.report("Automatic screen-lock inhibition unavailable; retrying while Java lives")
                self.warned = True
            return False


class DisplayInhibitor:
    """Own and verify policy 4 independently of ScreenSaver's delegation."""
    def __init__(self, bus, interface, pid, *, errors=(OSError,), report=print,
                 owner_for=None, clock=time.monotonic, helper_pid=None, uint32=int):
        self.interface_for, self.owner_for = interface, owner_for or bus.get_name_owner
        self.errors, self.report, self.clock = errors, report, clock
        self.uint32 = uint32
        helper_pid = os.getpid() if helper_pid is None else helper_pid
        self.application = "Nintekkit display Java %s helper %s" % (pid, helper_pid)
        self.reason = "Prevent automatic display dimming and blanking while the local Tekkit client runs"
        self.owner = self.interface = self.cookie = self.pending_since = None
        self.verified = self.warned = False

    def close(self):
        interface, cookie = self.interface, self.cookie
        self.owner = self.interface = self.cookie = self.pending_since = None
        self.verified = False
        if interface is not None and cookie is not None:
            try:
                interface.ReleaseInhibition(cookie, timeout=2)
                self.report("POWERDEVIL_DISPLAY_RELEASED")
            except self.errors:
                pass

    def refresh(self):
        try:
            owner = str(self.owner_for(POLICY_SERVICE))
            if owner != self.owner or self.cookie is None:
                self.close()
                interface = self.interface_for(owner)
                cookie = interface.AddInhibition(self.uint32(DISPLAY_POLICY), self.application, self.reason, timeout=2)
                self.owner, self.interface, self.cookie = owner, interface, cookie
                self.pending_since, self.verified, self.warned = self.clock(), False, False
                self.report("POWERDEVIL_DISPLAY_PENDING owner=" + owner + " cookie=" + str(int(cookie)) + " app=" + self.application)
                return False
            # PowerDevil deliberately defers enforcement for five seconds.
            age = self.clock() - self.pending_since
            if age < 5:
                return False
            entries = self.interface.ListInhibitions(timeout=2)
            own_entry = any(str(app) == self.application and str(reason) == self.reason for app, reason in entries)
            policy_active = bool(self.interface.HasInhibition(self.uint32(DISPLAY_POLICY), timeout=2))
            if own_entry and policy_active:
                if not self.verified:
                    self.report("POWERDEVIL_DISPLAY_VERIFIED owner=" + owner + " policy=4 app=" + self.application)
                self.verified, self.warned = True, False
                return True
            if self.verified or age >= 12:
                self.close()
                self.report("Own display inhibition missing; reacquiring on the next game poll")
            return False
        except self.errors:
            self.close()
            if not self.warned:
                self.report("Display inhibition unavailable; retrying while Java lives")
                self.warned = True
            return False


class GameInhibitor:
    def __init__(self, screen, display):
        self.screen, self.display = screen, display

    def refresh(self):
        try:
            screen_ready = self.screen.refresh()
            display_ready = self.display.refresh()
            return screen_ready and display_ready
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            self.screen.close()
        finally:
            self.display.close()


def make_inhibitor(pid, report=print):
    try:
        import dbus  # Existing Switch dbus-python; tests need no dependency.
    except ImportError as error:
        raise RuntimeError("The existing Switch dbus-python runtime is required for screen-lock inhibition") from error
    try:
        bus = dbus.bus.BusConnection("unix:path=/run/user/%d/bus" % os.getuid())
    except dbus.DBusException as error:
        raise RuntimeError("Cannot connect to this desktop user's existing session bus") from error
    # Explicit local-bus method deadlines also bound owner lookup during outages.
    owner_for = lambda service: bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "GetNameOwner", "s", (service,), timeout=2)
    screen_interface = lambda owner: dbus.Interface(bus.get_object(owner, OBJECT, introspect=False), SERVICE)
    display_interface = lambda owner: dbus.Interface(bus.get_object(owner, POLICY_OBJECT, introspect=False), POLICY_INTERFACE)
    return GameInhibitor(
        ScreenInhibitor(bus, screen_interface, pid, errors=(dbus.DBusException,), report=report, owner_for=owner_for),
        DisplayInhibitor(bus, display_interface, pid, errors=(dbus.DBusException,), report=report, owner_for=owner_for, uint32=dbus.UInt32))


def watch_java(identity, inhibitor, *, read=read_java, stop_requested=lambda: False,
               wait=time.sleep, report=print):
    try:
        while not stop_requested() and read(identity.pid) == identity:
            inhibitor.refresh()
            wait(1)
        report("Java watch ended; releasing game display/lock inhibitions")
    finally:
        inhibitor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True, help="Actual same-user Java PID; reads exe/starttime, never tokens")
    args = parser.parse_args()
    if not sys.platform.startswith("linux") or os.getuid() == 0 or args.pid <= 1:
        parser.error("Run as the Switch desktop user with an actual Java PID; no sudo")
    identity = read_java(args.pid)
    if identity is None:
        parser.error("PID must identify a live Java process owned by this user")
    stopped = threading.Event()
    old = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        inhibitor = make_inhibitor(args.pid, report=lambda message: print(message, flush=True))
        watch_java(identity, inhibitor, stop_requested=stopped.is_set, wait=stopped.wait,
                   report=lambda message: print(message, flush=True))
    finally:
        for sig, handler in old.items(): signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ImportError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
