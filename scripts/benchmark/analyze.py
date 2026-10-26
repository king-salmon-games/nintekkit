#!/usr/bin/env python3
"""Analyze actual RenderTickEvent END timestamps; never synthesize missing frames."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys

from control import read_properties


def intervals_from_csv(path: Path) -> list[tuple[int, int]]:
    intervals = []
    previous = None
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["elapsed_ns", "frame_ns"]:
            raise ValueError("Unexpected frame CSV schema")
        for line, row in enumerate(reader, 2):
            end, duration = int(row["elapsed_ns"]), int(row["frame_ns"])
            if end < 0:
                raise ValueError(f"Negative elapsed time at row {line}")
            if previous is None:
                if duration != 0:
                    raise ValueError("First endpoint must have frame_ns=0 (no preceding observation)")
            else:
                if end <= previous or duration != end - previous:
                    raise ValueError(f"Nonmonotonic, missing, or inconsistent frame at row {line}")
                intervals.append((previous, end))
            previous = end
    if not intervals:
        raise ValueError("Need at least two observed render endpoints")
    return intervals


def annotation_spans(path: Path) -> tuple[list[dict], list[str], list[dict]]:
    if not path.exists():
        return [], ["annotations.csv is missing"], []
    pending: dict[str, dict] = {}
    spans, warnings, points = [], [], []
    previous = -1
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            stamp = int(row["elapsed_ns"])
            kind, edge = row["kind"], row["edge"]
            if stamp < previous:
                raise ValueError("Annotations are not chronological")
            previous = stamp
            if edge == "point":
                points.append(row)
            elif edge == "start":
                if kind in pending:
                    warnings.append(f"Repeated {kind} start; no normal-play verdict")
                pending[kind] = {"start_ns": stamp, "kind": kind, "note": row["note"]}
            elif edge == "end":
                if kind not in pending:
                    warnings.append(f"Unmatched {kind} end; no normal-play verdict")
                else:
                    span = pending.pop(kind)
                    span["end_ns"] = stamp
                    spans.append(span)
            else:
                raise ValueError(f"Unknown annotation edge {edge}")
    warnings.extend(f"Unmatched {kind} start; no normal-play verdict" for kind in pending)
    return spans, warnings, points


def retained_intervals(intervals: list[tuple[int, int]], spans: list[dict]) -> list[tuple[int, int]]:
    exclusions = [span for span in spans if span["kind"] in {"loading", "teleport", "menu", "disconnect"}]
    # Keep intervals whole. Partial frame intervals are not measured frames.
    return [(start, end) for start, end in intervals
            if not any(start < span["end_ns"] and end > span["start_ns"] for span in exclusions)]


def slow_windows(intervals: list[tuple[int, int]], target_fps: float,
                 minimum_seconds: int = 3) -> tuple[list[dict], list[dict]]:
    """Full contiguous 1-second windows; crossing frames contribute proportional overlap."""
    if not intervals:
        return [], []
    origin = intervals[0][0]
    counts: dict[int, float] = {}
    covered: dict[int, int] = {}
    for start, end in intervals:
        first = (start - origin) // 1_000_000_000
        last = (end - origin - 1) // 1_000_000_000
        for index in range(first, last + 1):
            overlap = min(end, origin + (index + 1) * 1_000_000_000) - max(start, origin + index * 1_000_000_000)
            covered[index] = covered.get(index, 0) + overlap
            counts[index] = counts.get(index, 0) + overlap / (end - start)
    windows = [{"start_s": (origin / 1e9) + index, "duration_s": 1,
                "frame_equivalent_fps": counts[index]}
               for index in sorted(counts) if covered[index] == 1_000_000_000]
    runs = []
    current = []
    for window in windows + [{"start_s": math.inf, "frame_equivalent_fps": math.inf}]:
        slow = window["frame_equivalent_fps"] < target_fps - 1e-6
        contiguous = current and abs(window["start_s"] - current[-1]["start_s"] - 1) < 1e-6
        if current and (not slow or not contiguous):
            if len(current) >= minimum_seconds:
                runs.append({"start_s": current[0]["start_s"], "duration_s": len(current),
                             "worst_one_second_fps": min(item["frame_equivalent_fps"] for item in current)})
            current = []
        if slow:
            current.append(window)
    return windows, runs


def metrics(intervals: list[tuple[int, int]], target_fps: float = 30) -> dict | None:
    if not intervals:
        return None
    durations = sorted(end - start for start, end in intervals)
    count = len(durations)
    elapsed = sum(durations) / 1e9
    tail_count = max(1, math.ceil(count * 0.01))
    windows, dips = slow_windows(intervals, target_fps)
    stalls = [{"start_s": start / 1e9, "duration_ms": (end - start) / 1e6}
              for start, end in intervals if end - start >= 100_000_000]
    return {
        "interval_count": count, "measured_seconds": elapsed,
        "elapsed_span_seconds": (intervals[-1][1] - intervals[0][0]) / 1e9,
        "average_fps": count / elapsed,
        "one_percent_low_fps": tail_count * 1e9 / sum(durations[-tail_count:]),
        "p99_frame_ms": durations[math.ceil(count * 0.99) - 1] / 1e6,
        "maximum_frame_ms": durations[-1] / 1e6,
        "frames_over_33_3_ms": sum(duration > 33_300_000 for duration in durations),
        "frames_over_33_333_ms": sum(duration > 1e9 / target_fps for duration in durations),
        "stall_threshold_ms": 100, "stall_count": len(stalls), "stalls": stalls,
        "sustained_dip_definition": "At least 3 consecutive complete 1-second windows below target FPS",
        "one_second_window_method": "Each crossing frame contributes overlap_ns/frame_ns; excluded gaps invalidate windows",
        "complete_one_second_windows": len(windows),
        "worst_one_second_fps": min((window["frame_equivalent_fps"] for window in windows), default=None),
        "sustained_dips": dips,
    }


def analyze(directory: Path) -> dict:
    metadata = read_properties(directory / "metadata.properties")
    intervals = intervals_from_csv(directory / "frames.csv")
    spans, warnings, points = annotation_spans(directory / "annotations.csv")
    raw = metrics(intervals)
    normal = metrics(retained_intervals(intervals, spans))
    valid = True
    if metadata.get("status") != "complete":
        warnings.append("Capture status is " + metadata.get("status", "missing"))
        valid = False
    if metadata.get("overflow") != "false":
        warnings.append("Capture overflow is present or unverified")
        valid = False
    if int(metadata.get("frame_count", "-1")) != len(intervals) + 1:
        warnings.append("Final frame count does not match CSV; capture may be incomplete")
        valid = False
    if metadata.get("measurement") != "forge-render-tick-end-cpu-loop":
        warnings.append("Measurement source is unverified")
        valid = False
    for key in ("actual_server", "capture_disabled"):
        if metadata.get(key, "").lower() != "true":
            warnings.append(key + " was not attested")
            valid = False
    if int(metadata.get("warmup_seconds", "0")) < 120:
        warnings.append("At least 120 seconds of world warmup was not attested")
        valid = False
    if metadata.get("resolution") != "1280x720":
        warnings.append("1280x720 was not attested")
        valid = False
    if metadata.get("phase") not in {"base", "traversal"}:
        warnings.append("Capture does not identify a required base/traversal scenario")
        valid = False
    if not (metadata.get("uncapped", "").lower() == "true" or int(metadata.get("cap_fps", "0")) > 0):
        warnings.append("FPS limiter state was not attested")
        valid = False
    if raw["measured_seconds"] < 600:
        warnings.append("Fewer than 600 seconds between observed render endpoints")
        valid = False
    if normal is None or normal["measured_seconds"] < 600:
        warnings.append("Fewer than 600 measured normal-play seconds after annotations")
        valid = False
    if int(metadata.get("trailing_gap_ns", "0")) > 1_000_000_000:
        warnings.append("Over 1 second after last render endpoint is unmeasured; inspect hang/disconnect")
        valid = False
    if intervals[0][0] > 1_000_000_000:
        warnings.append("Over 1 second before first render endpoint is unmeasured; inspect startup/stall")
        valid = False
    if any("no normal-play verdict" in warning or "missing" in warning for warning in warnings):
        valid = False
    target = None if not valid else (normal["average_fps"] >= 30 and normal["p99_frame_ms"] <= 33.3
                                    and not normal["sustained_dips"])
    return {
        "schema": 1, "metadata": metadata,
        "measurement": "CPU intervals between Forge RenderTickEvent END callbacks; not GPU time or display presentation",
        "metric_definitions": {
            "average_fps": "observed intervals / sum interval seconds",
            "one_percent_low_fps": "1000 / mean duration_ms of slowest ceil(1% of intervals)",
            "p99_frame_ms": "nearest-rank 99th percentile of observed interval durations",
        },
        "raw_all_frames": raw, "normal_play": normal, "annotation_spans": spans,
        "leading_unmeasured_ms": intervals[0][0] / 1e6,
        "trailing_unmeasured_ms": int(metadata.get("trailing_gap_ns", "0")) / 1e6,
        "annotation_points": points, "capture_quality_valid": valid,
        "cpu_loop_target_met": target, "warnings": warnings,
        "final_gpu_display_or_playability_success": "Not established by this CPU timestamp report",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.run_directory)
    data = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(data)
    else:
        print(data, end="")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
