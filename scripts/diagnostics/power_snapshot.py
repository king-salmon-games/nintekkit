#!/usr/bin/env python3
"""Read-only post-wake power evidence; run as the Switch desktop user, without sudo.

No raw journal messages, user identities, account files or process arguments are
emitted; the requested machine hostname is included. A missing/empty readable
journal cannot establish a shutdown cause.
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
import selectors
import socket
import subprocess
import sys
import time


JOURNAL_BYTES = 512 * 1024
JOURNAL_LINES = 250
UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
POWER_FIELDS = {
    "type", "status", "present", "online", "capacity", "capacity_level", "health",
    "voltage_now", "current_now", "power_now", "charge_now", "charge_full",
    "charge_full_design", "energy_now", "energy_full", "energy_full_design", "temp",
}
POWER_ENUMS = {
    "Unknown", "Battery", "Mains", "USB", "USB_DCP", "USB_CDP", "USB_ACA",
    "USB_TYPE_C", "USB_PD", "USB_PD_DRP", "Wireless", "UPS", "Charging",
    "Discharging", "Not charging", "Full", "Normal", "High", "Low", "Critical",
    "Good", "Overheat", "Dead", "Over voltage", "Unspecified failure", "Cold",
    "Watchdog timer expire", "Safety timer expire", "No battery", "Warm", "Cool",
}
CATEGORIES = {
    "suspend_entry": r"PM: suspend entry|Suspending system|Entering sleep state|Performing sleep operation|Starting .*System Suspend|system is about to suspend",
    "resume": r"PM: suspend exit|PM: resume|System returned from sleep|Finished .*System Suspend|Waking up from system sleep",
    "shutdown": r"systemd-shutdown|Shutting down|Powering off|Power-off|Reached target .*(?:Shutdown|System Power Off)|Starting .*System Power Off|system is powering down",
    "out_of_memory": r"out of memory|oom-kill|oom_reaper|Killed process [0-9]+|Memory cgroup out of memory|systemd-oomd.*Killed",
    "thermal_critical": r"thermal.*(?:critical|shutdown|trip)|critical temperature|temperature above threshold",
    "battery_critical": r"(?:critical|low).{0,40}battery|battery.{0,40}critical|battery.{0,40}(?:empty|exhausted)|upower.*(?:critical|shutdown)",
}
PATTERNS = {name: re.compile(pattern, re.I) for name, pattern in CATEGORIES.items()}
JOURNAL_GREP = "|".join("(?:" + pattern + ")" for pattern in CATEGORIES.values())
KNOWN_UNITS = {
    "systemd-logind.service", "upower.service", "systemd-suspend.service",
    "systemd-hibernate.service", "systemd-sleep.service", "systemd-oomd.service",
    "powerdevil.service", "plasma-powerdevil.service",
}


def command(argv, *, limit=16384, timeout=4):
    """Bound both streams and runtime; never return raw stderr to the caller's JSON."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={**os.environ, "LC_ALL": "C"})
    except OSError as error:
        return {"available": False, "errorClass": type(error).__name__}, ""
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    truncated = timed_out = False
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = streams[key.data]
                cap = limit if key.data == "stdout" else 8192
                room = max(0, cap - len(target))
                target.extend(chunk[:room])
                if len(chunk) > room:
                    truncated = True
                    break
            if truncated:
                break
    if timed_out or truncated:
        if proc.poll() is None:
            proc.kill()
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait(timeout=2)
    proc.stdout.close()
    proc.stderr.close()
    stderr = streams["stderr"].decode("utf-8", "replace")
    notes = []
    for phrase, label in (("not seeing messages from other users", "journal_visibility_limited"),
                          ("Permission denied", "permission_denied"),
                          ("No journal files were found", "no_journal_files"),
                          ("no persistent journal", "no_persistent_journal"),
                          ("boot offset has no effect", "boot_history_unavailable")):
        if phrase.lower() in stderr.lower():
            notes.append(label)
    return {"available": True, "returnCode": proc.returncode, "timedOut": timed_out,
            "outputTruncated": truncated, "stderrPresent": bool(stderr),
            "diagnosticCategories": notes}, streams["stdout"].decode("utf-8", "replace")


def properties(argv, allowed):
    meta, text = command(argv)
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in allowed and re.fullmatch(r"[A-Za-z0-9_.@ :+-]{0,160}", value):
            result[key] = value
    return {**meta, "properties": result}


def small_text(path, limit=128):
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        return data.decode("ascii").strip() if len(data) <= limit else None
    except (OSError, UnicodeError):
        return None


def power_supplies(root=Path("/sys/class/power_supply")):
    result = []
    try:
        paths = sorted(root.iterdir())[:32]
    except OSError:
        return {"available": False, "supplies": []}
    for path in paths:
        fields = {}
        for field in sorted(POWER_FIELDS):
            value = small_text(path / field)
            if value is not None and (value in POWER_ENUMS or re.fullmatch(r"-?[0-9]{1,18}", value)):
                fields[field] = int(value) if re.fullmatch(r"-?[0-9]+", value) else value
        result.append({"supply": path.name if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", path.name) else "other",
                       "fields": fields})
    return {"available": True, "supplies": result,
            "units": "sysfs native units: microvolts/microamps/microwatts/microamp-hours/microwatt-hours; battery temp is tenths C"}


def thermals(root=Path("/sys/class/thermal")):
    result = []
    for path in sorted(root.glob("thermal_zone[0-9]*"))[:32]:
        temp, sensor = small_text(path / "temp"), small_text(path / "type")
        value = {"zone": path.name}
        if temp is not None and re.fullmatch(r"-?[0-9]{1,10}", temp):
            value["temperatureMilliC"] = int(temp)
        if sensor is not None and re.fullmatch(r"[A-Za-z0-9_-]{1,60}", sensor):
            value["sensor"] = sensor
        result.append(value)
    return result


def parse_journal(text):
    """Only categories and numeric times survive; never raw messages or process names."""
    counts, events, invalid = Counter(), [], 0
    for line in text.splitlines()[:JOURNAL_LINES]:
        if len(line) > 65536:
            invalid += 1
            continue
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            invalid += 1
            continue
        if not isinstance(item, dict) or not isinstance(item.get("MESSAGE"), str):
            invalid += 1
            continue
        message = item["MESSAGE"][:16384]
        categories = [name for name, pattern in PATTERNS.items() if pattern.search(message)]
        # Desktop greeters/applications also say "Shutting down" during login.
        # A system shutdown category needs system-manager/kernel provenance.
        if "shutdown" in categories and not (
                item.get("_PID") == "1" or item.get("_TRANSPORT") == "kernel"
                or item.get("_SYSTEMD_UNIT") in ("systemd-logind.service", "systemd-poweroff.service", "systemd-reboot.service", "systemd-halt.service")):
            categories.remove("shutdown")
        if not categories:
            continue
        counts.update(categories)
        event = {"categories": categories}
        for source, target in (("__REALTIME_TIMESTAMP", "epochUs"), ("__MONOTONIC_TIMESTAMP", "bootUs"), ("PRIORITY", "priority")):
            value = item.get(source)
            if isinstance(value, (str, int)) and re.fullmatch(r"[0-9]{1,20}", str(value)):
                event[target] = int(value)
        unit = item.get("_SYSTEMD_UNIT")
        event["source"] = "kernel" if item.get("_TRANSPORT") == "kernel" else unit if isinstance(unit, str) and unit in KNOWN_UNITS else "other"
        events.append(event)
    return {"categoryCounts": dict(counts), "lastCategorizedEvents": events[-40:],
            "invalidRecords": invalid, "maximumRecords": JOURNAL_LINES,
            "interpretation": "Only bounded readable matching records; no matches does not exclude a cause"}


def journals():
    result = {}
    for name, boot in (("currentBoot", "0"), ("previousBoot", "-1")):
        meta, text = command(["journalctl", "--boot=" + boot, "--no-pager", "--output=json",
                              "--lines=" + str(JOURNAL_LINES), "--case-sensitive=no", "--grep=" + JOURNAL_GREP,
                              "--output-fields=__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,MESSAGE,PRIORITY,_TRANSPORT,_SYSTEMD_UNIT,_PID"],
                             limit=JOURNAL_BYTES, timeout=6)
        unavailable = {"no_journal_files", "no_persistent_journal", "boot_history_unavailable", "permission_denied"}
        history_available = bool(meta["available"] and meta.get("returnCode") == 0
                                 and not unavailable.intersection(meta.get("diagnosticCategories", ())))
        result[name] = {**meta, "commandAvailable": meta["available"],
                        "available": history_available, "historyAvailable": history_available,
                        "bytesInspected": len(text.encode("utf-8")),
                        "completeSystemVisibility": False, **parse_journal(text)}
    return result


def dbus_state(unit_states):
    try:
        import dbus
    except ImportError:
        return {"available": False, "reason": "optional_dbus_python_unavailable"}
    result = {}
    try:
        bus = dbus.bus.BusConnection("unix:path=/run/user/%d/bus" % os.getuid())
        policy = dbus.Interface(bus.get_object("org.kde.Solid.PowerManagement", "/org/kde/Solid/PowerManagement/PolicyAgent", introspect=False),
                                "org.kde.Solid.PowerManagement.PolicyAgent")
        locks = policy.ListInhibitions(timeout=2)
        pid = unit_states["desktopPowerInhibitor"]["properties"].get("MainPID", "0")
        expected = "Nintekkit setup (" + pid + ")"
        own = any(str(pair[0]) == expected and str(pair[1]) == "Keep the existing KDE desktop awake during client setup" for pair in locks)
        result["powerDevil"] = {"available": True, "inhibitionCount": len(locks),
                                "persistentServiceOwnInhibitionPresent": own}
    except Exception as error:
        result["powerDevil"] = {"available": False, "errorClass": type(error).__name__}
    try:
        saver = dbus.Interface(bus.get_object("org.freedesktop.ScreenSaver", "/ScreenSaver", introspect=False), "org.freedesktop.ScreenSaver")
        result["screenSaver"] = {"available": True, "active": bool(saver.GetActive(timeout=2)),
                                 "gameLeasePresence": "API does not list individual inhibitor cookies"}
    except Exception as error:
        result["screenSaver"] = {"available": False, "errorClass": type(error).__name__}
    try:
        manager = dbus.Interface(dbus.SystemBus().get_object("org.freedesktop.login1", "/org/freedesktop/login1", introspect=False), "org.freedesktop.login1.Manager")
        locks = manager.ListInhibitors(timeout=2)
        own = [{"what": str(item[0]), "mode": str(item[3]), "uid": int(item[4]), "pid": int(item[5])}
               for item in locks if str(item[1]) == "Nintekkit" and str(item[2]) == "Setup-and-gameplay"
               and str(item[0]) in ("idle:sleep", "sleep:idle") and str(item[3]) == "block"]
        result["logind"] = {"available": True, "inhibitionCount": len(locks), "nintekkitIdleSleepBlocks": own}
    except Exception as error:
        result["logind"] = {"available": False, "errorClass": type(error).__name__}
    return result


def snapshot():
    boot_id = small_text(Path("/proc/sys/kernel/random/boot_id"))
    uptime = small_text(Path("/proc/uptime"))
    identity = {"hostname": socket.gethostname(), "architecture": platform.machine(),
                "kernel": platform.release(), "uid": os.getuid(),
                "bootId": boot_id if boot_id and UUID.fullmatch(boot_id) else None,
                "uptimeSeconds": float(uptime.split()[0]) if uptime and re.fullmatch(r"[0-9.]+ [0-9.]+", uptime) else None}
    user_fields = {"State", "Sessions", "Linger", "IdleHint", "IdleSinceHintMonotonic"}
    user = properties(["loginctl", "show-user", str(os.getuid()), *["--property=" + key for key in sorted(user_fields)]], user_fields)
    sessions = []
    session_fields = {"Id", "User", "State", "Class", "Type", "Active", "IdleHint", "LockedHint", "Remote"}
    for session in user["properties"].get("Sessions", "").split()[:16]:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", session):
            sessions.append(properties(["loginctl", "show-session", session, *["--property=" + key for key in sorted(session_fields)]], session_fields))
    fields = {"LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID", "Result", "ExecMainCode", "ExecMainStatus", "NRestarts"}
    units = {}
    for name, scope, unit in (("desktopPowerInhibitor", True, "nintekkit-desktop-awake.service"),
                              ("gameScreenInhibitor", True, "nintekkit-game-screen-awake.service"),
                              ("systemPowerInhibitor", False, "nintekkit-awake.service")):
        units[name] = properties(["systemctl", *(["--user"] if scope else []), "show", unit,
                                  *["--property=" + key for key in sorted(fields)]], fields)
    return {"schema": 1, "capturedUtc": datetime.now(timezone.utc).isoformat(),
            "machine": identity, "powerSupplies": power_supplies(), "thermalZones": thermals(),
            "logind": {"user": user, "sessions": sessions}, "units": units,
            "inhibitors": dbus_state(units), "journal": journals(),
            "conclusion": "Evidence only; current power state does not prove why an earlier connection disappeared"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional new absolute JSON filename in an existing private directory; refuses overwrite")
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine() != "aarch64" or os.getuid() == 0:
        parser.error("Run as the non-root Switch desktop user on Linux aarch64; no sudo")
    output = args.output
    if output:
        parent = output.parent
        if not output.is_absolute() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
            parser.error("Output must be absolute, with an existing same-user private parent directory (0700)")
    data = json.dumps(snapshot(), indent=2, sort_keys=True) + "\n"
    if output:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
    else:
        sys.stdout.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
