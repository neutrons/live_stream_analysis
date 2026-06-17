"""Top-level analyzer orchestration for histogram and basic modes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import adara as adara_module
from .histogram import apply_corrections, load_q_matrix_constants, validate_histogram_args, write_histogram_csv
from .live_plot import HistogramPlotter, maybe_update_live_plot
from .runner import build_reader, create_source_runner


def _run_histogram_mode(reader, args: argparse.Namespace) -> int:
    plotter: HistogramPlotter | None = None
    try:
        histogram_bins = validate_histogram_args(args)
        q_matrix_constants = load_q_matrix_constants(args.histogram_pixel_geometry_csv)
        plotter = adara_module._create_live_histogram_plotter(args, histogram_bins)
        runner = create_source_runner(args)
        chunk_size = adara_module.NEXUS_CHUNK_SIZE
        packet_count, total_events, histogram_events, hist = runner.accumulate_histogram(
            reader,
            args,
            q_matrix_constants,
            histogram_bins,
            plotter,
            chunk_size=chunk_size,
        )
        corrected_hist, corrected_error = apply_corrections(hist, args, histogram_bins)
        maybe_update_live_plot(
            plotter,
            corrected_hist,
            corrected_error,
            args.live_plot_refresh_every,
            1,
            force=True,
        )
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print(f"Error reading stream: {exc}", file=sys.stderr)
        return 1
    finally:
        if plotter is not None:
            plotter.close()

    if args.histogram_output_csv is not None:
        try:
            write_histogram_csv(
                corrected_hist,
                corrected_error,
                args.histogram_output_csv,
                args.histogram_q_bin_size,
            )
        except OSError as exc:
            print(f"Error writing histogram output: {exc}", file=sys.stderr)
            return 1

    print(f"Packets read         : {packet_count}")
    print(f"Total events         : {total_events}")
    print(f"Histogrammed events  : {histogram_events}")
    if args.background_subtraction is not None:
        print(f"Background CSV       : {Path(args.background_subtraction).resolve()}")
    if args.normalization is not None:
        print(f"Normalization CSV    : {Path(args.normalization).resolve()}")
    print(f"Histogram bins       : {histogram_bins}")
    print(f"Histogram Q bin size : {args.histogram_q_bin_size}")
    print(f"Histogram Q max      : {args.histogram_q_max}")
    print(f"TOF tick size (us)   : {args.tof_tick_us}")
    if args.histogram_output_csv is not None:
        print(f"Histogram CSV        : {Path(args.histogram_output_csv).resolve()}")
    return 0


def run_from_namespace(args: argparse.Namespace) -> int:
    """Execute the analyze command from a parsed argument namespace."""
    try:
        reader = build_reader(args)
    except (ValueError, OSError) as exc:
        print(f"Error creating data reader: {exc}", file=sys.stderr)
        return 1

    runner = create_source_runner(args)
    if args.histogram_pixel_geometry_csv is not None:
        return _run_histogram_mode(reader, args)

    try:
        return runner.run_basic_mode(reader)
    except OSError as exc:
        print(f"Error reading stream: {exc}", file=sys.stderr)
        return 1
