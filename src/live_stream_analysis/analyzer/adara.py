"""ADARA file and live-stream analysis CLI entrypoints."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path
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
        "--histogram-output-txt",
        metavar="FILE",
        help="Optional output text file with one line per bin: 'Index:<i> - Counts:<n>'.",
    )

    return parser


def _load_q_matrix_constants(pixel_geometry_csv: str) -> list[float]:
    path = Path(pixel_geometry_csv).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Pixel geometry CSV does not exist: {path}")

    by_pixel_id: dict[int, float] = {}
    max_pixel_id = -1
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required_columns = {"pixel id", "TOF-to-Q matrix element"}
        if reader.fieldnames is None or not required_columns.issubset(set(reader.fieldnames)):
            raise ValueError(
                "Pixel geometry CSV must include columns: 'pixel id' and 'TOF-to-Q matrix element'"
            )

        for row in reader:
            pixel_id = int(row["pixel id"])
            q_matrix_element = float(row["TOF-to-Q matrix element"])
            by_pixel_id[pixel_id] = q_matrix_element
            if pixel_id > max_pixel_id:
                max_pixel_id = pixel_id

    if max_pixel_id < 0:
        raise ValueError(f"No detector rows found in pixel geometry CSV: {path}")

    q_matrix_constants = [0.0] * (max_pixel_id + 1)
    for pixel_id, q_matrix_element in by_pixel_id.items():
        if pixel_id >= 0:
            q_matrix_constants[pixel_id] = q_matrix_element

    return q_matrix_constants


def _write_histogram_txt(hist: list[int], output_path: str) -> None:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index, counts in enumerate(hist):
            handle.write(f"Index:{index} - Counts:{counts}\n")


def _accumulate_histogram(
    reader,
    q_matrix_constants: list[float],
    histogram_bins: int,
    histogram_q_bin_size: float,
    tof_tick_us: float,
) -> tuple[int, int, int, list[int]]:
    packet_count = 0
    total_events = 0
    histogram_events = 0
    hist = [0] * histogram_bins

    inv_tof_tick_us = 1.0 / tof_tick_us

    for packet in reader.read_generator():
        packet_count += 1
        events = packet.get_events()
        total_events += len(events)

        for tof, pixel_id in events:
            if pixel_id < 0 or pixel_id >= len(q_matrix_constants):
                continue
            if tof <= 0:
                continue

            q = (q_matrix_constants[pixel_id] * inv_tof_tick_us) / tof
            bram_index = int(q / histogram_q_bin_size)
            if 0 <= bram_index < histogram_bins:
                hist[bram_index] += 1
                histogram_events += 1

    return packet_count, total_events, histogram_events, hist


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

    run_histogram = args.histogram_pixel_geometry_csv is not None

    if run_histogram:
        if args.histogram_q_bin_size <= 0:
            print("Error: --histogram-q-bin-size must be > 0", file=sys.stderr)
            return 1
        if args.histogram_q_max <= 0:
            print("Error: --histogram-q-max must be > 0", file=sys.stderr)
            return 1
        if args.tof_tick_us <= 0:
            print("Error: --tof-tick-us must be > 0", file=sys.stderr)
            return 1

        histogram_bins_f = args.histogram_q_max / args.histogram_q_bin_size
        histogram_bins = int(round(histogram_bins_f))
        if histogram_bins <= 0 or not math.isclose(histogram_bins_f, float(histogram_bins), rel_tol=1e-9, abs_tol=1e-9):
            print(
                "Error: --histogram-q-max must be an integer multiple of --histogram-q-bin-size",
                file=sys.stderr,
            )
            return 1

        try:
            q_matrix_constants = _load_q_matrix_constants(args.histogram_pixel_geometry_csv)
            packet_count, total_events, histogram_events, hist = _accumulate_histogram(
                reader=reader,
                q_matrix_constants=q_matrix_constants,
                histogram_bins=histogram_bins,
                histogram_q_bin_size=args.histogram_q_bin_size,
                tof_tick_us=args.tof_tick_us,
            )
        except KeyboardInterrupt:
            print("Interrupted by user", file=sys.stderr)
            return 130
        except (OSError, ValueError) as exc:
            print(f"Error reading stream: {exc}", file=sys.stderr)
            return 1

        if args.histogram_output_txt is not None:
            try:
                _write_histogram_txt(hist, args.histogram_output_txt)
            except OSError as exc:
                print(f"Error writing histogram output: {exc}", file=sys.stderr)
                return 1

        print(f"Packets read         : {packet_count}")
        print(f"Total events         : {total_events}")
        print(f"Histogrammed events  : {histogram_events}")
        print(f"Histogram bins       : {histogram_bins}")
        print(f"Histogram Q bin size : {args.histogram_q_bin_size}")
        print(f"Histogram Q max      : {args.histogram_q_max}")
        print(f"TOF tick size (us)   : {args.tof_tick_us}")
        if args.histogram_output_txt is not None:
            print(f"Histogram TXT        : {Path(args.histogram_output_txt).resolve()}")
        return 0

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
