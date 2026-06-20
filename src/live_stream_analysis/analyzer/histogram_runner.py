"""Top-level analyzer orchestration for histogram and basic modes."""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from pathlib import Path

from . import nexus
from .factory import build_reader, create_source_runner
from .histogram import apply_corrections, load_pixel_q_conversion, validate_histogram_args, write_histogram_csv
from ..intersect import (
    build_histogram_payload,
    build_run_complete_payload,
    create_event_publisher,
    infer_run_metadata,
    load_intersect_config,
)
from ..intersect.service import HistogramRuntimeState
from .live_plot import HistogramPlotter, create_live_histogram_plotter, maybe_update_live_plot

LOGGER = logging.getLogger(__name__)


def _should_keep_live_plot_open(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "live_plot_mode", None) == "browser" and getattr(args, "live_plot_keep_open", False))


def _configure_logging(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _run_histogram_mode(reader, args: argparse.Namespace) -> int:
    plotter: HistogramPlotter | None = None
    publisher = None
    corrected_hist: list[float] = []
    corrected_error: list[float] = []
    active_hist: list[int] | None = None
    next_snapshot_event_count = max(1, args.histogram_snapshot_every) if args.histogram_snapshot_every > 0 else None
    adara_stats = None
    packet_count = 0
    total_events = 0
    histogram_events = 0
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
        runtime_state = HistogramRuntimeState(pixel_q_conversion=q_conversion)
        runtime_state.correction_bins = histogram_bins
        if args.adara_file_delay_intersect:
            runtime_state.configure_adara_file_read_gate(False)
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
        intersect_config = None
        last_intersect_publish_at: float | None = None
        if args.enable_intersect:
            intersect_config = load_intersect_config(args.intersect_config)
            publisher = create_event_publisher(intersect_config, runtime_state=runtime_state)
        if args.adara_file is not None:
            LOGGER.info("Using ADARA file source: %s", Path(args.adara_file).resolve())
        elif args.nexus_file is not None:
            LOGGER.info("Using %s NeXus file(s)", len(args.nexus_file))
        elif args.adara_stream is not None:
            LOGGER.info("Using ADARA live stream source: %s:%s", args.adara_stream[0], args.adara_stream[1])
        if args.adara_file is not None and args.adara_file_delay_read > 0:
            LOGGER.info("Delaying ADARA file read for %s seconds", args.adara_file_delay_read)
            time.sleep(args.adara_file_delay_read)
        if args.adara_file is not None and args.adara_file_delay_intersect:
            LOGGER.info("Waiting for INTERSECT start_adara_file_read signal before reading ADARA file")
            runtime_state.wait_for_adara_file_read_release()
            LOGGER.info("Received INTERSECT start_adara_file_read signal")
        LOGGER.info("Beginning event accumulation")

        def _publish_histogram_snapshot(histogram_event_count: int, hist: list[int]) -> None:
            nonlocal last_intersect_publish_at, next_snapshot_event_count
            if publisher is None or intersect_config is None:
                pass
            else:
                _ = histogram_event_count
                now = time.monotonic()
                interval = max(1, intersect_config.publish_interval_seconds)
                if last_intersect_publish_at is None or (now - last_intersect_publish_at) >= interval:
                    last_intersect_publish_at = now
                    q_values = [args.histogram_q_min + (index * args.histogram_q_bin_size) for index in range(len(hist))]
                    errors = [math.sqrt(float(value)) for value in hist]
                    payload = build_histogram_payload(q_values, [float(value) for value in hist], errors)
                    LOGGER.info(
                        "Publishing histogram_updated event: bins=%d first_q=%.3f last_q=%.3f total_counts=%.0f",
                        len(hist),
                        q_values[0] if q_values else float("nan"),
                        q_values[-1] if q_values else float("nan"),
                        float(sum(hist)),
                    )
                    publisher.publish_event(intersect_config.histogram_event_name, payload)

            if (
                next_snapshot_event_count is not None
                and args.histogram_output_csv is not None
                and histogram_event_count >= next_snapshot_event_count
            ):
                snapshot_hist, snapshot_error = apply_corrections(hist, args, histogram_bins, runtime_state=runtime_state)
                write_histogram_csv(
                    snapshot_hist,
                    snapshot_error,
                    args.histogram_output_csv,
                    args.histogram_q_bin_size,
                    args.histogram_q_min,
                )
                LOGGER.info(
                    "Wrote histogram snapshot CSV after %s histogrammed events to %s",
                    histogram_event_count,
                    Path(args.histogram_output_csv).resolve(),
                )
                next_snapshot_event_count += args.histogram_snapshot_every

        def _finalize_run(hist: list[int]) -> tuple[list[float], list[float]]:
            final_hist, final_error = apply_corrections(hist, args, histogram_bins, runtime_state=runtime_state)
            maybe_update_live_plot(
                plotter,
                final_hist,
                final_error,
                args.live_plot_refresh_every,
                1,
                force=True,
            )
            if publisher is not None and intersect_config is not None:
                q_values = [args.histogram_q_min + (index * args.histogram_q_bin_size) for index in range(len(final_hist))]
                publisher.publish_event(
                    intersect_config.histogram_event_name,
                    build_histogram_payload(q_values, final_hist, final_error),
                )
                LOGGER.info(
                    "Publishing INTERSECT run-complete event '%s'",
                    intersect_config.run_complete_event_name,
                )
                publisher.publish_event(
                    intersect_config.run_complete_event_name,
                    build_run_complete_payload(infer_run_metadata(args)),
                )
            return final_hist, final_error

        run_completion_handled = False

        def _handle_run_complete(_packet) -> None:
            nonlocal corrected_hist, corrected_error, run_completion_handled
            if run_completion_handled:
                return
            if active_hist is None:
                return
            corrected_hist, corrected_error = _finalize_run(active_hist)
            run_completion_handled = True
            active_hist[:] = [0] * len(active_hist)

        def _set_active_hist(hist: list[int]) -> None:
            nonlocal active_hist
            active_hist = hist

        reconnect_attempts = 0
        while True:
            accumulation_result = runner.accumulate_histogram(
                reader,
                args,
                q_conversion,
                histogram_bins,
                plotter,
                chunk_size=chunk_size,
                q_conversion_provider=lambda: runtime_state.pixel_q_conversion,
                histogram_callback=_publish_histogram_snapshot,
                run_complete_callback=_handle_run_complete,
                histogram_state_callback=_set_active_hist,
            )
            if len(accumulation_result) == 5:
                packet_count, total_events, histogram_events, hist, adara_stats = accumulation_result
            elif len(accumulation_result) == 4:
                packet_count, total_events, histogram_events, hist = accumulation_result
                adara_stats = None
            else:
                raise ValueError(
                    "accumulate_histogram() must return 4 or 5 values "
                    f"(got {len(accumulation_result)})"
                )

            if args.adara_stream is None or run_completion_handled:
                break

            reconnect_attempts += 1
            max_reconnects = args.adara_stream_max_reconnects
            if max_reconnects >= 0 and reconnect_attempts > max_reconnects:
                raise OSError(
                    "ADARA live stream ended before END_RUN and reconnect limit was reached"
                )

            LOGGER.warning(
                "ADARA live stream disconnected before END_RUN after %s packets (%s total source events); reconnect attempt %s",
                packet_count,
                total_events,
                reconnect_attempts,
            )
            time.sleep(max(0.0, args.adara_stream_reconnect_delay))
            reader = build_reader(args)

        active_hist = hist
        LOGGER.info(
            "Finished event accumulation: packets_or_groups=%s total_events=%s histogrammed_events=%s",
            packet_count,
            total_events,
            histogram_events,
        )
        LOGGER.info("Applying background subtraction and normalization corrections")
        if any(hist) and not run_completion_handled:
            corrected_hist, corrected_error = _finalize_run(hist)
        else:
            corrected_hist = [0.0] * histogram_bins
            corrected_error = [0.0] * histogram_bins
        LOGGER.info("Final histogram update complete")
        if plotter is not None and _should_keep_live_plot_open(args):
            LOGGER.info("Keeping browser live plot available at %s until interrupted", getattr(plotter, "url", "configured host/port"))
            plotter.wait_until_closed()
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print(f"Error reading stream: {exc}", file=sys.stderr)
        return 1
    finally:
        if plotter is not None:
            plotter.close()
        if publisher is not None:
            publisher.close()

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
    if adara_stats is not None:
        print(f"Skipped non-banked   : {adara_stats.skipped_non_banked_packets}")
        print(f"Skipped bad pixel    : {adara_stats.skipped_invalid_pixel_ids}")
        print(f"Skipped bad tof      : {adara_stats.skipped_zero_or_negative_tof}")
        print(f"Skipped masked pixel : {adara_stats.skipped_masked_pixels}")
        print(f"Skipped no Q         : {adara_stats.skipped_unconvertible_events}")
        print(f"Skipped out-of-range : {adara_stats.skipped_out_of_range_bins}")
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
