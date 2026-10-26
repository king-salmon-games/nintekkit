#!/usr/bin/env python3
"""Compile original probe against a local Forge universal JAR; package no dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "patches" / "frameprobe"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(forge_jar: Path, output: Path, javac: str) -> dict:
    forge_jar = forge_jar.resolve(strict=True)
    sources = sorted((SOURCE / "src").rglob("*.java"))
    with zipfile.ZipFile(forge_jar) as archive:
        if "net/minecraftforge/fml/common/gameevent/TickEvent$RenderTickEvent.class" not in archive.namelist():
            raise ValueError("Expected Forge 1.12.2 universal JAR with RenderTickEvent")
    version = subprocess.check_output([javac, "-version"], stderr=subprocess.STDOUT, text=True).strip()
    language = ["-source", "8", "-target", "8"] if "javac 1.8." in version else ["--release", "8"]
    with tempfile.TemporaryDirectory(prefix="nintekkit-frameprobe-") as temporary:
        classes = Path(temporary) / "classes"
        classes.mkdir()
        subprocess.run([javac, *language, "-g:none", "-encoding", "UTF-8", "-classpath", str(forge_jar),
                        "-d", str(classes), *map(str, sources)], check=True)
        info = {
            "probe_version": "0.2.0", "compiler": version, "target_class_major": 52,
            "forge_jar_sha256": sha256(forge_jar),
            "sources": {str(path.relative_to(SOURCE)).replace("\\", "/"): sha256(path) for path in sources},
        }
        entries = {str(path.relative_to(classes)).replace("\\", "/"): path.read_bytes()
                   for path in classes.rglob("*.class")}
        entries["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\n\n"
        entries["META-INF/LICENSE"] = (SOURCE / "LICENSE").read_bytes()
        entries["META-INF/nintekkit-build.json"] = (json.dumps(info, indent=2, sort_keys=True) + "\n").encode()
        entries["mcmod.info"] = json.dumps([{
            "modid": "nintekkit_frameprobe", "name": "Nintekkit Client Tools", "version": "0.2.0",
            "mcversion": "1.12.2", "description": "Client frame timestamps and local GUI context for Joy-Con controls.",
            "authorList": ["King Salmon Games"], "clientSideOnly": True,
        }], sort_keys=True).encode()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite {output}; select a new --output or back it up first")
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(entries.items()):
                item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                archive.writestr(item, data)
    info["output"] = str(output.resolve())
    info["jar_sha256"] = sha256(output)
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forge-jar", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=SOURCE / "build" / "nintekkit-frameprobe-0.2.0.jar")
    parser.add_argument("--javac", default=shutil.which("javac") or str(Path(os.environ.get("JAVA_HOME", "")) / "bin" / "javac"))
    args = parser.parse_args()
    print(json.dumps(build(args.forge_jar, args.output, args.javac), indent=2))


if __name__ == "__main__":
    main()
