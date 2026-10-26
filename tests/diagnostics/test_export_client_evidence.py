"""Private fixture exports; no live process, desktop, game or network access."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/diagnostics"))
import export_client_evidence as export


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.instance = self.root / "instance"
        self.game = self.instance / ".minecraft"
        (self.game / "logs").mkdir(parents=True)
        self.uid = self.instance.stat().st_uid
    def tearDown(self): self.temp.cleanup()
    def test_only_typed_allowlisted_settings_escape_configuration(self):
        path = self.game / "options.txt"
        path.write_text("lastServer:PRIVATE_ADDRESS\naccessToken:PRIVATE_TOKEN\nrenderDistance:4\nfullscreen:true\nmaxFps:PRIVATE_TOKEN\nrenderClouds:false\n")
        result = export.selected_options(path, export.GAME_OPTIONS, ":", uid=self.uid)
        self.assertEqual(result["values"], {"renderDistance": 4, "fullscreen": True, "renderClouds": "false"})
        self.assertEqual(result["invalidAllowlistedValuesOmitted"], 1)
        self.assertNotIn("PRIVATE", json.dumps(result))
        path.write_text("JvmArgs=PRIVATE_TOKEN\nAccount=PRIVATE_NAME\nMaxMemAlloc=1792\nMinMemAlloc=512\n")
        result = export.selected_options(path, export.INSTANCE_OPTIONS, "=", uid=self.uid)
        self.assertEqual(result["values"], {"MaxMemAlloc": 1792, "MinMemAlloc": 512})
    def test_hash_is_explicitly_bounded_prefix_and_does_not_return_contents(self):
        path = self.game / "logs/latest.log"
        path.write_bytes(b"private raw log payload")
        result = export.file_metadata(path, uid=self.uid, limit=7)
        self.assertEqual(result["bytesHashed"], 7)
        self.assertFalse(result["entireFileAtOpenHashed"])
        self.assertEqual(result["sha256OfInspectedPrefix"], hashlib.sha256(b"private").hexdigest())
        self.assertNotIn("payload", json.dumps(result))
    def test_live_log_growth_is_marked_and_hash_stops_at_initial_size(self):
        path = self.game / "logs/latest.log"
        path.write_bytes(b"original")
        original = Path.open
        class Growing:
            def __init__(self, stream): self.stream, self.grown = stream, False
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def fileno(self): return self.stream.fileno()
            def read(self, length):
                value = self.stream.read(length)
                if not self.grown:
                    self.grown = True
                    with original(path, "ab") as stream: stream.write(b"new data")
                return value
        def opening(value, mode="r", *args, **kwargs):
            stream = original(value, mode, *args, **kwargs)
            return Growing(stream) if value == path and mode == "rb" else stream
        with patch.object(Path, "open", opening):
            result = export.file_metadata(path, uid=self.uid)
        self.assertEqual(result["bytesHashed"], 8)
        self.assertFalse(result["unchangedDuringHash"])
        self.assertEqual(result["fileBytesAfterRead"], 16)
    def test_output_refuses_existing_instance_and_git_locations(self):
        export.validate_output(self.root / "new", self.instance, self.uid)
        for value in (self.instance, self.game / "evidence"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                export.validate_output(value, self.instance, self.uid)
        (self.root / ".git").write_text("gitdir: fixture")
        with self.assertRaisesRegex(ValueError, "Git"):
            export.validate_output(self.root / "new", self.instance, self.uid)
    def test_source_owner_and_regular_file_are_required(self):
        path = self.game / "options.txt"
        path.write_text("renderDistance:4")
        with self.assertRaises(ValueError): export.ordinary(path, self.uid + 1)
        with self.assertRaises(ValueError): export.ordinary(self.game, self.uid)
    def test_stage_timings_handle_midnight_without_connection_text(self):
        (self.game / "logs/latest.log").write_text(
            "[23:59:59] [Client thread/INFO]: Forge Mod Loader has identified 179 mods to load\n"
            "[00:00:02] [Client thread/INFO]: Connecting to PRIVATE_ADDRESS\n"
            "[00:00:03] [Client thread/ERROR]: PRIVATE_CHAT PRIVATE_TOKEN\n")
        result = export.startup_timings(self.game)
        self.assertEqual(result["stages"]["connecting"]["firstSecondsFromFirstObservedLine"], 3)
        self.assertNotIn("PRIVATE", json.dumps(result))
    def test_export_writes_only_manifest_and_identity_change_writes_nothing(self):
        (self.instance / "instance.cfg").write_text("MaxMemAlloc=1792\nJvmArgs=PRIVATE_TOKEN\n")
        (self.game / "options.txt").write_text("renderDistance:4\nlastServer:PRIVATE_ADDRESS\n")
        proc = self.root / "proc"
        process = proc / "5842"
        process.mkdir(parents=True)
        (proc / "meminfo").write_text("MemTotal: 4000000 kB\nMemAvailable: 800000 kB\nPRIVATE_TOKEN: 123 kB\n")
        original_stat = Path.stat
        def stat(path, *args, **kwargs):
            return SimpleNamespace(st_uid=1000) if path == process else original_stat(path, *args, **kwargs)
        identity = ("/runtime/java", str(self.game), "1234")
        with ExitStack() as stack:
            stack.enter_context(patch.object(export.platform, "system", return_value="Linux"))
            stack.enter_context(patch.object(export.platform, "machine", return_value="aarch64"))
            stack.enter_context(patch.object(export.os, "getuid", return_value=1000, create=True))
            stack.enter_context(patch.object(Path, "stat", stat))
            stack.enter_context(patch.object(export, "validate_output"))
            stack.enter_context(patch.object(export, "ordinary", side_effect=lambda path, uid: path.lstat()))
            stack.enter_context(patch.object(export.client_snapshot, "snapshot", return_value={"safeSnapshot": True}))
            identities = stack.enter_context(patch.object(export.client_snapshot, "process_identity", return_value=identity))
            output = self.root / "private-export"
            destination = export.export(5842, self.instance, output, proc=proc)
            self.assertEqual([path.name for path in output.iterdir()], ["manifest.json"])
            text = destination.read_text()
            self.assertNotIn("PRIVATE", text)
            self.assertEqual(json.loads(text)["selectedOptions"]["graphics"]["values"]["renderDistance"], 4)
            identities.side_effect = [identity, ("/runtime/java", str(self.game), "different")]
            aborted = self.root / "aborted"
            with self.assertRaisesRegex(ValueError, "identity changed"):
                export.export(5842, self.instance, aborted, proc=proc)
            self.assertFalse(aborted.exists())


if __name__ == "__main__": unittest.main()
