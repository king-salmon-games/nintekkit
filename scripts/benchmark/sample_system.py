#!/usr/bin/env python3
"""Read-only Linux CPU/RAM/swap/temperature/GPU/power telemetry; one JSONL row/s."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from itertools import islice
import os
from pathlib import Path
import re
import socket
import time


MAX_POWER_SUPPLIES = 32
POWER_NUMBER_UNITS = {
    "present": "boolean", "online": "boolean", "capacity": "percent",
    "voltage_now": "microvolts", "current_now": "microamps", "power_now": "microwatts",
    "charge_now": "microamp_hours", "charge_full": "microamp_hours",
    "charge_full_design": "microamp_hours", "energy_now": "microwatt_hours",
    "energy_full": "microwatt_hours", "energy_full_design": "microwatt_hours",
    "temp": "tenths_degree_C",
}
POWER_ENUM_FIELDS = {
    "type": {"Unknown", "Battery", "Mains", "USB", "USB_DCP", "USB_CDP", "USB_ACA",
             "USB_TYPE_C", "USB_PD", "USB_PD_DRP", "Wireless", "UPS"},
    "status": {"Unknown", "Charging", "Discharging", "Not charging", "Full"},
    "capacity_level": {"Unknown", "Critical", "Low", "Normal", "High", "Full"},
    "health": {"Unknown", "Good", "Overheat", "Dead", "Over voltage", "Unspecified failure",
               "Cold", "Watchdog timer expire", "Safety timer expire", "No battery", "Warm", "Cool"},
    "scope": {"Unknown", "System", "Device"},
}
BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def bounded_text(path: Path, limit: int = 128) -> str | None:
    try:
        with path.open("rb") as stream:
            value = stream.read(limit + 1)
        return value.decode("ascii").strip() if len(value) <= limit else None
    except (OSError, UnicodeError):
        return None


def power_supplies(root: Path) -> dict:
    """No identifying serial/model/driver names; raw native units and signs survive."""
    try:
        with os.scandir(root) as entries:
            paths = sorted((Path(entry.path) for entry in islice(entries, MAX_POWER_SUPPLIES + 1)),
                           key=lambda path: path.name)
    except OSError:
        return {"available": False, "supplies": [], "truncated": False}
    supplies = []
    for index, path in enumerate(paths[:MAX_POWER_SUPPLIES]):
        fields = {}
        for field in POWER_NUMBER_UNITS:
            value = bounded_text(path / field)
            if value is not None and re.fullmatch(r"-?[0-9]{1,18}", value):
                fields[field] = int(value)
        for field, allowed in POWER_ENUM_FIELDS.items():
            value = bounded_text(path / field)
            if value in allowed:
                fields[field] = value
        # Wireless controller supply names can embed a MAC; never emit them.
        name = path.name if re.fullmatch(r"(?:battery|usb|ac|mains|BAT[0-9]+|AC[0-9]*|ACAD|ADP[0-9]+)", path.name) else "other"
        supplies.append({"index": index, "supply": name, "fields": fields})
    return {"available": True, "supplies": supplies, "truncated": len(paths) > MAX_POWER_SUPPLIES}


def boot_state(proc: Path) -> dict:
    boot_id = bounded_text(proc / "sys/kernel/random/boot_id")
    uptime = bounded_text(proc / "uptime")
    return {"boot_id": boot_id if boot_id and BOOT_ID.fullmatch(boot_id) else None,
            "uptime_seconds": float(uptime.split()[0]) if uptime and re.fullmatch(r"[0-9]+(?:\.[0-9]+)? [0-9]+(?:\.[0-9]+)?", uptime) else None}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return None


def cpu_times(proc: Path = Path("/proc")) -> dict[str, tuple[int, int]]:
    result = {}
    for line in (proc / "stat").read_text().splitlines():
        fields = line.split()
        if fields and fields[0].startswith("cpu"):
            values = list(map(int, fields[1:9])) # exclude guest fields already included in user/nice
            result[fields[0]] = (sum(values), values[3] + (values[4] if len(values) > 4 else 0))
    return result


def cpu_percent(previous: dict, current: dict) -> dict[str, float | None]:
    result = {}
    for name, (total, idle) in current.items():
        before_total, before_idle = previous.get(name, (total, idle))
        delta = total - before_total
        result[name] = 100 * (delta - idle + before_idle) / delta if delta > 0 else None
    return result


def snapshot(pid: int | None, proc: Path = Path("/proc"), system: Path = Path("/sys")) -> dict:
    memory = {}
    for line in (proc / "meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Dirty", "Writeback"}:
            memory[name + "_kib"] = int(value.split()[0])
    vm = {}
    for line in (proc / "vmstat").read_text().splitlines():
        key, value = line.split()
        if key in {"pswpin", "pswpout", "pgmajfault", "oom_kill"}:
            vm[key] = int(value)
    thermal = {}
    for zone in (system / "class" / "thermal").glob("thermal_zone*"):
        value = read_text(zone / "temp")
        if value is not None:
            try:
                thermal[zone.name + ":" + (read_text(zone / "type") or "unknown")] = int(value) / 1000
            except ValueError:
                pass
    gpu = {}
    # Known Tegra GPU load nodes use a 0..1000 scale; preserve the path/raw value too.
    for relative in ("devices/gpu.0/load", "devices/57000000.gpu/load"):
        value = read_text(system / relative)
        if value is not None:
            try:
                raw = int(value)
                gpu[relative] = {"raw": raw, "percent": raw / 10 if 0 <= raw <= 1000 else None}
            except ValueError:
                gpu[relative] = {"raw": value, "percent": None}
    frequencies = {}
    patterns = ("class/devfreq/*/cur_freq", "devices/system/cpu/cpu*/cpufreq/scaling_cur_freq")
    for pattern in patterns:
        for path in system.glob(pattern):
            frequencies[str(path.relative_to(system))] = read_text(path)
    process = None
    if pid is not None:
        process = {"pid": pid, "alive": (proc / str(pid)).is_dir()}
        value = read_text(proc / str(pid) / "status")
        if value:
            for line in value.splitlines():
                key, _, raw = line.partition(":")
                if key in {"VmRSS", "VmSwap", "VmSize", "Threads"}:
                    process[key] = raw.strip()
        stat = read_text(proc / str(pid) / "stat")
        if stat:
            fields = stat[stat.rfind(")") + 2:].split()
            process["cpu_ticks"] = int(fields[11]) + int(fields[12])
    return {"utc": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns(),
            "memory": memory, "vm_counters": vm, "thermal_c": thermal,
            "gpu_load": gpu, "frequencies_raw": frequencies, "process": process,
            "boot": boot_state(proc), "power_supplies": power_supplies(system / "class/power_supply")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--seconds", type=float, default=620)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not Path("/proc/stat").exists():
        parser.error("Run on the Linux Switch, not the Windows workstation")
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    previous = cpu_times()
    previous_process_ticks = None
    previous_time = time.monotonic()
    deadline = previous_time + args.seconds
    next_sample = previous_time + 1
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"schema": 1, "hostname": socket.gethostname(), "pid": args.pid,
                                 "kernel": os.uname().release, "machine": os.uname().machine,
                                 "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
                                 "missing_sysfs_values": "unavailable; not zero or inferred",
                                 "power_supply_number_units": POWER_NUMBER_UNITS,
                                 "power_supply_values": "Raw native values/signs; Unknown stays Unknown; missing fields unavailable",
                                 "maximum_power_supplies": MAX_POWER_SUPPLIES}) + "\n")
        while time.monotonic() < deadline:
            time.sleep(max(0, next_sample - time.monotonic()))
            now = time.monotonic()
            current = cpu_times()
            row = snapshot(args.pid)
            row["cpu_busy_percent"] = cpu_percent(previous, current)
            process = row["process"]
            if process is not None and "cpu_ticks" in process:
                ticks = process["cpu_ticks"]
                process["cpu_percent_one_core_100"] = ((ticks - previous_process_ticks) / os.sysconf("SC_CLK_TCK")
                                                       / (now - previous_time) * 100
                                                       if previous_process_ticks is not None else None)
                previous_process_ticks = ticks
            stream.write(json.dumps(row, allow_nan=False) + "\n")
            stream.flush()
            previous, previous_time = current, now
            next_sample = now + 1


if __name__ == "__main__":
    main()
