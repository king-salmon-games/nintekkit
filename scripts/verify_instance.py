#!/usr/bin/env python3
"""Verify a fresh Nintekkit import before installation, without modifying it.

Strict by design: every file must be declared in the import manifest, all hashes
and sizes must match, and no link/reparse point is accepted. Minecraft/Prism alter
files after launch; this verifier is for staging and newly copied installations,
not a runtime health check. It uses only Python's standard library and can be
copied to the Switch independently of the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys


PACK = "1.2.4"
MINECRAFT = "1.12.2"
FORGE = "14.23.5.2860"
LWJGL = "2.9.4-nightly-20150209"
MANIFEST = "nintekkit-import-manifest.json"
SHA256 = re.compile(r"[0-9a-f]{64}")
DEFAULT_LOCK = Path(__file__).resolve().parent.parent / "config" / "client-1.2.4.lock.json"


class VerificationFailure(ValueError):
    """The candidate is incomplete, altered, unsafe, or not the pinned pack."""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(data)
    return result.hexdigest()


def require_ordinary(path: Path, *, directory: bool = False) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise VerificationFailure(f"Missing or unreadable path: {path.name}") from error
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise VerificationFailure(f"Links and reparse points are forbidden: {path.name}")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise VerificationFailure(f"Expected an ordinary {'directory' if directory else 'file'}: {path.name}")


def read_object(path: Path) -> dict:
    require_ordinary(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationFailure(f"Invalid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise VerificationFailure(f"Expected a JSON object: {path.name}")
    return value


def relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise VerificationFailure("Manifest paths must be ordinary relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")) or path.as_posix() != value:
        raise VerificationFailure("Manifest paths cannot traverse outside the instance")
    return value


def pinned_identity(value: dict) -> None:
    if (value.get("pack"), value.get("minecraft"), value.get("forge")) != (PACK, MINECRAFT, FORGE):
        raise VerificationFailure("Expected Tekkit 2 1.2.4, Minecraft 1.12.2, Forge 14.23.5.2860")


def verify_components(root: Path) -> None:
    metadata = read_object(root / "mmc-pack.json")
    entries = metadata.get("components")
    if metadata.get("formatVersion") != 1 or not isinstance(entries, list):
        raise VerificationFailure("Invalid Prism component metadata")
    expected = {"net.minecraft": MINECRAFT, "net.minecraftforge": FORGE, "org.lwjgl": LWJGL}
    actual = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("uid"), str) or entry["uid"] in actual:
            raise VerificationFailure("Invalid or duplicate Prism component")
        actual[entry["uid"]] = entry.get("version")
    if actual != expected:
        raise VerificationFailure("Prism Minecraft/Forge/LWJGL components differ from the pinned baseline")


def verify_lock(root: Path, manifest: dict, entries: dict, lock_path: Path) -> None:
    lock = read_object(lock_path)
    pinned_identity(lock)
    if lock.get("formatVersion") != 1 or digest(lock_path) != manifest.get("modLockSha256"):
        raise VerificationFailure("Mod lock format or digest does not match the import manifest")
    mods = lock.get("mods")
    if not isinstance(mods, list) or not mods:
        raise VerificationFailure("Mod lock must contain a nonempty mods list")
    expected = {}
    for entry in mods:
        if not isinstance(entry, dict):
            raise VerificationFailure("Invalid mod lock entry")
        path = relative_path(entry.get("path"))
        if not path.startswith("mods/") or path in expected or not SHA256.fullmatch(str(entry.get("sha256", ""))):
            raise VerificationFailure("Invalid or duplicate mod lock entry")
        expected[path] = (entry.get("bytes"), entry["sha256"])
    actual = {path[len(".minecraft/"):]: (entry["bytes"], entry["sha256"])
              for path, entry in entries.items() if path.startswith(".minecraft/mods/")}
    if actual != expected:
        raise VerificationFailure("Installed mod filenames, sizes, or hashes differ from the reviewed mod lock")


def verify_instance(instance: Path, *, lock_path: Path | None = None) -> dict:
    root = instance.expanduser().absolute()
    require_ordinary(root, directory=True)
    root = root.resolve()
    manifest_path = root / MANIFEST
    manifest = read_object(manifest_path)
    identity = manifest.get("identity")
    if manifest.get("formatVersion") != 1 or not isinstance(identity, dict):
        raise VerificationFailure("Invalid import manifest format or identity")
    pinned_identity(identity)
    if manifest.get("lwjgl") != LWJGL or not SHA256.fullmatch(str(manifest.get("modLockSha256", ""))):
        raise VerificationFailure("Import manifest LWJGL or mod lock identity is invalid")
    metadata_hashes = identity.get("sourceMetadataSha256")
    if not isinstance(metadata_hashes, dict) or set(metadata_hashes) != {"bin/version", "bin/version.json"} or any(not SHA256.fullmatch(str(v)) for v in metadata_hashes.values()):
        raise VerificationFailure("Source metadata digest records are invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise VerificationFailure("Import manifest must contain a nonempty files list")
    entries = {}
    folded_paths = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise VerificationFailure("Invalid import manifest file entry")
        path = relative_path(entry.get("path"))
        if path == MANIFEST or path in entries or path.casefold() in folded_paths:
            raise VerificationFailure("Duplicate, case-conflicting, or recursive manifest file entry")
        if type(entry.get("bytes")) is not int or entry["bytes"] < 0 or not SHA256.fullmatch(str(entry.get("sha256", ""))):
            raise VerificationFailure(f"Invalid file size or SHA-256: {path}")
        if "sourcePath" in entry:
            source_path = relative_path(entry["sourcePath"])
            if path != ".minecraft/" + source_path or entry.get("sourceSha256") != entry["sha256"]:
                raise VerificationFailure(f"Transferred-file provenance mismatch: {path}")
        entries[path] = entry
        folded_paths.add(path.casefold())
    for required in ("instance.cfg", "mmc-pack.json", ".minecraft/options.txt"):
        if required not in entries:
            raise VerificationFailure(f"Required file absent from manifest: {required}")
    if not any(path.startswith(".minecraft/mods/") and path.endswith(".jar") for path in entries):
        raise VerificationFailure("No mod jars declared")
    actual = set()
    # Path.rglob does not follow directory symlinks; inspecting every result first
    # also rejects such links and Windows directory junctions before opening data.
    for path in root.rglob("*"):
        if path.is_dir():
            require_ordinary(path, directory=True)
        else:
            require_ordinary(path)
            actual.add(path.relative_to(root).as_posix())
    expected = set(entries) | {MANIFEST}
    if actual != expected:
        raise VerificationFailure(f"Instance file inventory differs; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
    for relative, entry in entries.items():
        path = root / relative
        require_ordinary(path)
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise VerificationFailure(f"File size or SHA-256 mismatch: {relative}")
    verify_components(root)
    if lock_path is not None:
        verify_lock(root, manifest, entries, lock_path)
    return {
        "filesVerified": len(entries),
        "modJars": sum(path.startswith(".minecraft/mods/") and path.endswith(".jar") for path in entries),
        "payloadBytes": sum(entry["bytes"] for entry in entries.values()),
        "manifestSha256": digest(manifest_path),
        "reviewedModLockVerified": lock_path is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path, help="Fresh isolated Prism instance directory")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK if DEFAULT_LOCK.is_file() else None,
                        help="Reviewed mod lock (defaults to repository config lock when available)")
    args = parser.parse_args(argv)
    try:
        result = verify_instance(args.instance, lock_path=args.lock)
    except (VerificationFailure, OSError) as error:
        parser.exit(1, f"Verification failed: {error}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
