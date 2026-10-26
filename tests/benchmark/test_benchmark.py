"""Synthetic correctness tests. None of these numbers describe Switch performance."""
from __future__ import annotations

import csv
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))
import analyze  # noqa: E402
import control  # noqa: E402
import sample_system  # noqa: E402


def write_capture(directory: Path, durations: list[int], **overrides) -> None:
    metadata = {"status": "complete", "overflow": "false", "frame_count": str(len(durations) + 1),
                "measurement": "forge-render-tick-end-cpu-loop", "actual_server": "True",
                "capture_disabled": "True", "warmup_seconds": "120", "resolution": "1280x720",
                "phase": "base", "uncapped": "True", "trailing_gap_ns": "1000000"}
    metadata.update(overrides)
    (directory / "metadata.properties").write_text("".join(f"{key}={value}\n" for key, value in metadata.items()))
    with (directory / "frames.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["elapsed_ns", "frame_ns"])
        writer.writerow([0, 0])
        stamp = 0
        for duration in durations:
            stamp += duration
            writer.writerow([stamp, duration])
    (directory / "annotations.csv").write_text("elapsed_ns,kind,edge,note\n")


class MetricsTests(unittest.TestCase):
    def test_average_tail_and_nearest_rank_are_distinct(self):
        durations = [10_000_000] * 99 + [100_000_000]
        endpoints, stamp = [], 0
        for duration in durations:
            endpoints.append((stamp, stamp + duration))
            stamp += duration
        values = analyze.metrics(endpoints)
        self.assertAlmostEqual(values["average_fps"], 100 / 1.09)
        self.assertEqual(values["one_percent_low_fps"], 10)
        self.assertEqual(values["p99_frame_ms"], 10)
        self.assertEqual(values["stall_count"], 1)
        self.assertEqual(values["maximum_frame_ms"], 100)

    def test_sub30_sustained_dip_and_exact_30_no_false_dip(self):
        slow = [(i * 50_000_000, (i + 1) * 50_000_000) for i in range(100)]
        values = analyze.metrics(slow)
        self.assertEqual(values["sustained_dips"][0]["duration_s"], 5)
        self.assertEqual(values["sustained_dips"][0]["worst_one_second_fps"], 20)
        exact_30 = [(round(i * 1e9 / 30), round((i + 1) * 1e9 / 30)) for i in range(300)]
        self.assertEqual(analyze.metrics(exact_30)["sustained_dips"], [])

    def test_gaps_never_merge_into_sustained_dips(self):
        intervals = [(i * 50_000_000, (i + 1) * 50_000_000) for i in range(40)]
        intervals += [(10_000_000_000 + i * 50_000_000, 10_000_000_000 + (i + 1) * 50_000_000)
                      for i in range(40)]
        self.assertEqual(analyze.metrics(intervals)["sustained_dips"], [])

    def test_annotations_preserve_raw_stalls_and_remove_overlapping_frames_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            write_capture(path, [10_000_000] * 100 + [1_000_000_000] + [10_000_000] * 100)
            (path / "annotations.csv").write_text("elapsed_ns,kind,edge,note\n1000000000,teleport,start,portal\n2000000000,teleport,end,arrived\n")
            result = analyze.analyze(path)
            self.assertEqual(result["raw_all_frames"]["stall_count"], 1)
            self.assertEqual(result["normal_play"]["stall_count"], 0)
            self.assertAlmostEqual(result["normal_play"]["measured_seconds"], 2)
            self.assertFalse(result["capture_quality_valid"])
            self.assertIsNone(result["cpu_loop_target_met"])

    def test_600_second_valid_capture_and_missing_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            write_capture(path, [20_000_000] * 30_000)
            report = analyze.analyze(path)
            self.assertTrue(report["capture_quality_valid"], report["warnings"])
            self.assertTrue(report["cpu_loop_target_met"])
            write_capture(path, [20_000_000] * 30_000, status="recording")
            report = analyze.analyze(path)
            self.assertFalse(report["capture_quality_valid"])
            self.assertIsNone(report["cpu_loop_target_met"])

    def test_corruption_and_truncation_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            write_capture(path, [20_000_000] * 10, frame_count="99")
            self.assertFalse(analyze.analyze(path)["capture_quality_valid"])
            (path / "frames.csv").write_text("elapsed_ns,frame_ns\n0,0\n200,100\n")
            with self.assertRaisesRegex(ValueError, "inconsistent"):
                analyze.intervals_from_csv(path / "frames.csv")

    def test_unmatched_annotations_never_claim_normal_play_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            write_capture(path, [20_000_000] * 30_000)
            (path / "annotations.csv").write_text("elapsed_ns,kind,edge,note\n1000000000,teleport,start,portal\n")
            report = analyze.analyze(path)
            self.assertFalse(report["capture_quality_valid"])
            self.assertIsNone(report["cpu_loop_target_met"])

    def test_rare_stalls_cannot_hide_an_average_below_30(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            write_capture(path, ([10_000_000] * 100 + [2_500_000_000]) * 172)
            report = analyze.analyze(path)
            self.assertTrue(report["capture_quality_valid"], report["warnings"])
            self.assertEqual(report["normal_play"]["p99_frame_ms"], 10)
            self.assertLess(report["normal_play"]["average_fps"], 30)
            self.assertEqual(report["normal_play"]["sustained_dips"], [])
            self.assertFalse(report["cpu_loop_target_met"])

    def test_capture_guard_and_java_properties(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "123").mkdir()
            (path / "123" / "comm").write_text("x11vnc\n")
            self.assertEqual(control.capture_processes(path), [{"pid": 123, "name": "x11vnc"}])
            properties = path / "test.properties"
            properties.write_text("note=" + control.property_escape("colon: equals= slash\\ café") + "\n", encoding="ascii")
            self.assertEqual(control.read_properties(properties)["note"], "colon: equals= slash\\ café")

    def test_capture_guard_detects_kde_sharing_without_flagging_desktop(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            capture_names = ["krfb", "krdpserver", "krdp-server", "krfb-virtualmonitor"[:15]]
            desktop_names = ["plasmashell", "kwin_x11", "kded5", "pipewire"]
            for pid, name in enumerate(capture_names + desktop_names, 200):
                process = path / str(pid)
                process.mkdir()
                (process / "comm").write_text(name + "\n")
            found = control.capture_processes(path)
            self.assertEqual({entry["name"] for entry in found}, set(capture_names))
            self.assertEqual({entry["pid"] for entry in found}, set(range(200, 204)))

    def test_cpu_times_do_not_double_count_guest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "stat").write_text("cpu 100 0 20 400 10 0 0 0 15 0\ncpu0 50 0 10 200 5 0 0 0 7 0\n")
            current = sample_system.cpu_times(path)
            self.assertEqual(current["cpu"], (530, 410))
            self.assertEqual(sample_system.cpu_percent({"cpu": (430, 360)}, current)["cpu"], 50)


@unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "Java compiler/runtime required")
class JavaRecorderTests(unittest.TestCase):
    def test_queue_order_overflow_and_real_async_csv_protocol(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            classes = path / "classes"
            classes.mkdir()
            sources = ROOT / "patches" / "frameprobe" / "src" / "com" / "kingsalmon" / "nintekkit" / "frameprobe"
            checks = ROOT / "tests" / "benchmark"
            version = subprocess.check_output(["javac", "-version"], stderr=subprocess.STDOUT, text=True)
            target = ["-source", "8", "-target", "8"] if "javac 1.8." in version else ["--release", "8"]
            subprocess.run(["javac", *target, "-d", str(classes), str(sources / "FrameRing.java"),
                            str(sources / "Recorder.java"), str(checks / "FrameRingCheck.java"),
                            str(checks / "RecorderCheck.java")], check=True, capture_output=True)
            result = subprocess.run(["java", "-cp", str(classes), "com.kingsalmon.nintekkit.frameprobe.FrameRingCheck"],
                                    check=True, capture_output=True, text=True, timeout=15)
            self.assertIn("checks passed", result.stdout)
            directory = path / "capture"
            process = subprocess.Popen(["java", "-cp", str(classes),
                                        "com.kingsalmon.nintekkit.frameprobe.RecorderCheck", str(directory)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not (directory / "status.properties").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                status = control.request(directory, {"action": "start", "run_id": "synthetic-test-only", "seconds": "0.8"})
                self.assertEqual(status["state"], "recording")
                control.request(directory, {"action": "mark", "kind": "note", "edge": "point", "note": 'quotes " and café'})
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    metadata_file = directory / "synthetic-test-only" / "metadata.properties"
                    if metadata_file.exists() and control.read_properties(metadata_file).get("status") == "complete":
                        break
                    time.sleep(0.05)
                metadata = control.read_properties(metadata_file)
                self.assertEqual(metadata["status"], "complete")
                frames = analyze.intervals_from_csv(directory / "synthetic-test-only" / "frames.csv")
                self.assertGreater(len(frames), 10)
                spans, warnings, points = analyze.annotation_spans(directory / "synthetic-test-only" / "annotations.csv")
                self.assertEqual(points[0]["note"], 'quotes " and café')
                self.assertEqual(int(metadata["frame_count"]), len(frames) + 1)
            finally:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
