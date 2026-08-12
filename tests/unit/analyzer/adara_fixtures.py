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
    """Build an AdaraBankedEventPacket (format_int=0x400001) with (pixid, tof) events.

    Events are given as (pixel_id, tof) tuples to match the ADARA naming convention used in
    higher-level tests, but are packed in (tof, pixel_id) order as required by the banked format.
    """
    # Each event is packed as (tof, pixel_id) -- the order returned by AdaraBankedEventPacket.get_events()
    events_b = b"".join(struct.pack("<II", tof, pixid) for pixid, tof in events)
    # One bank section: bank_id=0, event_count, then events
    bank_section = struct.pack("<II", 0, len(events)) + events_b
    # One source section: source_id=0, intra_pulse_time=0, tof_offset_cor=0, bank_count=1
    source_section = struct.pack("<IIII", 0, 0, 0, 1) + bank_section
    # 16 bytes of banked event metadata (charge, energy, etc.) -- all zeros for tests
    banked_meta = b"\x00" * 16
    payload = banked_meta + source_section
    return _pack_header(len(payload), 0x400001, timestamp_s) + payload


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
    """Build an AdaraRunStatusPacket (format_int=0x400301)."""
    payload = struct.pack("<IIIII", run_number, run_start, (status << 24) | file_number, (paused << 24) | pause_file_number, (addendum << 24) | addendum_file_number)
    return _pack_header(len(payload), 0x400301, timestamp_s)
