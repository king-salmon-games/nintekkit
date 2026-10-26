#!/usr/bin/env python3
"""Control the in-game frame probe on the same host as the game (usually Switch)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


CAPTURE_PROCESSES = {"ffmpeg", "obs", "obs-studio", "x11vnc", "wayvnc", "Xtigervnc",
                     "Xvnc", "vncserver", "x0vncserver", "wf-recorder", "recordmydesktop",
                     "gnome-remote-de", "sunshine", "nxserver", "rustdesk", "anydesk",
                     "krfb", "krfb-virtualmonitor", "krdpserver", "krdp-server"}
# Linux /proc/<pid>/comm exposes at most 15 bytes; these command names are ASCII.
CAPTURE_PROCESSES |= {name[:15] for name in CAPTURE_PROCESSES}


def read_properties(path: Path) -> dict[str, str]:
    """Read our Java status/metadata files, including escaped unicode and separators."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="iso-8859-1").splitlines():
        if not line or line.lstrip().startswith(("#", "!")):
            continue
        match = re.match(r"((?:\\.|[^:=\s])*)\s*[:=]?\s*(.*)$", line)
        if match:
            def decode(value: str) -> str:
                return re.sub(r"\\u([0-9a-fA-F]{4})|\\(.)", lambda m: chr(int(m[1], 16)) if m[1]
                              else {"t": "\t", "r": "\r", "n": "\n", "f": "\f"}.get(m[2], m[2]), value)
            result[decode(match[1])] = decode(match[2])
    return result


def property_escape(value: object) -> str:
    text = str(value)
    output = ""
    for char in text:
        if char in "\\:=#! ":
            output += "\\" + char
        elif char == "\n": output += "\\n"
        elif char == "\r": output += "\\r"
        elif ord(char) > 127:
            output += "".join("\\u%04x" % int.from_bytes(pair, "big")
                              for pair in [char.encode("utf-16-be")[i:i+2]
                                           for i in range(0, len(char.encode("utf-16-be")), 2)])
        else: output += char
    return output


def capture_processes(proc: Path = Path("/proc")) -> list[dict]:
    if not proc.is_dir():
        raise RuntimeError("Capture process guard requires Linux /proc; run control.py on the Switch via SSH")
    found = []
    for process in proc.iterdir():
        if process.name.isdigit():
            try:
                name = (process / "comm").read_text().strip()
                if name in CAPTURE_PROCESSES or any(name.startswith(prefix) for prefix in ("Xtigervnc", "Xvnc")):
                    found.append({"pid": int(process.name), "name": name})
            except (OSError, UnicodeError):
                pass
    return found


def request(directory: Path, values: dict) -> dict:
    directory = directory.resolve(strict=True)
    status_path = directory / "status.properties"
    if not status_path.is_file():
        raise RuntimeError("Probe is not ready: launch with -Dnintekkit.frameprobe.dir=<directory>")
    old = read_properties(status_path)
    if old.get("state") in {"closed", "error"}:
        raise RuntimeError("Probe is not running: " + json.dumps(old))
    if values["action"] == "start" and old.get("state") == "recording":
        raise RuntimeError("A capture is already recording")
    command_id = uuid.uuid4().hex
    data = {"id": command_id, **values}
    temporary = directory / ("request-" + command_id + ".tmp")
    temporary.write_text("".join(f"{key}={property_escape(value)}\n" for key, value in data.items()), encoding="ascii")
    os.replace(temporary, directory / "request.properties")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            status = read_properties(status_path)
            if status.get("command_id") == command_id:
                if status.get("message"):
                    raise RuntimeError(status["message"])
                return status
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Probe did not acknowledge command within 5 seconds; inspect game/probe status")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    start = commands.add_parser("start")
    start.add_argument("--phase", choices=["base", "traversal", "experiment"], required=True)
    start.add_argument("--seconds", type=float, default=610)
    start.add_argument("--actual-server", action="store_true", help="Attest this is the user's real world server")
    start.add_argument("--capture-disabled", action="store_true", help="Attest all desktop/video capture is stopped")
    start.add_argument("--warmup-seconds", type=int, required=True)
    start.add_argument("--uncapped", action="store_true", help="Attest VSync is off and FPS limiter is unlimited")
    start.add_argument("--cap-fps", type=int, help="Declared final limiter, after a separate uncapped run")
    start.add_argument("--instance", required=True, help="Local instance name, no account or authentication data")
    start.add_argument("--server-alias", required=True, help="Non-secret label, e.g. existing-world")
    start.add_argument("--resolution", default="1280x720")
    start.add_argument("--conditions", default="", help="View distance, heap, graphics, route, config revision")
    mark = commands.add_parser("mark")
    mark.add_argument("--kind", choices=["loading", "teleport", "menu", "disconnect", "note"], required=True)
    mark.add_argument("--edge", choices=["start", "end", "point"], default="point")
    mark.add_argument("--note", default="")
    commands.add_parser("stop")
    commands.add_parser("status")
    commands.add_parser("check-capture")
    args = parser.parse_args()
    if args.action == "status":
        result = read_properties(args.directory / "status.properties")
    elif args.action == "check-capture":
        result = {"capture_processes": capture_processes(), "guard": "known names only; not a proof of absence"}
    elif args.action == "start":
        if not (600 <= args.seconds <= 86400):
            parser.error("Use at least 600 seconds; 610 is recommended for >=600 s between observed endpoints")
        if not args.actual_server or not args.capture_disabled:
            parser.error("--actual-server and --capture-disabled attestations are required")
        if args.warmup_seconds < 120:
            parser.error("Warm up in the world for at least 120 seconds before recording")
        if args.uncapped == (args.cap_fps is not None):
            parser.error("Declare exactly one of --uncapped or --cap-fps")
        if args.cap_fps is not None and args.cap_fps < 1:
            parser.error("--cap-fps must be positive")
        if not re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", args.resolution):
            parser.error("--resolution must be WIDTHxHEIGHT")
        captures = capture_processes()
        if captures:
            parser.error("Stop capture/sharing before benchmarking: " + json.dumps(captures))
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + args.phase + "-" + uuid.uuid4().hex[:8]
        values = {key: value for key, value in vars(args).items() if key != "directory" and value is not None}
        values["run_id"] = run_id
        values["capture_guard"] = "known-process-name scan passed; operator attested capture disabled"
        result = request(args.directory, values)
    else:
        result = request(args.directory, {key: value for key, value in vars(args).items() if key != "directory"})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
