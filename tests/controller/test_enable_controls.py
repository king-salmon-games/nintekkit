"""Desktop entry tests use temporary files, never a real launcher or input."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/controller"))
import enable_controls as enable


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "nintekkit"
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True)
        self.applications = Path(self.temporary.name) / "applications"
        self.applications.mkdir()
        for name in ("launch.sh", "play_with_controls.py", "nintekkit_controller.py", "screen_awake.py", "window_presentation.py", "configure_server.py", "on_screen_keyboard.py", "onboard_stacking.py"):
            (self.bin / name).write_text("fixture")
        self.jar = self.root / "prism/instances/Nintekkit-1.2.4/.minecraft/mods" / enable.COMPANION
        self.jar.parent.mkdir(parents=True)
        self.write_jar()
        self.uid = (self.bin / "launch.sh").stat().st_uid
    def tearDown(self): self.temporary.cleanup()
    def write_jar(self, version="0.2.0", context=True):
        with zipfile.ZipFile(self.jar, "w") as archive:
            archive.writestr("mcmod.info", json.dumps([{"modid": "nintekkit_frameprobe", "version": version}]))
            if context:
                archive.writestr("com/kingsalmon/nintekkit/frameprobe/ControlsContext.class", b"fixture")
    def change(self, **kwargs):
        return enable.change_entry(self.root, self.applications, uid=self.uid, **kwargs)
    def test_install_is_separate_idempotent_and_remove_matches_exact_content(self):
        original = self.applications / "nintekkit.desktop"
        original.write_text("original launcher")
        entry = self.change()
        content = entry.read_bytes()
        self.assertIn(b" --controls\n", content)
        self.assertEqual(self.change().read_bytes(), content)
        self.change(remove=True)
        self.assertFalse(entry.exists())
        self.assertEqual(original.read_text(), "original launcher")
    def test_missing_or_wrong_companion_refuses_before_entry_creation(self):
        for version, context in (("0.1.0", True), ("0.2.0", False)):
            self.write_jar(version, context)
            with self.assertRaises(ValueError): self.change()
            self.assertFalse((self.applications / enable.ENTRY).exists())
        self.jar.unlink()
        with self.assertRaises(FileNotFoundError): self.change()
    def test_changed_entry_is_never_overwritten_or_removed(self):
        entry = self.change()
        entry.write_text("foreign edit")
        for remove in (False, True):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.change(remove=remove)
            self.assertEqual(entry.read_text(), "foreign edit")
    def test_removal_does_not_require_companion_or_helper_still_present(self):
        entry = self.change()
        self.jar.unlink()
        (self.bin / "play_with_controls.py").unlink()
        self.change(remove=True)
        self.assertFalse(entry.exists())
        self.change(remove=True)
    def test_exclusive_create_cannot_overwrite_a_racing_entry(self):
        entry = self.applications / enable.ENTRY
        original_open = Path.open
        def race(path, mode="r", *args, **kwargs):
            if path == entry and mode == "xb":
                with original_open(path, "wb") as stream: stream.write(b"other writer")
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", race), self.assertRaises(FileExistsError):
            self.change()
        self.assertEqual(entry.read_bytes(), b"other writer")
    def test_exec_quotes_spaces_metacharacters_and_literal_percent(self):
        value = enable.desktop_text(Path('/home/user/example % "$`'))
        self.assertIn(r'example %% \\"\\$\\`', value)
        with self.assertRaises(ValueError): enable.desktop_text(Path("/home/user/bad\npath"))


if __name__ == "__main__": unittest.main()
