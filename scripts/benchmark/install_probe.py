#!/usr/bin/env python3
"""Install/roll back the original client probe only while Java and Prism are closed.

QSettings scalar strings are handled narrowly; ambiguous encodings are refused.
Backups and the transaction record remain in the private capture directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
import zipfile


PROPERTY = "-Dnintekkit.frameprobe.dir"
VERSION = "0.2.0"
JAR_NAME = "nintekkit-frameprobe-" + VERSION + ".jar"
STATE_NAME = ".nintekkit-probe-overlay"
LICENSE_SHA256 = "b06058f2f3f62cdcb61ab7d91a27e60e6b6a3b479bc9c81c72f42f985a540be7"
PREFIX = "com/kingsalmon/nintekkit/frameprobe/"
CLASSES = {PREFIX + name + ".class" for name in ("FrameProbe", "FrameRing", "Recorder", "Recorder$1", "ControlsContext")}
META = {"META-INF/LICENSE", "META-INF/MANIFEST.MF", "META-INF/nintekkit-build.json", "mcmod.info"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""): value.update(data)
    return value.hexdigest()


def ordinary(path: Path, directory: bool = False) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or path.is_symlink() or info.st_uid != os.getuid():
        raise ValueError("Expected an ordinary path owned by the current user: " + path.name)


def bounded(path: Path, maximum: int = 1024 * 1024) -> bytes:
    ordinary(path)
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("File exceeds the overlay helper's size limit: " + path.name)
    return data


def require_platform() -> None:
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise ValueError("Run as the instance owner on the Linux ARM64 Switch")


def require_stopped(instance: Path, proc: Path = Path("/proc")) -> None:
    require_platform()
    expected_cwd = (instance / ".minecraft").resolve(strict=True)
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            name = (process / "comm").read_text().strip().lower()
            if name.startswith("prismlauncher"):
                raise ValueError("Close every Prism Launcher process before changing the overlay")
            if name in {"java", "javaw"}:
                try:
                    cwd = (process / "cwd").resolve(strict=True)
                except PermissionError as error:
                    raise ValueError("Cannot rule out an active Java process; close it or inspect its cwd first") from error
                if cwd == expected_cwd:
                    raise ValueError("The specified instance still has a running Java process")
        except (FileNotFoundError, ProcessLookupError):
            pass  # A process exited while enumerating /proc.


def ini_values(data: bytes) -> dict[str, str]:
    # Latin-1 preserves every untouched byte, including unrelated UTF-8 values.
    result = {}
    section = None
    seen_general = False
    for line in data.decode("latin-1").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            if section == "General":
                if seen_general: raise ValueError("Repeated General section in INI")
                seen_general = True
        elif section == "General" and "=" in line and not stripped.startswith(("#", ";")):
            key, value = line.split("=", 1)
            key = key.strip()
            if key in {"JvmArgs", "OverrideJavaArgs"}:
                if key in result: raise ValueError("Duplicate Java key in INI")
                result[key] = value.strip()
    if not seen_general:
        raise ValueError("Expected a General section in INI")
    return result


def qt_string(raw: str) -> str:
    """Decode canonical Qt scalar ASCII strings; refuse lists/variants/ambiguity."""
    raw = raw.strip()
    if raw.startswith("@"):
        raise ValueError("Typed QSettings values require manual review")
    if raw.startswith('"'):
        if not raw.endswith('"') or len(raw) < 2:
            raise ValueError("Unterminated QSettings string")
        raw = raw[1:-1]
    elif any(char in raw for char in ',;"'):
        raise ValueError("Unquoted QSettings list/comment/quote requires manual review")
    output = []
    index = 0
    escapes = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", '"': '"', "\\": "\\", "?": "?", "'": "'"}
    while index < len(raw):
        char = raw[index]
        index += 1
        if char == '"': raise ValueError("Mixed QSettings quoting requires manual review")
        if char == "\\":
            if index == len(raw): raise ValueError("Incomplete QSettings escape")
            char = raw[index]
            index += 1
            if char in escapes:
                char = escapes[char]
            elif char == "x" or char in "01234567":
                start = index if char == "x" else index - 1
                digits = "0123456789abcdefABCDEF" if char == "x" else "01234567"
                while index < len(raw) and raw[index] in digits: index += 1
                if start == index: raise ValueError("Invalid QSettings numeric escape")
                char = chr(int(raw[start:index], 16 if char == "x" else 8) & 0xFFFF)
            else:
                raise ValueError("Unsupported QSettings escape requires manual review")
        output.append(char)
    decoded = "".join(output)
    if any(ord(char) < 32 or ord(char) > 126 for char in decoded):
        raise ValueError("Non-ASCII/control characters in JVM arguments require manual review")
    return decoded


def qt_encoded(value: str) -> str:
    encoded = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + encoded + '"' if any(c in value for c in ",;=") or value != value.strip() else encoded


def changed_config(before: bytes, effective_args: str, output: Path) -> bytes:
    after_values = {"OverrideJavaArgs": "true", "JvmArgs": qt_encoded((effective_args + " " if effective_args else "") + PROPERTY + "=" + str(output))}
    lines = before.decode("latin-1").splitlines(keepends=True)
    newline = "\r\n" if b"\r\n" in before else "\n"
    start = next(i for i, line in enumerate(lines) if line.strip() == "[General]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    seen = set()
    for index in range(start + 1, end):
        match = re.match(r"^([ \t]*)(JvmArgs|OverrideJavaArgs)[ \t]*=", lines[index])
        if match:
            key = match[2]
            lines[index] = match[1] + key + "=" + after_values[key] + newline
            seen.add(key)
    missing = [key + "=" + value + newline for key, value in after_values.items() if key not in seen]
    if missing:
        if end and not lines[end - 1].endswith(("\n", "\r")): lines[end - 1] += newline
        lines[end:end] = missing
    return "".join(lines).encode("latin-1")


def validate_jar(path: Path) -> tuple[bytes, dict]:
    data = bounded(path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) != len(CLASSES | META) or {entry.filename for entry in entries} != CLASSES | META:
            raise ValueError("Probe must contain only the original client-tool classes and metadata")
        if sum(entry.file_size for entry in entries) > 1024 * 1024:
            raise ValueError("Probe archive expands beyond the allowed size")
        if sha(archive.read("META-INF/LICENSE")) != LICENSE_SHA256:
            raise ValueError("Probe MIT license differs from the original project license")
        build = json.loads(archive.read("META-INF/nintekkit-build.json"))
        if not isinstance(build, dict) or build.get("probe_version") != VERSION or build.get("target_class_major") != 52:
            raise ValueError("Probe build metadata does not identify the Java 8 original")
        source_hashes = build.get("sources")
        expected_sources = {"src/" + PREFIX + name + ".java" for name in ("FrameProbe", "FrameRing", "Recorder", "ControlsContext")}
        if not isinstance(source_hashes, dict) or set(source_hashes) != expected_sources or any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in source_hashes.values()):
            raise ValueError("Probe source provenance metadata is incomplete")
        for name in CLASSES:
            code = archive.read(name)
            if len(code) < 8 or code[:4] != bytes.fromhex("cafebabe") or int.from_bytes(code[6:8], "big") != 52:
                raise ValueError("Every probe class must target Java class version 52")
        mods = json.loads(archive.read("mcmod.info"))
        if not isinstance(mods, list) or len(mods) != 1 or not isinstance(mods[0], dict) or mods[0].get("modid") != "nintekkit_frameprobe" or mods[0].get("version") != VERSION or mods[0].get("mcversion") != "1.12.2" or mods[0].get("clientSideOnly") is not True:
            raise ValueError("Expected the single client-only probe mod")
    return data, build


def atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".nintekkit-probe-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def exclusive_copy(path: Path, data: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".nintekkit-probe-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Atomic publication; never replaces an existing target.
    finally:
        temporary.unlink(missing_ok=True)


def record(state: Path, value: dict) -> None:
    atomic(state / "transaction.json", (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def paths(instance: Path, output: Path) -> tuple[Path, Path, Path, Path]:
    if not instance.is_absolute() or not output.is_absolute():
        raise ValueError("Instance and output directory must be explicit absolute paths")
    ordinary(instance, True)
    instance = instance.resolve(strict=True)
    ordinary(instance / ".minecraft", True)
    ordinary(instance / ".minecraft/mods", True)
    ordinary(instance / "instance.cfg")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", output.as_posix()):
        raise ValueError("Use a plain ASCII Unix output path without spaces, quotes or argument metacharacters")
    if output.is_symlink(): raise ValueError("Output directory cannot be a symlink")
    output = output.resolve()
    if output == instance or instance in output.parents:
        raise ValueError("Keep capture output outside the game instance")
    if output.exists():
        ordinary(output, True)
        if stat.S_IMODE(output.stat().st_mode) & 0o077:
            raise ValueError("Existing output directory must already be private (mode 700)")
    return instance, output, instance / "instance.cfg", instance / ".minecraft/mods" / JAR_NAME


def install(instance: Path, jar: Path, output: Path) -> dict:
    require_platform()
    instance, output, config, target = paths(instance, output)
    require_stopped(instance)
    jar_data, build = validate_jar(jar)
    before = bounded(config)
    values = ini_values(before)
    override = qt_string(values.get("OverrideJavaArgs", "false")).lower()
    if override not in {"true", "false"}: raise ValueError("OverrideJavaArgs must be a plain boolean")
    local_args = qt_string(values.get("JvmArgs", ""))
    global_config = instance.parent.parent / "prismlauncher.cfg"
    global_bytes = bounded(global_config) if global_config.exists() else None
    global_args = qt_string(ini_values(global_bytes).get("JvmArgs", "")) if global_bytes is not None else ""
    if PROPERTY in local_args or PROPERTY in global_args:
        raise ValueError("An existing frame-probe JVM property is preserved; resolve it before installing")
    effective = local_args if override == "true" else global_args
    after = changed_config(before, effective, output)
    mods = []
    for path in sorted((instance / ".minecraft/mods").rglob("*.jar")):
        ordinary(path)
        if "frameprobe" in path.name.lower() or "frame-probe" in path.name.lower():
            raise ValueError("An existing probe JAR is preserved; use its original rollback record")
        mods.append({"path": path.relative_to(instance).as_posix(), "bytes": path.stat().st_size, "sha256": file_sha(path)})
    if target.exists() or target.is_symlink(): raise ValueError("Existing probe target is preserved")
    state = output / STATE_NAME
    if state.exists(): raise ValueError("An overlay journal already exists; inspect or roll back that transaction")
    require_stopped(instance)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    ordinary(output, True)
    state.mkdir(mode=0o700)
    atomic(state / "instance.cfg.before", before)
    atomic(state / "instance.cfg.after", after)
    transaction = {"schema": 1, "phase": "prepared", "utc": datetime.now(timezone.utc).isoformat(),
                   "instance": str(instance), "output": str(output), "jarName": JAR_NAME,
                   "jarSha256": sha(jar_data), "configBeforeSha256": sha(before), "configAfterSha256": sha(after),
                   "configMode": stat.S_IMODE(config.stat().st_mode), "preexistingMods": mods,
                   "globalJvmArgsInherited": override == "false", "globalConfigSha256": sha(global_bytes) if global_bytes is not None else None,
                   "originalOverrideJavaArgs": override, "probeBuild": build}
    record(state, transaction)  # Backups/record precede either runtime-content write.
    require_stopped(instance)
    current_global = bounded(global_config) if global_config.exists() else None
    if bounded(config) != before or current_global != global_bytes:
        raise ValueError("Launcher configuration changed during preparation; no probe copied")
    # Exclusive creation never overwrites a file that appeared concurrently.
    exclusive_copy(target, jar_data)
    transaction["phase"] = "jar-copied"
    record(state, transaction)
    if sha(target.read_bytes()) != transaction["jarSha256"]: raise ValueError("Copied probe verification failed")
    require_stopped(instance)
    if bounded(config) != before: raise ValueError("Configuration changed; probe copied but config preserved; use rollback")
    atomic(config, after, transaction["configMode"])
    transaction["phase"] = "installed"
    record(state, transaction)
    return {"installed": True, "journal": str(state), "jarSha256": transaction["jarSha256"],
            "globalJvmArgsInherited": transaction["globalJvmArgsInherited"], "performanceValidated": False}


def rollback(instance: Path, output: Path) -> dict:
    require_platform()
    instance, output, config, target = paths(instance, output)
    require_stopped(instance)
    state = output / STATE_NAME
    ordinary(state, True)
    transaction = json.loads(bounded(state / "transaction.json"))
    if transaction.get("schema") != 1 or transaction.get("instance") != str(instance) or transaction.get("output") != str(output) or transaction.get("jarName") != JAR_NAME:
        raise ValueError("Overlay journal identity differs; no changes made")
    before = bounded(state / "instance.cfg.before")
    after = bounded(state / "instance.cfg.after")
    if sha(before) != transaction["configBeforeSha256"] or sha(after) != transaction["configAfterSha256"]:
        raise ValueError("Configuration backup integrity failed; restore manually after review")
    current = bounded(config)
    if current not in {before, after}:
        raise ValueError("instance.cfg changed after installation (possibly Prism/game writes); manual merge required, nothing overwritten")
    if target.is_symlink(): raise ValueError("Probe target became a symlink; nothing overwritten")
    if target.exists():
        ordinary(target)
        if sha(target.read_bytes()) != transaction["jarSha256"]:
            raise ValueError("Probe JAR changed; manual review required, nothing overwritten")
    elif current == after:
        raise ValueError("Installed probe JAR is absent; cannot verify it before rollback, nothing overwritten")
    require_stopped(instance)
    if bounded(config) != current: raise ValueError("Configuration changed during rollback; nothing overwritten")
    if current == after: atomic(config, before, transaction["configMode"])
    if target.exists():
        if sha(bounded(target)) != transaction["jarSha256"]: raise ValueError("Probe changed during rollback; configuration restored but JAR preserved")
        target.unlink()
    transaction["phase"] = "rolled-back"
    record(state, transaction)
    return {"rolledBack": True, "journalRetained": str(state), "otherModsModifiedByRollback": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--jar", type=Path, help="Original MIT probe JAR; required for --install")
    parser.add_argument("--output-directory", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if args.install and not args.jar: parser.error("--install requires --jar")
    result = install(args.instance, args.jar, args.output_directory) if args.install else rollback(args.instance, args.output_directory)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        raise SystemExit("Probe overlay refused/incomplete: " + str(error))
