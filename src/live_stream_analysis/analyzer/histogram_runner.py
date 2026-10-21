"""Top-level analyzer orchestration for histogram and basic modes."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..correction_totals import load_correction_totals
from ..intersect import (
    IntersectConfig,
    build_histogram_payload,
    build_run_complete_payload,
    create_event_publisher,
    infer_run_metadata,
    load_intersect_config,
)
from ..intersect.service import EventPublisher, HistogramRuntimeState
from ..statistics import (
    StatisticsSample,
    StatisticsWindow,
    write_statistics_csv,
    write_statistics_map_csv,
)
from . import nexus
from .adara import AdaraHistogramStats
from .factory import build_reader, create_source_runner
from .histogram import (
    apply_corrections,
    bin_centre_q_values,
    compute_relative_uncertainty,
    load_pixel_q_conversion,
    preload_corrections,
    validate_histogram_args,
    write_histogram_csv,
)
from .live_plot import HistogramPlotter, create_live_histogram_plotter, maybe_update_live_plot

LOGGER = logging.getLogger(__name__)

# Socket failures that mean "the live stream went away" rather than "the analysis is broken".
# ConnectionError covers reset/aborted/refused/broken-pipe. These must route into the reconnect
# path; letting them reach the top-level OSError handler kills the analyzer on a network blip.
STREAM_DROP_ERRORS = (ConnectionError, TimeoutError)


def _should_keep_live_plot_open(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "live_plot_mode", None) == "browser" and getattr(args, "live_plot_keep_open", False))


def _write_histogram_output(
    corrected_hist: list[float],
    corrected_error: list[float],
    args: argparse.Namespace,
) -> bool:
    """Write the histogram CSV, returning False if it could not be written."""
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
        return False
    return True


def _warn_if_correction_lacks_totals(csv_path: str, kind: str, normalize_by: str) -> None:
    """Flag a correction that will not be put on the sample's per-exposure scale.

    Without its own totals a background is subtracted as though it were measured for exactly
    as long as the sample, which silently skews the result whenever the two exposures differ.
    """
    if normalize_by == "none":
        return
    totals = load_correction_totals(csv_path)
    if totals.divisor(normalize_by) is None:
        LOGGER.warning(
            "%s CSV %s records no %s total; it will not be normalized by its own exposure. "
            "Regenerate it with the preparer to include one.",
            kind,
            Path(csv_path).resolve(),
            "proton charge" if normalize_by == "proton-charge" else "duration",
        )
    else:
        LOGGER.info("%s CSV exposure totals: %s", kind, totals)


def _configure_logging(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


@dataclass
class _AccumulationCounts:
    packets: int = 0
    total_events: int = 0
    histogram_events: int = 0

    def __iadd__(self, other: _AccumulationCounts) -> _AccumulationCounts:
        self.packets += other.packets
        self.total_events += other.total_events
        self.histogram_events += other.histogram_events
        return self


@dataclass
class _HistogramSession:
    """State the source runner's callbacks share over one analyzer invocation."""

    args: argparse.Namespace
    histogram_bins: int
    q_values: list[float]
    statistics_window: StatisticsWindow
    runtime_state: HistogramRuntimeState
    plotter: HistogramPlotter | None
    publisher: EventPublisher | None
    intersect_config: IntersectConfig | None
    active_hist: list[int] | None = field(default=None, init=False)
    run_completion_handled: bool = field(default=False, init=False)
    _last_intersect_publish_at: float | None = field(default=None, init=False)
    _next_snapshot_event_count: int | None = field(init=False)

    def __post_init__(self) -> None:
        snapshot_every = self.args.histogram_snapshot_every
        self._next_snapshot_event_count = max(1, snapshot_every) if snapshot_every > 0 else None

    def corrected(self, hist: list[int]) -> tuple[list[float], list[float]]:
        return apply_corrections(hist, self.args, self.histogram_bins, runtime_state=self.runtime_state)

    def accumulate(self, runner, reader):
        return runner.accumulate_histogram(
            reader,
            self.args,
            self.runtime_state.pixel_q_conversion,
            self.histogram_bins,
            self.plotter,
            chunk_size=nexus.DEFAULT_NEXUS_CHUNK_SIZE,
            q_conversion_provider=lambda: self.runtime_state.pixel_q_conversion,
            histogram_callback=self.publish_snapshot,
            run_complete_callback=self.handle_run_complete,
            histogram_state_callback=self.set_active_hist,
            hist=self.active_hist,
            run_statistics=self.runtime_state.run_statistics,
            # The plot shows the same corrected, exposure-normalized I(Q) as the CSV and
            # INTERSECT events, rather than raw counts that only ever grow.
            display_transform=self.corrected,
        )

    def record_statistics_sample(self, histogram_event_count: int, hist: list[int], *, force: bool = False) -> None:
        """Append a point to the error-decay series if enough run time has accumulated.

        The end of a run almost never lands on a sample boundary, so finalizing forces a
        last point. Without it the series stops short of the totals it actually reached and
        the final histogram gets published alongside a decay point from slightly earlier.
        """
        accumulator = self.runtime_state.run_statistics.accumulator
        series = self.runtime_state.run_statistics.series
        if not force and not series.is_due(accumulator.elapsed_seconds):
            return

        intensity, error = self.corrected(hist)
        relative_uncertainty = compute_relative_uncertainty(intensity, error)
        mean_relative_uncertainty, profile = self.statistics_window.summarize(relative_uncertainty)
        if mean_relative_uncertainty == 0.0:
            # Nothing populated in the window yet. Recording this would put a "zero error at
            # zero charge" point at the head of the decay curve, which reads as perfect data
            # rather than no data and is exactly backwards for anything fitting the series.
            return

        sample = StatisticsSample(
            elapsed_seconds=accumulator.elapsed_seconds,
            proton_charge_pc=accumulator.proton_charge_pc,
            mean_relative_uncertainty=mean_relative_uncertainty,
            total_counts=float(sum(intensity)),
            nonzero_bins=sum(1 for value in intensity if value != 0.0),
            histogram_events=histogram_event_count,
            relative_uncertainty_profile=profile,
        )
        series.record(sample)
        if self.args.statistics_output_csv is not None:
            write_statistics_csv(series.samples(), self.args.statistics_output_csv)
        if self.args.statistics_map_output_csv is not None:
            write_statistics_map_csv(series.snapshot(), self.args.statistics_map_output_csv)

    def publish_snapshot(self, histogram_event_count: int, hist: list[int]) -> None:
        self.record_statistics_sample(histogram_event_count, hist)
        self._maybe_publish_histogram_update(hist)
        self._maybe_write_snapshot_csv(histogram_event_count, hist)

    def _maybe_publish_histogram_update(self, hist: list[int]) -> None:
        if self.publisher is None or self.intersect_config is None:
            return
        now = time.monotonic()
        interval = max(1, self.intersect_config.publish_interval_seconds)
        if self._last_intersect_publish_at is not None and (now - self._last_intersect_publish_at) < interval:
            return
        self._last_intersect_publish_at = now
        # Corrected, not raw counts. The run-complete event publishes corrected values, so
        # publishing raw ones here would make a subscriber's series jump by the whole
        # correction factor at the end of the run -- a factor of ~1e12 once normalization by
        # proton charge is applied.
        intensity, errors = self.corrected(hist)
        payload = build_histogram_payload(
            self.q_values,
            intensity,
            errors,
            self.runtime_state.run_statistics.series.latest(),
        )
        LOGGER.info(
            "Publishing histogram_updated event: bins=%d first_q=%.3f last_q=%.3f total_counts=%.6g",
            len(intensity),
            self.q_values[0] if self.q_values else float("nan"),
            self.q_values[-1] if self.q_values else float("nan"),
            float(sum(intensity)),
        )
        self.publisher.publish_event(self.intersect_config.histogram_event_name, payload)

    def _maybe_write_snapshot_csv(self, histogram_event_count: int, hist: list[int]) -> None:
        if (
            self._next_snapshot_event_count is None
            or self.args.histogram_output_csv is None
            or histogram_event_count < self._next_snapshot_event_count
        ):
            return
        snapshot_hist, snapshot_error = self.corrected(hist)
        write_histogram_csv(
            snapshot_hist,
            snapshot_error,
            self.args.histogram_output_csv,
            self.args.histogram_q_bin_size,
            self.args.histogram_q_min,
        )
        LOGGER.info(
            "Wrote histogram snapshot CSV after %s histogrammed events to %s",
            histogram_event_count,
            Path(self.args.histogram_output_csv).resolve(),
        )
        self._next_snapshot_event_count += self.args.histogram_snapshot_every

    def finalize_run(self, hist: list[int]) -> tuple[list[float], list[float]]:
        LOGGER.info(
            "Finalizing histogram: bins=%s total_counts=%.0f run_completion_handled=%s",
            len(hist),
            float(sum(hist)),
            self.run_completion_handled,
        )
        final_hist, final_error = self.corrected(hist)
        # Close the decay series on the totals this run actually reached, before the plot
        # and the INTERSECT event below read it. Every histogrammed event increments exactly
        # one bin, so the raw histogram sums to this run's accepted-event count -- the
        # caller's counters are still stale here when finalizing from the END_RUN callback.
        self.record_statistics_sample(int(sum(hist)), hist, force=True)
        maybe_update_live_plot(
            self.plotter,
            final_hist,
            final_error,
            self.args.live_plot_refresh_every,
            1,
            force=True,
            statistics_series=self.runtime_state.run_statistics.series,
        )
        LOGGER.info(
            "Forced live plot refresh with finalized histogram: bins=%s total_counts=%.0f",
            len(final_hist),
            float(sum(final_hist)),
        )
        self._publish_run_complete(final_hist, final_error)
        return final_hist, final_error

    def _publish_run_complete(self, final_hist: list[float], final_error: list[float]) -> None:
        if self.publisher is None or self.intersect_config is None:
            return
        self.publisher.publish_event(
            self.intersect_config.histogram_event_name,
            build_histogram_payload(
                self.q_values,
                final_hist,
                final_error,
                self.runtime_state.run_statistics.series.latest(),
            ),
        )
        LOGGER.info(
            "Publishing INTERSECT run-complete event '%s'",
            self.intersect_config.run_complete_event_name,
        )
        self.publisher.publish_event(
            self.intersect_config.run_complete_event_name,
            build_run_complete_payload(infer_run_metadata(self.args)),
        )

    def handle_run_complete(self, _packet) -> None:
        if self.run_completion_handled:
            LOGGER.info("Ignoring duplicate run-complete callback because run_completion_handled is already true")
            return
        if self.active_hist is None:
            LOGGER.warning("Received run-complete callback but active_hist is not set")
            return
        LOGGER.info("Received run-complete callback; finalizing active histogram before continuing")
        self.finalize_run(self.active_hist)
        self.run_completion_handled = True
        LOGGER.info(
            "Run-complete callback finished; run_completion_handled=%s active_hist_total_before_reset=%.0f",
            self.run_completion_handled,
            float(sum(self.active_hist)),
        )
        self.active_hist[:] = [0] * len(self.active_hist)

    def set_active_hist(self, hist: list[int]) -> None:
        self.active_hist = hist

    def finish(self, hist: list[int]) -> tuple[list[float], list[float]]:
        """Correct whatever the last accumulation left behind."""
        LOGGER.info("Applying background subtraction and normalization corrections")
        # handle_run_complete already zeroes the histogram, so anything left here belongs to a
        # run that started after the last boundary and still needs finalizing.
        if any(hist):
            return self.finalize_run(hist)
        return [0.0] * self.histogram_bins, [0.0] * self.histogram_bins


def _prepare_runtime_state(
    args: argparse.Namespace,
) -> tuple[int, list[float], StatisticsWindow, HistogramRuntimeState]:
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
    q_values = bin_centre_q_values(args.histogram_q_min, args.histogram_q_bin_size, histogram_bins)
    statistics_window = StatisticsWindow.build(q_values, args.statistics_q_min, args.statistics_q_max)
    runtime_state.run_statistics.series.configure(args.statistics_sample_interval_seconds, statistics_window)
    LOGGER.info(
        "Error-decay statistics window: bins=%s map_columns=%s q_min=%s q_max=%s",
        len(statistics_window.indices),
        len(statistics_window.map_q_values),
        args.statistics_q_min,
        args.statistics_q_max,
    )
    if args.adara_file_delay_intersect:
        runtime_state.configure_adara_file_read_gate(False)
    LOGGER.info("Loaded pixel geometry for %s detector ids", len(q_conversion.q_matrix_constants))
    if args.background_subtraction is not None:
        LOGGER.info("Background subtraction enabled: %s", Path(args.background_subtraction).resolve())
        _warn_if_correction_lacks_totals(args.background_subtraction, "Background", args.normalize_by)
    if args.normalization is not None:
        LOGGER.info("Normalization enabled: %s", Path(args.normalization).resolve())
        _warn_if_correction_lacks_totals(args.normalization, "Normalization", args.normalize_by)
    preload_corrections(runtime_state, args, histogram_bins)
    if args.histogram_output_csv is not None:
        LOGGER.info("Histogram CSV output will be written to %s", Path(args.histogram_output_csv).resolve())
    LOGGER.info("Live plot mode: %s", args.live_plot_mode or "disabled")
    LOGGER.info("Event progress log interval set to %s histogrammed events", args.event_log_interval)
    return histogram_bins, q_values, statistics_window, runtime_state


def _wait_for_source(args: argparse.Namespace, runtime_state: HistogramRuntimeState) -> None:
    """Log which source is being read and hold off reading it until it is due."""
    if args.adara_file is not None:
        LOGGER.info("Using ADARA file source: %s", Path(args.adara_file).resolve())
    elif args.nexus_file is not None:
        LOGGER.info("Using %s NeXus file(s)", len(args.nexus_file))
    elif args.adara_stream is not None:
        LOGGER.info("Using ADARA live stream source: %s:%s", args.adara_stream[0], args.adara_stream[1])
    if args.adara_file is None:
        return
    if args.adara_file_delay_read > 0:
        LOGGER.info("Delaying ADARA file read for %s seconds", args.adara_file_delay_read)
        time.sleep(args.adara_file_delay_read)
    if args.adara_file_delay_intersect:
        LOGGER.info("Waiting for INTERSECT start_adara_file_read signal before reading ADARA file")
        runtime_state.wait_for_adara_file_read_release()
        LOGGER.info("Received INTERSECT start_adara_file_read signal")


def _accumulate_once(session: _HistogramSession, runner, reader):
    try:
        return session.accumulate(runner, reader)
    except STREAM_DROP_ERRORS as exc:
        if session.args.adara_stream is None:
            raise
        # A reset or timeout part-way through a read means the stream went away, not that the
        # analysis failed. Return to the reconnect path with whatever this attempt accumulated
        # -- active_hist is mutated in place, so the counts collected before the drop survive.
        LOGGER.warning("ADARA live stream dropped mid-read (%s); treating as a disconnect", exc)
        return None


def _unpack_accumulation(
    result, session: _HistogramSession
) -> tuple[_AccumulationCounts, list[int], AdaraHistogramStats | None]:
    if result is None:
        hist = session.active_hist if session.active_hist is not None else [0] * session.histogram_bins
        return _AccumulationCounts(), hist, None
    if len(result) == 5:
        packets, total_events, histogram_events, hist, adara_stats = result
    elif len(result) == 4:
        packets, total_events, histogram_events, hist = result
        adara_stats = None
    else:
        raise ValueError(f"accumulate_histogram() must return 4 or 5 values (got {len(result)})")
    return _AccumulationCounts(packets, total_events, histogram_events), hist, adara_stats


def _reconnect_limit_reached(args: argparse.Namespace, reconnect_attempts: int) -> bool:
    max_reconnects = args.adara_stream_max_reconnects
    return max_reconnects >= 0 and reconnect_attempts > max_reconnects


def _reconnect(args: argparse.Namespace, reconnect_attempts: int) -> tuple[object, int]:
    """Open a fresh live-stream reader, returning it with the updated attempt count."""
    reconnect_delay = max(0.0, args.adara_stream_reconnect_delay)
    LOGGER.info(
        "Sleeping %.3f seconds before reconnecting to ADARA live stream %s:%s",
        reconnect_delay,
        args.adara_stream[0],
        args.adara_stream[1],
    )
    time.sleep(reconnect_delay)
    # Keep retrying the connection itself. If the SMS is restarting, the first few attempts
    # get refused, and giving up there would defeat the point of reconnecting.
    while True:
        LOGGER.info(
            "Reconnecting to ADARA live stream %s:%s (attempt %s)",
            args.adara_stream[0],
            args.adara_stream[1],
            reconnect_attempts,
        )
        try:
            return build_reader(args), reconnect_attempts
        except STREAM_DROP_ERRORS as exc:
            LOGGER.warning("Reconnect attempt %s failed: %s", reconnect_attempts, exc)
            reconnect_attempts += 1
            if _reconnect_limit_reached(args, reconnect_attempts):
                raise OSError("ADARA live stream ended before END_RUN and reconnect limit was reached") from exc
            time.sleep(reconnect_delay)


def _accumulate_until_done(
    session: _HistogramSession, runner, reader
) -> tuple[_AccumulationCounts, list[int], AdaraHistogramStats | None]:
    """Accumulate from the source, reconnecting a live stream until it can no longer continue.

    File sources return after one pass. A live stream only leaves this loop by raising, once
    its reconnect budget is spent.
    """
    args = session.args
    cumulative = _AccumulationCounts()
    reconnect_attempts = 0
    while True:
        result = _accumulate_once(session, runner, reader)
        counts, hist, adara_stats = _unpack_accumulation(result, session)
        cumulative += counts
        LOGGER.info(
            "ADARA accumulate_histogram returned: packets=%s total_events=%s histogrammed_events=%s "
            "run_completion_handled=%s active_hist_is_set=%s active_hist_total=%.0f",
            counts.packets,
            counts.total_events,
            counts.histogram_events,
            session.run_completion_handled,
            session.active_hist is not None,
            float(sum(session.active_hist)) if session.active_hist is not None else 0.0,
        )

        if args.adara_stream is None:
            return counts, hist, adara_stats

        if session.run_completion_handled:
            LOGGER.info("ADARA live stream run completed; histogram state reset and stream will continue")
            session.run_completion_handled = False
            continue

        reconnect_attempts += 1
        if _reconnect_limit_reached(args, reconnect_attempts):
            raise OSError("ADARA live stream ended before END_RUN and reconnect limit was reached")
        LOGGER.warning(
            "ADARA live stream disconnected before END_RUN after %s packets (%s total source events); "
            "reconnect attempt %s; cumulative packets=%s cumulative source events=%s "
            "cumulative histogrammed events=%s",
            counts.packets,
            counts.total_events,
            reconnect_attempts,
            cumulative.packets,
            cumulative.total_events,
            cumulative.histogram_events,
        )
        reader, reconnect_attempts = _reconnect(args, reconnect_attempts)


def _write_final_outputs(
    args: argparse.Namespace,
    runtime_state: HistogramRuntimeState,
    corrected_hist: list[float],
    corrected_error: list[float],
) -> bool:
    """Write the final histogram and statistics CSVs, returning False if the histogram failed."""
    if args.histogram_output_csv is not None and not _write_histogram_output(corrected_hist, corrected_error, args):
        return False
    if args.statistics_output_csv is not None:
        LOGGER.info("Writing statistics CSV to %s", Path(args.statistics_output_csv).resolve())
        write_statistics_csv(runtime_state.run_statistics.series.samples(), args.statistics_output_csv)
    if args.statistics_map_output_csv is not None:
        LOGGER.info("Writing statistics map CSV to %s", Path(args.statistics_map_output_csv).resolve())
        write_statistics_map_csv(runtime_state.run_statistics.series.snapshot(), args.statistics_map_output_csv)
    return True


def _print_adara_stats(adara_stats: AdaraHistogramStats) -> None:
    if adara_stats.rtdl_packets == 0 and adara_stats.packet_count > 0:
        # Without RTDL packets the charge axis stays flat at zero, which silently makes the
        # decay series unusable for anything plotted or forecast against charge.
        LOGGER.warning(
            "No ADARA RTDL packets seen in %s packets: proton charge is unavailable, so the "
            "error-decay series has a time axis only and --normalize-by proton-charge is a no-op",
            adara_stats.packet_count,
        )
    print(f"Skipped non-banked   : {adara_stats.skipped_non_banked_packets}")
    print(f"Skipped bad pixel    : {adara_stats.skipped_invalid_pixel_ids}")
    print(f"Skipped bad tof      : {adara_stats.skipped_zero_or_negative_tof}")
    print(f"Skipped masked pixel : {adara_stats.skipped_masked_pixels}")
    print(f"Skipped no Q         : {adara_stats.skipped_unconvertible_events}")
    print(f"Skipped out-of-range : {adara_stats.skipped_out_of_range_bins}")


def _print_summary(
    args: argparse.Namespace,
    histogram_bins: int,
    counts: _AccumulationCounts,
    adara_stats: AdaraHistogramStats | None,
) -> None:
    print(f"Packets read         : {counts.packets}")
    print(f"Total events         : {counts.total_events}")
    print(f"Histogrammed events  : {counts.histogram_events}")
    if adara_stats is not None:
        _print_adara_stats(adara_stats)
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


def _run_histogram_mode(reader, args: argparse.Namespace) -> int:
    plotter: HistogramPlotter | None = None
    publisher = None
    try:
        LOGGER.info("Starting histogram analysis")
        histogram_bins, q_values, statistics_window, runtime_state = _prepare_runtime_state(args)
        plotter = create_live_histogram_plotter(args, histogram_bins)
        runner = create_source_runner(args)
        intersect_config = None
        if args.enable_intersect:
            intersect_config = load_intersect_config(args.intersect_config)
            publisher = create_event_publisher(intersect_config, runtime_state=runtime_state)
        session = _HistogramSession(
            args=args,
            histogram_bins=histogram_bins,
            q_values=q_values,
            statistics_window=statistics_window,
            runtime_state=runtime_state,
            plotter=plotter,
            publisher=publisher,
            intersect_config=intersect_config,
        )
        _wait_for_source(args, runtime_state)
        LOGGER.info("Beginning event accumulation")
        counts, hist, adara_stats = _accumulate_until_done(session, runner, reader)
        LOGGER.info(
            "Finished event accumulation: packets_or_groups=%s total_events=%s histogrammed_events=%s",
            counts.packets,
            counts.total_events,
            counts.histogram_events,
        )
        corrected_hist, corrected_error = session.finish(hist)
        LOGGER.info("Final histogram update complete")
        # Write the CSVs before blocking on the live plot. --live-plot-keep-open parks the
        # process until it is interrupted, so deferring the write would leave the caller
        # with no output file for as long as the plot stays up.
        if not _write_final_outputs(args, runtime_state, corrected_hist, corrected_error):
            return 1
        if plotter is not None and _should_keep_live_plot_open(args):
            LOGGER.info(
                "Keeping browser live plot available at %s until interrupted",
                getattr(plotter, "url", "configured host/port"),
            )
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

    _print_summary(args, histogram_bins, counts, adara_stats)
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

    if args.histogram_pixel_geometry_csv is not None:
        return _run_histogram_mode(reader, args)

    try:
        LOGGER.info("Running analyze command in basic mode")
        return create_source_runner(args).run_basic_mode(reader, chunk_size=nexus.DEFAULT_NEXUS_CHUNK_SIZE)
    except OSError as exc:
        print(f"Error reading stream: {exc}", file=sys.stderr)
        return 1
