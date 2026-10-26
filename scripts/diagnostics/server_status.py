#!/usr/bin/env python3
"""Read-only Minecraft 1.12.2 server-list status probe; never logs in or joins.

Player samples, names, UUIDs, favicon data and custom MOTD text are discarded.
No authentication, server/world writes, or third-party dependencies are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import time


MAX_PACKET = 4 * 1024 * 1024
MAX_MODS = 1024
MAX_DESCRIPTION_NODES = 4096
MAX_DESCRIPTION_CHARACTERS = 8192


def varint(value: int) -> bytes:
    if not 0 <= value < 2**31:
        raise ValueError("VarInt is outside the positive 32-bit range")
    data = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        data.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(data)


def exact(stream: socket.socket, size: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Status probe deadline expired")
        stream.settimeout(remaining)
        part = stream.recv(size - len(data))
        if not part:
            raise ValueError("Server closed an incomplete status response")
        data.extend(part)
    return bytes(data)


def socket_varint(stream: socket.socket, deadline: float) -> int:
    value = 0
    for offset in range(5):
        byte = exact(stream, 1, deadline)[0]
        value |= (byte & 0x7F) << (offset * 7)
        if not byte & 0x80:
            if value >= 2**31:
                raise ValueError("Invalid positive packet length")
            return value
    raise ValueError("Oversized packet VarInt")


def packet_varint(packet: bytes, cursor: int = 0) -> tuple[int, int]:
    value = 0
    for offset in range(5):
        if cursor >= len(packet):
            raise ValueError("Truncated packet VarInt")
        byte = packet[cursor]
        cursor += 1
        value |= (byte & 0x7F) << (offset * 7)
        if not byte & 0x80:
            if value >= 2**31:
                raise ValueError("Invalid positive packet VarInt")
            return value, cursor
    raise ValueError("Oversized packet VarInt")


def plain_description(value: object) -> tuple[str, bool]:
    # Only collect literal chat text; do not resolve translation keys or selectors.
    pending = [value]
    pieces = []
    characters = 0
    visited = 0
    while pending:
        value = pending.pop()
        visited += 1
        if visited > MAX_DESCRIPTION_NODES:
            return "".join(pieces), True
        if isinstance(value, str):
            remaining = MAX_DESCRIPTION_CHARACTERS - characters
            pieces.append(value[:remaining])
            characters += min(remaining, len(value))
            if len(value) > remaining or characters == MAX_DESCRIPTION_CHARACTERS and pending:
                return "".join(pieces), True
        elif isinstance(value, list):
            if len(value) > MAX_DESCRIPTION_NODES - visited:
                return "".join(pieces), True
            pending.extend(reversed(value))
        elif isinstance(value, dict):
            pending.append(value.get("extra", []))
            pending.append(value.get("text", ""))
    return "".join(pieces), False


def clean(value: object, maximum: int = 128) -> str:
    text = value if isinstance(value, str) else ""
    return "".join(char for char in text if char.isprintable())[:maximum]


def summarized(raw: dict) -> dict:
    version = raw.get("version", {})
    players = raw.get("players", {})
    modinfo = raw.get("modinfo", {})
    if not isinstance(version, dict) or not isinstance(players, dict) or not isinstance(modinfo, dict):
        raise ValueError("Unexpected server status object schema")
    mods = []
    modlist = modinfo.get("modList", [])
    if not isinstance(modlist, list):
        raise ValueError("Unexpected Forge mod-list schema")
    if len(modlist) > MAX_MODS:
        raise ValueError("Server mod list exceeds the diagnostic limit")
    for item in modlist:
        if isinstance(item, dict) and isinstance(item.get("modid"), str):
            mods.append({"id": clean(item["modid"]), "version": clean(item.get("version", ""))})
    description, description_truncated = plain_description(raw.get("description", ""))
    description = re.sub(r"\u00a7.", "", description)
    wire_protocol = version.get("protocol")
    return {
        "version": {"name": clean(version.get("name", "")),
                    "protocol": wire_protocol if type(wire_protocol) is int else None},
        "players": {key: value for key in ("online", "max")
                    if type(value := players.get(key)) is int and value >= 0},
        "motd": {"present": bool(description), "characters": len(description),
                 "textSha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
                 "fingerprintTruncated": description_truncated, "textWithheld": True},
        "forge": {"type": clean(modinfo.get("type", "")), "modCount": len(mods),
                  "mods": sorted(mods, key=lambda item: item["id"])},
        "privacy": "Player samples/names/UUIDs, favicon and custom MOTD text discarded",
    }


def probe(host: str, port: int = 25565, *, protocol: int = 340, timeout: float = 5.0) -> dict:
    encoded_host = host.encode("utf-8")
    if not 1 <= len(encoded_host) <= 255 or any(ord(char) < 32 for char in host):
        raise ValueError("Host must be a nonempty ordinary address of at most 255 UTF-8 bytes")
    started = time.monotonic()
    deadline = started + timeout
    with socket.create_connection((host, port), timeout=timeout) as stream:
        handshake = b"\x00" + varint(protocol) + varint(len(encoded_host)) + encoded_host + struct.pack(">H", port) + b"\x01"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Status probe deadline expired during connection")
        stream.settimeout(remaining)
        stream.sendall(varint(len(handshake)) + handshake + b"\x01\x00")
        length = socket_varint(stream, deadline)
        if not 1 <= length <= MAX_PACKET:
            raise ValueError("Status response exceeds the allowed packet size")
        packet = exact(stream, length, deadline)
        packet_id, cursor = packet_varint(packet)
        if packet_id != 0:
            raise ValueError("Expected server-list status packet ID 0")
        text_size, cursor = packet_varint(packet, cursor)
        if text_size != len(packet) - cursor:
            raise ValueError("Status JSON size does not match packet framing")
        try:
            raw = json.loads(packet[cursor:].decode("utf-8"))
        except RecursionError as error:
            raise ValueError("Status JSON nesting exceeds the diagnostic limit") from error
        if not isinstance(raw, dict):
            raise ValueError("Expected a status JSON object")
    return {"schema": 1, "observedUtc": datetime.now(timezone.utc).isoformat(),
            "probeMachine": socket.gethostname(), "readOnlyServerListStatus": True,
            "statusElapsedMs": round((time.monotonic() - started) * 1000, 2), **summarized(raw)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--protocol", type=int, default=340)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--alias", default="existing-world", help="Nonsecret endpoint label")
    parser.add_argument("--output", type=Path, help="Fresh private evidence file; never overwritten")
    parser.add_argument("--summary", action="store_true", help="Omit the mod list from console output")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 0 < args.timeout <= 30:
        parser.error("Use port 1..65535 and timeout 0..30 seconds")
    result = probe(args.host, args.port, protocol=args.protocol, timeout=args.timeout)
    result["endpointAlias"] = clean(args.alias)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    if args.summary:
        result["forge"] = {key: value for key, value in result["forge"].items() if key != "mods"}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Status probe failed: {type(error).__name__}: {error}")
