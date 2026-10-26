#!/usr/bin/env python3
"""Hold a temporary PowerDevil inhibition in the existing user's KDE session."""
import os
import signal
import threading
import time

import dbus

stop = threading.Event()
for signum in (signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, lambda *_: stop.set())
bus = dbus.bus.BusConnection('unix:path=/run/user/%d/bus' % os.getuid())
name = 'org.freedesktop.PowerManagement'
path = '/org/freedesktop/PowerManagement/Inhibit'
owner = None
cookie = None
interface = None
application = 'Nintekkit setup (%d)' % os.getpid()
reason = 'Keep the existing KDE desktop awake during client setup'
try:
    while not stop.is_set():
        current_owner = bus.get_name_owner(name)
        if current_owner != owner:
            interface = dbus.Interface(bus.get_object(name, path), name + '.Inhibit')
            cookie = interface.Inhibit(application, reason)
            policy = dbus.Interface(bus.get_object('org.kde.Solid.PowerManagement',
                                    '/org/kde/Solid/PowerManagement/PolicyAgent'),
                                    'org.kde.Solid.PowerManagement.PolicyAgent')
            # PowerDevil 5.27 applies cookies only after its 5-second pending timer.
            deadline = time.monotonic() + 12
            while not stop.is_set():
                if bool(interface.HasInhibit()) and str(dict(policy.ListInhibitions()).get(application, '')) == reason:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('PowerDevil did not apply this process\'s inhibition')
                stop.wait(0.25)
            if stop.is_set():
                break
            owner = current_owner
            print('KDE_INHIBITOR_ACTIVE owner=%s cookie=%s pid=%s' % (owner, cookie, os.getpid()), flush=True)
        stop.wait(5)
finally:
    if cookie is not None and interface is not None:
        try:
            if bus.get_name_owner(name) == owner:
                interface.UnInhibit(cookie)
                print('KDE_INHIBITOR_RELEASED', flush=True)
        except dbus.DBusException:
            pass
