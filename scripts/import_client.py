#!/usr/bin/env python3
"""Build a fresh, private Prism instance from the user's Tekkit 2 1.2.4 client.

This script uses only Python's standard library. It never starts Minecraft,
downloads game files, imports accounts, or changes the source instance. Prism
downloads Minecraft/Forge and the platform-specific libraries after sign-in.
The generated instance is an unmeasured candidate, not a performance result.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile


PACK_VERSION = "1.2.4"
MINECRAFT_VERSION = "1.12.2"
FORGE_VERSION = "14.23.5.2860"
LWJGL_VERSION = "2.9.4-nightly-20150209"
IMPORTER_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent

TEXT_EXTENSIONS = {".cfg", ".conf", ".properties", ".json", ".txt", ".xml", ".csv", ".map", ".lang"}
RESOURCE_EXTENSIONS = {
    ".png", ".gif", ".jpg", ".jpeg", ".bmp", ".ogg", ".wav", ".mp3",
    ".ttf", ".otf", ".json", ".txt", ".mcmeta", ".lang",
}
ALLOWED_TREES = {
    "mods": {".jar"},
    "config": TEXT_EXTENSIONS,
    "scripts": {".zs"},
    "asm": {".xml"},
    "resources": RESOURCE_EXTENSIONS,
    "resourcepacks": {".zip", ".png", ".json", ".mcmeta", ".txt"},
    "fonts": {".ttf", ".otf"},
}
BLOCKED_COMPONENTS = {
    "saves", "world", "worlds", "backups", "backup", "waypoints", "voxelmap",
    "schematics", "screenshots", "logs", "crash-reports", "natives", "libraries",
    "accounts", "oauth", "sessions", "tokens", "credentials", "secrets",
}
BLOCKED_FILENAMES = {
    "servers.dat", "servers.dat_old", "usercache.json", "usernamecache.json",
    "worldsettings.cfg", "configdata.nbt", "time.history", "startup_times.json",
    "supporters.json", "supporters.marker", "chiselsandbits_clipboard.cfg",
}
SECRET_NAME = re.compile(r"(?:^|[._\- ])(?:oauth|reauth|auth|account|accounts|token|tokens|credential|credentials|secret|secrets|session|sessions|password|passwords|cookie|cookies)(?:$|[._\- ])", re.IGNORECASE)


class ImportFailure(ValueError):
    """An input cannot be imported safely or does not match the pinned pack."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def read_json(path: Path) -> dict:
    if not path.is_file() or is_link_or_reparse(path):
        raise ImportFailure(f"Required ordinary metadata file missing: {path.name}")
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ImportFailure(f"Cannot parse metadata {path.name}: {error}") from error
    if not isinstance(result, dict):
        raise ImportFailure(f"Expected an object in metadata {path.name}")
    return result


def verify_source(source: Path) -> dict:
    if not source.is_dir() or is_link_or_reparse(source):
        raise ImportFailure("Source must be an ordinary existing Technic instance directory")
    if not (source / "bin").is_dir() or is_link_or_reparse(source / "bin"):
        raise ImportFailure("Source bin directory must exist and cannot be a link")
    release = read_json(source / "bin" / "version")
    loader = read_json(source / "bin" / "version.json")
    expected_id = f"{MINECRAFT_VERSION}-forge-{FORGE_VERSION}"
    if release.get("version") != PACK_VERSION:
        raise ImportFailure(f"Tekkit 2 must be exactly {PACK_VERSION}; found {release.get('version')!r}")
    if loader.get("inheritsFrom") != MINECRAFT_VERSION or loader.get("id") != expected_id:
        raise ImportFailure(f"Source must use Minecraft {MINECRAFT_VERSION}, Forge {FORGE_VERSION}")
    libraries = loader.get("libraries", [])
    forge = f"net.minecraftforge:forge:{MINECRAFT_VERSION}-{FORGE_VERSION}"
    if not isinstance(libraries, list) or not any(isinstance(item, dict) and item.get("name") == forge for item in libraries):
        raise ImportFailure("Pinned Forge library missing from source version metadata")
    return {
        "pack": PACK_VERSION,
        "minecraft": MINECRAFT_VERSION,
        "forge": FORGE_VERSION,
        "sourceMetadataSha256": {
            "bin/version": sha256(source / "bin" / "version"),
            "bin/version.json": sha256(source / "bin" / "version.json"),
        },
    }


def blocked_components(relative: Path) -> bool:
    """Exclude world data while retaining CoFH's shipped ore-generation config."""
    parts = [part.lower() for part in relative.parts]
    for index, part in enumerate(parts):
        if part.startswith("."):
            return True
        if part in BLOCKED_COMPONENTS:
            if part == "world" and index == 2 and parts[:3] == ["config", "cofh", "world"]:
                continue
            return True
    return False


def permitted(relative: Path, extensions: set[str]) -> bool:
    parts = [part.lower() for part in relative.parts]
    if blocked_components(relative):
        return False
    # ReAuth is a required, locked mod jar, not an account/token store.
    if parts[0] != "mods" and any(SECRET_NAME.search(part) for part in parts):
        return False
    return relative.name.lower() not in BLOCKED_FILENAMES and relative.suffix.lower() in extensions


def collect_files(source: Path, include_server_list: bool) -> tuple[list[Path], list[str]]:
    selected: list[Path] = []
    omitted: list[str] = []
    for tree_name, extensions in ALLOWED_TREES.items():
        tree = source / tree_name
        if not tree.exists():
            continue
        if is_link_or_reparse(tree) or not tree.is_dir():
            raise ImportFailure(f"Allowlisted tree is not an ordinary directory: {tree_name}")
        for current, directories, files in os.walk(tree, followlinks=False):
            current_path = Path(current)
            # Do not enter excluded/private directories or any symlink/junction.
            for name in list(directories):
                path = current_path / name
                relative = path.relative_to(source)
                if is_link_or_reparse(path):
                    raise ImportFailure(f"Link or reparse point in allowlisted tree: {relative}")
                if blocked_components(relative) or any(SECRET_NAME.search(part) for part in relative.parts):
                    directories.remove(name)
                    omitted.append(relative.as_posix() + "/")
            for name in files:
                path = current_path / name
                relative = path.relative_to(source)
                if is_link_or_reparse(path):
                    raise ImportFailure(f"Link or reparse point in allowlisted tree: {relative}")
                if not path.is_file():
                    raise ImportFailure(f"Non-regular source file: {relative}")
                if permitted(relative, extensions):
                    selected.append(path)
                else:
                    omitted.append(relative.as_posix())
    if not any(path.relative_to(source).parts[0] == "mods" for path in selected):
        raise ImportFailure("No mod jars found in the pinned source instance")
    if include_server_list:
        path = source / "servers.dat"
        if not path.is_file() or is_link_or_reparse(path):
            raise ImportFailure("--include-server-list requires an ordinary source servers.dat")
        selected.append(path)
    return sorted(selected, key=lambda p: p.relative_to(source).as_posix()), sorted(omitted)


def verify_mod_lock(source: Path, selected: list[Path], lock_path: Path) -> str:
    lock = read_json(lock_path)
    if (lock.get("formatVersion"), lock.get("pack"), lock.get("minecraft"), lock.get("forge")) != (1, PACK_VERSION, MINECRAFT_VERSION, FORGE_VERSION):
        raise ImportFailure("Mod lock identity does not match the pinned client")
    entries = lock.get("mods")
    if not isinstance(entries, list) or not entries:
        raise ImportFailure("Mod lock must contain a non-empty mods list")
    expected = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ImportFailure("Invalid mod lock entry")
        relative = entry.get("path")
        if not isinstance(relative, str) or "\\" in relative or not relative.startswith("mods/") or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise ImportFailure("Mod lock paths must be relative paths under mods/")
        if relative in expected or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))) or not isinstance(entry.get("bytes"), int):
            raise ImportFailure("Duplicate or invalid mod lock entry")
        expected[relative] = entry
    actual = {path.relative_to(source).as_posix(): path for path in selected if path.relative_to(source).parts[0] == "mods"}
    if actual.keys() != expected.keys():
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        raise ImportFailure(f"Mod filenames differ from lock; missing={missing}, extra={extra}")
    for relative, path in actual.items():
        entry = expected[relative]
        if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise ImportFailure(f"Mod digest differs from lock: {relative}")
    return sha256(lock_path)


def config_text(java: str | None, minimum: int, maximum: int) -> str:
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    values = {
        "name": "Nintekkit - Tekkit 2 1.2.4",
        "InstanceType": "OneSix",
        "iconKey": "default",
        "OverrideMemory": "true",
        "MinMemAlloc": str(minimum),
        "MaxMemAlloc": str(maximum),
        "OverrideWindow": "true",
        "LaunchMaximized": "false",
        "MinecraftWinWidth": "1280",
        "MinecraftWinHeight": "720",
        "OverrideJavaLocation": "true" if java else "false",
        "OverrideJavaArgs": "false",
    }
    if java:
        values["JavaPath"] = java
        values["IgnoreJavaCompatibility"] = "false"
    config["General"] = values
    from io import StringIO
    output = StringIO()
    config.write(output, space_around_delimiters=False)
    return output.getvalue()


def component_metadata() -> dict:
    return {
        "formatVersion": 1,
        "components": [
            {"uid": "net.minecraft", "version": MINECRAFT_VERSION, "important": True},
            {"uid": "net.minecraftforge", "version": FORGE_VERSION},
            {"uid": "org.lwjgl", "version": LWJGL_VERSION, "cachedName": "LWJGL 2", "dependencyOnly": True},
        ],
    }


def options_text(resourcepacks: list[str]) -> str:
    return "\n".join([
        "version:1343", "renderDistance:4", "guiScale:2", "particles:1",
        "maxFps:260", "fboEnable:true", "fancyGraphics:false", "ao:0",
        "resourcePacks:" + json.dumps(resourcepacks, separators=(",", ":")),
        # LWJGL 2 fullscreen retains portrait mode dimensions on the rotated Switch
        # panel. Windowed rendering uses actual drawable geometry; the launcher
        # may request window-manager fullscreen separately after PID verification.
        "fullscreen:false", "enableVsync:false", "useVbo:true", "mipmapLevels:0",
        "overrideWidth:1280", "overrideHeight:720", "entityShadows:false", "",
    ])


def validate_java(java: str | None) -> None:
    if java is not None and (not java.startswith("/") or any(char in java for char in "\r\n\x00")):
        raise ImportFailure("--java must name an absolute Linux Java 8 executable path, without newlines")


def build_instance(source: Path, output: Path, *, java: str | None = None,
                   minimum: int = 512, maximum: int = 2400,
                   include_server_list: bool = False,
                   lock_path: Path = REPO_ROOT / "config" / "client-1.2.4.lock.json") -> dict:
    source = source.expanduser().absolute()
    output = output.expanduser().absolute()
    # Check the provided source itself before resolving to prevent hidden links.
    if source.exists() and is_link_or_reparse(source):
        raise ImportFailure("Source instance cannot be a symlink or reparse point")
    source = source.resolve()
    output = output.resolve()
    if inside(output, source) or inside(source, output):
        raise ImportFailure("Output and source directories must be entirely separate")
    if output.exists():
        raise ImportFailure("Output already exists; choose a fresh path (existing instances are never overwritten)")
    validate_java(java)
    if not (128 <= minimum <= maximum <= 32768):
        raise ImportFailure("Heap MB values must satisfy 128 <= min-memory <= max-memory <= 32768")
    identity = verify_source(source)
    selected, omitted = collect_files(source, include_server_list)
    lock_sha256 = verify_mod_lock(source, selected, lock_path)
    # Snapshot source hashes first, then verify each copied file against them.
    source_hashes = {path: sha256(path) for path in selected}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".nintekkit-import-", dir=output.parent))
    try:
        minecraft = temporary / ".minecraft"
        minecraft.mkdir()
        files = []
        for path in selected:
            relative = path.relative_to(source)
            target = minecraft / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            digest = sha256(target)
            if digest != source_hashes[path] or sha256(path) != source_hashes[path]:
                raise ImportFailure(f"Source changed during import: {relative}")
            files.append({"path": target.relative_to(temporary).as_posix(), "bytes": target.stat().st_size,
                          "sha256": digest, "sourcePath": relative.as_posix(), "sourceSha256": digest})
        # Preserve the installed audio resource pack's explicit selection only.
        # Reading options is narrowly scoped; no source options or keybinds copied.
        resourcepacks: list[str] = []
        source_options = source / "options.txt"
        if source_options.is_file():
            if is_link_or_reparse(source_options):
                raise ImportFailure("Source options.txt cannot be a link")
            for line in source_options.read_text(encoding="utf-8", errors="strict").splitlines():
                if line.startswith("resourcePacks:"):
                    try:
                        candidate = json.loads(line.split(":", 1)[1])
                    except json.JSONDecodeError as error:
                        raise ImportFailure("Source resourcePacks option is not valid JSON") from error
                    if isinstance(candidate, list):
                        copied_packs = {p.name for p in selected if p.parent == source / "resourcepacks"}
                        resourcepacks = [name for name in candidate if isinstance(name, str) and name in copied_packs]
                    break
        generated = {
            "mmc-pack.json": json.dumps(component_metadata(), indent=2) + "\n",
            "instance.cfg": config_text(java, minimum, maximum),
            ".minecraft/options.txt": options_text(resourcepacks),
        }
        for relative, content in generated.items():
            path = temporary / relative
            path.write_text(content, encoding="utf-8", newline="\n")
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path), "generated": True})
        # Detect selected source files modified after their individual copy.
        for path, digest in source_hashes.items():
            if sha256(path) != digest:
                raise ImportFailure(f"Source changed during import: {path.relative_to(source)}")
        if verify_source(source) != identity:
            raise ImportFailure("Source version metadata changed during import")
        if verify_mod_lock(source, selected, lock_path) != lock_sha256:
            raise ImportFailure("Mod lock changed during import")
        manifest = {
            "formatVersion": IMPORTER_VERSION,
            "identity": identity,
            "modLockSha256": lock_sha256,
            "lwjgl": LWJGL_VERSION,
            "settings": {"minHeapMB": minimum, "maxHeapMB": maximum,
                         "javaSelection": java or "Prism Java 8 selection required",
                         "serverListIncluded": include_server_list,
                         "resolution": [1280, 720], "renderDistance": 4,
                         "minecraftFullscreen": False,
                         "maxFps": 260, "vSync": False, "performanceValidated": False},
            "files": sorted(files, key=lambda item: item["path"]),
            "omittedAllowlistedEntries": omitted,
            "excludedTopLevel": "All trees except mods, config, scripts, asm, resources, resourcepacks, fonts; servers.dat only with explicit flag",
        }
        (temporary / "nintekkit-import-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        if output.exists():
            raise ImportFailure("Output appeared during import; existing instances are never overwritten")
        temporary.rename(output)
        return manifest
    except BaseException:
        # This directory was freshly created by mkdtemp, never a supplied path.
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def default_source() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / ".technic" / "modpacks" / "tekkit-2"
    return Path.home() / ".technic" / "modpacks" / "tekkit-2"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=default_source(), help="Existing Tekkit 2 1.2.4 Technic instance")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / ".local" / "staging" / "Nintekkit-1.2.4", help="Fresh Prism instance directory; never overwritten")
    parser.add_argument("--java", help="Absolute Java 8 executable path on the Switch; otherwise select Java 8 in Prism")
    parser.add_argument("--min-memory", type=int, default=512, help="Initial heap MB (default 512)")
    parser.add_argument("--max-memory", type=int, default=2400, help="Provisional maximum heap MB (default 2400; measure memory pressure)")
    parser.add_argument("--include-server-list", action="store_true", help="Explicitly copy private servers.dat; default excludes it")
    parser.add_argument("--lock", type=Path, default=REPO_ROOT / "config" / "client-1.2.4.lock.json", help="Reviewed mod filename/size/SHA256 lock; all source jars must match")
    args = parser.parse_args(argv)
    try:
        manifest = build_instance(args.source, args.output, java=args.java,
                                  minimum=args.min_memory, maximum=args.max_memory,
                                  include_server_list=args.include_server_list, lock_path=args.lock)
    except (ImportFailure, OSError, UnicodeError) as error:
        parser.exit(1, f"Import stopped: {error}\n")
    mod_count = sum(item["path"].startswith(".minecraft/mods/") for item in manifest["files"])
    print(f"Built isolated candidate: {args.output.resolve()}")
    print(f"Pinned Tekkit 2 {PACK_VERSION}; Minecraft {MINECRAFT_VERSION}; Forge {FORGE_VERSION}; {mod_count} mod jars")
    print(f"SHA-256 manifest: {args.output.resolve() / 'nintekkit-import-manifest.json'}")
    print("Select ARM64 Java 8 in Prism, sign in locally, and validate graphics/audio/natives before benchmarking.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
