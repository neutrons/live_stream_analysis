"""Helpers for generating synthetic ADARA binary test data."""

from __future__ import annotations

import struct


def _pack_header(payload_length: int, format_int: int, timestamp_s: int = 0, timestamp_ns: int = 0) -> bytes:
    """Return a 16-byte ADARA packet header."""
    return struct.pack("<IIII", payload_length, format_int, timestamp_s, timestamp_ns)


def rtdl_packet(timestamp_s: int = 1, charge_10pc: int = 100) -> bytes:
    """Build a minimal AdaraRTDLPacket (format_int=0x100)."""
    payload = struct.pack("<I", charge_10pc)
    payload += struct.pack("<I", 0)
    payload += struct.pack("<I", 0)
    payload += struct.pack("<I", 0)
    payload += struct.pack("<I", 0)
    payload += b"\x00" * (25 * 4)
    return _pack_header(len(payload), 0x100, timestamp_s) + payload


def null_packet(format_int: int = 0, timestamp_s: int = 0) -> bytes:
    """Build a minimal AdaraNullPacket (no events, no payload)."""
    return _pack_header(0, format_int, timestamp_s)


def event_packet(events: list[tuple[int, int]], timestamp_s: int = 2) -> bytes:
    """Build an AdaraEventPacket (format_int=0x300) with (pixid, tof) events."""
    header_meta = b"\x00" * 24
    events_b = b"".join(struct.pack("<II", pixid, tof) for pixid, tof in events)
    payload = header_meta + events_b
    return _pack_header(len(payload), 0x300, timestamp_s) + payload


def run_status_packet(
    *,
    run_number: int,
    run_start: int,
    status: int,
    file_number: int = 0,
    pause_file_number: int = 0,
    paused: int = 0,
    addendum_file_number: int = 0,
    addendum: int = 0,
    timestamp_s: int = 0,
) -> bytes:
    """Build an AdaraRunStatusPacket (base format_int=0x400001)."""
    payload = struct.pack("<IIIII", run_number, run_start, (status << 24) | file_number, (paused << 24) | pause_file_number, (addendum << 24) | addendum_file_number)
    return _pack_header(len(payload), 0x400001, timestamp_s)
