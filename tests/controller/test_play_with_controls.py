"""Lifecycle tests use fake processes and proc metadata; no launches or input."""
import argparse
from contextlib import redirect_stderr
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/controller"))
import play_with_controls as play


GAME = play.JavaProcess(123, "/runtime/bin/java", "100", "/isolated/.minecraft")


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Process:
    def __init__(self, pid, code=None, stuck=False):
        self.pid, self.code, self.stuck = pid, code, stuck
        self.terminated, self.waited, self.killed = 0, [], 0
    def poll(self): return self.code
    def terminate(self):
        self.terminated += 1
        if not self.stuck: self.code = 0
    def wait(self, timeout):
        self.waited.append(timeout)
        if self.stuck: raise subprocess.TimeoutExpired("fake-mapper", timeout)
        return self.code
    def kill(self):
        self.killed += 1
        raise AssertionError("No process may be killed by lifecycle cleanup")


class Sequence:
    def __init__(self, values): self.values, self.index = values, 0
    def __call__(self):
        result = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return result


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.dispatcher, self.mapper = Process(200, 0), Process(201)
        self.commands = []
    def spawn(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return self.dispatcher if len(self.commands) == 1 else self.mapper
    def run_lifecycle(self, discover, **kwargs):
        return play.supervise(["prismlauncher", "--dir", "/isolated/prism", "--launch", "Nintekkit-1.2.4"],
                              ["python3", "mapper.py"], discover, spawn=self.spawn,
                              clock=self.clock, sleep=self.clock.sleep, report=lambda _: None, **kwargs)
    def test_dispatch_exit_zero_does_not_end_wait_for_real_java(self):
        values = Sequence([[], [], [GAME], [GAME], [GAME], []])
        self.assertEqual(self.run_lifecycle(values), 0)
        self.assertEqual(self.clock.now, 2)
        self.assertEqual(self.commands[1][0], ["python3", "mapper.py", "--run", "--pid", "123"])
        self.assertEqual(self.commands[0][1], {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})
        self.assertNotIn("--server", self.commands[0][0])
        self.assertEqual((self.mapper.terminated, self.mapper.waited), (1, [10]))
        self.assertEqual(self.dispatcher.terminated, 0)
    def test_preexisting_or_ambiguous_existing_clients_never_dispatch(self):
        for clients in ([GAME], [GAME, play.JavaProcess(124, GAME.executable, "101", GAME.cwd)]):
            with self.subTest(clients=clients), self.assertRaisesRegex(play.LifecycleError, "pre-existing"):
                self.run_lifecycle(lambda: clients)
        self.assertEqual(self.commands, [])
    def test_ambiguous_new_clients_never_start_mapper(self):
        other = play.JavaProcess(124, GAME.executable, "101", GAME.cwd)
        with self.assertRaisesRegex(play.LifecycleError, "Multiple new"):
            self.run_lifecycle(Sequence([[], [GAME, other]]))
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.dispatcher.terminated, 0)
    def test_timeout_is_bounded_even_when_prism_dispatch_succeeded(self):
        with self.assertRaisesRegex(play.LifecycleError, "within 3"):
            self.run_lifecycle(lambda: [], startup_timeout=3)
        self.assertEqual(self.clock.now, 3)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.dispatcher.terminated, 0)
    def test_nonzero_dispatch_failure_is_reported_without_starting_mapper(self):
        self.dispatcher.code = 7
        with self.assertRaisesRegex(play.LifecycleError, "exit code 7"):
            self.run_lifecycle(lambda: [])
        self.assertEqual(len(self.commands), 1)
    def test_stop_while_waiting_does_not_start_mapper_or_kill_prism(self):
        self.assertEqual(self.run_lifecycle(lambda: [], stop_requested=lambda: self.clock.now >= 2), 0)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.dispatcher.terminated, 0)
    def test_stop_with_game_running_only_terminates_mapper(self):
        values = Sequence([[], [GAME], [GAME], [GAME]])
        self.assertEqual(self.run_lifecycle(values, stop_requested=lambda: self.clock.now >= 1), 0)
        self.assertEqual(self.mapper.terminated, 1)
        self.assertEqual(self.dispatcher.terminated, 0)
        self.assertEqual(self.mapper.killed, 0)
    def test_reused_pid_starttime_cannot_keep_old_controller_session_alive(self):
        reused = play.JavaProcess(GAME.pid, GAME.executable, "999", GAME.cwd)
        self.assertEqual(self.run_lifecycle(Sequence([[], [GAME], [GAME], [reused]])), 0)
        self.assertEqual(self.mapper.terminated, 1)
    def test_java_exit_before_mapper_start_is_not_an_attach_attempt(self):
        with self.assertRaisesRegex(play.LifecycleError, "before mapper startup"):
            self.run_lifecycle(Sequence([[], [GAME], []]))
        self.assertEqual(len(self.commands), 1)
    def test_mapper_early_exit_is_visible_and_java_is_left_running(self):
        self.mapper.code = 2
        with self.assertRaisesRegex(play.LifecycleError, "while Java remained running"):
            self.run_lifecycle(Sequence([[], [GAME], [GAME], [GAME]]))
        self.assertEqual(self.dispatcher.terminated, 0)
        self.assertEqual(self.mapper.terminated, 0)
    def test_java_exit_during_mapper_poll_is_normal_lifecycle_cleanup(self):
        self.mapper.code = 0
        self.assertEqual(self.run_lifecycle(Sequence([[], [GAME], [GAME], [GAME], []])), 0)
        self.assertEqual(self.dispatcher.terminated, 0)
    def test_cleanup_timeout_never_escalates_to_sigkill(self):
        self.mapper.stuck = True
        with self.assertRaisesRegex(play.LifecycleError, "did not finish cleanup"):
            self.run_lifecycle(Sequence([[], [GAME], [GAME], []]), shutdown_timeout=2)
        self.assertEqual(self.mapper.terminated, 1)
        self.assertEqual(self.mapper.waited, [2])
        self.assertEqual(self.mapper.killed, 0)


def stat_text(start):
    fields = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 5
    return "123 (java (pack client)) " + " ".join(fields)


class DiscoveryTests(unittest.TestCase):
    def test_later_launch_preserves_rewritten_existing_server_list_without_reading(self):
        with tempfile.TemporaryDirectory() as temporary:
            instance = Path(temporary)
            game = instance / ".minecraft"
            game.mkdir()
            path = game / "servers.dat"
            contents = b"legitimate Minecraft-rewritten NBT with private content"
            path.write_bytes(contents)
            messages = []
            def configure(*args): raise AssertionError("Existing list must not be compared or replaced")
            with patch.object(Path, "read_bytes", side_effect=AssertionError("Existing list must not be read")):
                self.assertEqual(play.prepare_server_bookmark(instance, "example.test:25565", configure, messages.append),
                                 {"status": "preserved_existing"})
            self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(messages, ["Existing server list preserved; continuing Prism direct join"])
            self.assertNotIn("example.test", " ".join(messages))
    def test_absent_bookmark_is_configured_and_identity_refusal_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            instance = Path(temporary)
            (instance / ".minecraft").mkdir()
            calls = []
            def configure(path, server):
                calls.append((path, server))
                return {"status": "created"}
            self.assertEqual(play.prepare_server_bookmark(instance, "example.test", configure), {"status": "created"})
            self.assertEqual(calls, [(instance, "example.test")])
            def refuse(*args): raise ValueError("unsafe identity")
            with self.assertRaisesRegex(ValueError, "unsafe identity"):
                play.prepare_server_bookmark(instance, "example.test", refuse)
    def test_optional_server_is_one_structured_argument_and_default_omits_it(self):
        default = play.build_prism_command(Path("/isolated/prism"))
        self.assertNotIn("--server", default)
        self.assertEqual(play.build_prism_command(Path("/isolated/prism"), "example.test:25565"),
                         default + ["--server", "example.test:25565"])
        self.assertEqual(play.validate_server("192.0.2.17"), "192.0.2.17")
        for value in ("--bad", "-host", "host name", "host\n", "host;other", "host:0", "host:65536", "a..b", "host-"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                play.build_prism_command(Path("/isolated/prism"), value)

    def test_invalid_server_error_does_not_echo_private_input(self):
        private_input = "private.example:bad"
        output = io.StringIO()
        with patch.object(sys, "argv", ["helper", "--server", private_input]), redirect_stderr(output):
            with self.assertRaises(SystemExit) as result:
                play.main()
        self.assertEqual(result.exception.code, 2)
        self.assertNotIn(private_input, output.getvalue())
        self.assertIn("Server must be", output.getvalue())

    def test_stat_starttime_handles_parentheses_and_spaces(self):
        self.assertEqual(play.parse_starttime(stat_text(123456)), "123456")
        with self.assertRaises(ValueError): play.parse_starttime("123 (java) malformed")
    def test_only_java_with_exact_canonical_game_cwd_matches_and_no_tokens_are_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            minecraft, other, java, python = root / "game/.minecraft", root / "other", root / "bin/java", root / "bin/python3"
            minecraft.mkdir(parents=True)
            other.mkdir()
            java.parent.mkdir()
            java.touch()
            python.touch()
            proc = root / "proc"
            proc.mkdir()
            mapping = {}
            for pid, executable, cwd in ((123, java, minecraft), (124, java, other), (125, python, minecraft)):
                process = proc / str(pid)
                process.mkdir()
                (process / "stat").write_text(stat_text(pid * 10))
                mapping[process / "exe"] = executable
                mapping[process / "cwd"] = cwd
            original_resolve, original_read = Path.resolve, Path.read_text
            reads = []
            def resolve(path, strict=False):
                return mapping[path] if path in mapping else original_resolve(path, strict=strict)
            def read(path, *args, **kwargs):
                reads.append(path.name)
                if path.name in {"cmdline", "environ"}: raise AssertionError("Credentials must not be read")
                return original_read(path, *args, **kwargs)
            uid = (proc / "123").stat().st_uid
            with patch.object(Path, "resolve", resolve), patch.object(Path, "read_text", read):
                found = play.discover_clients(minecraft, proc=proc, uid=uid)
            self.assertEqual(found, [play.JavaProcess(123, str(java), "1230", str(minecraft))])
            self.assertEqual(reads, ["stat"])
            with patch.object(Path, "resolve", resolve):
                self.assertEqual(play.discover_clients(minecraft, proc=proc, uid=uid + 1), [])


if __name__ == "__main__":
    unittest.main()
