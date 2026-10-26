"""Native-call ordering/refcount tests with no actual X display or input."""
import ctypes as C
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/controller"))
import nintekkit_controller as mapper


class Function:
    def __init__(self, call): self.call = call
    def __call__(self, *args): return self.call(*args)


class Library:
    def __init__(self, open_ok=True, threads_ok=True):
        self.calls, self.handler = [], 12345
        self.next_display, self.open_ok, self.threads_ok = 100, open_ok, threads_ok
        self.functions = {}
    def __getattr__(self, name):
        if name not in self.functions:
            def call(*args):
                self.calls.append((name, args))
                if name == "XInitThreads": return int(self.threads_ok)
                if name == "XSetErrorHandler":
                    old = self.handler
                    self.handler = args[0].value if isinstance(args[0], C.c_void_p) else args[0]
                    return old
                if name == "XOpenDisplay":
                    self.next_display += 1
                    return self.next_display if self.open_ok else None
                return 1
            self.functions[name] = Function(call)
        return self.functions[name]


class XlibRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime, self.library = mapper.XlibRuntime(), Library()
        patches = [patch.object(mapper, "_XLIB_RUNTIME", self.runtime),
                   patch.object(mapper.ctypes.util, "find_library", return_value="fake-X11"),
                   patch.object(mapper.C, "CDLL", return_value=self.library),
                   patch.dict(mapper.os.environ, {"DISPLAY": ":0"}),
                   patch.object(mapper.XConnection, "_open_xres")]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_init_first_once_callback_retained_for_either_close_order(self):
        for reverse in (False, True):
            self.library.calls.clear()
            first, second = mapper.XConnection(), mapper.XConnection()
            calls = [name for name, _ in self.library.calls]
            self.assertEqual(calls[0], "XInitThreads" if not reverse else "XSetErrorHandler")
            self.assertEqual(calls.count("XInitThreads"), 0 if reverse else 1)
            self.assertEqual(calls.count("XSetErrorHandler"), 1)
            callback = self.runtime.callback
            ordered = (second, first) if reverse else (first, second)
            ordered[0].close()
            self.assertEqual(self.runtime.users, 1)
            self.assertIs(self.runtime.callback, callback)
            self.assertNotEqual(self.library.handler, 12345)
            ordered[1].close()
            self.assertEqual(self.runtime.users, 0)
            self.assertIsNone(self.runtime.callback)
            self.assertEqual(self.library.handler, 12345)
            ordered[1].close()
            self.assertEqual(self.runtime.users, 0)

    def test_open_failure_releases_only_its_registration(self):
        live = mapper.XConnection()
        self.library.open_ok = False
        with self.assertRaisesRegex(mapper.ControllerError, "Cannot open"):
            mapper.XConnection()
        self.assertEqual(self.runtime.users, 1)
        self.assertIsNotNone(self.runtime.callback)
        live.close()
        self.assertEqual(self.library.handler, 12345)

    def test_extension_failure_closes_display_and_restores_handler(self):
        with patch.object(mapper.XConnection, "_open_xres", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError): mapper.XConnection()
        self.assertEqual(self.runtime.users, 0)
        self.assertEqual(self.library.handler, 12345)
        self.assertEqual(sum(name == "XCloseDisplay" for name, _ in self.library.calls), 1)

    def test_thread_init_failure_never_opens_or_registers(self):
        self.library.threads_ok = False
        with self.assertRaisesRegex(mapper.ControllerError, "thread initialization"):
            mapper.XConnection()
        self.assertEqual([name for name, _ in self.library.calls], ["XInitThreads"])
        self.assertEqual(self.runtime.users, 0)
        self.assertIsNone(self.runtime.callback)


if __name__ == "__main__": unittest.main()
