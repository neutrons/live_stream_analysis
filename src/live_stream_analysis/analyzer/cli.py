from __future__ import annotations

import argparse


def add_intersect_listener_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the 'intersect-listen' subcommand and return its parser."""
    parser = subparsers.add_parser(
        "intersect-listen",
        help="Subscribe to INTERSECT events emitted by the analyzer service.",
        description="Listen for histogram and run-complete INTERSECT events using the configured broker.",
    )
    parser.set_defaults(_cmd="intersect-listen")
    parser.add_argument(
        "--intersect-config",
        metavar="FILE",
        required=True,
        help="YAML config file containing INTERSECT broker, data store, and hierarchy settings.",
    )
    return parser


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the 'analyze' subcommand and return its parser."""
    parser = subparsers.add_parser(
        "analyze",
        help="Read and analyze ADARA packet streams.",
        description=(
            "Analyze an ADARA or NeXus data source.  "
            "Exactly one of --adara-file, --nexus-file, or --adara-stream must be provided."
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
        "--nexus-file",
        metavar="FILE",
        action="append",
        default=None,
        help="Path to an event NeXus HDF5 file to analyze. Repeat to combine multiple files.",
    )
    source.add_argument(
        "--adara-stream",
        nargs=2,
        metavar=("HOSTNAME", "PORT"),
        help="Hostname and port of a live ADARA stream (e.g. bl10-daq1 31415).",
    )

    parser.add_argument(
        "--enable-intersect",
        action="store_true",
        help="Enable INTERSECT event publishing for histogram updates and run completion.",
    )
    parser.add_argument(
        "--intersect-config",
        metavar="FILE",
        help="YAML config file containing INTERSECT broker, data store, and event settings.",
    )
    parser.add_argument(
        "--adara-file-delay-read",
        type=float,
        default=0.0,
        help="Delay reading an ADARA file source by N seconds after startup (default: 0).",
    )
    parser.add_argument(
        "--adara-file-delay-intersect",
        action="store_true",
        help="Wait for the INTERSECT start_adara_file_read operation before reading an ADARA file source.",
    )
    parser.add_argument(
        "--histogram-pixel-geometry-csv",
        metavar="FILE",
        help=(
            "Pixel geometry CSV from the preparer command. "
            "When provided, compute FPGA-style Q-bin histogram while reading ADARA events."
        ),
    )
    parser.add_argument(
        "--histogram-q-bin-size",
        type=float,
        default=0.02,
        help="Histogram bin width in Q units (default: 0.02).",
    )
    parser.add_argument(
        "--histogram-q-min",
        type=float,
        default=0.0,
        help="Expected Q lower bound used for bin scaling (default: 0.0).",
    )
    parser.add_argument(
        "--histogram-q-max",
        type=float,
        default=30.0,
        help="Expected Q upper bound used for bin scaling (default: 30.0).",
    )
    parser.add_argument(
        "--tof-tick-us",
        type=float,
        default=1.0,
        help=(
            "TOF tick size in microseconds used by ADARA events. "
            "Use 0.1 if CSV constants are unscaled; use 1.0 if CSV constants were pre-scaled by 10."
        ),
    )
    parser.add_argument(
        "--histogram-output-csv",
        metavar="FILE",
        help="Optional output CSV file with columns: Q value, I(Q), Error I(Q).",
    )
    parser.add_argument(
        "--background-subtraction",
        metavar="FILE",
        help="Optional three-column CSV with Q value, I(Q), Error I(Q) to subtract from the histogrammed signal.",
    )
    parser.add_argument(
        "--normalization",
        metavar="FILE",
        help="Optional three-column CSV with Q value, I(Q), Error I(Q) used to normalize the histogrammed signal.",
    )
    parser.add_argument(
        "--live-plot-mode",
        choices=("desktop", "browser"),
        help=(
            "Show a live-updating histogram while processing. "
            "Use 'desktop' for an interactive matplotlib window or 'browser' for a local web UI."
        ),
    )
    parser.add_argument(
        "--live-plot-refresh-every",
        type=int,
        default=1,
        help="Refresh the live plot every N ADARA packets or NeXus chunks (default: 1).",
    )
    parser.add_argument(
        "--live-plot-host",
        default="127.0.0.1",
        help="Host interface for browser live plot mode (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--live-plot-port",
        type=int,
        default=8000,
        help="Port for browser live plot mode (default: 8000).",
    )
    parser.add_argument(
        "--live-plot-no-open-browser",
        action="store_true",
        help="Do not automatically open the browser when using browser live plot mode.",
    )
    parser.add_argument(
        "--live-plot-keep-open",
        action="store_true",
        help="Keep the browser live plot server running after processing completes until interrupted.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level for analyzer setup and progress messages (default: INFO).",
    )
    parser.add_argument(
        "--event-log-interval",
        type=int,
        default=100_000,
        help="Log histogramming progress every N accepted events (default: 100000).",
    )

    return parser
