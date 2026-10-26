#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install/remove a separate per-user Joy-Con launcher after live validation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import stat
import zipfile


ENTRY = "nintekkit-joycons.desktop"
COMPANION = "nintekkit-frameprobe-0.2.0.jar"


def desktop_text(root):
    launcher = str(root / "bin/launch.sh")
    if any(not 32 <= ord(character) <= 126 for character in launcher) or "=" in launcher:
        raise ValueError("Launcher Exec path must be ASCII without control characters or equals signs")
    # freedesktop.org desktop-entry Exec: argument quoting follows string escaping.
    argument = "".join("\\" + character if character in '\\"`$' else character for character in launcher)
    quoted = '"' + argument.replace('\\', '\\\\').replace('%', '%%') + '"'
    return ("[Desktop Entry]\nType=Application\nName=Nintekkit Joy-Cons — Tekkit 2\n"
            "Comment=Native Switch client with contextual Joy-Con controls\nExec=" + quoted
            + " --controls\nIcon=applications-games\nTerminal=false\nCategories=Game;\n")


def owned_regular(path, uid):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
        raise ValueError("Expected an ordinary current-user file: " + path.name)


def verify_tools(root, uid):
    for name in ("launch.sh", "play_with_controls.py", "nintekkit_controller.py", "screen_awake.py", "window_presentation.py", "configure_server.py", "on_screen_keyboard.py", "onboard_stacking.py"):
        owned_regular(root / "bin" / name, uid)
    jar = root / "prism/instances/Nintekkit-1.2.4/.minecraft/mods" / COMPANION
    owned_regular(jar, uid)
    with zipfile.ZipFile(jar) as archive:
        # The overlay installer provides the complete provenance/hash checks.
        info = json.loads(archive.read("mcmod.info"))
        if (not isinstance(info, list) or len(info) != 1 or not isinstance(info[0], dict)
                or info[0].get("modid") != "nintekkit_frameprobe" or info[0].get("version") != "0.2.0"):
            raise ValueError("Expected the original 0.2.0 client companion")
        if "com/kingsalmon/nintekkit/frameprobe/ControlsContext.class" not in archive.namelist():
            raise ValueError("Installed companion does not contain GUI context support")


def change_entry(root, applications, *, remove=False, uid=None):
    uid = os.getuid() if uid is None else uid
    expected = desktop_text(root).encode("utf-8")
    entry = applications / ENTRY
    if not remove:
        verify_tools(root, uid)
    if entry.exists() or entry.is_symlink():
        owned_regular(entry, uid)
        if entry.read_bytes() != expected:
            raise ValueError("Existing Joy-Con entry was changed; review it before replacing/removing")
        if remove:
            entry.unlink()
        return entry
    if remove:
        return entry
    applications.mkdir(parents=True, exist_ok=True)
    parent = applications.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid:
        raise ValueError("Applications directory must be an ordinary current-user directory")
    # Exclusive creation preserves any file appearing between inspection and write.
    with entry.open("xb") as stream:
        os.chmod(entry, 0o700)
        stream.write(expected)
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true", help="Create the separate entry; original launcher stays unchanged")
    action.add_argument("--remove", action="store_true", help="Remove only the unchanged generated Joy-Con entry")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "aarch64" or os.getuid() == 0:
        parser.error("Run as the Switch desktop user on ARM64 Linux; no sudo")
    root = (Path.home() / ".local/share/nintekkit").resolve(strict=True)
    applications = Path.home() / ".local/share/applications"
    entry = change_entry(root, applications, remove=args.remove)
    print(("Removed if present: " if args.remove else "Joy-Con launcher available: ") + str(entry))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(str(error), file=__import__("sys").stderr)
        raise SystemExit(1)
