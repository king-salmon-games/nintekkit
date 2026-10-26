"""Isolation and content-integrity checks using synthetic, non-game fixtures."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("import_client", ROOT / "scripts" / "import_client.py")
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
VERIFY_SPEC = importlib.util.spec_from_file_location("verify_instance", ROOT / "scripts" / "verify_instance.py")
verifier = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(verifier)


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def inventory(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


class InstanceImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.output = self.base / "output"
        write(self.source, "bin/version", json.dumps({"version": "1.2.4", "legacy": False}))
        write(self.source, "bin/version.json", json.dumps({
            "id": "1.12.2-forge-14.23.5.2860", "inheritsFrom": "1.12.2",
            "libraries": [{"name": "net.minecraftforge:forge:1.12.2-14.23.5.2860"}],
        }))
        write(self.source, "mods/required.jar", "synthetic mod, not a game binary")
        write(self.source, "mods/1.12.2/helper.jar", "synthetic helper")
        write(self.source, "mods/ReAuth-1.12-Forge-4.0.7.jar", "synthetic required ReAuth code")
        write(self.source, "config/required.cfg", "enabled=true\n")
        write(self.source, "scripts/recipes.zs", "// synthetic recipe\n")
        write(self.source, "asm/patch.xml", "<patch/>\n")
        write(self.source, "resources/textures/test.png", b"synthetic texture")
        write(self.source, "resourcepacks/audio.zip", b"synthetic opaque resource archive")
        write(self.source, "fonts/test.ttf", b"synthetic font")
        write(self.source, "options.txt", 'renderDistance:32\nresourcePacks:["audio.zip"]\n')
        for name in ["saves/world/level.dat", "backups/world.zip", "waypoints/server.txt",
                     "voxelmap/server.points", "bin/natives/liblwjgl.so", "accounts.json",
                     ".ReAuth.cfg", "config/oauth.cfg", "config/token.json", "config/private/session.json",
                     "config/jei/worldSettings.cfg", "config/unexpected.dll", "usercache.json"]:
            write(self.source, name, "PRIVATE SYNTHETIC DATA")
        write(self.source, "servers.dat", "PRIVATE SERVER LIST")
        self.lock = self.base / "lock.json"
        self.write_lock()

    def write_lock(self):
        mods = [{"path": path.relative_to(self.source).as_posix(), "bytes": path.stat().st_size,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted((self.source / "mods").rglob("*.jar"))]
        self.lock.write_text(json.dumps({"formatVersion": 1, "pack": "1.2.4", "minecraft": "1.12.2",
                                         "forge": "14.23.5.2860", "mods": mods}))

    def build(self, **kwargs):
        return client.build_instance(self.source, self.output, lock_path=self.lock, **kwargs)

    def test_isolated_matching_content_and_source_untouched(self):
        before = inventory(self.source)
        manifest = self.build(java="/usr/lib/jvm/java-8-openjdk-arm64/jre/bin/java")
        self.assertEqual(before, inventory(self.source))
        paths = {p.relative_to(self.output).as_posix() for p in self.output.rglob("*") if p.is_file()}
        required = {".minecraft/mods/required.jar", ".minecraft/mods/1.12.2/helper.jar",
                    ".minecraft/mods/ReAuth-1.12-Forge-4.0.7.jar", ".minecraft/config/required.cfg",
                    ".minecraft/scripts/recipes.zs", ".minecraft/asm/patch.xml",
                    ".minecraft/resources/textures/test.png", ".minecraft/resourcepacks/audio.zip",
                    ".minecraft/fonts/test.ttf", ".minecraft/options.txt", "instance.cfg", "mmc-pack.json",
                    "nintekkit-import-manifest.json"}
        self.assertEqual(required, paths)
        for item in manifest["files"]:
            path = self.output / item["path"]
            self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(item["bytes"], path.stat().st_size)
        options = (self.output / ".minecraft/options.txt").read_text()
        self.assertIn("renderDistance:4\n", options)
        self.assertIn("fullscreen:false\n", options)
        self.assertNotIn("fullscreen:true", options)
        self.assertIn("overrideWidth:1280\n", options)
        self.assertIn("overrideHeight:720\n", options)
        self.assertIn('resourcePacks:["audio.zip"]\n', options)
        config = (self.output / "instance.cfg").read_text()
        self.assertIn("MaxMemAlloc=2400", config)
        self.assertIn("OverrideJavaLocation=true", config)
        self.assertFalse(manifest["settings"]["performanceValidated"])
        self.assertFalse(manifest["settings"]["minecraftFullscreen"])

    def test_landscape_candidate_preserves_heap_content_and_source(self):
        before = inventory(self.source)
        manifest = self.build(java="/usr/lib/jvm/java-8-openjdk-arm64/jre/bin/java", maximum=1792)
        self.assertEqual(before, inventory(self.source))
        self.assertEqual(manifest["settings"]["maxHeapMB"], 1792)
        self.assertEqual(manifest["settings"]["resolution"], [1280, 720])
        self.assertFalse(manifest["settings"]["serverListIncluded"])
        config = (self.output / "instance.cfg").read_text()
        for setting in ("MaxMemAlloc=1792\n", "OverrideWindow=true\n", "LaunchMaximized=false\n",
                        "MinecraftWinWidth=1280\n", "MinecraftWinHeight=720\n"):
            self.assertIn(setting, config)
        copied = [entry for entry in manifest["files"] if "sourcePath" in entry]
        self.assertTrue(copied)
        for entry in copied:
            self.assertEqual(entry["sha256"], hashlib.sha256((self.source / entry["sourcePath"]).read_bytes()).hexdigest())
        self.assertEqual(verifier.verify_instance(self.output, lock_path=self.lock)["modJars"], 3)

    def test_server_list_requires_explicit_opt_in(self):
        self.build(include_server_list=True)
        self.assertEqual((self.output / ".minecraft/servers.dat").read_bytes(), b"PRIVATE SERVER LIST")

    def test_cofh_ore_configuration_preserved_but_world_data_excluded(self):
        ore_config = write(self.source, "config/cofh/world/00_minecraft.json", '{"populate":true}\n')
        write(self.source, "config/cofh/world/config.cfg", "ore_generation=true\n")
        write(self.source, "config/unrelated/world/private.cfg", "unrelated world data")
        write(self.source, "config/cofh/world/saves/level.dat", "world data nested in ore config")
        before = inventory(self.source)
        self.build()
        self.assertEqual((self.output / ".minecraft/config/cofh/world/00_minecraft.json").read_bytes(), ore_config.read_bytes())
        self.assertTrue((self.output / ".minecraft/config/cofh/world/config.cfg").is_file())
        self.assertFalse((self.output / ".minecraft/config/unrelated/world").exists())
        self.assertFalse((self.output / ".minecraft/config/cofh/world/saves").exists())
        self.assertEqual(before, inventory(self.source))

    def test_existing_output_preserved(self):
        marker = write(self.output, "existing.txt", "must remain")
        with self.assertRaisesRegex(client.ImportFailure, "already exists"):
            self.build()
        self.assertEqual(marker.read_text(), "must remain")

    def test_source_overlap_refused_without_changes(self):
        before = inventory(self.source)
        for output in (self.source / "new", self.base):
            with self.assertRaisesRegex(client.ImportFailure, "entirely separate"):
                client.build_instance(self.source, output, lock_path=self.lock)
        self.assertEqual(before, inventory(self.source))

    def test_wrong_release_or_forge_refused(self):
        for path, contents in [("bin/version", {"version": "1.2.6"}),
                               ("bin/version.json", {"id": "wrong", "inheritsFrom": "1.12.2"})]:
            target = self.source / path
            original = target.read_bytes()
            target.write_text(json.dumps(contents))
            with self.assertRaises(client.ImportFailure):
                self.build()
            self.assertFalse(self.output.exists())
            target.write_bytes(original)

    def test_added_or_modified_mod_refused_by_lock(self):
        mod = self.source / "mods/required.jar"
        mod.write_text("a different unreviewed mod")
        with self.assertRaisesRegex(client.ImportFailure, "digest differs"):
            self.build()
        self.write_lock()
        write(self.source, "mods/new.jar", "extra mod")
        with self.assertRaisesRegex(client.ImportFailure, "filenames differ"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_links_refused_without_following(self):
        target = self.base / "private.cfg"
        target.write_text("secret outside source")
        try:
            (self.source / "config/link.cfg").symlink_to(target)
        except OSError as error:
            self.skipTest(f"Creating symlinks unavailable on this host: {error}")
        with self.assertRaisesRegex(client.ImportFailure, "Link or reparse"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_reparse_detection_blocks_allowlisted_file(self):
        target = self.source / "config/required.cfg"
        real_check = client.is_link_or_reparse
        with mock.patch.object(client, "is_link_or_reparse", side_effect=lambda path: path == target or real_check(path)):
            with self.assertRaisesRegex(client.ImportFailure, "Link or reparse"):
                self.build()
        self.assertFalse(self.output.exists())

    def test_mid_copy_change_removes_only_temporary_output(self):
        real_copy = client.shutil.copyfile
        changed = False
        def change_source(source, target):
            nonlocal changed
            result = real_copy(source, target)
            if not changed:
                Path(source).write_text("concurrently modified")
                changed = True
            return result
        with mock.patch.object(client.shutil, "copyfile", side_effect=change_source):
            with self.assertRaisesRegex(client.ImportFailure, "Source changed"):
                self.build()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.base.glob(".nintekkit-import-*")), [])
        self.assertTrue(self.source.is_dir())

    def test_invalid_java_and_memory_refused(self):
        for kwargs in ({"java": "java"}, {"java": "/java\nInjected=true"},
                       {"minimum": 2400, "maximum": 512}):
            with self.assertRaises(client.ImportFailure):
                self.build(**kwargs)
        self.assertFalse(self.output.exists())

    def test_identical_imports_are_reproducible(self):
        first = self.build()
        other = self.base / "output-two"
        second = client.build_instance(self.source, other, lock_path=self.lock)
        self.assertEqual(first, second)
        self.assertEqual({key: value[0] for key, value in inventory(self.output).items()},
                         {key: value[0] for key, value in inventory(other).items()})


class InstanceVerificationTests(unittest.TestCase):
    write_lock = InstanceImportTests.write_lock
    build = InstanceImportTests.build

    def setUp(self):
        InstanceImportTests.setUp(self)
        self.build()

    def verify(self):
        return verifier.verify_instance(self.output, lock_path=self.lock)

    def edit_manifest(self, edit):
        path = self.output / "nintekkit-import-manifest.json"
        manifest = json.loads(path.read_text())
        edit(manifest)
        path.write_text(json.dumps(manifest))

    def refresh_entry(self, relative):
        path = self.output / relative
        def refresh(manifest):
            entry = next(item for item in manifest["files"] if item["path"] == relative)
            entry["bytes"] = path.stat().st_size
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            if "sourceSha256" in entry:
                entry["sourceSha256"] = entry["sha256"]
        self.edit_manifest(refresh)

    def test_verifies_fresh_instance_and_reviewed_lock(self):
        before = inventory(self.output)
        result = self.verify()
        self.assertTrue(result["reviewedModLockVerified"])
        self.assertEqual(result["modJars"], 3)
        self.assertEqual(len(before) - 1, result["filesVerified"])
        self.assertEqual(before, inventory(self.output))

    def test_rejects_modified_and_missing_files(self):
        target = self.output / ".minecraft/config/required.cfg"
        target.write_text("modified after staging")
        with self.assertRaisesRegex(verifier.VerificationFailure, "SHA-256 mismatch"):
            self.verify()
        target.unlink()
        with self.assertRaisesRegex(verifier.VerificationFailure, "missing="):
            self.verify()

    def test_rejects_extra_runtime_or_world_files(self):
        write(self.output, ".minecraft/saves/private-world/level.dat", "world file must never be accepted in fresh import")
        with self.assertRaisesRegex(verifier.VerificationFailure, "extra="):
            self.verify()

    def test_rejects_manifest_traversal_and_case_conflicts(self):
        original = (self.output / "nintekkit-import-manifest.json").read_bytes()
        self.edit_manifest(lambda m: m["files"][0].update(path="../outside.cfg"))
        with self.assertRaisesRegex(verifier.VerificationFailure, "traverse"):
            self.verify()
        (self.output / "nintekkit-import-manifest.json").write_bytes(original)
        self.edit_manifest(lambda m: m["files"].append(dict(m["files"][0], path=m["files"][0]["path"].upper())))
        with self.assertRaisesRegex(verifier.VerificationFailure, "case-conflicting"):
            self.verify()

    def test_rejects_wrong_component_even_if_manifest_rehashed(self):
        path = self.output / "mmc-pack.json"
        components = json.loads(path.read_text())
        components["components"][0]["version"] = "1.20.1"
        path.write_text(json.dumps(components))
        self.refresh_entry("mmc-pack.json")
        with self.assertRaisesRegex(verifier.VerificationFailure, "components differ"):
            self.verify()

    def test_reviewed_lock_rejects_rehashed_different_mod(self):
        path = self.output / ".minecraft/mods/required.jar"
        path.write_text("different mod, with self-consistent rewritten manifest")
        self.refresh_entry(".minecraft/mods/required.jar")
        # Self-consistent hashes alone do not authenticate a reviewed mod set.
        verifier.verify_instance(self.output)
        with self.assertRaisesRegex(verifier.VerificationFailure, "reviewed mod lock"):
            self.verify()

    def test_reparse_marker_refused(self):
        target = self.output / ".minecraft/config/required.cfg"
        real_lstat = Path.lstat
        def lstat(path):
            result = real_lstat(path)
            if path == target:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
            return result
        with mock.patch.object(Path, "lstat", lstat):
            with self.assertRaisesRegex(verifier.VerificationFailure, "reparse points"):
                self.verify()


if __name__ == "__main__":
    unittest.main()
