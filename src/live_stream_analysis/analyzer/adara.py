"""ADARA-specific reader construction and histogram accumulation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import math

try:
    from readadara import AdaraFileReader, AdaraLiveStreamReader
    from readadara.adara_reader import AdaraRunStatusPacket
except ImportError:  # pragma: no cover
    AdaraFileReader = None
    AdaraLiveStreamReader = None
    AdaraRunStatusPacket = None

from .histogram import PixelQConversion, pixel_tof_to_q
from .live_plot import HistogramPlotter, maybe_update_live_plot

LOGGER = logging.getLogger(__name__)

ADARA_RUN_STATUS_NEW_RUN = 1
ADARA_RUN_STATUS_END_RUN = 4
ADARA_BANKED_EVENT_FORMAT = 0x400001


def _safe_packet_attr(packet, attr: str):
    value = getattr(packet, attr, None)
    if callable(value):
        try:
            return value()
        except Exception:
            return '<error>'
    return value if value is not None else '<missing>'


def _run_status(packet) -> int | None:
    """Return the run status of a genuine ADARA run-status packet, otherwise None.

    The type check matters: other packet types expose get_status() too (AdaraDoublePacket
    reports 1 for some device readings), and treating those as run boundaries wipes the
    live histogram mid-run.
    """
    if AdaraRunStatusPacket is not None and not isinstance(packet, AdaraRunStatusPacket):
        return None
    status_getter = getattr(packet, "get_status", None)
    if status_getter is None:
        return None
    try:
        return status_getter()
    except Exception:
        return None


@dataclass
class AdaraHistogramStats:
    packet_count: int = 0
    total_events: int = 0
    histogram_events: int = 0
    skipped_non_banked_packets: int = 0
    skipped_invalid_pixel_ids: int = 0
    skipped_zero_or_negative_tof: int = 0
    skipped_masked_pixels: int = 0
    skipped_unconvertible_events: int = 0
    skipped_out_of_range_bins: int = 0


def build_reader(args: argparse.Namespace):
    """Construct the appropriate ADARA reader from parsed arguments."""
    has_file = args.adara_file is not None
    has_nexus = args.nexus_file is not None
    has_stream = args.adara_stream is not None

    if sum((has_file, has_nexus, has_stream)) > 1:
        raise ValueError("Provide exactly one of --adara-file, --nexus-file, or --adara-stream.")

    if has_file:
        if AdaraFileReader is None:
            raise ImportError("readadara is required to read ADARA files")
        return AdaraFileReader(filename=args.adara_file)

    if has_nexus:
        return args.nexus_file

    if has_stream:
        if AdaraLiveStreamReader is None:
            raise ImportError("readadara is required to read ADARA live streams")
        hostname, port_str = args.adara_stream
        try:
            port = int(port_str)
        except ValueError as exc:
            raise ValueError(f"PORT must be an integer, got: {port_str!r}") from exc
        return AdaraLiveStreamReader(hostname, port)

    raise ValueError(
        "No data source specified. Use --adara-file FILE, --nexus-file FILE, or --adara-stream HOSTNAME PORT."
    )


def accumulate_adara_histogram(
    reader,
    q_conversion: PixelQConversion | None,
    histogram_bins: int,
    histogram_q_min: float,
    histogram_q_bin_size: float,
    tof_tick_us: float,
    plotter: HistogramPlotter,
    live_plot_refresh_every: int,
    event_log_interval: int,
    q_conversion_provider=None,
    histogram_callback=None,
    run_complete_callback=None,
    histogram_state_callback=None,
    hist: list[int] | None = None,
) -> tuple[int, int, int, list[int], AdaraHistogramStats]:
    stats = AdaraHistogramStats()
    if hist is None:
        hist = [0] * histogram_bins
    elif len(hist) != histogram_bins:
        raise ValueError("existing histogram length does not match histogram_bins")
    if histogram_q_bin_size <= 0.0:
        raise ValueError("histogram_q_bin_size must be > 0")
    q_index_scale = 1.0 / histogram_q_bin_size
    if histogram_state_callback is not None:
        histogram_state_callback(hist)
    event_log_interval = max(1, event_log_interval)
    next_event_log = event_log_interval

    for packet in reader.read_generator():
        stats.packet_count += 1
        run_status = _run_status(packet)

        if run_status == ADARA_RUN_STATUS_NEW_RUN:
            # A new run is starting. Drop whatever the previous run left behind so the two
            # never blend, but keep reading -- only END_RUN completes a run. Run files open
            # with a NEW_RUN packet, so treating this as a completion would end the read
            # before a single event was seen.
            discarded = sum(hist)
            if discarded:
                LOGGER.info(
                    "Received ADARA NEW_RUN status packet after %s packets; discarding %s counts from the previous run",
                    stats.packet_count,
                    discarded,
                )
            hist[:] = [0] * len(hist)
            maybe_update_live_plot(
                plotter,
                hist,
                [0.0] * len(hist),
                live_plot_refresh_every,
                stats.packet_count,
                force=True,
            )
            if histogram_callback is not None:
                histogram_callback(0, hist)
            continue

        if run_complete_callback is not None and run_status == ADARA_RUN_STATUS_END_RUN:
            maybe_update_live_plot(
                plotter,
                hist,
                [math.sqrt(float(value)) for value in hist],
                live_plot_refresh_every,
                stats.packet_count,
                force=True,
            )
            if histogram_callback is not None:
                histogram_callback(stats.histogram_events, hist)
            LOGGER.info(
                "Received ADARA END_RUN status packet after %s packets (%s total source events): packet_type=%s status=%s run_number=%s run_start=%s file_number=%s",
                stats.packet_count,
                stats.total_events,
                type(packet).__name__,
                _safe_packet_attr(packet, 'get_status'),
                _safe_packet_attr(packet, 'get_run_number'),
                _safe_packet_attr(packet, 'get_run_start'),
                _safe_packet_attr(packet, 'get_file_number'),
            )
            run_complete_callback(packet)
            return stats.packet_count, stats.total_events, stats.histogram_events, hist, stats

        if getattr(packet, "get_format_int", None) is not None:
            if packet.get_format_int() != ADARA_BANKED_EVENT_FORMAT:
                stats.skipped_non_banked_packets += 1
                continue

        events = packet.get_events()
        stats.total_events += len(events)

        for tof, pixel_id in events:

            active_q_conversion = q_conversion_provider() if q_conversion_provider is not None else q_conversion
            if active_q_conversion is None:
                continue

            if pixel_id < 0 or pixel_id >= len(active_q_conversion.q_matrix_constants):
                stats.skipped_invalid_pixel_ids += 1
                continue

            if float(tof) <= 0.0:
                stats.skipped_zero_or_negative_tof += 1
                continue

            if active_q_conversion.use[pixel_id] <= 0:
                stats.skipped_masked_pixels += 1
                continue

            # Share the NeXus conversion so DIFA/DIFC/TZERO calibration is honoured here too.
            q = pixel_tof_to_q(active_q_conversion, pixel_id, float(tof) * tof_tick_us)
            if q is None or q <= 0.0:
                stats.skipped_unconvertible_events += 1
                continue
            bram_index = int((q - histogram_q_min) * q_index_scale)

            if 0 <= bram_index < histogram_bins:
                hist[bram_index] += 1
                stats.histogram_events += 1
                if stats.histogram_events >= next_event_log:
                    LOGGER.info(
                        "Histogrammed %s events after %s packets (%s total source events)",
                        stats.histogram_events,
                        stats.packet_count,
                        stats.total_events,
                    )
                    next_event_log += event_log_interval
            else:
                stats.skipped_out_of_range_bins += 1

        maybe_update_live_plot(
            plotter,
            hist,
            [math.sqrt(float(value)) for value in hist],
            live_plot_refresh_every,
            stats.packet_count,
        )
        if histogram_callback is not None:
            histogram_callback(stats.histogram_events, hist)

    return stats.packet_count, stats.total_events, stats.histogram_events, hist, stats


def run_basic_mode(reader) -> int:
    packet_count = 0
    event_count = 0

    try:
        for packet in reader.read_generator():
            packet_count += 1
            events = packet.get_events()
            event_count += len(events)
    except KeyboardInterrupt:
        pass

    print(f"Packets read : {packet_count}")
    print(f"Total events : {event_count}")
    return 0


__all__ = ["AdaraHistogramStats", "accumulate_adara_histogram", "build_reader", "run_basic_mode"]
