#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export a private safe manifest for one actual ARM64 client, without game changes.

Run through desktop-run.py if needed, but this exporter makes no X connections,
captures no screenshots and reads no account/server files, cmdline or environment.
Raw logs/configs are not copied. The new output directory must be outside the
instance and any Git checkout; no existing output is replaced.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys

import client_snapshot


HASH_LIMIT = 32 * 1024 * 1024
CONFIG_LIMIT = 64 * 1024
GAME_OPTIONS = {
    "renderDistance": (0, 64), "maxFps": (0, 1000), "mipmapLevels": (0, 4),
    "particles": (0, 2), "guiScale": (0, 8), "ao": (0, 2),
    "fancyGraphics": bool, "enableVsync": bool, "useVbo": bool,
    "fullscreen": bool, "entityShadows": bool,
    "renderClouds": {"fast", "fancy", "false"},
}
INSTANCE_OPTIONS = {
    "MaxMemAlloc": (0, 1048576), "MinMemAlloc": (0, 1048576),
    "MinecraftWinWidth": (0, 16384), "MinecraftWinHeight": (0, 16384),
    "OverrideMemory": bool, "OverrideResolution": bool, "Fullscreen": bool,
}
MEMORY_FIELDS = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Dirty", "Writeback"}
HASH_FILES = ("instance.cfg", "mmc-pack.json", ".minecraft/options.txt",
              ".minecraft/logs/latest.log", ".minecraft/logs/debug.log", ".minecraft/logs/fml-client-latest.log")


def ordinary(path, uid):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
        raise ValueError("Selected diagnostic source must be an ordinary same-user file")
    return info


def selected_options(path, allowed, separator, *, uid):
    if not path.exists():
        return {"available": False, "values": {}}
    ordinary(path, uid)
    with path.open("rb") as stream:
        data = stream.read(CONFIG_LIMIT + 1)
    if len(data) > CONFIG_LIMIT:
        raise ValueError("Selected configuration exceeds the read limit")
    values, invalid = {}, 0
    for line in data.decode("utf-8", "replace").splitlines():
        key, found, raw = line.partition(separator)
        key, raw = key.strip(), raw.strip()
        if not found or key not in allowed:
            continue
        rule = allowed[key]
        if rule is bool and raw.lower() in {"true", "false"}:
            values[key] = raw.lower() == "true"
        elif isinstance(rule, tuple) and re.fullmatch(r"[0-9]{1,9}", raw) and rule[0] <= int(raw) <= rule[1]:
            values[key] = int(raw)
        elif isinstance(rule, set) and raw in rule:
            values[key] = raw
        else:
            invalid += 1
    return {"available": True, "values": values, "invalidAllowlistedValuesOmitted": invalid}


def file_metadata(path, *, uid, limit=HASH_LIMIT):
    if not path.exists():
        return {"available": False}
    before = ordinary(path, uid)
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        # Hash exactly the initial bounded prefix, even if a live log grows.
        remaining = min(opened.st_size, limit)
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk: break
            digest.update(chunk)
            count += len(chunk)
            remaining -= len(chunk)
    after = path.lstat()
    signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    stable = signature(before) == signature(opened) == signature(after)
    return {"available": True, "fileBytesAtOpen": opened.st_size, "bytesHashed": count,
            "sha256OfInspectedPrefix": digest.hexdigest(), "maximumHashBytes": limit,
            "entireFileAtOpenHashed": count == opened.st_size,
            "unchangedDuringHash": stable, "fileBytesAfterRead": after.st_size,
            "modifiedUtcAtOpen": datetime.fromtimestamp(opened.st_mtime, timezone.utc).isoformat()}


def startup_timings(game):
    path = game / "logs/latest.log"
    if not path.exists(): return {"available": False}
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = max(0, size - client_snapshot.LOG_LIMIT)
        stream.seek(offset)
        data = stream.read(client_snapshot.LOG_LIMIT)
    if offset: data = data.partition(b"\n")[2]
    stages, day, previous, first = {}, 0, None, None
    for line in data.decode("utf-8", "replace").splitlines():
        clock = re.match(r"^\[([0-2][0-9]):([0-5][0-9]):([0-5][0-9])\]", line)
        if not clock or int(clock[1]) > 23: continue
        seconds = int(clock[1]) * 3600 + int(clock[2]) * 60 + int(clock[3])
        if previous is not None and seconds < previous - 43200: day += 86400
        previous = seconds
        first = day + seconds if first is None else first
        message = line.rsplit("]: ", 1)[-1]
        for name, pattern in client_snapshot.STAGES.items():
            if pattern.match(message):
                row = stages.setdefault(name, {"count": 0, "firstSecondsFromFirstObservedLine": day + seconds - first})
                row["count"] += 1
                row["lastSecondsFromFirstObservedLine"] = day + seconds - first
    return {"available": True, "tailWindow": bool(offset), "maximumReadBytes": client_snapshot.LOG_LIMIT,
            "stages": stages, "interpretation": "Seconds relative to first timestamp in bounded tail; not whole-startup durations"}


def validate_output(output, instance, uid):
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError("Output must be an absolute new directory")
    if output.resolve() != output:
        raise ValueError("Output must not traverse symlinks or parent aliases")
    if output == instance or instance in output.parents:
        raise ValueError("Output must be outside the game instance")
    if any((parent / ".git").exists() for parent in (output.parent, *output.parents)):
        raise ValueError("Output must be outside Git checkouts")
    parent = output.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid:
        raise ValueError("Output parent must be an existing same-user directory")


def export(pid, instance, output, *, proc=Path("/proc")):
    if platform.system() != "Linux" or platform.machine() != "aarch64" or os.getuid() == 0:
        raise ValueError("Run as the Switch desktop user on ARM64 Linux, without sudo")
    uid, process = os.getuid(), proc / str(pid)
    instance = instance.resolve(strict=True)
    game = instance / ".minecraft"
    validate_output(output, instance, uid)
    identity = client_snapshot.process_identity(process)
    snapshot = client_snapshot.snapshot(pid, instance)
    for relative in HASH_FILES:
        path = instance / relative
        if path.exists() or path.is_symlink():
            ordinary(path, uid)
            if instance not in path.resolve().parents:
                raise ValueError("Selected source resolves outside the specified instance")
    selected = {"instance": selected_options(instance / "instance.cfg", INSTANCE_OPTIONS, "=", uid=uid),
                "graphics": selected_options(game / "options.txt", GAME_OPTIONS, ":", uid=uid)}
    metadata = {}
    for relative in HASH_FILES:
        path = instance / relative
        if path.exists() and instance not in path.resolve().parents:
            raise ValueError("Selected source resolves outside the specified instance")
        metadata[relative] = file_metadata(path, uid=uid)
    memory = {}
    for line in (proc / "meminfo").read_text().splitlines():
        key, _, raw = line.partition(":")
        if key in MEMORY_FIELDS and re.fullmatch(r"\s*[0-9]+ kB\s*", raw):
            memory[key + "KiB"] = int(raw.split()[0])
    result = {"schema": 1, "kind": "private-safe-client-evidence-manifest", "client": snapshot,
              "selectedOptions": selected, "fileMetadata": metadata, "memory": memory,
              "startupTimings": startup_timings(game), "performanceOrPlayabilityValidated": False,
              "privacy": "No raw logs/configs, accounts, server lists, JVM arguments, environments, chat or session tokens copied"}
    if client_snapshot.process_identity(process) != identity or process.stat().st_uid != uid:
        raise ValueError("Java identity changed during export; no bundle written")
    output.mkdir(mode=0o700)
    destination = output / "manifest.json"
    with destination.open("x", encoding="utf-8") as stream:
        os.chmod(destination, 0o600)
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True, help="Actual Java PID, not Prism")
    parser.add_argument("--instance", type=Path, required=True, help="Absolute exact isolated instance directory")
    parser.add_argument("--output", type=Path, required=True, help="Absolute NEW private directory outside instance/Git")
    args = parser.parse_args()
    if args.pid <= 1 or not args.instance.is_absolute(): parser.error("Use actual positive Java PID and absolute instance")
    destination = export(args.pid, args.instance, args.output)
    print(json.dumps({"exported": True, "manifest": str(destination), "rawLogsCopied": False}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, IndexError) as error:
        print(json.dumps({"exported": False, "errorClass": type(error).__name__,
                          "safeReason": str(error) if isinstance(error, ValueError) else "Selected process/path unavailable"}))
        raise SystemExit(1)
