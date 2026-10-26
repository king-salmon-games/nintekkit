#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Game-scoped raw Combined Joy-Con mapping using existing X11/XTest libraries."""
from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
from dataclasses import asdict, dataclass
import errno
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid

# Native helper imports must share this registry when the mapper is a script.
if __name__ == "__main__":
    sys.modules.setdefault("nintekkit_controller", sys.modules[__name__])

NAME = "Nintendo Switch Combined Joy-Cons"
DEFAULT_WINDOW_REGEX = r"^(?:Tekkit 2$|Minecraft(?:\s|$))"
BUTTON, AXIS, INITIAL = 1, 2, 0x80
EVENT = struct.Struct("=IhBB")


class ControllerError(RuntimeError):
    pass


class DeviceDisconnected(ControllerError):
    pass


@dataclass(frozen=True)
class JsEvent:
    time_ms: int
    value: int
    event_type: int
    number: int
    initial: bool = False

    @classmethod
    def parse(cls, data: bytes) -> "JsEvent":
        if len(data) != EVENT.size:
            raise ValueError("A Linux js_event is exactly 8 bytes")
        stamp, value, flags, number = EVENT.unpack(data)
        kind = flags & ~INITIAL
        if kind not in (BUTTON, AXIS):
            raise ValueError("Unexpected joystick event type: " + str(flags))
        if kind == BUTTON and value not in (0, 1):
            raise ValueError("A joystick button must be 0 or 1")
        return cls(stamp, value, kind, number, bool(flags & INITIAL))


class EventBuffer:
    def __init__(self):
        self.pending = b""

    def feed(self, data: bytes) -> list[JsEvent]:
        self.pending += data
        count = len(self.pending) // EVENT.size
        events = [JsEvent.parse(self.pending[i * 8:(i + 1) * 8]) for i in range(count)]
        self.pending = self.pending[count * 8:]
        return events


@dataclass(frozen=True)
class ControlContext:
    mode: str
    gui_class: str | None = None
    reason: str = ""


class ContextReader:
    """Read atomic, private companion state; unknown/stale never means world."""
    def __init__(self, path, pid, *, max_age_ms=2500, clock_ms=None, uid=None, check_private=True):
        self.path, self.pid = Path(path), pid
        self.max_age_ms = max_age_ms
        self.clock_ms = clock_ms or (lambda: time.time() * 1000)
        self.uid = os.getuid() if uid is None else uid
        self.check_private = check_private
        self.last_sequence = -1

    def read(self):
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise ValueError("context must be a small regular file")
            if info.st_uid != self.uid or (self.check_private and info.st_mode & 0o077):
                raise ValueError("context must be private to the current user")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
                raise ValueError("unsupported context schema")
            if type(data.get("pid")) is not int or data["pid"] != self.pid:
                raise ValueError("context belongs to a different Java PID")
            sequence, stamp = data.get("sequence"), data.get("updated_epoch_ms")
            if type(sequence) is not int or sequence < 0 or sequence < self.last_sequence:
                raise ValueError("context sequence rolled back or is invalid")
            if type(stamp) is not int or stamp <= 0:
                raise ValueError("invalid context timestamp")
            age = self.clock_ms() - stamp
            if age < -250 or age > self.max_age_ms:
                raise ValueError("context heartbeat is stale or in the future")
            world, gui, gui_class = data.get("world_loaded"), data.get("gui_open"), data.get("gui_class")
            if type(world) is not bool or type(gui) is not bool:
                raise ValueError("context flags must be booleans")
            if gui:
                if not isinstance(gui_class, str) or not re.fullmatch(r"[A-Za-z0-9_.$]{1,256}", gui_class):
                    raise ValueError("missing or invalid GUI class")
                result = ControlContext("gui", gui_class)
            elif gui_class is not None:
                raise ValueError("closed GUI must have null gui_class")
            elif world:
                result = ControlContext("world")
            else:
                result = ControlContext("unknown", reason="no world or GUI is ready")
            self.last_sequence = sequence
            return result
        except (OSError, ValueError, UnicodeError) as error:
            return ControlContext("unknown", reason=str(error))


class MappingState:
    """Pure state machine. Only this class owns generated key/button holds."""
    WORLD_KEYS = {0: ("Shift_L",), 1: ("space",), 2: ("e",), 3: ("e",),
                  9: ("Tab",), 10: ("Escape",), 12: ("Control_L",), 16: ("1",), 17: ("9",)}
    GUI_KEYS = {0: ("Escape",), 3: ("Escape",), 9: ("Control_L", "f"),
                10: ("Escape",), 12: ("Shift_L",), 14: ("r",), 15: ("u",), 16: ("1",), 17: ("9",)}
    WORLD_MOUSE = {7: 3, 8: 1, 13: 2}
    GUI_MOUSE = {1: 1, 7: 3, 8: 1, 13: 2}
    # Only used by deliberate unclean-exit recovery, never ordinary startup.
    ALL_KEYS = {"Shift_L", "space", "e", "Tab", "Escape", "Control_L", "1", "9", "f", "r", "u"}
    WHEEL_BUTTONS = {5: 4, 6: 5}

    def __init__(self, backend, deadzone=8000, camera_deadzone=6500, camera_speed=900, active=True,
                 context="unknown", gui_speed=700, gui_fine_speed=200, keyboard=None):
        if not 0 <= deadzone < 30000 or not 0 <= camera_deadzone < 30000:
            raise ValueError("Deadzones must be in [0, 30000)")
        if any(not math.isfinite(speed) or not 0 < speed <= 5000 for speed in (camera_speed, gui_speed, gui_fine_speed)):
            raise ValueError("Pointer speeds must be in (0, 5000] pixels/second")
        self.backend = backend
        self.keyboard = keyboard
        self.deadzone, self.camera_deadzone, self.camera_speed = deadzone, camera_deadzone, camera_speed
        self.gui_speed, self.gui_fine_speed = gui_speed, gui_fine_speed
        self.context, self.gui_class = "unknown", None
        self.axes = [0] * 4
        self.buttons = [0] * 18
        self.seen_axes, self.seen_buttons = set(), set()
        self.keys, self.mouse = set(), set()
        self.active, self.focused, self.armed = active, True, False
        self.collecting_initial = True
        self.remainder = [0.0, 0.0]
        self.set_context(context)

    @property
    def complete(self):
        return len(self.seen_axes) == 4 and len(self.seen_buttons) == 18

    @property
    def neutral(self):
        return (self.complete and not any(self.buttons)
                and all(abs(value) <= (self.deadzone if i < 2 else self.camera_deadzone)
                        for i, value in enumerate(self.axes)))

    @staticmethod
    def key_order(key):
        return ({"Control_L": 0, "Shift_L": 1}.get(key, 2), key)

    def _arm_if_neutral(self):
        if self.active and self.focused and self.context != "unknown" and self.neutral:
            self.armed = True
            self.collecting_initial = False
        self._keyboard_gate()

    def _keyboard_gate(self):
        if self.keyboard:
            self.keyboard.set_allowed(self.active and self.focused and self.armed and self.context == "gui")

    def set_context(self, context, gui_class=None):
        if context not in {"world", "gui", "unknown"}:
            context = "unknown"
        signature = (context, gui_class if context == "gui" else None)
        if signature != (self.context, self.gui_class):
            if self.keyboard: self.keyboard.set_allowed(False)
            self.release_all()
            self.armed = False
            self.context, self.gui_class = signature
            self._arm_if_neutral()

    def set_active(self, active):
        self.active = bool(active)
        if not active:
            self.release_all()
            self.armed = False
            self._keyboard_gate()
        else:
            self._arm_if_neutral()

    def set_focus(self, focused):
        self.focused = bool(focused)
        if not focused:
            self.release_all()
            self.armed = False
            self._keyboard_gate()
        else:
            self._arm_if_neutral()

    def handle(self, event: JsEvent):
        if event.event_type not in (AXIS, BUTTON):
            self.release_all()
            self.armed = False
            raise ValueError("Unsupported joystick event")
        limit = 4 if event.event_type == AXIS else 18
        if not 0 <= event.number < limit:
            self.release_all()
            self.armed = False
            raise ValueError("Controller capabilities changed")
        # joydev sends another INIT series when its event queue overflows.
        seen = self.seen_axes if event.event_type == AXIS else self.seen_buttons
        if event.initial and (not self.collecting_initial or event.number in seen):
            self.release_all()
            self.armed = False
            self.seen_axes.clear()
            self.seen_buttons.clear()
            self.collecting_initial = True
        previous = self.buttons[event.number] if event.event_type == BUTTON else None
        if event.event_type == AXIS:
            self.axes[event.number] = event.value
            self.seen_axes.add(event.number)
        else:
            self.buttons[event.number] = event.value
            self.seen_buttons.add(event.number)
        if not self.armed:
            self._arm_if_neutral()
            return
        if not self.active or not self.focused:
            return
        self._sync_holds()
        if event.event_type == BUTTON and not event.initial and event.value and not previous:
            if event.number == 4 and self.context == "gui" and self.keyboard:
                self.keyboard.toggle()
            wheel = self.WHEEL_BUTTONS.get(event.number)
            if wheel:
                self.mouse.add(wheel)
                self.backend.button(wheel, True)
                self.backend.button(wheel, False)
                self.mouse.discard(wheel)

    def _sync_holds(self):
        if self.context == "unknown":
            self.release_all()
            return
        bindings = self.WORLD_KEYS if self.context == "world" else self.GUI_KEYS
        desired_keys = {key for button, chord in bindings.items() if self.buttons[button]
                        for key in chord}
        x, y = self.axes[:2]
        for condition, key in ((x < -self.deadzone, "a"), (x > self.deadzone, "d"),
                               (y < -self.deadzone, "w"), (y > self.deadzone, "s")):
            if condition and self.context == "world":
                desired_keys.add(key)
        mouse_bindings = self.WORLD_MOUSE if self.context == "world" else self.GUI_MOUSE
        desired_mouse = {mouse for button, mouse in mouse_bindings.items() if self.buttons[button]}
        for key in sorted(self.keys - desired_keys, key=self.key_order, reverse=True):
            self.backend.key(key, False)
            self.keys.remove(key)
        for button in sorted(self.mouse - desired_mouse):
            self.backend.button(button, False)
            self.mouse.remove(button)
        for key in sorted(desired_keys - self.keys, key=self.key_order):
            # Track before the call so cleanup retries after an uncertain backend failure.
            self.keys.add(key)
            self.backend.key(key, True)
        for button in sorted(desired_mouse - self.mouse):
            self.mouse.add(button)
            self.backend.button(button, True)

    def tick(self, dt):
        if not (self.active and self.focused and self.armed and self.context != "unknown"):
            return
        dt = min(max(float(dt), 0), 0.05) # no camera catch-up after scheduling stalls
        movement = []
        def speed(value, deadzone, maximum):
            if abs(value) <= deadzone:
                return 0
            magnitude = min(1, (abs(value) - deadzone) / (32767 - deadzone))
            return math.copysign(magnitude ** 1.7 * maximum, value)
        for i, value in enumerate(self.axes[2:]):
            rate = speed(value, self.camera_deadzone, self.camera_speed if self.context == "world" else self.gui_fine_speed)
            if self.context == "gui":
                rate += speed(self.axes[i], self.deadzone, self.gui_speed)
            if rate == 0:
                self.remainder[i] = 0
                movement.append(0)
                continue
            self.remainder[i] += rate * dt
            pixels = math.trunc(self.remainder[i])
            self.remainder[i] -= pixels
            movement.append(pixels)
        if any(movement):
            self.backend.motion(*movement)

    def release_all(self):
        if self.keyboard: self.keyboard.set_allowed(False)
        failures = []
        for button in sorted(self.mouse):
            try:
                self.backend.button(button, False)
                self.mouse.discard(button)
            except Exception as error:
                failures.append(error)
        for key in sorted(self.keys, key=self.key_order, reverse=True):
            try:
                self.backend.key(key, False)
                self.keys.discard(key)
            except Exception as error:
                failures.append(error)
        self.remainder[:] = [0, 0]
        if failures:
            raise ControllerError("Could not release every generated hold: " + str(failures[0]))


@dataclass(frozen=True)
class JoystickInfo:
    path: str
    sysfs: str
    name: str
    rdev: int


def discover_joystick(sysfs=Path("/sys/class/input"), devices=Path("/dev/input")):
    found = []
    for item in sorted(sysfs.glob("js*")):
        try:
            if (item / "device/name").read_text().strip() == NAME:
                path = devices / item.name
                found.append(JoystickInfo(str(path), str(item.resolve()), NAME, path.stat().st_rdev))
        except OSError:
            continue
    if len(found) != 1:
        raise ControllerError("Expected one combined Joy-Con joystick; found " + str(len(found)))
    return found[0]


class Joystick:
    def __init__(self, info):
        import fcntl
        self.info, self.fd = info, None
        self.buffer = EventBuffer()
        try:
            self.fd = os.open(info.path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            axes, buttons = bytearray(1), bytearray(1)
            fcntl.ioctl(self.fd, 0x80016a11, axes, True)
            fcntl.ioctl(self.fd, 0x80016a12, buttons, True)
            if (axes[0], buttons[0]) != (4, 18):
                raise ControllerError("Expected exactly 4 axes and 18 raw buttons")
            if os.fstat(self.fd).st_rdev != info.rdev:
                raise ControllerError("Joystick identity changed during open")
        except BaseException:
            self.close()
            raise

    def read(self):
        events = []
        # Drain stale queued input before the next camera tick; bound a faulty device.
        for _ in range(128):
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                return events
            except OSError as error:
                if error.errno in (errno.ENODEV, errno.ENXIO, errno.EIO):
                    raise DeviceDisconnected("Combined joystick disconnected") from error
                raise
            if not data:
                raise DeviceDisconnected("Joystick disconnected (EOF)")
            events.extend(self.buffer.feed(data))
        raise ControllerError("Joystick event flood; stopped without camera catch-up")

    def identity_valid(self):
        try:
            current = discover_joystick()
            return current == self.info
        except (OSError, ControllerError):
            return False

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@dataclass(frozen=True)
class XDevice:
    id: int
    name: str
    role: str
    master: int
    master_name: str = ""


def xinput(*arguments):
    try:
        return subprocess.check_output(["xinput", *map(str, arguments)], text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ControllerError("xinput failed: " + str(error)) from error


def discover_x_devices(call=xinput):
    found = []
    for name, role in ((NAME, "pointer"), (NAME + " (keys)", "keyboard")):
        value = call("list", "--id-only", name)
        if not re.fullmatch(r"[0-9]+", value):
            raise ControllerError("Expected exactly one XInput device named " + name)
        device_id = int(value)
        if call("list", "--name-only", device_id) != name:
            raise ControllerError("XInput identity changed")
        line = call("list", "--short", device_id)
        attachment = re.search(r"\[slave\s+(pointer|keyboard)\s+\(([0-9]+)\)\]", line)
        if not attachment or attachment[1] != role:
            raise ControllerError("Combined XInput device must already have its normal " + role + " attachment")
        master = int(attachment[2])
        if not re.search(r"\[master\s+" + role + r"\s+\([0-9]+\)\]", call("list", "--short", master)):
            raise ControllerError("Original XInput master role is not valid")
        found.append(XDevice(device_id, name, role, master, call("list", "--name-only", master)))
    if found[0].id == found[1].id:
        raise ControllerError("Pointer and keyboard identities overlap")
    return found


def property_int(properties, name):
    match = re.search(r"^\s*" + re.escape(name) + r"\s+\([0-9]+\):\s*([0-9]+)\s*$", properties, re.M)
    if not match:
        raise ControllerError("Missing or unexpected XInput property: " + name)
    return int(match[1])


class SessionGuard:
    """Transactional ownership of two XInput attachments; never changes system files."""
    def __init__(self, call=xinput):
        self.call = call
        self.devices = discover_x_devices(call)
        properties = call("list-props", self.devices[0].id)
        if property_int(properties, "Buttons") != 18 or property_int(properties, "Axes") != 4:
            raise ControllerError("Unexpected Xorg controller capabilities")
        if property_int(properties, "Device Enabled") != 1:
            raise ControllerError("Existing controller is disabled; refusing to take ownership")
        self.flags = {name: property_int(properties, name) for name in ("Generate Mouse Events", "Generate Key Events")}
        if any(value not in (0, 1) for value in self.flags.values()):
            raise ControllerError("Unexpected driver output flags")
        self.touched = []
        self.flags_dirty = False

    def identity_valid(self, device):
        try:
            return self.call("list", "--name-only", device.id) == device.name
        except ControllerError:
            return False

    def check(self, query=None):
        for device in self.devices:
            if query is None:
                if not self.identity_valid(device):
                    raise ControllerError("An XInput device disappeared or changed identity")
                line = self.call("list", "--short", device.id)
                floating = "[floating slave]" in line
                attached = bool(re.search(r"\[slave\s+" + device.role + r"\s+\(" + str(device.master) + r"\)\]", line))
            else:
                current = query(device.id)
                if current is None or current["name"] != device.name:
                    raise ControllerError("An XInput device disappeared or changed identity")
                floating = current["use"] == 5 # XIFloatingSlave
                attached = current["use"] == (3 if device.role == "pointer" else 4) and current["attachment"] == device.master
            if self.touched and not floating:
                raise ControllerError("Original X input was reattached while mapping; stopped to avoid duplicates")
            if not self.touched and not attached:
                raise ControllerError("Original XInput attachment changed before acquisition")

    def snapshot(self):
        return {"schema": 1, "display": os.environ.get("DISPLAY"), "uid": os.getuid(),
                "devices": [asdict(device) for device in self.devices], "driver_flags": self.flags,
                "state": "before-float"}

    def float_all(self):
        self.check()
        self.flags_dirty = True
        try:
            for device in self.devices:
                # Mark before the operation: a failed client call can have an uncertain server result.
                self.touched.append(device)
                self.call("float", device.id)
                if "[floating slave]" not in self.call("list", "--short", device.id):
                    raise ControllerError("XInput float was not confirmed")
        except BaseException:
            self.restore()
            raise

    def restore(self):
        failures = []
        if not self.touched and not self.flags_dirty:
            return
        pointer = self.devices[0]
        flag_failures = False
        if self.identity_valid(pointer):
            for name, value in self.flags.items():
                try:
                    self.call("set-prop", "--type=int", "--format=8", pointer.id, name, value)
                    if property_int(self.call("list-props", pointer.id), name) != value:
                        raise ControllerError("Driver flag restoration was not confirmed")
                except Exception as error:
                    flag_failures = True
                    failures.append(error)
        self.flags_dirty = flag_failures
        # Do not attach an unrelated recycled ID. A disappeared device's successor
        # starts with the system's untouched default attachments/configuration.
        remaining = []
        for device in reversed(self.touched):
            if not self.identity_valid(device):
                continue
            try:
                if (self.call("list", "--name-only", device.master) != device.master_name
                        or not re.search(r"\[master\s+" + device.role + r"\s+\([0-9]+\)\]",
                                         self.call("list", "--short", device.master))):
                    raise ControllerError("Original XInput master identity changed")
                self.call("reattach", device.id, device.master)
                line = self.call("list", "--short", device.id)
                if not re.search(r"\[slave\s+" + device.role + r"\s+\(" + str(device.master) + r"\)\]", line):
                    raise ControllerError("XInput restoration was not confirmed")
            except Exception as error:
                remaining.append(device)
                failures.append(error)
        self.touched = list(reversed(remaining))
        if failures:
            raise ControllerError("Controller restoration needs attention: " + str(failures[0]))


class XResClientIdSpec(C.Structure):
    # libXres 1.2 ABI: XID is unsigned long, but mask is unsigned int.
    _fields_ = [("client", C.c_ulong), ("mask", C.c_uint)]


class XResClientIdValue(C.Structure):
    _fields_ = [("spec", XResClientIdSpec), ("length", C.c_long), ("value", C.c_void_p)]


class XlibRuntime:
    """One process-wide callback, retained until every X connection has closed."""
    def __init__(self):
        self.lock = threading.RLock()
        self.initialized, self.users = False, 0
        self.callback = self.previous = None

    def acquire(self, library):
        with self.lock:
            if not self.initialized:
                library.XInitThreads.argtypes, library.XInitThreads.restype = [], C.c_int
                if not library.XInitThreads():
                    raise ControllerError("Xlib thread initialization failed")
                self.initialized = True
            library.XSetErrorHandler.argtypes, library.XSetErrorHandler.restype = [C.c_void_p], C.c_void_p
            if not self.users:
                self.callback = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda display, event: 0)
                self.previous = library.XSetErrorHandler(C.cast(self.callback, C.c_void_p))
            self.users += 1

    def release(self, library):
        with self.lock:
            self.users -= 1
            if not self.users:
                library.XSetErrorHandler(self.previous)
                self.callback = self.previous = None


_XLIB_RUNTIME = XlibRuntime()


class XConnection:
    def __init__(self, inject=False, query_extensions=False):
        display = os.environ.get("DISPLAY", "")
        if not display.startswith(":"):
            raise ControllerError("Use the existing local X11 session through desktop-run.py")
        library = ctypes.util.find_library("X11")
        if not library:
            raise ControllerError("libX11 is unavailable")
        self.x = C.CDLL(library)
        self._xlib_registered = False
        self.x.XOpenDisplay.argtypes, self.x.XOpenDisplay.restype = [C.c_char_p], C.c_void_p
        self.x.XCloseDisplay.argtypes = [C.c_void_p]
        self.x.XFlush.argtypes = [C.c_void_p]
        self.x.XSync.argtypes = [C.c_void_p, C.c_int]
        self.x.XGetInputFocus.argtypes = [C.c_void_p, C.POINTER(C.c_ulong), C.POINTER(C.c_int)]
        self.x.XFetchName.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_char_p)]
        self.x.XQueryTree.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
                                     C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint)]
        self.x.XFree.argtypes = [C.c_void_p]
        self.x.XInternAtom.argtypes, self.x.XInternAtom.restype = [C.c_void_p, C.c_char_p, C.c_int], C.c_ulong
        self.x.XGetWindowProperty.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long,
                                             C.c_int, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
                                             C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)]
        self.x.XStringToKeysym.argtypes, self.x.XStringToKeysym.restype = [C.c_char_p], C.c_ulong
        self.x.XKeysymToKeycode.argtypes, self.x.XKeysymToKeycode.restype = [C.c_void_p, C.c_ulong], C.c_ubyte
        # No Xlib calls occur above: thread support is initialized before the
        # shared error registration and before the first XOpenDisplay.
        _XLIB_RUNTIME.acquire(self.x)
        self._xlib_registered = True
        try:
            self.display = self.x.XOpenDisplay(None)
        except BaseException:
            self.close()
            raise
        if not self.display:
            self.close()
            raise ControllerError("Cannot open the existing X display")
        self.xtest, self.xi, self.xres, self.keycodes = None, None, None, {}
        self.xres_version, self.last_pid_source = None, None
        self.allow_injection = inject
        try:
            self.pid_atom = self.x.XInternAtom(self.display, b"_NET_WM_PID", 1)
            self._open_xres()
            if inject or query_extensions:
                library = ctypes.util.find_library("Xtst")
                if not library:
                    raise ControllerError("libXtst is unavailable")
                self.xtest = C.CDLL(library)
                self.xtest.XTestQueryExtension.argtypes = [C.c_void_p, *([C.POINTER(C.c_int)] * 4)]
                values = [C.c_int() for _ in range(4)]
                if not self.xtest.XTestQueryExtension(self.display, *map(C.byref, values)):
                    raise ControllerError("XTest extension is unavailable")
                self.xtest.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
                self.xtest.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
                self.xtest.XTestFakeRelativeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_ulong]
                library = ctypes.util.find_library("Xi")
                if not library:
                    raise ControllerError("libXi is unavailable")
                self.xi = C.CDLL(library)
                class DeviceInfo(C.Structure):
                    _fields_ = [("deviceid", C.c_int), ("name", C.c_char_p), ("use", C.c_int),
                                ("attachment", C.c_int), ("enabled", C.c_int), ("num_classes", C.c_int),
                                ("classes", C.c_void_p)]
                self.xi.XIQueryDevice.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_int)]
                self.xi.XIQueryDevice.restype = C.POINTER(DeviceInfo)
                self.xi.XIFreeDeviceInfo.argtypes = [C.POINTER(DeviceInfo)]
        except BaseException:
            self.close()
            raise

    def _open_xres(self):
        """Negotiate the optional read-only local-client PID API (XRes 1.2)."""
        library = ctypes.util.find_library("XRes")
        if not library:
            return
        try:
            xres = C.CDLL(library)
            xres.XResQueryExtension.argtypes = [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]
            xres.XResQueryExtension.restype = C.c_int
            xres.XResQueryVersion.argtypes = [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]
            xres.XResQueryVersion.restype = C.c_int
            xres.XResQueryClientIds.argtypes = [C.c_void_p, C.c_long, C.POINTER(XResClientIdSpec),
                                               C.POINTER(C.c_long), C.POINTER(C.POINTER(XResClientIdValue))]
            xres.XResQueryClientIds.restype = C.c_int
            xres.XResGetClientPid.argtypes = [C.POINTER(XResClientIdValue)]
            xres.XResGetClientPid.restype = C.c_int # pid_t on the Linux target
            xres.XResClientIdsDestroy.argtypes = [C.c_long, C.POINTER(XResClientIdValue)]
            xres.XResClientIdsDestroy.restype = None
        except (OSError, AttributeError):
            return
        event, error, major, minor = C.c_int(), C.c_int(), C.c_int(), C.c_int()
        if not xres.XResQueryExtension(self.display, C.byref(event), C.byref(error)):
            return
        if not xres.XResQueryVersion(self.display, C.byref(major), C.byref(minor)):
            return
        if (major.value, minor.value) < (1, 2):
            return
        self.xres, self.xres_version = xres, (major.value, minor.value)

    def device_info(self, device_id):
        count = C.c_int()
        devices = self.xi.XIQueryDevice(self.display, device_id, C.byref(count))
        if not devices:
            return None
        try:
            if count.value != 1:
                return None
            device = devices[0]
            return {"id": device.deviceid, "name": device.name.decode(errors="replace"),
                    "use": device.use, "attachment": device.attachment, "enabled": bool(device.enabled)}
        finally:
            self.xi.XIFreeDeviceInfo(devices)

    def window_pid(self, window):
        self.last_pid_source = None
        if self.pid_atom:
            actual, format_, count, after, data = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong(), C.c_void_p()
            status = self.x.XGetWindowProperty(self.display, window, self.pid_atom, 0, 1, 0, 6,
                                              C.byref(actual), C.byref(format_), C.byref(count),
                                              C.byref(after), C.byref(data))
            try:
                if status == 0 and actual.value == 6 and format_.value == 32 and count.value == 1 and data:
                    pid = int(C.cast(data, C.POINTER(C.c_ulong))[0])
                    if 0 < pid <= 0x7fffffff:
                        self.last_pid_source = "_NET_WM_PID"
                        return pid
            finally:
                if data:
                    self.x.XFree(data)
        pid = self._xres_window_pid(window)
        if pid is not None:
            self.last_pid_source = "XRes"
        return pid

    def _xres_window_pid(self, window):
        """Ask Xorg which local process owns this resource; never infer from its title."""
        if not self.xres:
            return None
        spec, count = XResClientIdSpec(window, 2), C.c_long()
        values = C.POINTER(XResClientIdValue)()
        # Unlike QueryExtension/QueryVersion, this API returns Success == 0.
        status = self.xres.XResQueryClientIds(self.display, 1, C.byref(spec), C.byref(count), C.byref(values))
        try:
            if status != 0 or not values or not 0 < count.value <= 16:
                return None
            pids = set()
            for index in range(count.value):
                value = values[index]
                if value.spec.mask != 2 or value.length != 4 or not value.value:
                    continue
                pid = int(self.xres.XResGetClientPid(C.byref(value)))
                if pid > 0:
                    pids.add(pid)
            if len(pids) == 1:
                return pids.pop()
        finally:
            if values:
                self.xres.XResClientIdsDestroy(count.value, values)
        return None

    def focused(self, pattern, pid=None, window_id=None):
        window, revert = C.c_ulong(), C.c_int()
        self.x.XGetInputFocus(self.display, C.byref(window), C.byref(revert))
        current = window.value
        title_match, pid_match, id_match = False, pid is None, window_id is None
        for _ in range(16):
            if current in (0, 1):
                break
            title = C.c_char_p()
            if self.x.XFetchName(self.display, current, C.byref(title)) and title:
                try:
                    if pattern.search(title.value.decode(errors="replace")):
                        title_match = True
                finally:
                    self.x.XFree(C.cast(title, C.c_void_p))
            if pid is not None and self.window_pid(current) == pid:
                pid_match = True
            if window_id is not None and current == window_id:
                id_match = True
            if title_match and pid_match and id_match:
                return True
            root, parent, children, count = C.c_ulong(), C.c_ulong(), C.POINTER(C.c_ulong)(), C.c_uint()
            if not self.x.XQueryTree(self.display, current, C.byref(root), C.byref(parent), C.byref(children), C.byref(count)):
                break
            if children:
                self.x.XFree(children)
            if not parent.value or parent.value == current:
                break
            current = parent.value
        return False

    def key(self, name, down):
        if not self.allow_injection or not self.xtest:
            raise ControllerError("Injection was not explicitly enabled")
        if name not in self.keycodes:
            keysym = self.x.XStringToKeysym(name.encode("ascii"))
            code = self.x.XKeysymToKeycode(self.display, keysym)
            if not keysym or not code:
                raise ControllerError("Current X keymap does not contain " + name)
            self.keycodes[name] = code
        if not self.xtest.XTestFakeKeyEvent(self.display, self.keycodes[name], int(down), 0):
            raise ControllerError("XTest key event failed")
        self.x.XFlush(self.display)

    def button(self, number, down):
        if not self.allow_injection or not self.xtest or not self.xtest.XTestFakeButtonEvent(self.display, number, int(down), 0):
            raise ControllerError("XTest mouse event failed or injection was not enabled")
        self.x.XFlush(self.display)

    def motion(self, dx, dy):
        if not self.allow_injection or not self.xtest or not self.xtest.XTestFakeRelativeMotionEvent(self.display, dx, dy, 0):
            raise ControllerError("XTest motion failed or injection was not enabled")
        self.x.XFlush(self.display)

    def close(self):
        try:
            if getattr(self, "display", None):
                self.x.XCloseDisplay(self.display)
                self.display = None
        finally:
            if getattr(self, "_xlib_registered", False):
                self._xlib_registered = False
                _XLIB_RUNTIME.release(self.x)


class DryBackend:
    def key(self, name, down): print(json.dumps({"would_key": name, "down": down}), flush=True)
    def button(self, number, down): print(json.dumps({"would_button": number, "down": down}), flush=True)
    def motion(self, dx, dy): print(json.dumps({"would_motion": [dx, dy]}), flush=True)


def private_directory():
    path = Path(os.environ.get("XDG_RUNTIME_DIR", str(Path.home() / ".cache/nintekkit")))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ControllerError("Runtime directory must belong to the current user")
    return path


def write_snapshot(guard, joystick, path):
    data = guard.snapshot()
    data["joystick"] = asdict(joystick)
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(data, stream, indent=2)
        stream.write("\n")


def restore_snapshot(path, call=xinput):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("uid") != os.getuid() or data.get("display") != os.environ.get("DISPLAY"):
        raise ControllerError("Recovery snapshot does not belong to this user/display")
    devices = [XDevice(**item) for item in data["devices"]]
    if (len(devices) != 2 or [(d.name, d.role) for d in devices] != [(NAME, "pointer"), (NAME + " (keys)", "keyboard")]
            or any(type(d.id) is not int or type(d.master) is not int or d.id <= 0 or d.master <= 0 for d in devices)):
        raise ControllerError("Recovery snapshot has unexpected devices")
    flags = data["driver_flags"]
    if set(flags) != {"Generate Mouse Events", "Generate Key Events"} or any(type(value) is not int or value not in (0, 1) for value in flags.values()):
        raise ControllerError("Recovery snapshot has unexpected flags")
    guard = object.__new__(SessionGuard)
    guard.call, guard.devices, guard.flags = call, devices, flags
    guard.touched, guard.flags_dirty = devices[:], True
    guard.restore()
    print("Original controller attachments and driver flags restored", flush=True)


def process_identity(pid):
    """Never inspect Java command lines, which can contain authentication tokens."""
    proc = Path("/proc") / str(pid)
    try:
        executable = (proc / "exe").resolve(strict=True)
        if executable.name != "java":
            raise ControllerError("--pid must identify the actual game's Java process")
        stat = (proc / "stat").read_text()
        # Field 22: starttime. The comm field may contain spaces and parentheses.
        start = stat[stat.rfind(")") + 2:].split()[19]
        return str(executable), start
    except OSError as error:
        raise ControllerError("Cannot inspect the game's Java process") from error


def run(args):
    import fcntl
    pattern = re.compile(args.window_regex, re.I)
    if not os.environ.get("DISPLAY", "").startswith(":"):
        raise ControllerError("Run through desktop-run.py in the actual local X11 display")
    if args.restore_state:
        with (private_directory() / "nintekkit-controller.lock").open("a+") as recovery_lock:
            try:
                fcntl.flock(recovery_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ControllerError("Stop the active mapper before restoring its snapshot") from error
            if args.release_stuck:
                connection = XConnection(inject=True)
                try:
                    recovery = MappingState(connection, active=False)
                    recovery.keys = MappingState.ALL_KEYS | set("wasd")
                    recovery.mouse = {1, 2, 3, 4, 5}
                    recovery.release_all()
                finally:
                    connection.close()
            restore_snapshot(args.restore_state)
        return 0
    info = discover_joystick()
    if args.inspect:
        joystick = Joystick(info)
        inspection = None
        try:
            guard = SessionGuard()
            inspection = XConnection(inject=False, query_extensions=True)
            xi_devices = [inspection.device_info(device.id) for device in guard.devices]
            guard.check(query=inspection.device_info)
            context = ContextReader(args.context_file, args.pid).read() if args.context_file and args.pid else None
            print(json.dumps({"joystick": asdict(info), "axes": 4, "buttons": 18,
                              "x_session": guard.snapshot(), "xi_query_devices": xi_devices,
                              "native_abi_queries": "X11/XTest extension/XInput2 read-only queries succeeded",
                              "focused_game_title": inspection.focused(pattern),
                              "control_context": asdict(context) if context else None,
                              "mode": "inspect-no-input-changes"}, indent=2))
        finally:
            if inspection: inspection.close()
            joystick.close()
        return 0
    tracking = process_identity(args.pid) if args.pid else None
    contexts = ContextReader(args.context_file, args.pid) if args.context_file and args.pid else None
    stop = False
    def terminate(signum, frame):
        nonlocal stop
        stop = True
    old_handlers = {sig: signal.signal(sig, terminate) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    connection = lock = keyboard = None
    deadline = time.monotonic() + args.seconds if args.seconds else math.inf
    try:
        if args.run:
            directory = private_directory()
            lock = (directory / "nintekkit-controller.lock").open("a+")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ControllerError("Another Nintekkit mapper is already active") from error
        connection = XConnection(inject=args.run)
        from on_screen_keyboard import make_keyboard
        keyboard = make_keyboard(dry_run=not args.run, pid=args.pid,
                                 valid=lambda: tracking is not None and process_identity(args.pid) == tracking)
        while not stop and time.monotonic() < deadline:
            if tracking is not None:
                try:
                    if process_identity(args.pid) != tracking:
                        break
                except ControllerError:
                    break
            info = discover_joystick()
            joystick, guard, state = None, None, None
            disconnected = False
            try:
                joystick = Joystick(info)
                guard = SessionGuard() if args.run else None
                backend = connection if args.run else DryBackend()
                state = MappingState(backend, args.deadzone, args.camera_deadzone, args.camera_speed, active=False,
                                     gui_speed=args.gui_speed, gui_fine_speed=args.gui_fine_speed, keyboard=keyboard)
                previous, next_tick, last_check, neutral_since = time.monotonic(), 0, 0, None
                last_context = None
                print("Waiting for complete joystick initialization, neutral controls and game focus", flush=True)
                while not stop and time.monotonic() < deadline:
                    now = time.monotonic()
                    if now >= next_tick:
                        state.set_focus(connection.focused(pattern, args.pid, args.focus_window))
                        context = contexts.read() if contexts else ControlContext("unknown", reason="No verified companion context configured")
                        state.set_context(context.mode, context.gui_class)
                        summary = (context.mode, context.gui_class, context.reason)
                        if summary != last_context:
                            print("Control context: " + json.dumps(asdict(context)), flush=True)
                            last_context = summary
                    if now - last_check >= 0.1:
                        last_check = now
                        if not joystick.identity_valid():
                            raise DeviceDisconnected("Combined joystick disconnected or changed identity")
                        if guard:
                            guard.check(query=connection.device_info)
                        if tracking is not None:
                            try:
                                if process_identity(args.pid) != tracking:
                                    stop = True
                            except ControllerError:
                                stop = True
                    if stop:
                        break
                    for event in joystick.read():
                        if stop:
                            break
                        state.handle(event)
                    if stop:
                        break
                    if not state.active and state.neutral and state.focused:
                        neutral_since = now if neutral_since is None else neutral_since
                    else:
                        neutral_since = None
                    if neutral_since is not None and now - neutral_since >= 0.1 and not stop:
                        if guard:
                            # Capture/desktop use can change flags while waiting.
                            # Snapshot the state immediately before taking ownership.
                            guard = SessionGuard()
                            snapshot = private_directory() / ("nintekkit-controller-state-" + uuid.uuid4().hex + ".json")
                            write_snapshot(guard, info, snapshot)
                            print("Recovery snapshot: " + str(snapshot), flush=True)
                            guard.float_all()
                            # Re-read events after the transaction; a movement during
                            # setup must wait for neutral rather than injecting a jump.
                            for event in joystick.read():
                                state.handle(event)
                        if stop:
                            break
                        state.set_active(True)
                        print("Controller acquired; neutral controls arm mapping only in matching game windows", flush=True)
                    if not stop and now >= next_tick:
                        state.tick(now - previous)
                        previous, next_tick = now, now + 1 / 60
                    select.select([joystick.fd], [], [], max(0, next_tick - time.monotonic()))
            except (OSError, ControllerError) as error:
                print("Controller paused: " + str(error), file=sys.stderr, flush=True)
                disconnected = isinstance(error, DeviceDisconnected)
                if not disconnected or not args.reconnect:
                    raise
            finally:
                cleanup_errors = []
                if state:
                    if keyboard: keyboard.set_allowed(False)
                    try: state.release_all()
                    except Exception as error: cleanup_errors.append(str(error))
                if guard:
                    try: guard.restore()
                    except Exception as error: cleanup_errors.append(str(error))
                if joystick:
                    joystick.close()
                if cleanup_errors:
                    raise ControllerError("; ".join(cleanup_errors))
            if not (disconnected and args.reconnect and not stop):
                break
            print("Waiting for a fresh combined controller; desktop state restored", flush=True)
            while not stop and time.monotonic() < deadline:
                time.sleep(0.2)
                if tracking is not None:
                    try:
                        if process_identity(args.pid) != tracking:
                            stop = True
                    except ControllerError:
                        stop = True
                if stop:
                    break
                try:
                    discover_joystick()
                    break
                except ControllerError:
                    time.sleep(0.2)
        return 0
    finally:
        if keyboard: keyboard.close()
        if connection: connection.close()
        if lock: lock.close()
        for sig, handler in old_handlers.items(): signal.signal(sig, handler)
        # The game remains owned by the user; never kill it.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--inspect", action="store_true", help="Read identities/capabilities only; never float or inject")
    modes.add_argument("--dry-run", action="store_true", help="Read events and print intended actions; never float or inject")
    modes.add_argument("--run", action="store_true", help="Explicitly enable game-scoped XTest input and temporary floating")
    parser.add_argument("--deadzone", type=int, default=8000)
    parser.add_argument("--camera-deadzone", type=int, default=6500)
    parser.add_argument("--camera-speed", type=float, default=900, help="Maximum pixels/second; physically unvalidated default")
    parser.add_argument("--gui-speed", type=float, default=700, help="Left-stick GUI pointer pixels/second")
    parser.add_argument("--gui-fine-speed", type=float, default=200, help="Right-stick fine GUI pointer pixels/second")
    parser.add_argument("--context-file", type=Path, help="Private state JSON written by the client companion")
    parser.add_argument("--window-regex", default=DEFAULT_WINDOW_REGEX, help="Focused game window title expression")
    parser.add_argument("--seconds", type=float, help="Optional bounded test duration")
    parser.add_argument("--reconnect", action="store_true", help="Restore on unplug, then reacquire fresh identities and neutral state")
    parser.add_argument("--pid", type=int, help="Actual Java game PID; reads exe/starttime, never command-line tokens")
    parser.add_argument("--focus-window", type=lambda value: int(value, 0), help="Explicit observed game window ID, decimal or 0xHEX")
    parser.add_argument("--restore-state", type=Path, help="With --run, explicitly restore a saved snapshot after an unclean exit")
    parser.add_argument("--release-stuck", action="store_true", help="Recovery only: issue releases for the finite mapped key/button set")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Run on the Linux Switch in its existing X11 session")
    if args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds <= 0):
        parser.error("--seconds must be positive and finite")
    if args.restore_state and (not args.run or args.pid or args.focus_window or args.context_file):
        parser.error("Recovery requires --run --restore-state FILE without game tracking/launch options")
    if args.release_stuck and not args.restore_state:
        parser.error("--release-stuck requires explicit --run --restore-state FILE recovery")
    if args.run and not args.restore_state and (args.pid is None or args.context_file is None):
        parser.error("--run requires the actual Java --pid and explicit --context-file")
    if args.context_file is not None and args.pid is None:
        parser.error("--context-file requires the actual Java --pid for state ownership")
    if args.focus_window is not None and args.focus_window <= 1:
        parser.error("--focus-window must identify a real game window")
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControllerError, OSError, ValueError, re.error) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
