"""Top-level analyzer orchestration for histogram and basic modes."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import nexus
from .factory import build_reader, create_source_runner
from .histogram import apply_corrections, load_pixel_q_conversion, validate_histogram_args, write_histogram_csv
from .live_plot import HistogramPlotter, create_live_histogram_plotter, maybe_update_live_plot

LOGGER = logging.getLogger(__name__)


def _configure_logging(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _run_histogram_mode(reader, args: argparse.Namespace) -> int:
    plotter: HistogramPlotter | None = None
    try:
        LOGGER.info("Starting histogram analysis")
        histogram_bins = validate_histogram_args(args)
        LOGGER.info(
            "Validated histogram settings: q_min=%s q_max=%s q_bin_size=%s bins=%s tof_tick_us=%s",
            args.histogram_q_min,
            args.histogram_q_max,
            args.histogram_q_bin_size,
            histogram_bins,
            args.tof_tick_us,
        )
        LOGGER.info("Loading pixel geometry CSV from %s", Path(args.histogram_pixel_geometry_csv).resolve())
        q_conversion = load_pixel_q_conversion(args.histogram_pixel_geometry_csv)
        LOGGER.info("Loaded pixel geometry for %s detector ids", len(q_conversion.q_matrix_constants))
        if args.background_subtraction is not None:
            LOGGER.info("Background subtraction enabled: %s", Path(args.background_subtraction).resolve())
        if args.normalization is not None:
            LOGGER.info("Normalization enabled: %s", Path(args.normalization).resolve())
        if args.histogram_output_csv is not None:
            LOGGER.info("Histogram CSV output will be written to %s", Path(args.histogram_output_csv).resolve())
        LOGGER.info("Live plot mode: %s", args.live_plot_mode or "disabled")
        LOGGER.info("Event progress log interval set to %s histogrammed events", args.event_log_interval)
        plotter = create_live_histogram_plotter(args, histogram_bins)
        runner = create_source_runner(args)
        chunk_size = nexus.DEFAULT_NEXUS_CHUNK_SIZE
        if args.adara_file is not None:
            LOGGER.info("Using ADARA file source: %s", Path(args.adara_file).resolve())
        elif args.nexus_file is not None:
            LOGGER.info("Using %s NeXus file(s)", len(args.nexus_file))
        elif args.adara_stream is not None:
            LOGGER.info("Using ADARA live stream source: %s:%s", args.adara_stream[0], args.adara_stream[1])
        LOGGER.info("Beginning event accumulation")
        packet_count, total_events, histogram_events, hist = runner.accumulate_histogram(
            reader,
            args,
            q_conversion,
            histogram_bins,
            plotter,
            chunk_size=chunk_size,
        )
        LOGGER.info(
            "Finished event accumulation: packets_or_groups=%s total_events=%s histogrammed_events=%s",
            packet_count,
            total_events,
            histogram_events,
        )
        LOGGER.info("Applying background subtraction and normalization corrections")
        corrected_hist, corrected_error = apply_corrections(hist, args, histogram_bins)
        maybe_update_live_plot(
            plotter,
            corrected_hist,
            corrected_error,
            args.live_plot_refresh_every,
            1,
            force=True,
        )
        LOGGER.info("Final histogram update complete")
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
            LOGGER.info("Writing histogram CSV to %s", Path(args.histogram_output_csv).resolve())
            write_histogram_csv(
                corrected_hist,
                corrected_error,
                args.histogram_output_csv,
                args.histogram_q_bin_size,
                args.histogram_q_min,
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
    print(f"Histogram Q min      : {args.histogram_q_min}")
    print(f"Histogram Q bin size : {args.histogram_q_bin_size}")
    print(f"Histogram Q max      : {args.histogram_q_max}")
    print(f"TOF tick size (us)   : {args.tof_tick_us}")
    if args.histogram_output_csv is not None:
        print(f"Histogram CSV        : {Path(args.histogram_output_csv).resolve()}")
    return 0


def run_from_namespace(args: argparse.Namespace) -> int:
    """Execute the analyze command from a parsed argument namespace."""
    _configure_logging(args)
    try:
        LOGGER.info("Creating data reader")
        reader = build_reader(args)
    except (ValueError, OSError) as exc:
        print(f"Error creating data reader: {exc}", file=sys.stderr)
        return 1

    runner = create_source_runner(args)
    if args.histogram_pixel_geometry_csv is not None:
        return _run_histogram_mode(reader, args)

    try:
        LOGGER.info("Running analyze command in basic mode")
        return runner.run_basic_mode(reader, chunk_size=nexus.DEFAULT_NEXUS_CHUNK_SIZE)
    except OSError as exc:
        print(f"Error reading stream: {exc}", file=sys.stderr)
        return 1
