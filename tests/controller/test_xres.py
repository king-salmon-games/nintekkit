"""Read-only XRes PID resolution tests with fake Xlib/libXres calls."""
from __future__ import annotations

import ctypes as C
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "controller"))
import nintekkit_controller as mapper  # noqa: E402


class Function:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


class FakeXRes:
    def __init__(self, entries=((2, 4, 14702),), status=0, version=(1, 2), extension=True):
        self.entries, self.status, self.version, self.extension = entries, status, version, extension
        self.queries, self.destroyed, self.negotiated = [], [], []
        self.XResQueryExtension = Function(self.extension_query)
        self.XResQueryVersion = Function(self.version_query)
        self.XResQueryClientIds = Function(self.ids_query)
        self.XResGetClientPid = Function(self.get_pid)
        self.XResClientIdsDestroy = Function(self.destroy)

    def extension_query(self, display, event, error):
        self.negotiated.append("extension")
        return int(self.extension)

    def version_query(self, display, major, minor):
        self.negotiated.append("version")
        major._obj.value, minor._obj.value = self.version
        return 1

    def ids_query(self, display, num_specs, spec, count, out):
        self.queries.append((num_specs, spec._obj.client, spec._obj.mask))
        count._obj.value = len(self.entries)
        if not self.entries:
            return self.status
        self.payloads = [C.c_uint32(pid) if pid is not None else None for _, _, pid in self.entries]
        self.values = (mapper.XResClientIdValue * len(self.entries))()
        for index, ((mask, length, _), payload) in enumerate(zip(self.entries, self.payloads)):
            self.values[index].spec = mapper.XResClientIdSpec(0x4A00000, mask)
            self.values[index].length = length
            self.values[index].value = C.cast(C.pointer(payload), C.c_void_p) if payload is not None else None
        C.cast(out, C.POINTER(C.POINTER(mapper.XResClientIdValue)))[0] = C.cast(
            self.values, C.POINTER(mapper.XResClientIdValue))
        return self.status

    def get_pid(self, value):
        entry = C.cast(value, C.POINTER(mapper.XResClientIdValue)).contents
        return C.c_int(C.cast(entry.value, C.POINTER(C.c_uint32)).contents.value).value

    def destroy(self, count, values):
        self.destroyed.append(count)


class FakeX:
    def __init__(self, pid=None, format_=32):
        self.pid = C.c_ulong(pid) if pid is not None else None
        self.format = format_
        self.freed = 0

    def XGetWindowProperty(self, display, window, atom, offset, length, delete, type_,
                           actual, format_, count, after, data):
        if self.pid is not None:
            actual._obj.value, format_._obj.value, count._obj.value = 6, self.format, 1
            data._obj.value = C.cast(C.pointer(self.pid), C.c_void_p).value
        return 0

    def XFree(self, value):
        self.freed += 1


def connection(xres=None, net_pid=None, atom=1, format_=32):
    result = object.__new__(mapper.XConnection)
    result.display, result.pid_atom = None, atom
    result.x, result.xres = FakeX(net_pid, format_), xres
    result.xres_version, result.last_pid_source = None, None
    return result


class XResTests(unittest.TestCase):
    def test_exact_native_abi_types_and_negotiation(self):
        self.assertEqual(mapper.XResClientIdSpec._fields_, [("client", C.c_ulong), ("mask", C.c_uint)])
        self.assertEqual(mapper.XResClientIdValue._fields_[1:], [("length", C.c_long), ("value", C.c_void_p)])
        library = FakeXRes()
        client = connection()
        with patch.object(mapper.ctypes.util, "find_library", return_value="libXRes.so.1"), \
                patch.object(mapper.C, "CDLL", return_value=library):
            client._open_xres()
        self.assertIs(client.xres, library)
        self.assertEqual(client.xres_version, (1, 2))
        self.assertEqual(library.negotiated, ["extension", "version"])
        self.assertIs(library.XResQueryClientIds.argtypes[1], C.c_long)
        self.assertIs(library.XResGetClientPid.restype, C.c_int)
        self.assertIsNone(library.XResClientIdsDestroy.restype)

    def test_old_protocol_or_absent_extension_disables_fallback(self):
        for library in (FakeXRes(version=(1, 1)), FakeXRes(extension=False)):
            client = connection()
            with patch.object(mapper.ctypes.util, "find_library", return_value="libXRes.so.1"), \
                    patch.object(mapper.C, "CDLL", return_value=library):
                client._open_xres()
            self.assertIsNone(client.xres)
            self.assertEqual(library.queries, [])

    def test_missing_library_is_optional_and_does_not_guess_pid(self):
        client = connection(atom=0)
        with patch.object(mapper.ctypes.util, "find_library", return_value=None):
            client._open_xres()
        self.assertIsNone(client.window_pid(0x4A00002))
        self.assertIsNone(client.last_pid_source)

    def test_net_wm_pid_is_preferred_and_its_storage_freed(self):
        library = FakeXRes(entries=((2, 4, 999),))
        client = connection(library, net_pid=14702)
        self.assertEqual(client.window_pid(0x4A00002), 14702)
        self.assertEqual(client.last_pid_source, "_NET_WM_PID")
        self.assertEqual(client.x.freed, 1)
        self.assertEqual(library.queries, [])

    def test_no_net_atom_or_property_uses_exact_resource_xres_pid(self):
        for atom in (0, 1):
            library = FakeXRes()
            client = connection(library, atom=atom)
            self.assertEqual(client.window_pid(0x4A00002), 14702)
            self.assertEqual(client.last_pid_source, "XRes")
            self.assertEqual(library.queries, [(1, 0x4A00002, 2)])
            self.assertEqual(library.destroyed, [1])

    def test_malformed_net_property_is_freed_before_xres_fallback(self):
        library = FakeXRes()
        client = connection(library, net_pid=999, format_=8)
        self.assertEqual(client.window_pid(0x4A00002), 14702)
        self.assertEqual(client.x.freed, 1)
        self.assertEqual(client.last_pid_source, "XRes")

    def test_query_error_is_not_success_and_returned_storage_is_destroyed(self):
        library = FakeXRes(status=1)
        client = connection(library, atom=0)
        self.assertIsNone(client.window_pid(0x4A00002))
        self.assertEqual(library.destroyed, [1])
        self.assertIsNone(client.last_pid_source)

    def test_non_pid_malformed_null_and_unknown_values_fail_closed(self):
        for entry in ((1, 4, 14702), (2, 8, 14702), (2, 4, None), (2, 4, 0), (2, 4, 0xFFFFFFFF)):
            with self.subTest(entry=entry):
                library = FakeXRes(entries=(entry,))
                self.assertIsNone(connection(library, atom=0).window_pid(0x4A00002))
                self.assertEqual(library.destroyed, [1])

    def test_ambiguous_pid_values_fail_closed(self):
        library = FakeXRes(entries=((2, 4, 14702), (2, 4, 999)))
        self.assertIsNone(connection(library, atom=0).window_pid(0x4A00002))
        self.assertEqual(library.destroyed, [2])

    def test_empty_response_does_not_destroy_null_pointer(self):
        library = FakeXRes(entries=())
        self.assertIsNone(connection(library, atom=0).window_pid(0x4A00002))
        self.assertEqual(library.destroyed, [])

    def test_default_title_accepts_observed_tekkit_2_and_minecraft(self):
        pattern = re.compile(mapper.DEFAULT_WINDOW_REGEX, re.I)
        for title in ("Tekkit 2", "Minecraft 1.12.2", "Minecraft"):
            self.assertIsNotNone(pattern.search(title))
        for title in ("Prism Launcher", "Tekkit 2 launcher", "Terminal"):
            self.assertIsNone(pattern.search(title))


if __name__ == "__main__":
    unittest.main()
