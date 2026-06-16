#!/usr/bin/env python
"""Probe NOMAD ADARA live-stream endpoints and optionally read sample packets.

This script helps identify a working hostname/port pair for NOMAD live data,
then shows the exact package CLI command to run with --adara-stream.
"""

from __future__ import annotations

import argparse
import queue
import socket
import threading
import time
from dataclasses import dataclass


DEFAULT_HOSTS = [
    "bl1b-daq1.sns.gov",
    "bl1b-daq0.sns.gov",
    "bl1b-daq1",
    "bl1b-daq0",
]
DEFAULT_PORTS = [31415, 31416]


@dataclass
class ProbeResult:
    host: str
    port: int
    dns_ok: bool
    resolved_ips: list[str]
    tcp_ok: bool
    tcp_error: str | None
    stream_ok: bool
    stream_message: str
    elapsed_s: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe NOMAD ADARA stream endpoints and optionally read the first packet "
            "using readadara.AdaraLiveStreamReader."
        )
    )
    parser.add_argument(
        "--hosts",
        nargs="+",
        default=DEFAULT_HOSTS,
        help="Hostnames to test (default includes common NOMAD DAQ names).",
    )
    parser.add_argument(
        "--ports",
        nargs="+",
        type=int,
        default=DEFAULT_PORTS,
        help="Ports to test for each host (default: 31415 31416).",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=2.0,
        help="Seconds allowed for a TCP connect attempt.",
    )
    parser.add_argument(
        "--stream-timeout",
        type=float,
        default=8.0,
        help="Seconds allowed to receive the first packet after connecting.",
    )
    parser.add_argument(
        "--skip-stream-read",
        action="store_true",
        help="Only test DNS and TCP connectivity. Do not attempt to read packets.",
    )
    return parser.parse_args()


def _resolve_host(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    ips = sorted({info[4][0] for info in infos})
    return ips


def _tcp_connect(host: str, port: int, timeout_s: float) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True, None
    except OSError as exc:
        return False, str(exc)


def _stream_read_worker(host: str, port: int, out_q: queue.Queue) -> None:
    try:
        from readadara import AdaraLiveStreamReader

        reader = AdaraLiveStreamReader(host, port)
        packet = next(reader.read_generator())
        out_q.put((True, f"Received packet type {type(packet).__name__}"))
    except Exception as exc:  # noqa: BLE001
        out_q.put((False, str(exc)))


def _try_stream_read(host: str, port: int, timeout_s: float) -> tuple[bool, str]:
    out_q: queue.Queue = queue.Queue(maxsize=1)
    thread = threading.Thread(
        target=_stream_read_worker,
        args=(host, port, out_q),
        daemon=True,
    )
    thread.start()
    try:
        ok, message = out_q.get(timeout=timeout_s)
        return bool(ok), str(message)
    except queue.Empty:
        return False, f"Timed out waiting for first packet after {timeout_s:.1f}s"


def probe_endpoint(host: str, port: int, connect_timeout: float, stream_timeout: float, read_stream: bool) -> ProbeResult:
    start = time.perf_counter()

    try:
        ips = _resolve_host(host)
        dns_ok = True
    except socket.gaierror:
        elapsed = time.perf_counter() - start
        return ProbeResult(
            host=host,
            port=port,
            dns_ok=False,
            resolved_ips=[],
            tcp_ok=False,
            tcp_error="DNS resolution failed",
            stream_ok=False,
            stream_message="Skipped (DNS failed)",
            elapsed_s=elapsed,
        )

    tcp_ok, tcp_error = _tcp_connect(host, port, connect_timeout)
    if not read_stream:
        elapsed = time.perf_counter() - start
        return ProbeResult(
            host=host,
            port=port,
            dns_ok=dns_ok,
            resolved_ips=ips,
            tcp_ok=tcp_ok,
            tcp_error=tcp_error,
            stream_ok=False,
            stream_message="Skipped by --skip-stream-read",
            elapsed_s=elapsed,
        )

    if not tcp_ok:
        elapsed = time.perf_counter() - start
        return ProbeResult(
            host=host,
            port=port,
            dns_ok=dns_ok,
            resolved_ips=ips,
            tcp_ok=False,
            tcp_error=tcp_error,
            stream_ok=False,
            stream_message="Skipped (TCP connect failed)",
            elapsed_s=elapsed,
        )

    stream_ok, stream_message = _try_stream_read(host, port, stream_timeout)
    elapsed = time.perf_counter() - start
    return ProbeResult(
        host=host,
        port=port,
        dns_ok=dns_ok,
        resolved_ips=ips,
        tcp_ok=tcp_ok,
        tcp_error=tcp_error,
        stream_ok=stream_ok,
        stream_message=stream_message,
        elapsed_s=elapsed,
    )


def main() -> int:
    args = parse_args()

    print("Probing NOMAD ADARA endpoints...")
    print()

    results: list[ProbeResult] = []
    for host in args.hosts:
        for port in args.ports:
            result = probe_endpoint(
                host=host,
                port=port,
                connect_timeout=args.connect_timeout,
                stream_timeout=args.stream_timeout,
                read_stream=not args.skip_stream_read,
            )
            results.append(result)

            dns_state = "OK" if result.dns_ok else "FAIL"
            tcp_state = "OK" if result.tcp_ok else "FAIL"
            stream_state = "OK" if result.stream_ok else "FAIL"
            print(f"{result.host}:{result.port}")
            print(f"  DNS:    {dns_state} {result.resolved_ips if result.resolved_ips else ''}".rstrip())
            print(f"  TCP:    {tcp_state} {'' if result.tcp_ok else result.tcp_error}")
            print(f"  Stream: {stream_state} {result.stream_message}")
            print(f"  Time:   {result.elapsed_s:.2f}s")
            print()

    successful_streams = [r for r in results if r.stream_ok]
    successful_tcp = [r for r in results if r.tcp_ok]

    print("Summary")
    print("-------")
    print(f"Endpoints tested: {len(results)}")
    print(f"TCP reachable:    {len(successful_tcp)}")
    print(f"Stream readable:  {len(successful_streams)}")
    print()

    preferred = successful_streams[0] if successful_streams else (successful_tcp[0] if successful_tcp else None)
    if preferred is not None:
        print("Recommended CLI test command:")
        print(
            "pixi run live_stream_analysis analyze --adara-stream "
            f"{preferred.host} {preferred.port}"
        )
        return 0

    print("No working endpoint found from provided candidates.")
    print("If DNS fails for all names, run from SNS network/VPN or use fully-qualified hostnames.")
    print("Mantid default for NOMAD is typically bl1b-daq1.sns.gov:31415.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())