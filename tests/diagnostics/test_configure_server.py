"""Generated uncompressed NBT, create-only safety, and active-client guards."""
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("configure_server", ROOT / "scripts/diagnostics/configure_server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


def decode_nbt(payload):
    """Independent minimal NBT reader: strings, compounds and lists only."""
    source = io.BytesIO(payload)

    def exact(length):
        value = source.read(length)
        if len(value) != length:
            raise ValueError("Truncated NBT")
        return value

    def text():
        return exact(struct.unpack(">H", exact(2))[0]).decode("utf-8")

    def body(tag):
        if tag == 8:
            return text()
        if tag == 9:
            child, length = exact(1)[0], struct.unpack(">i", exact(4))[0]
            if not 0 <= length <= 100:
                raise ValueError("Invalid list length")
            return [body(child) for _ in range(length)]
        if tag == 10:
            result = {}
            while True:
                child = exact(1)[0]
                if child == 0:
                    return result
                key = text()
                if key in result:
                    raise ValueError("Repeated NBT key")
                result[key] = body(child)
        raise ValueError("Unexpected NBT tag")

    if exact(1) != b"\x0a" or text() != "":
        raise ValueError("Expected unnamed root compound")
    result = body(10)
    if source.read(1):
        raise ValueError("Trailing NBT bytes")
    return result


class ConfigureServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nintekkit-server-test-")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.instance = base / "Nintekkit-1.2.4"
        self.instance.mkdir(mode=0o700)
        self.minecraft = self.instance / ".minecraft"
        self.minecraft.mkdir(mode=0o700)
        (self.instance / "instance.cfg").write_text("[General]\nname=Nintekkit\n")
        (self.instance / "nintekkit-import-manifest.json").write_text(json.dumps({"identity": server.IDENTITY}))
        self.proc = base / "proc"
        self.proc.mkdir()

    def configure(self, endpoint="example.test:25565"):
        return server.configure_server(self.instance, endpoint, staging=True, proc=self.proc)

    def test_independent_nbt_reader_matches_1122_server_list(self):
        payload = server.server_nbt("example.test:25565")
        self.assertEqual(decode_nbt(payload), {"servers": [{"name": "Existing Tekkit world", "ip": "example.test:25565"}]})
        self.assertEqual(set(decode_nbt(payload)["servers"][0]), {"name", "ip"})
        self.assertEqual(payload[:3], b"\x0a\x00\x00")  # Uncompressed, unnamed compound.
        self.assertEqual(decode_nbt(server.server_nbt("192.0.2.10"))["servers"][0]["ip"], "192.0.2.10")

    def test_create_idempotent_and_private(self):
        self.assertEqual(self.configure(), {"status": "created"})
        target = self.minecraft / "servers.dat"
        original = target.stat()
        self.assertEqual(self.configure(), {"status": "already_configured"})
        self.assertEqual(target.stat().st_mtime_ns, original.st_mtime_ns)
        self.assertEqual(len(list(self.minecraft.iterdir())), 1)
        if sys.platform.startswith("linux"):
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_foreign_file_and_changed_endpoint_are_preserved(self):
        target = self.minecraft / "servers.dat"
        target.write_bytes(b"foreign confidential existing server list")
        with self.assertRaises(ValueError):
            self.configure()
        self.assertEqual(target.read_bytes(), b"foreign confidential existing server list")
        target.unlink()
        self.configure()
        original = target.read_bytes()
        with self.assertRaises(ValueError):
            self.configure("other.test")
        self.assertEqual(target.read_bytes(), original)

    def test_invalid_endpoint_and_instance_are_refused(self):
        for endpoint in ("-server", "user@host", "a/b", "a b", "a\n", "a:0", "a:65536", "a..b", "-a.test", "a-.test", "[::1]", "a;token", "a" * 254):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.configure(endpoint)
        marker = self.instance / "nintekkit-import-manifest.json"
        marker.write_text(json.dumps({"identity": {"pack": "other"}}))
        with self.assertRaises(ValueError):
            self.configure()
        self.assertFalse((self.minecraft / "servers.dat").exists())

    def test_racing_destination_is_never_overwritten(self):
        target = self.minecraft / "servers.dat"
        original_link = os.link

        def racing_link(source, destination):
            target.write_bytes(b"created by another application")
            return original_link(source, destination)

        with mock.patch.object(server.os, "link", side_effect=racing_link), self.assertRaises(ValueError):
            self.configure()
        self.assertEqual(target.read_bytes(), b"created by another application")
        self.assertEqual([item.name for item in self.minecraft.iterdir()], ["servers.dat"])

    def test_cli_refusal_redacts_exception_messages_and_endpoint(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["configure_server.py", "--instance", str(self.instance), "--server", "private-server.test", "--staging"]), \
             mock.patch.object(server, "configure_server", side_effect=ValueError("private-server.test credential")), \
             mock.patch("sys.stdout", output):
            self.assertEqual(server.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "refused", "error_class": "ValueError"})
        self.assertNotIn("private-server", output.getvalue())

    def test_windows_requires_explicit_staging(self):
        with mock.patch.object(server.sys, "platform", "win32"), self.assertRaises(ValueError):
            server.configure_server(self.instance, "example.test")

    def test_same_user_proc_guard_and_permission_failure_with_metadata_fixture(self):
        process = mock.MagicMock()
        process.name = "1234"
        process.stat.return_value.st_uid = 1000
        executable, cwd, comm = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        executable.resolve.return_value = Path("/opt/java/bin/java")
        cwd.resolve.return_value = self.minecraft
        comm.open.return_value.__enter__.return_value.read.return_value = b"Client thread\n"
        process.__truediv__.side_effect = lambda name: {"exe": executable, "cwd": cwd, "comm": comm}[name]
        proc = mock.MagicMock()
        proc.iterdir.return_value = [process]
        with mock.patch.object(server.os, "getuid", return_value=1000, create=True):
            with self.assertRaisesRegex(ValueError, "active Java"):
                server.require_stopped(self.minecraft, proc)
            cwd.resolve.return_value = self.instance
            server.require_stopped(self.minecraft, proc)
            executable.resolve.side_effect = PermissionError("private process")
            with self.assertRaisesRegex(ValueError, "Cannot inspect"):
                server.require_stopped(self.minecraft, proc)
            process.stat.return_value.st_uid = 2000
            server.require_stopped(self.minecraft, proc)  # Another user's process is never inspected.

    def test_private_ancestor_metadata_fixture(self):
        def directory(mode):
            value = mock.MagicMock()
            value.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=1000)
            return value

        parent = directory(0o700)
        child = directory(0o755)
        child.parents = [parent]
        with mock.patch.object(server.os, "getuid", return_value=1000, create=True):
            server.require_private_parent(child)
            child.lstat.return_value.st_mode = stat.S_IFDIR | 0o775
            with self.assertRaises(ValueError):
                server.require_private_parent(child)
            child.lstat.return_value.st_mode = stat.S_IFDIR | 0o755
            parent.lstat.return_value.st_mode = stat.S_IFDIR | 0o755
            with self.assertRaises(ValueError):
                server.require_private_parent(child)

    def test_unreadable_possible_java_and_renamed_client_thread_remain_refused(self):
        process = mock.MagicMock()
        process.name = "1234"
        process.stat.return_value.st_uid = 1000
        executable, comm = mock.MagicMock(), mock.MagicMock()
        executable.resolve.side_effect = PermissionError("hidden executable")
        process.__truediv__.side_effect = lambda name: {"exe": executable, "comm": comm}[name]
        proc = mock.MagicMock()
        proc.iterdir.return_value = [process]
        with mock.patch.object(server.os, "getuid", return_value=1000, create=True):
            for name in (b"java\n", b"javaw\n", b"Client thread\n", b"unknown\n", b"x" * 33,
                         b"sshd", b"sshd\nextra", b"systemd-other\n", b"\xff\n"):
                with self.subTest(comm=name):
                    comm.open.return_value.__enter__.return_value.read.return_value = name
                    with self.assertRaisesRegex(ValueError, "Cannot inspect"):
                        server.require_stopped(self.minecraft, proc)

    def test_readable_java_with_unreadable_cwd_remains_refused(self):
        process = mock.MagicMock()
        process.name = "1234"
        process.stat.return_value.st_uid = 1000
        executable, cwd, comm = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        executable.resolve.return_value = Path("/opt/java/bin/java")
        cwd.resolve.side_effect = PermissionError("hidden cwd")
        comm.open.return_value.__enter__.return_value.read.return_value = b"systemd\n"
        process.__truediv__.side_effect = lambda name: {"exe": executable, "cwd": cwd, "comm": comm}[name]
        proc = mock.MagicMock()
        proc.iterdir.return_value = [process]
        with mock.patch.object(server.os, "getuid", return_value=1000, create=True):
            with self.assertRaisesRegex(ValueError, "Cannot inspect"):
                server.require_stopped(self.minecraft, proc)
        comm.open.assert_not_called()  # A known name never overrides a verified Java executable.

    def test_only_exact_observed_hardened_native_names_allow_denied_exe(self):
        process = mock.MagicMock()
        process.name = "1234"
        process.stat.return_value.st_uid = 1000
        executable, comm = mock.MagicMock(), mock.MagicMock()
        executable.resolve.side_effect = PermissionError("hidden executable")
        process.__truediv__.side_effect = lambda name: {"exe": executable, "comm": comm}[name]
        proc = mock.MagicMock()
        proc.iterdir.return_value = [process]
        expected = {b"org_kde_powerde\n", b"polkit-kde-auth\n", b"ssh-agent\n", b"sshd\n", b"systemd\n", b"(sd-pam)\n"}
        self.assertEqual(server.HARDENED_NATIVE_COMM, expected)
        with mock.patch.object(server.os, "getuid", return_value=1000, create=True):
            for name in expected:
                with self.subTest(comm=name):
                    stream = comm.open.return_value.__enter__.return_value
                    stream.read.return_value = name
                    server.require_stopped(self.minecraft, proc)
                    stream.read.assert_called_with(33)
            comm.open.side_effect = PermissionError("hidden comm")
            with self.assertRaisesRegex(ValueError, "Cannot inspect"):
                server.require_stopped(self.minecraft, proc)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Real POSIX ownership/symlink/proc fixture")
    def test_linux_refuses_active_exact_instance_without_command_lines(self):
        process = self.proc / "1234"
        process.mkdir()
        java = self.proc.parent / "java"
        java.write_text("fixture executable")
        (process / "exe").symlink_to(java)
        (process / "cwd").symlink_to(self.minecraft, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "active Java"):
            self.configure()
        self.assertFalse((self.minecraft / "servers.dat").exists())
        (process / "cwd").unlink()
        (process / "cwd").symlink_to(self.instance, target_is_directory=True)
        self.assertEqual(self.configure()["status"], "created")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Real POSIX permissions/symlinks")
    def test_linux_refuses_symlink_or_public_parent(self):
        target = self.minecraft / "servers.dat"
        outside = self.instance / "outside"
        outside.write_text("preserve")
        target.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.configure()
        self.assertEqual(outside.read_text(), "preserve")
        target.unlink()
        self.minecraft.chmod(0o777)
        with self.assertRaises(ValueError):
            self.configure()
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
