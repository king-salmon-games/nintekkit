"""Fake bus/process tests; never contact a desktop or change locking state."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/controller"))
import screen_awake as screen
import test_play_with_controls as lifecycle


class BusError(Exception): pass


class Bus:
    def __init__(self): self.owner = ":1.42"
    def get_name_owner(self, name):
        assert name == "org.freedesktop.ScreenSaver"
        if self.owner is None: raise BusError("service unavailable")
        return self.owner


class Interface:
    def __init__(self, owner, calls): self.owner, self.calls = owner, calls
    def Inhibit(self, application, reason, timeout):
        self.calls.append((self.owner, "Inhibit", application, reason, timeout))
        return 17
    def UnInhibit(self, cookie, timeout):
        self.calls.append((self.owner, "UnInhibit", cookie, timeout))


class InhibitorTests(unittest.TestCase):
    def setUp(self):
        self.bus, self.calls, self.messages = Bus(), [], []
        self.inhibitor = screen.ScreenInhibitor(self.bus, lambda owner: Interface(owner, self.calls),
                                                123, errors=(BusError,), report=self.messages.append)
    def test_only_inhibit_and_uninhibit_are_used_and_same_owner_is_not_duplicated(self):
        self.assertTrue(self.inhibitor.refresh())
        self.assertTrue(self.inhibitor.refresh())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][:3], (":1.42", "Inhibit", "Nintekkit Java 123"))
        self.inhibitor.close()
        self.inhibitor.close()
        self.assertEqual(self.calls[1], (":1.42", "UnInhibit", 17, 3))
        self.assertEqual(len(self.calls), 2)
    def test_restart_releases_old_cookie_against_old_unique_owner_then_reacquires(self):
        self.inhibitor.refresh()
        self.bus.owner = ":1.43"
        self.inhibitor.refresh()
        self.assertEqual([(call[0], call[1]) for call in self.calls],
                         [(":1.42", "Inhibit"), (":1.42", "UnInhibit"), (":1.43", "Inhibit")])
        self.inhibitor.close()
        self.assertEqual(self.calls[-1], (":1.43", "UnInhibit", 17, 3))
    def test_missing_service_retries_without_reusing_stale_cookie_or_repeated_warning(self):
        self.inhibitor.refresh()
        self.bus.owner = None
        self.assertFalse(self.inhibitor.refresh())
        self.assertFalse(self.inhibitor.refresh())
        self.assertIsNone(self.inhibitor.cookie)
        self.assertEqual(sum("unavailable" in message for message in self.messages), 1)
        self.bus.owner = ":1.44"
        self.assertTrue(self.inhibitor.refresh())
        self.assertEqual(self.calls[-1][:2], (":1.44", "Inhibit"))
    def test_stop_process_exit_reused_pid_and_error_always_release(self):
        original = screen.JavaIdentity(123, 1000, "/runtime/java", "100")
        changed = screen.JavaIdentity(123, 1000, "/runtime/java", "101")
        for sequence in ([original, None], [original, changed]):
            with self.subTest(sequence=sequence):
                self.calls.clear()
                values = lifecycle.Sequence(sequence)
                screen.watch_java(original, self.inhibitor, read=lambda _: values(), wait=lambda _: None, report=lambda _: None)
                self.assertEqual([call[1] for call in self.calls], ["Inhibit", "UnInhibit"])
        self.calls.clear()
        screen.watch_java(original, self.inhibitor, read=lambda _: original,
                          stop_requested=lambda: True, wait=lambda _: None, report=lambda _: None)
        self.assertEqual(self.calls, [])
        with self.assertRaises(RuntimeError):
            screen.watch_java(original, self.inhibitor, read=lambda _: original,
                              wait=lambda _: (_ for _ in ()).throw(RuntimeError("test")), report=lambda _: None)
        self.assertEqual([call[1] for call in self.calls], ["Inhibit", "UnInhibit"])
    def test_java_reader_requires_same_user_java_and_starttime_without_reading_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process, java = root / "proc/123", root / "runtime/java"
            process.mkdir(parents=True)
            java.parent.mkdir()
            java.touch()
            fields = ["S"] + ["0"] * 18 + ["45123"]
            (process / "stat").write_text("123 (java (pack)) " + " ".join(fields))
            uid = process.stat().st_uid
            original_resolve, original_read = Path.resolve, Path.read_text
            reads = []
            def resolve(path, strict=False):
                return java if path == process / "exe" else original_resolve(path, strict=strict)
            def read(path, *args, **kwargs):
                self.assertNotIn(path.name, {"cmdline", "environ"})
                reads.append(path.name)
                return original_read(path, *args, **kwargs)
            with patch.object(Path, "resolve", resolve), patch.object(Path, "read_text", read):
                self.assertEqual(screen.read_java(123, proc=root / "proc", uid=uid), screen.JavaIdentity(123, uid, str(java), "45123"))
                self.assertIsNone(screen.read_java(123, proc=root / "proc", uid=uid + 1))
            self.assertEqual(reads, ["stat"])


class SupervisorAwakeTests(unittest.TestCase):
    def setUp(self):
        self.harness = lifecycle.LifecycleTests()
        self.harness.setUp()
    def test_screen_lease_precedes_mapper_and_is_released_on_game_exit(self):
        calls = []
        class Lease:
            def refresh(self): calls.append("refresh")
            def close(self): calls.append("close")
        lease = Lease()
        game = lifecycle.GAME
        self.assertEqual(self.harness.run_lifecycle(lifecycle.Sequence([[], [game], [game], [game], [game], []]), awake_factory=lambda pid: lease), 0)
        self.assertEqual(calls, ["refresh", "refresh", "close"])
    def test_mapper_cleanup_timeout_still_releases_screen_lease(self):
        calls = []
        class Lease:
            def refresh(self): calls.append("refresh")
            def close(self): calls.append("close")
        self.harness.mapper.stuck = True
        game = lifecycle.GAME
        with self.assertRaisesRegex(Exception, "did not finish cleanup"):
            self.harness.run_lifecycle(lifecycle.Sequence([[], [game], [game], [game], []]), awake_factory=lambda pid: Lease())
        self.assertEqual(calls, ["refresh", "close"])
    def test_stop_during_dbus_acquisition_never_starts_mapper(self):
        calls, stop = [], [False]
        class Lease:
            def refresh(self):
                calls.append("refresh")
                stop[0] = True
            def close(self): calls.append("close")
        game = lifecycle.GAME
        self.assertEqual(self.harness.run_lifecycle(lifecycle.Sequence([[], [game], [game]]),
                         awake_factory=lambda pid: Lease(), stop_requested=lambda: stop[0]), 0)
        self.assertEqual(calls, ["refresh", "close"])
        self.assertEqual(len(self.harness.commands), 1)
    def test_java_exit_during_dbus_acquisition_never_starts_mapper(self):
        calls = []
        class Lease:
            def refresh(self): calls.append("refresh")
            def close(self): calls.append("close")
        game = lifecycle.GAME
        with self.assertRaisesRegex(Exception, "while acquiring"):
            self.harness.run_lifecycle(lifecycle.Sequence([[], [game], [game], []]), awake_factory=lambda pid: Lease())
        self.assertEqual(calls, ["refresh", "close"])
        self.assertEqual(len(self.harness.commands), 1)


class UInt32(int): pass


class PolicyBus:
    def __init__(self): self.owners = {screen.SERVICE: ":1.42", screen.POLICY_SERVICE: ":1.50"}
    def get_name_owner(self, service):
        value = self.owners[service]
        if value is None: raise BusError("service unavailable")
        return value


class PolicyInterface:
    def __init__(self, owner, calls):
        self.owner, self.calls, self.entries = owner, calls, []
        self.active, self.fail = True, None
        self.next_cookie = 31
    def AddInhibition(self, policy, application, reason, timeout):
        assert type(policy) is UInt32 and policy == 4
        self.next_cookie += 1
        self.calls.append((self.owner, "AddInhibition", policy, application, reason, self.next_cookie, timeout))
        if self.fail == "add": raise BusError("test")
        return self.next_cookie
    def ListInhibitions(self, timeout):
        self.calls.append((self.owner, "ListInhibitions", timeout))
        if self.fail == "list": raise BusError("test")
        return self.entries
    def HasInhibition(self, policy, timeout):
        assert type(policy) is UInt32 and policy == 4
        self.calls.append((self.owner, "HasInhibition", policy, timeout))
        return self.active
    def ReleaseInhibition(self, cookie, timeout):
        self.calls.append((self.owner, "ReleaseInhibition", cookie, timeout))


class DisplayInhibitorTests(unittest.TestCase):
    def setUp(self):
        self.bus, self.calls, self.messages = PolicyBus(), [], []
        self.clock = lifecycle.Clock()
        self.interfaces = {}
        def interface(owner):
            if owner not in self.interfaces:
                self.interfaces[owner] = PolicyInterface(owner, self.calls)
            return self.interfaces[owner]
        self.display = screen.DisplayInhibitor(self.bus, interface, 123, helper_pid=456,
            clock=self.clock, uint32=UInt32, errors=(BusError,), report=self.messages.append)
    def own_entry(self, owner=":1.50"):
        self.interfaces[owner].entries = [(self.display.application, self.display.reason)]
    def test_pending_delay_is_nonblocking_and_requires_own_entry_and_mask4(self):
        self.assertFalse(self.display.refresh())
        self.assertEqual(self.clock.now, 0)
        self.clock.now = 4.99
        self.assertFalse(self.display.refresh())
        self.assertEqual([call[1] for call in self.calls], ["AddInhibition"])
        self.assertIn("Java 123 helper 456", self.display.application)
        self.clock.now = 5
        policy = self.interfaces[":1.50"]
        policy.entries = [("Nintekkit setup (1)", "persistent policy 1"), ("other app", self.display.reason)]
        self.assertFalse(self.display.refresh())
        self.own_entry()
        policy.active = False
        self.assertFalse(self.display.refresh())
        policy.active = True
        self.assertTrue(self.display.refresh())
        self.assertTrue(self.display.verified)
        self.assertEqual(sum("VERIFIED" in message for message in self.messages), 1)
        self.display.refresh()
        self.assertEqual(sum("VERIFIED" in message for message in self.messages), 1)
    def test_power_owner_restart_releases_only_its_old_cookie_and_reacquires(self):
        self.display.refresh()
        old_cookie = self.display.cookie
        self.bus.owners[screen.POLICY_SERVICE] = ":1.51"
        self.assertFalse(self.display.refresh())
        methods = [(call[0], call[1]) for call in self.calls]
        self.assertEqual(methods, [(":1.50", "AddInhibition"), (":1.50", "ReleaseInhibition"), (":1.51", "AddInhibition")])
        self.assertEqual(self.calls[1][2], old_cookie)
        self.display.close()
        self.assertEqual(self.calls[-1][:3], (":1.51", "ReleaseInhibition", self.interfaces[":1.51"].next_cookie))
    def test_verified_lease_loss_or_pending_timeout_releases_then_reacquires(self):
        self.display.refresh()
        self.clock.now = 5
        self.own_entry()
        self.assertTrue(self.display.refresh())
        self.interfaces[":1.50"].entries = []
        self.assertFalse(self.display.refresh())
        self.assertIsNone(self.display.cookie)
        self.assertFalse(self.display.refresh())
        self.clock.now = 17
        self.assertFalse(self.display.refresh())
        self.assertIsNone(self.display.cookie)
        self.assertEqual([call[1] for call in self.calls].count("ReleaseInhibition"), 2)
    def test_partial_bus_failure_releases_known_cookie_and_can_retry(self):
        self.display.refresh()
        cookie = self.display.cookie
        self.clock.now = 5
        self.interfaces[":1.50"].fail = "list"
        self.assertFalse(self.display.refresh())
        self.assertIsNone(self.display.cookie)
        self.assertEqual(self.calls[-1][:3], (":1.50", "ReleaseInhibition", cookie))
        self.interfaces[":1.50"].fail = None
        self.assertFalse(self.display.refresh())
        self.display.close()
        self.assertNotEqual(self.calls[-1][2], cookie)
    def test_screen_and_power_owner_changes_are_independent_and_cleanup_covers_both(self):
        screen_calls = []
        lock = screen.ScreenInhibitor(self.bus, lambda owner: Interface(owner, screen_calls), 123,
                                     errors=(BusError,), report=lambda _: None)
        game = screen.GameInhibitor(lock, self.display)
        self.assertFalse(game.refresh())
        self.clock.now = 5
        self.own_entry()
        self.assertTrue(game.refresh())
        self.bus.owners[screen.POLICY_SERVICE] = ":1.51"
        self.assertFalse(game.refresh())
        self.assertEqual([call[1] for call in screen_calls], ["Inhibit"])
        self.bus.owners[screen.SERVICE] = ":1.43"
        self.assertFalse(game.refresh())
        self.assertEqual([call[1] for call in screen_calls], ["Inhibit", "UnInhibit", "Inhibit"])
        game.close()
        self.assertEqual(screen_calls[-1][:3], (":1.43", "UnInhibit", 17))
        self.assertEqual(self.calls[-1][:2], (":1.51", "ReleaseInhibition"))
    def test_missing_service_and_failed_verification_never_claim_success(self):
        self.bus.owners[screen.POLICY_SERVICE] = None
        self.assertFalse(self.display.refresh())
        self.assertFalse(self.display.refresh())
        self.assertEqual(self.calls, [])
        self.assertEqual(sum("unavailable" in message for message in self.messages), 1)
        self.bus.owners[screen.POLICY_SERVICE] = ":1.50"
        self.assertFalse(self.display.refresh())
    def test_composite_partial_exception_closes_both_leases(self):
        calls = []
        class Lease:
            def __init__(self, name, fail=False): self.name, self.fail = name, fail
            def refresh(self):
                calls.append(self.name + " refresh")
                if self.fail: raise RuntimeError("test")
                return True
            def close(self): calls.append(self.name + " close")
        game = screen.GameInhibitor(Lease("screen"), Lease("display", True))
        with self.assertRaises(RuntimeError): game.refresh()
        self.assertEqual(calls, ["screen refresh", "display refresh", "screen close", "display close"])
    def test_factory_uses_existing_user_bus_correct_interface_uint32_and_deadlines(self):
        calls, owners = [], {screen.SERVICE: ":1.42", "org.kde.Solid.PowerManagement": ":1.50"}
        test = self
        class SessionBus:
            def call_blocking(self, service, path, interface, method, signature, args, timeout):
                test.assertEqual((service, path, interface, method, signature, timeout),
                    ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner", "s", 2))
                calls.append(("owner", args[0]))
                return owners[args[0]]
            def get_object(self, owner, path, introspect):
                test.assertFalse(introspect)
                return owner, path
        bus = SessionBus()
        def connect(address):
            self.assertEqual(address, "unix:path=/run/user/1000/bus")
            return bus
        def interface(proxy, name):
            owner, path = proxy
            if name == screen.SERVICE:
                self.assertEqual(path, "/ScreenSaver")
                return Interface(owner, calls)
            self.assertEqual(name, "org.kde.Solid.PowerManagement.PolicyAgent")
            self.assertEqual(path, "/org/kde/Solid/PowerManagement/PolicyAgent")
            return PolicyInterface(owner, calls)
        fake_dbus = SimpleNamespace(bus=SimpleNamespace(BusConnection=connect), Interface=interface,
                                    UInt32=UInt32, DBusException=BusError)
        with patch.dict(sys.modules, {"dbus": fake_dbus}), patch.object(screen.os, "getuid", return_value=1000, create=True):
            game = screen.make_inhibitor(123, report=lambda _: None)
            self.assertFalse(game.refresh())
            game.close()
        self.assertIn(("owner", "org.kde.Solid.PowerManagement"), calls)
        self.assertTrue(any(call[0:2] == (":1.50", "AddInhibition") for call in calls))


if __name__ == "__main__": unittest.main()
