"""Focused bounded-I/O and privacy tests; no game or real server fixture required."""
import importlib.util
import json
from pathlib import Path
import socket
import threading
import time
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "diagnostics" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


status = module("server_status")
client = module("client_snapshot")


class WireServer:
    def __init__(self, response=None, *, pause=0):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.response = response
        self.pause = pause
        self.observed = {}
        self.errors = []

    def __enter__(self):
        def serve():
            try:
                self.listener.settimeout(2)
                stream, _ = self.listener.accept()
                with stream:
                    deadline = time.monotonic() + 2
                    size = status.socket_varint(stream, deadline)
                    handshake = status.exact(stream, size, deadline)
                    self.observed["nextState"] = handshake[-1]
                    size = status.socket_varint(stream, deadline)
                    self.observed["request"] = status.exact(stream, size, deadline)
                    if self.pause:
                        time.sleep(self.pause)
                    if self.response:
                        stream.sendall(self.response)
            except Exception as error:
                self.errors.append(error)
            finally:
                self.listener.close()
        self.worker = threading.Thread(target=serve, daemon=True)
        self.worker.start()
        return self

    def __exit__(self, *_):
        self.worker.join(3)
        if self.worker.is_alive():
            raise AssertionError("Synthetic status server did not finish")
        if self.errors:
            raise AssertionError(self.errors)


def packet(text):
    data = text.encode()
    payload = b"\x00" + status.varint(len(data)) + data
    return status.varint(len(payload)) + payload


class ServerStatusTests(unittest.TestCase):
    def test_real_framing_is_status_only_and_private_fields_are_discarded(self):
        response = {"version": {"name": "1.12.2", "protocol": 340},
                    "players": {"online": 0, "max": 20,
                                "sample": [{"name": "PRIVATE_PLAYER_NAME", "id": "PRIVATE_UUID"}]},
                    "description": {"text": "PRIVATE_MOTD_TEXT", "extra": [{"text": "PRIVATE_TOKEN"}]},
                    "favicon": "data:image/png;base64,PRIVATE_PNG_BYTES",
                    "modinfo": {"type": "FML", "modList": [{"modid": "forge", "version": "14.23.5.2860"}]}}
        with WireServer(packet(json.dumps(response))) as server:
            result = status.probe("127.0.0.1", server.port)
        self.assertEqual(server.observed, {"nextState": 1, "request": b"\x00"})
        self.assertEqual(result["players"], {"online": 0, "max": 20})
        self.assertEqual(result["forge"]["modCount"], 1)
        self.assertTrue(result["motd"]["textWithheld"])
        self.assertNotIn("PRIVATE_", json.dumps(result))
        self.assertNotIn("favicon", result)

    def test_oversized_packet_rejected_before_receiving_its_body(self):
        with WireServer(status.varint(status.MAX_PACKET + 1)) as server:
            with self.assertRaisesRegex(ValueError, "packet size"):
                status.probe("127.0.0.1", server.port)

    def test_socket_deadline_is_bounded(self):
        started = time.monotonic()
        with WireServer(pause=0.2) as server:
            with self.assertRaises(TimeoutError):
                status.probe("127.0.0.1", server.port, timeout=0.05)
        self.assertLess(time.monotonic() - started, 1)

    def test_deep_json_and_excessive_mod_lists_are_rejected(self):
        text = '{"description":' + '[' * 2000 + '0' + ']' * 2000 + '}'
        with WireServer(packet(text)) as server:
            with self.assertRaisesRegex(ValueError, "nesting"):
                status.probe("127.0.0.1", server.port)
        with self.assertRaisesRegex(ValueError, "mod list"):
            status.summarized({"modinfo": {"modList": [{}] * (status.MAX_MODS + 1)}})

    def test_description_traversal_is_bounded_without_raw_text_output(self):
        result = status.summarized({"description": {"text": "x" * 100_000}})
        self.assertTrue(result["motd"]["fingerprintTruncated"])
        self.assertLessEqual(result["motd"]["characters"], status.MAX_DESCRIPTION_CHARACTERS)
        self.assertNotIn("xxxx", json.dumps(result))


class ClientSnapshotTests(unittest.TestCase):
    def test_only_allowlisted_info_and_class_names_escape_parser(self):
        text = "\n".join([
            "[10:00:00] [Client thread/INFO] [FML]: LWJGL Version: 2.9.4",
            "[10:00:01] [Client thread/INFO] [FML]: OpenGL: NVIDIA Tegra X1 GL version 4.6",
            "[10:00:02] [Client thread/INFO] [FML]: Forge Mod Loader has identified 179 mods to load",
            "[10:00:03] [Client thread/INFO] [FML]: Forge Mod Loader has successfully loaded 179 mods",
            "[10:00:04] [Client thread/INFO] [Minecraft]: Connecting to PRIVATE_ADDRESS, 25565",
            "[10:00:05] [Client thread/ERROR] [FML]: OpenGL: PRIVATE_ERROR_MESSAGE",
            "[10:00:06] [Client thread/ERROR] [FML]: PRIVATE_PLAYER_NAME PRIVATE_TOKEN",
            "java.lang.IllegalArgumentException: PRIVATE_ERROR_DETAIL",
            "Caused by: java.io.FileNotFoundException: PRIVATE_PATH",
            "[10:00:07] [Client thread/INFO] [CHAT]: PRIVATE_CHAT_TEXT",
        ])
        result = client.parse_log(text)
        self.assertEqual(result["discoveredModCount"], 179)
        self.assertEqual(result["loadedModCount"], 179)
        self.assertEqual(result["severityCounts"], {"ERROR": 2})
        self.assertEqual(result["startupStagesObserved"]["connecting"], 1)
        self.assertEqual(result["exceptionClassCounts"]["java.lang.IllegalArgumentException"], 1)
        self.assertEqual(len(result["graphicsMessages"]), 2)
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_log_read_and_map_selection_are_bounded_and_narrow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "logs").mkdir()
            (root / "logs/latest.log").write_bytes(b"PRIVATE_PREFIX\n" * client.LOG_LIMIT
                                                   + b"[1] [Client thread/INFO] [FML]: Sound engine started\n")
            result = client.inspect_log(root)
            self.assertLessEqual(result["bytesInspected"], client.LOG_LIMIT)
            self.assertTrue(result["tailWindow"])
            self.assertEqual(result["startupStagesObserved"], {"audio_started": 1})
            (root / "maps").write_text(
                "1000-2000 r--p 0 00:00 1 /usr/lib/tegra/libGLX_nvidia.so.0\n"
                "2000-3000 r--p 0 00:00 1 /PRIVATE_SECRET_PATH/accounts.json\n"
                "3000-4000 r--p 0 00:00 1 /tmp/natives/libopenal.so\n")
            selected = client.libraries(root)
            self.assertEqual(len(selected["selectedSharedLibraries"]), 2)
            self.assertNotIn("PRIVATE_", json.dumps(selected))
            self.assertNotIn("1000-2000", json.dumps(selected))


if __name__ == "__main__":
    unittest.main()
