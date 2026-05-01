"""ADARA file and live-stream analysis CLI entrypoints."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from readadara import AdaraFileReader, AdaraLiveStreamReader


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the 'analyze' subcommand and return its parser."""
    parser = subparsers.add_parser(
        "analyze",
        help="Read and analyze ADARA packet streams.",
        description=(
            "Analyze an ADARA data source.  "
            "Exactly one of --adara-file or --adara-stream must be provided."
        ),
    )
    parser.set_defaults(_cmd="analyze")

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--adara-file",
        metavar="FILE",
        help="Path to an ADARA binary file to analyze.",
    )
    source.add_argument(
        "--adara-stream",
        nargs=2,
        metavar=("HOSTNAME", "PORT"),
        help="Hostname and port of a live ADARA stream (e.g. bl10-daq1 31415).",
    )

    return parser


def _build_reader(args: argparse.Namespace):
    """Construct the appropriate ADARA reader from parsed arguments."""
    from readadara import AdaraFileReader, AdaraLiveStreamReader

    has_file = args.adara_file is not None
    has_stream = args.adara_stream is not None

    if has_file and has_stream:
        raise ValueError("Provide either --adara-file or --adara-stream, not both.")

    if has_file:
        return AdaraFileReader(filename=args.adara_file)

    if has_stream:
        hostname, port_str = args.adara_stream
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"PORT must be an integer, got: {port_str!r}")
        return AdaraLiveStreamReader(hostname, port)

    raise ValueError("No data source specified. Use --adara-file FILE or --adara-stream HOSTNAME PORT.")


def run_from_namespace(args: argparse.Namespace) -> int:
    """Execute the analyze command from a parsed argument namespace."""
    try:
        reader = _build_reader(args)
    except (ValueError, OSError) as exc:
        print(f"Error creating ADARA reader: {exc}", file=sys.stderr)
        return 1

    packet_count = 0
    event_count = 0
    try:
        for packet in reader.read_generator():
            packet_count += 1
            events = packet.get_events()
            event_count += len(events)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"Error reading stream: {exc}", file=sys.stderr)
        return 1

    print(f"Packets read : {packet_count}")
    print(f"Total events : {event_count}")
    return 0
