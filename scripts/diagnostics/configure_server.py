#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate one private server entry in a stopped isolated Nintekkit instance.

No existing server list is imported or overwritten. Never prints the endpoint.
Windows use requires explicit --staging; deployed Linux use inspects /proc.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile


NAME = "Existing Tekkit world"
IDENTITY = {"pack": "1.2.4", "minecraft": "1.12.2", "forge": "14.23.5.2860"}
# Exact /proc/comm values observed on this Switch on 2026-10-25. These native
# desktop/session daemons deny exe readlink while running as the same user.
# This exception is considered only after exe inspection was denied; it is not
# a general non-Java-name test (Minecraft may rename its main thread).
HARDENED_NATIVE_COMM = frozenset(name + b"\n" for name in (
    b"org_kde_powerde", b"polkit-kde-auth", b"ssh-agent", b"sshd", b"systemd", b"(sd-pam)"))


def validate_server(value: str) -> str:
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9.-]*)(?::([0-9]{1,5}))?", value)
    if not match:
        raise ValueError("Server must be an ordinary DNS/IPv4 host[:port]")
    host, port = match.groups()
    if len(host) > 253 or any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-") for part in host.split(".")):
        raise ValueError("Invalid server hostname")
    if port is not None and not 1 <= int(port) <= 65535:
        raise ValueError("Server port is outside the valid range")
    return value


def server_nbt(server: str) -> bytes:
    """Uncompressed NBT used by Java 1.12.2 ServerList/CompressedStreamTools.read."""
    server = validate_server(server)

    def utf(value):
        encoded = value.encode("ascii")  # Accepted names/endpoint are ASCII; Java modified UTF-8 is identical.
        return struct.pack(">H", len(encoded)) + encoded

    def string(name, value):
        return b"\x08" + utf(name) + utf(value)

    return (b"\x0a" + utf("") + b"\x09" + utf("servers") + b"\x0a" + struct.pack(">i", 1)
            + string("name", NAME) + string("ip", server) + b"\x00\x00")


def ordinary(path: Path, *, directory=False) -> os.stat_result:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if path.is_symlink() or not expected(info.st_mode):
        raise ValueError("An ordinary nonsymlinked instance path is required")
    if sys.platform.startswith("linux"):
        if info.st_uid != os.getuid():
            raise ValueError("The instance path must be owned by the current user")
    return info


def require_private_parent(path: Path) -> None:
    # The installer protects ~/.local/share/nintekkit with 0700. Child directories
    # may be 0755, but remain inaccessible to other users through that ancestor.
    for directory in (path, *path.parents):
        info = directory.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode) or mode & 0o022:
            raise ValueError("Server-list parent is not protected from other users")
        if info.st_uid == os.getuid() and mode == 0o700:
            return
    raise ValueError("Server-list parent needs a private owned 0700 ancestor")


def checked_instance(instance: Path) -> Path:
    instance = Path(instance)
    if not instance.is_absolute() or instance != instance.resolve(strict=True):
        raise ValueError("Use an absolute canonical instance path without symlinks")
    ordinary(instance, directory=True)
    minecraft = instance / ".minecraft"
    ordinary(minecraft, directory=True)
    if sys.platform.startswith("linux"):
        require_private_parent(minecraft)
    ordinary(instance / "instance.cfg")
    marker = instance / "nintekkit-import-manifest.json"
    if ordinary(marker).st_size > 2 * 1024 * 1024:
        raise ValueError("Unexpected instance manifest size")
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        identity = manifest.get("identity", {})
        if not isinstance(identity, dict) or any(identity.get(key) != value for key, value in IDENTITY.items()):
            raise ValueError("Unexpected Nintekkit instance identity")
    except (json.JSONDecodeError, AttributeError) as error:
        raise ValueError("Invalid Nintekkit instance manifest") from error
    return minecraft


def require_stopped(minecraft: Path, proc: Path = Path("/proc")) -> None:
    """Inspect same-user exe/cwd, plus bounded comm only for denied native exe."""
    uid = os.getuid()
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != uid:
                continue
            try:
                executable = (process / "exe").resolve(strict=True)
            except PermissionError:
                try:
                    with (process / "comm").open("rb") as stream:
                        comm = stream.read(33)
                except PermissionError:
                    raise
                if comm in HARDENED_NATIVE_COMM:
                    continue
                raise  # Unknown/Java/renamed-client names remain fail-closed.
            if executable.name not in {"java", "javaw"}:
                continue
            if (process / "cwd").resolve(strict=True) == minecraft:
                raise ValueError("The isolated instance still has an active Java process")
        except (FileNotFoundError, ProcessLookupError):
            continue  # Process exited between reads.
        except PermissionError as error:
            raise ValueError("Cannot inspect a same-user process safely") from error


def existing_matches(path: Path, expected: bytes) -> bool:
    try:
        info = ordinary(path)
    except FileNotFoundError:
        return False
    if info.st_size != len(expected) or info.st_size > 4096:
        raise ValueError("Existing server list differs; it was preserved")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        actual = os.fstat(stream.fileno())
        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("Server list identity changed; nothing was written")
        if stream.read(4097) != expected:
            raise ValueError("Existing server list differs; it was preserved")
        if sys.platform.startswith("linux") and stat.S_IMODE(actual.st_mode) != 0o600:
            raise ValueError("Existing generated server list is not private; it was preserved")
    return True


def configure_server(instance: Path, server: str, *, staging: bool = False,
                     proc: Path = Path("/proc")) -> dict:
    if not sys.platform.startswith("linux") and not (sys.platform == "win32" and staging):
        raise ValueError("Run on Linux, or explicitly select offline Windows staging")
    payload = server_nbt(server)
    minecraft = checked_instance(instance)
    if sys.platform.startswith("linux"):
        require_stopped(minecraft, proc)
    target = minecraft / "servers.dat"
    if existing_matches(target, payload):
        return {"status": "already_configured"}
    descriptor, temporary_name = tempfile.mkstemp(prefix=".nintekkit-server-", dir=minecraft)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if sys.platform.startswith("linux"):
            require_stopped(minecraft, proc)
        # Hard-link publication is atomic and refuses any preexisting destination.
        # It never replaces a foreign file, even if one appeared during preparation.
        os.link(temporary, target)
        return {"status": "created"}
    except FileExistsError:
        if existing_matches(target, payload):
            return {"status": "already_configured"}
        raise ValueError("Server list appeared during configuration; it was preserved") from None
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--server", required=True, help="Private runtime endpoint; never written to console")
    parser.add_argument("--staging", action="store_true", help="Permit explicit offline Windows staging")
    args = parser.parse_args()
    try:
        result = configure_server(args.instance, args.server, staging=args.staging)
    except (OSError, ValueError) as error:
        # Filesystem errors can contain paths; exception classes alone are sufficient here.
        print(json.dumps({"status": "refused", "error_class": type(error).__name__}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
