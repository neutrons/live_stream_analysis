"""Helpers for generating synthetic ADARA binary test data."""

from __future__ import annotations

import struct


def _pack_header(payload_length: int, format_int: int, timestamp_s: int = 0, timestamp_ns: int = 0) -> bytes:
    """Return a 16-byte ADARA packet header."""
    return struct.pack("<IIII", payload_length, format_int, timestamp_s, timestamp_ns)


def rtdl_packet(timestamp_s: int = 1, charge_10pc: int = 100) -> bytes:
    """Build a minimal AdaraRTDLPacket (format_int=0x100).

    The RTDL payload is 136 bytes: 4 bytes charge/flavor + 4 cycle/veto +
    4 intra_pulse_time + 4 tof_offset_cor + 4 ring_period + 25*4 frames.
    """
    payload = struct.pack("<I", charge_10pc)          # charge (3 bytes used) | flavor
    payload += struct.pack("<I", 0)                    # cycle / veto / tstat / bcy / bvt
    payload += struct.pack("<I", 0)                    # intra_pulse_time
    payload += struct.pack("<I", 0)                    # tof_offset_cor
    payload += struct.pack("<I", 0)                    # ring_period | FNA
    payload += b"\x00" * (25 * 4)                     # 25 frame entries
    return _pack_header(len(payload), 0x100, timestamp_s) + payload


def null_packet(format_int: int = 0, timestamp_s: int = 0) -> bytes:
    """Build a minimal AdaraNullPacket (no events, no payload)."""
    return _pack_header(0, format_int, timestamp_s)


def event_packet(events: list[tuple[int, int]], timestamp_s: int = 2) -> bytes:
    """Build an AdaraEventPacket (format_int=0x300) with the given (pixid, tof) events.

    Payload layout (legacy SNS format):
      16 bytes header metadata  + 8 bytes per event.
    The reader reads events from bytes [16+24:] so we pad 24 bytes of zeros.
    """
    header_meta = b"\x00" * 24          # charge, accelerator, tof_offset etc.
    events_b = b"".join(struct.pack("<II", pixid, tof) for pixid, tof in events)
    payload = header_meta + events_b
    return _pack_header(len(payload), 0x300, timestamp_s) + payload
