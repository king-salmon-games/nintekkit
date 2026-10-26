#!/usr/bin/env python3
"""Build dependency-free Java 8 one-shot render diagnostic attach agent."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "patches/render-diagnostics"


def build(javac, output):
    version = subprocess.check_output([javac, "-version"], stderr=subprocess.STDOUT, text=True).strip()
    flags = ["-source", "8", "-target", "8"] if "javac 1.8." in version else ["--release", "8"]
    sources = sorted((SOURCE / "src").rglob("*.java"))
    metadata = {"version": "0.1.1", "compiler": version, "target_class_major": 52,
                "sources": {str(p.relative_to(SOURCE)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    with tempfile.TemporaryDirectory(prefix="nintekkit-render-") as temp:
        classes = Path(temp) / "classes"
        classes.mkdir()
        subprocess.run([javac, *flags, "-g:none", "-encoding", "UTF-8", "-d", str(classes), *map(str, sources)], check=True)
        entries = {str(p.relative_to(classes)).replace("\\", "/"): p.read_bytes() for p in classes.rglob("*.class")}
        entries["META-INF/MANIFEST.MF"] = ("Manifest-Version: 1.0\nAgent-Class: com.kingsalmon.nintekkit.diagnostics.RenderSnapshot\nCan-Redefine-Classes: false\nCan-Retransform-Classes: false\n\n").encode()
        entries["META-INF/LICENSE"] = (SOURCE / "LICENSE").read_bytes()
        entries["META-INF/nintekkit-build.json"] = (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode()
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(entries.items()):
                item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                archive.writestr(item, content)
    metadata["jar_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    metadata["output"] = str(output.resolve())
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--javac", default=shutil.which("javac"))
    parser.add_argument("--output", type=Path, default=SOURCE / "build/nintekkit-render-probe-0.1.1.jar")
    args = parser.parse_args()
    if not args.javac:
        parser.error("Use --javac with an installed JDK compiler")
    print(json.dumps(build(args.javac, args.output), indent=2))
