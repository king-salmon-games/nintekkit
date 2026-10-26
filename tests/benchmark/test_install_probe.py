"""Overlay transaction tests use only synthetic configs/JARs and temporary files."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("install_probe", ROOT / "scripts/benchmark/install_probe.py")
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)
REAL_REQUIRE_STOPPED = overlay.require_stopped


class ProbeOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.instance = self.base / "prism/instances/Nintekkit"
        self.mods = self.instance / ".minecraft/mods"
        self.mods.mkdir(parents=True)
        (self.mods / "required.jar").write_bytes(b"synthetic pre-existing mod")
        self.config = self.instance / "instance.cfg"
        self.before = b'[General]\r\n# caf\xc3\xa9 bytes retained\r\nOverrideJavaArgs=false\r\nJvmArgs="-Dlocal=dormant"\r\nMaxMemAlloc=1792\r\n[GUI]\r\nPosition=@ByteArray(data)\r\n'
        self.config.write_bytes(self.before)
        self.global_config = self.base / "prism/prismlauncher.cfg"
        self.global_config.write_bytes(b'[General]\nJvmArgs="-XX:+UseG1GC -Dglobal=value"\n')
        self.output = self.base / "captures"
        self.jar = self.base / "probe.jar"
        self.make_jar()
        self.addCleanup(mock.patch.stopall)
        # Production entry points still enforce Linux/AArch64. Only fixture I/O
        # path normalization is replaced so Windows can test transactions too.
        mock.patch.object(overlay, "require_platform").start()
        self.stopped = mock.patch.object(overlay, "require_stopped").start()
        mock.patch.object(overlay.os, "getuid", return_value=self.instance.stat().st_uid, create=True).start()
        mock.patch.object(overlay, "paths", side_effect=lambda instance, output: (
            instance, output, instance / "instance.cfg", instance / ".minecraft/mods" / overlay.JAR_NAME)).start()

    def make_jar(self, *, extra=None, major=52):
        entries = {name: bytes.fromhex("cafebabe0000") + major.to_bytes(2, "big") + b"fixture" for name in overlay.CLASSES}
        entries.update({
            "META-INF/LICENSE": (ROOT / "patches/frameprobe/LICENSE").read_bytes(),
            "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\n\n",
            "META-INF/nintekkit-build.json": json.dumps({"probe_version": "0.2.0", "target_class_major": 52,
                "sources": {"src/" + overlay.PREFIX + name + ".java": "0" * 64
                            for name in ("FrameProbe", "FrameRing", "Recorder", "ControlsContext")}}).encode(),
            "mcmod.info": b'[{"modid":"nintekkit_frameprobe","version":"0.2.0","mcversion":"1.12.2","clientSideOnly":true}]',
        })
        if extra: entries.update(extra)
        with zipfile.ZipFile(self.jar, "w") as archive:
            for name, data in entries.items(): archive.writestr(name, data)

    def install(self):
        return overlay.install(self.instance, self.jar, self.output)

    def test_inherited_arguments_and_other_config_bytes_preserved_then_rollback(self):
        required = (self.mods / "required.jar").read_bytes()
        result = self.install()
        values = overlay.ini_values(self.config.read_bytes())
        self.assertTrue(result["installed"])
        self.assertTrue(result["globalJvmArgsInherited"])
        self.assertEqual(overlay.qt_string(values["JvmArgs"]),
                         "-XX:+UseG1GC -Dglobal=value " + overlay.PROPERTY + "=" + str(self.output))
        self.assertEqual(values["OverrideJavaArgs"], "true")
        self.assertIn(b"# caf\xc3\xa9 bytes retained\r\n", self.config.read_bytes())
        self.assertIn(b"MaxMemAlloc=1792\r\n[GUI]\r\nPosition=@ByteArray(data)\r\n", self.config.read_bytes())
        state = self.output / overlay.STATE_NAME
        self.assertEqual((state / "instance.cfg.before").read_bytes(), self.before)
        transaction = json.loads((state / "transaction.json").read_bytes())
        self.assertEqual(transaction["preexistingMods"][0]["sha256"], overlay.sha(required))
        overlay.rollback(self.instance, self.output)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertFalse((self.mods / overlay.JAR_NAME).exists())
        self.assertEqual((self.mods / "required.jar").read_bytes(), required)

    def test_existing_override_uses_local_args_and_preserves_quoted_argument(self):
        local = '-Xmx1792m -Dlabel="hello world"'
        self.config.write_bytes(('[General]\nOverrideJavaArgs=true\nJvmArgs=' + overlay.qt_encoded(local) + '\n').encode())
        self.install()
        actual = overlay.qt_string(overlay.ini_values(self.config.read_bytes())["JvmArgs"])
        self.assertEqual(actual, local + " " + overlay.PROPERTY + "=" + str(self.output))

    def test_running_game_or_prism_refuses_before_any_writes(self):
        self.stopped.side_effect = ValueError("close game")
        with self.assertRaisesRegex(ValueError, "close game"): self.install()
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.mods / overlay.JAR_NAME).exists())

    def test_existing_probe_or_property_is_preserved(self):
        target = self.mods / overlay.JAR_NAME
        target.write_bytes(b"different existing probe")
        with self.assertRaisesRegex(ValueError, "existing probe"): self.install()
        self.assertEqual(target.read_bytes(), b"different existing probe")
        target.unlink()
        self.config.write_bytes(b'[General]\nOverrideJavaArgs=true\nJvmArgs="-Dnintekkit.frameprobe.dir=/old"\n')
        with self.assertRaisesRegex(ValueError, "existing frame-probe"): self.install()
        self.assertFalse(self.output.exists())

    def test_rollback_refuses_prism_config_changes_without_overwriting(self):
        self.install()
        changed = self.config.read_bytes() + b"# Prism rewrote something\n"
        self.config.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "manual merge"): overlay.rollback(self.instance, self.output)
        self.assertEqual(self.config.read_bytes(), changed)
        self.assertTrue((self.mods / overlay.JAR_NAME).exists())

    def test_rollback_refuses_changed_probe(self):
        self.install()
        after = self.config.read_bytes()
        (self.mods / overlay.JAR_NAME).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Probe JAR changed"): overlay.rollback(self.instance, self.output)
        self.assertEqual(self.config.read_bytes(), after)
        self.assertEqual((self.mods / overlay.JAR_NAME).read_bytes(), b"changed")

    def test_jar_validation_rejects_foreign_classes_or_new_class_version(self):
        self.make_jar(extra={"net/minecraft/Client.class": b"foreign"})
        with self.assertRaisesRegex(ValueError, "only the original"): self.install()
        self.make_jar(major=61)
        with self.assertRaisesRegex(ValueError, "class version 52"): self.install()
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertFalse(self.output.exists())

    def test_ambiguous_ini_refused_without_changes(self):
        for raw in ('-Dvalues=a,b', '@Variant(abc)', '"unterminated', '"-Dfoo=one" "-Dbar=two"'):
            self.config.write_bytes(('[General]\nOverrideJavaArgs=true\nJvmArgs=' + raw + '\n').encode())
            before = self.config.read_bytes()
            with self.assertRaises(ValueError): self.install()
            self.assertEqual(self.config.read_bytes(), before)
            self.assertFalse(self.output.exists())

    def test_actual_proc_name_and_cwd_guard_without_command_lines(self):
        proc = self.base / "fake-proc"
        process = proc / "123"
        process.mkdir(parents=True)
        (process / "comm").write_text("prismlauncher\n")
        with self.assertRaisesRegex(ValueError, "Close every Prism"): REAL_REQUIRE_STOPPED(self.instance, proc)
        (process / "comm").write_text("java\n")
        real_resolve = overlay.Path.resolve
        def resolve(path, *args, **kwargs):
            return (self.instance / ".minecraft") if path == process / "cwd" else real_resolve(path, *args, **kwargs)
        with mock.patch.object(overlay.Path, "resolve", autospec=True, side_effect=resolve):
            with self.assertRaisesRegex(ValueError, "running Java"): REAL_REQUIRE_STOPPED(self.instance, proc)


if __name__ == "__main__": unittest.main()
