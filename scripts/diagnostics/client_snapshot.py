#!/usr/bin/env python3
"""Narrow, read-only startup evidence from the same user's ARM64 Java client.

Never reads command lines, environment variables or account files, or copies logs.
Only allowlisted graphics messages, startup-stage counts and exception class names
leave the bounded log-tail parser; no raw error, chat or connection text is output.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import stat


LOG_LIMIT = 512 * 1024
MAPS_LIMIT = 2 * 1024 * 1024
LIBRARY = re.compile(r"^lib(?:lwjgl(?:64)?|openal(?:64)?|jinput-linux(?:64)?|jawt|jvm|GL[^/]*|EGL[^/]*|GLES[^/]*|glapi|glx[^/]*|nvidia[^/]*|nvrm[^/]*|cuda)\.so(?:\.[0-9]+)*$")
GRAPHICS = re.compile(r"^(?:OpenGL:|OpenGL API (?:version|renderer|vendor):|GL_(?:VERSION|RENDERER|VENDOR):|LWJGL Version:)\s*[ -~]{1,500}$")
EXCEPTION = re.compile(r"(?<![\w.$])((?:[A-Za-z_$][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error))(?=[:\s]|$)")
STAGES = {
    "forge_loader_identified": re.compile(r"^Forge Mod Loader version "),
    "mod_discovery": re.compile(r"^Forge Mod Loader has identified ([0-9]+) mods? to load"),
    "mods_loaded": re.compile(r"^Forge Mod Loader has successfully loaded ([0-9]+) mods?"),
    "resource_reload": re.compile(r"^Reloading ResourceManager:"),
    "audio_started": re.compile(r"^(?:Sound engine started|OpenAL initialized\.)"),
    "texture_atlas_created": re.compile(r"^Created: [0-9]+x[0-9]+ textures-atlas"),
    "connecting": re.compile(r"^Connecting to "),
    "connection_negotiation": re.compile(r"^Attempting connection with missing mods "),
    "modded_connection_established": re.compile(r"^Client side modded connection established"),
    "preinitialization": re.compile(r"^(?:Processing ObjectHolder annotations|Applying holder lookups|Forge Mod Loader has detected|MinecraftForge v|Beginning (?:Minecraft )?preinitialization|Preinitializ)"),
    "client_stopping": re.compile(r"^Stopping!$"),
}


def parse_log(text: str) -> dict:
    levels: Counter = Counter()
    stages: Counter = Counter()
    exceptions: Counter = Counter()
    graphics = []
    discovered = None
    loaded = None
    for line in text.splitlines():
        level = re.search(r"\[[^\]\r\n]*/(WARN|ERROR|FATAL)\]", line)
        if level:
            levels[level[1]] += 1
        # Do not output thread/logger prefixes; they may contain personal text.
        message = line.rsplit("]: ", 1)[-1]
        if re.search(r"\[[^\]\r\n]*/INFO\]", line) and GRAPHICS.fullmatch(message):
            graphics.append(message)
        for name, pattern in STAGES.items():
            match = pattern.match(message)
            if match:
                stages[name] += 1
                if name == "mod_discovery": discovered = int(match[1])
                if name == "mods_loaded": loaded = int(match[1])
        exceptions.update(EXCEPTION.findall(line))
    return {"graphicsMessages": graphics[-12:], "startupStagesObserved": dict(stages),
            "discoveredModCount": discovered, "loadedModCount": loaded,
            "severityCounts": dict(levels),
            "exceptionClassCounts": dict(exceptions.most_common(128)),
            "exceptionClassesOmitted": max(0, len(exceptions) - 128),
            "interpretation": "Counts describe only the inspected tail; absence is not proof a stage failed"}


def inspect_log(game_dir: Path) -> dict:
    path = game_dir / "logs" / "latest.log"
    if not path.exists():
        return {"available": False}
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("latest.log must be an ordinary file")
    if game_dir not in path.resolve().parents:
        raise ValueError("Log path resolves outside this instance")
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = max(0, size - LOG_LIMIT)
        stream.seek(offset)
        data = stream.read(LOG_LIMIT)
    if offset:
        data = data.partition(b"\n")[2]  # Drop the partial first line.
    return {"available": True, "fileBytesAtOpen": size, "bytesInspected": len(data),
            "tailWindow": offset > 0, "maximumReadBytes": LOG_LIMIT,
            **parse_log(data.decode("utf-8", errors="replace"))}


def process_identity(proc: Path) -> tuple[str, str, str]:
    # The start tick guards against a PID being reused during the snapshot.
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    return str((proc / "exe").resolve(strict=True)), str((proc / "cwd").resolve(strict=True)), fields[19]


def libraries(proc: Path) -> dict:
    with (proc / "maps").open("rb") as stream:
        data = stream.read(MAPS_LIMIT + 1)
    truncated = len(data) > MAPS_LIMIT
    data = data[:MAPS_LIMIT]
    if truncated:
        data = data.rsplit(b"\n", 1)[0]
    selected = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or not fields[5].startswith("/"):
            continue
        path = fields[5]
        deleted = path.endswith(" (deleted)")
        ordinary_path = path[:-10] if deleted else path
        basename = Path(ordinary_path).name
        if LIBRARY.fullmatch(basename):
            selected[path] = {"basename": basename, "path": ordinary_path, "deleted": deleted}
    return {"maximumReadBytes": MAPS_LIMIT, "truncated": truncated,
            "selectedSharedLibraries": sorted(selected.values(), key=lambda row: row["path"])}


def snapshot(pid: int, instance: Path) -> dict:
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise ValueError("Run on the Linux ARM64 Switch")
    if pid <= 0 or not instance.is_absolute():
        raise ValueError("Use a positive --pid and absolute --instance directory")
    instance = instance.resolve(strict=True)
    game_dir = (instance / ".minecraft").resolve(strict=True)
    if not instance.is_dir() or not game_dir.is_dir() or game_dir.parent != instance:
        raise ValueError("Expected the specified instance's ordinary .minecraft directory")
    proc = Path("/proc") / str(pid)
    if proc.stat().st_uid != os.getuid():
        raise ValueError("The game must belong to the current user")
    identity = process_identity(proc)
    executable, cwd, _start_tick = identity
    if Path(executable).name != "java" or Path(cwd) != game_dir:
        raise ValueError("PID is not Java running from this instance's .minecraft directory")
    with Path(executable).open("rb") as stream:
        header = stream.read(20)
    if len(header) < 20 or header[:6] != bytes.fromhex("7f454c460201") or header[18:20] != bytes.fromhex("b700"):
        raise ValueError("Game Java executable is not little-endian ELF64 AArch64")
    status = {}
    for line in (proc / "status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"VmRSS", "VmSwap", "Threads"}:
            status[key] = value.strip()
    result = {"schema": 1, "observedUtc": datetime.now(timezone.utc).isoformat(),
              "machine": platform.node(), "architecture": platform.machine(), "pid": pid,
              "javaExecutable": executable, "javaElfAArch64": True,
              "cwdMatchesSpecifiedInstance": True, "processStatus": status,
              "maps": libraries(proc), "log": inspect_log(game_dir),
              "privacy": "No cmdline, environment, account files, raw errors, chat, addresses or complete log copied",
              "performanceOrPlayabilityValidated": False}
    if process_identity(proc) != identity or proc.stat().st_uid != os.getuid():
        raise ValueError("Process identity changed during the snapshot; repeat with the actual game PID")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True, help="Actual game Java PID, not Prism")
    parser.add_argument("--instance", type=Path, required=True, help="Absolute isolated instance directory")
    args = parser.parse_args()
    print(json.dumps(snapshot(args.pid, args.instance), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, IndexError) as error:
        print(json.dumps({"snapshotAvailable": False, "errorType": type(error).__name__,
                          "safeReason": str(error) if isinstance(error, ValueError)
                                        else "Process or selected diagnostic file is unavailable"}))
        raise SystemExit(1)
