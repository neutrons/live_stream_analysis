#!/usr/bin/env python3
"""Serve a recorded .adara file over TCP as if it were a live SMS stream.

An ADARA file is a plain concatenation of framed packets, and `readadara` parses a socket
with exactly the same reader it uses for a file -- so replaying a file is just writing its
bytes to a connected client, with no protocol translation.

That makes it possible to exercise the whole `--adara-stream` path (run transitions,
reconnects, INTERSECT publishing) from recorded data, without waiting for beam time.

Standard library only; run it from the project's uv environment or any Python 3.10+.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

ADARA_HEADER_BYTES = 16


def iter_packets(adara_files: list[Path]):
    """Yield raw packet bytes (header + payload) across the given files, in order.

    The 16-byte header starts with a little-endian payload length, so each packet is
    header + payload_length bytes. Framing the replay this way lets us pace and count
    packets, and cut the connection on a real packet boundary.

    A single run is split across many files: the first carries NEW_RUN, the middle ones
    RUN_BOF/RUN_EOF, and only the last carries END_RUN. Pass the whole set to replay a
    complete run, or a subset to stop mid-run.
    """
    for adara_file in adara_files:
        with adara_file.open("rb") as handle:
            while True:
                header = handle.read(ADARA_HEADER_BYTES)
                if len(header) < ADARA_HEADER_BYTES:
                    break
                payload_length = int.from_bytes(header[:4], "little")
                payload = handle.read(payload_length)
                if len(payload) < payload_length:
                    break  # truncated trailing packet
                yield header + payload


def serve_once(connection: socket.socket, adara_files: list[Path], args, *, drop_after: int) -> tuple[str, bool]:
    """Stream the files to one client. Returns (reason, close_gracefully)."""
    delay = 1.0 / args.packets_per_second if args.packets_per_second > 0 else 0.0
    sent = 0
    for packet in iter_packets(adara_files):
        if drop_after and sent >= drop_after:
            # Abrupt on purpose: this is what the analyzer's reconnect path has to survive.
            return f"cut after {sent} packets (--drop-after)", False
        if args.max_packets and sent >= args.max_packets:
            return f"reached --max-packets ({sent})", True
        try:
            connection.sendall(packet)
        except (BrokenPipeError, ConnectionResetError):
            return f"client disconnected after {sent} packets", False
        sent += 1
        if args.log_every and sent % args.log_every == 0:
            print(f"  sent {sent} packets", file=sys.stderr)
        if delay:
            time.sleep(delay)
    return f"end of stream after {sent} packets", True


def close_gracefully(connection: socket.socket, timeout: float) -> None:
    """Half-close and drain so the client sees EOF instead of a connection reset.

    Closing outright while the client still has unread bytes buffered makes the kernel send
    RST, which surfaces as 'Connection reset by peer' rather than a clean end of stream.
    """
    try:
        connection.shutdown(socket.SHUT_WR)
    except OSError:
        return
    connection.settimeout(timeout)
    try:
        while connection.recv(65536):
            pass
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adara-file",
        type=Path,
        required=True,
        nargs="+",
        help="Recorded .adara file(s) to replay, in order. Pass a whole run's files to reach END_RUN.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=31415, help="Port to bind (default: 31415).")
    parser.add_argument(
        "--packets-per-second",
        type=float,
        default=0.0,
        help="Throttle to roughly this many packets per second. 0 (default) replays as fast as possible.",
    )
    parser.add_argument("--max-packets", type=int, default=0, help="Stop after this many packets (0 = whole file).")
    parser.add_argument(
        "--drop-after",
        type=int,
        default=0,
        help="Cut the connection after this many packets, to exercise the analyzer's reconnect path.",
    )
    parser.add_argument(
        "--drop-once",
        action="store_true",
        help="Apply --drop-after to the first client only, so the reconnecting client can finish the run.",
    )
    parser.add_argument("--log-every", type=int, default=0, help="Log progress every N packets (0 = quiet).")
    parser.add_argument("--once", action="store_true", help="Exit after the first client instead of serving again.")
    parser.add_argument(
        "--linger-seconds",
        type=float,
        default=5.0,
        help="How long to wait for the client to close after the stream ends (default: 5).",
    )
    args = parser.parse_args()

    adara_files = [path.resolve() for path in args.adara_file]
    missing = [path for path in adara_files if not path.exists()]
    if missing:
        for path in missing:
            print(f"ADARA file does not exist: {path}", file=sys.stderr)
        return 1

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.host, args.port))
        server.listen(1)
        print(f"Replaying {len(adara_files)} file(s) on {args.host}:{args.port}", file=sys.stderr)
        print(f"  analyze --adara-stream {args.host} {args.port}", file=sys.stderr)
        clients = 0
        try:
            while True:
                connection, peer = server.accept()
                clients += 1
                print(f"Client {clients} connected from {peer[0]}:{peer[1]}", file=sys.stderr)
                # With --drop-once only the first client is cut, so the reconnecting client
                # gets a clean run all the way to END_RUN.
                drop_after = args.drop_after if (clients == 1 or not args.drop_once) else 0
                with connection:
                    reason, graceful = serve_once(connection, adara_files, args, drop_after=drop_after)
                    if graceful:
                        close_gracefully(connection, args.linger_seconds)
                print(f"Stream ended: {reason}", file=sys.stderr)
                if args.once:
                    return 0
        except KeyboardInterrupt:
            print("Interrupted by user", file=sys.stderr)
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
