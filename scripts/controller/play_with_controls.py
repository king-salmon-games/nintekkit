#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prism dispatch + Java lifecycle, awake leases, presentation and optional controls."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


class LifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class JavaProcess:
    pid: int
    executable: str
    starttime: str
    cwd: str


def validate_server(value):
    """Accept an ordinary DNS/IPv4 host[:port], never an option or shell fragment."""
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9.-]*)(?::([0-9]{1,5}))?", value)
    if not match:
        raise argparse.ArgumentTypeError("Server must be an ordinary host[:port] without whitespace or option prefixes")
    host, port = match.groups()
    labels = host.split(".")
    if len(host) > 253 or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
        raise argparse.ArgumentTypeError("Server hostname is invalid")
    if port is not None and not 1 <= int(port) <= 65535:
        raise argparse.ArgumentTypeError("Server port must be between 1 and 65535")
    return value


def build_prism_command(prism_root, server=None):
    command = ["prismlauncher", "--dir", str(prism_root), "--launch", "Nintekkit-1.2.4"]
    if server is not None:
        command.extend(["--server", validate_server(server)])
    return command


def prepare_server_bookmark(instance, server, configure, report=print):
    # Minecraft can rewrite legitimate NBT ordering/icons. Existing lists are the
    # user's data: do not read/compare/replace them during an ordinary launch.
    path = instance / ".minecraft/servers.dat"
    if path.exists() or path.is_symlink():
        report("Existing server list preserved; continuing Prism direct join")
        return {"status": "preserved_existing"}
    return configure(instance, server) # Unsafe identity/process refusals propagate.


def parse_starttime(stat_text: str) -> str:
    # /proc/PID/stat field 2 can contain spaces and ')' characters.
    fields = stat_text[stat_text.rfind(")") + 2:].split()
    if len(fields) < 20 or not fields[19].isdigit():
        raise ValueError("Malformed Java process stat")
    return fields[19]


def discover_clients(minecraft_dir: Path, proc: Path = Path("/proc"), uid: int | None = None) -> list[JavaProcess]:
    """Inspect only UID, executable, cwd and stat; never command lines/environments."""
    uid = os.getuid() if uid is None else uid
    expected = minecraft_dir.resolve(strict=True)
    found = []
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != uid:
                continue
            executable = (process / "exe").resolve(strict=True)
            if executable.name != "java":
                continue
            cwd = (process / "cwd").resolve(strict=True)
            if cwd != expected:
                continue
            start = parse_starttime((process / "stat").read_text())
            found.append(JavaProcess(int(process.name), str(executable), start, str(cwd)))
        except (OSError, ValueError):
            # A process can exit between any two reads. It is not a live candidate.
            continue
    return sorted(found, key=lambda item: item.pid)


def stop_mapper(process, timeout=10):
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate() # SIGTERM lets the mapper release holds and restore XInput.
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise LifecycleError("Mapper PID " + str(process.pid) + " did not finish cleanup; inspect its recovery snapshot. Game was not killed.") from error


def supervise(prism_command, mapper_command, discover, *, startup_timeout=300, poll_seconds=1,
              stop_requested=lambda: False, spawn=subprocess.Popen, clock=time.monotonic,
              sleep=time.sleep, report=print, shutdown_timeout=10, awake_factory=None,
              presentation_factory=None, before_dispatch=None):
    existing = discover()
    if existing:
        raise LifecycleError("Refusing pre-existing Java client(s) in the isolated instance: " + ", ".join(str(item.pid) for item in existing))
    if stop_requested():
        return 0
    if before_dispatch:
        before_dispatch()
    if stop_requested():
        return 0
    # Do not copy launcher output into diagnostics: authentication remains in Prism.
    dispatcher = spawn(prism_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = clock() + startup_timeout
    game, mapper = None, None
    report("Prism dispatched; waiting for one new Java process in the exact isolated game directory")
    while not stop_requested():
        clients = discover()
        if len(clients) > 1:
            raise LifecycleError("Multiple new Java clients match the instance; refusing controller ownership")
        if clients:
            game = clients[0]
            break
        code = dispatcher.poll()
        if code is not None and code != 0:
            raise LifecycleError("Prism dispatch failed with exit code " + str(code) + "; inspect its local GUI. No mapper started.")
        if clock() >= deadline:
            raise LifecycleError("No matching Java client appeared within " + str(startup_timeout) + " seconds; check local Prism sign-in/startup")
        sleep(min(poll_seconds, max(0, deadline - clock())))
    if game is None or stop_requested():
        return 0
    # A short-lived JVM can disappear between discovery and mapper startup.
    current = discover()
    if len(current) > 1:
        raise LifecycleError("Multiple Java clients appeared before mapper startup")
    if current != [game]:
        raise LifecycleError("Discovered Java client exited or changed identity before mapper startup")
    if stop_requested():
        return 0
    awake = presentation = None
    try:
        if awake_factory:
            awake = awake_factory(game.pid)
            awake.refresh()
            # A D-Bus call can block briefly: never start input after stop/PID reuse.
            if stop_requested():
                return 0
            if discover() != [game]:
                raise LifecycleError("Java client changed identity while acquiring screen-lock inhibition; no mapper started")
        if presentation_factory:
            presentation = presentation_factory(game.pid)
            presentation.refresh()
            if stop_requested():
                return 0
            if discover() != [game]:
                raise LifecycleError("Java client changed identity during presentation setup; no mapper started")
        if mapper_command is not None:
            mapper = spawn([*mapper_command, "--run", "--pid", str(game.pid)])
        report("Watching actual Java PID " + str(game.pid) + ("; contextual controls enabled" if mapper else "; controls inactive"))
        while not stop_requested():
            clients = discover()
            if len(clients) > 1:
                raise LifecycleError("Instance now has multiple Java clients; stopping mapper without killing games")
            if clients != [game]:
                report("Watched Java process exited or changed identity; stopping mapper")
                break
            if awake:
                awake.refresh()
            if stop_requested():
                break
            if presentation:
                presentation.refresh()
            if mapper is not None:
                code = mapper.poll()
                if code is not None:
                    if discover() != [game]:
                        report("Watched Java exited while mapper was stopping")
                        break
                    raise LifecycleError("Controller mapper exited while Java remained running (code " + str(code) + "). Game was not killed.")
            sleep(poll_seconds)
        return 0
    finally:
        try:
            stop_mapper(mapper, timeout=shutdown_timeout)
        finally:
            try:
                if presentation:
                    presentation.close()
            finally:
                if awake:
                    awake.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("NINTEKKIT_ROOT", str(Path.home() / ".local/share/nintekkit"))))
    parser.add_argument("--startup-timeout", type=float, default=300, help="Seconds for Java to appear; not the pack's world-load deadline")
    parser.add_argument("--inspect", action="store_true", help="Read matching Java identities only; do not dispatch or start controls")
    parser.add_argument("--server", type=validate_server, help="Optional host[:port]; creates one private isolated bookmark if absent and passes direct-join to Prism")
    parser.add_argument("--no-controls", action="store_true", help="Manage game presentation/awake leases without starting the Joy-Con mapper")
    args = parser.parse_args()
    if not sys.platform.startswith("linux") or not os.environ.get("DISPLAY", "").startswith(":"):
        parser.error("Run in the Switch's actual local X11 desktop, through desktop-run.py when using SSH")
    if not math.isfinite(args.startup_timeout) or args.startup_timeout <= 0:
        parser.error("--startup-timeout must be positive and finite")
    root = args.root.expanduser().resolve(strict=True)
    prism_root = root / "prism"
    instance = prism_root / "instances/Nintekkit-1.2.4"
    minecraft = instance / ".minecraft"
    if not (instance / "mmc-pack.json").is_file() or not minecraft.is_dir():
        raise LifecycleError("Exact isolated Nintekkit-1.2.4 instance is missing")
    discover = lambda: discover_clients(minecraft)
    if args.inspect:
        print(json.dumps({"mode": "inspect-no-launch-or-input", "game_directory": str(minecraft.resolve()),
                          "matching_java": [asdict(process) for process in discover()]}, indent=2))
        return 0
    mapper = Path(__file__).resolve().with_name("nintekkit_controller.py")
    if not mapper.is_file():
        raise LifecycleError("Adjacent nintekkit_controller.py is missing")
    import fcntl
    from nintekkit_controller import private_directory
    from screen_awake import make_inhibitor
    from window_presentation import make_presentation
    before_dispatch = None
    if args.server:
        from configure_server import configure_server
        before_dispatch = lambda: prepare_server_bookmark(instance, args.server, configure_server,
            report=lambda message: print(message, flush=True))
    interrupted = 0
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = signum
    old_handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        with (private_directory() / "nintekkit-play.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LifecycleError("Another Nintekkit launch/control supervisor owns this session") from error
            result = supervise(build_prism_command(prism_root, args.server),
                               None if args.no_controls else [sys.executable, str(mapper), "--context-file", str(minecraft / "nintekkit-controls/state.json")], discover,
                               startup_timeout=args.startup_timeout, stop_requested=lambda: bool(interrupted),
                               report=lambda message: print(message, flush=True),
                               awake_factory=lambda pid: make_inhibitor(pid, report=lambda message: print(message, flush=True)),
                               presentation_factory=lambda pid: make_presentation(pid, report=lambda message: print(message, flush=True)),
                               before_dispatch=before_dispatch)
            return 128 + interrupted if interrupted else result
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
