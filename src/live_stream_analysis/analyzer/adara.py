"""ADARA-specific reader construction and histogram accumulation."""

from __future__ import annotations

import argparse
import logging
import math

try:
    from readadara import AdaraFileReader, AdaraLiveStreamReader
except ImportError:  # pragma: no cover
    AdaraFileReader = None
    AdaraLiveStreamReader = None

from .histogram import PixelQConversion, pixel_tof_to_q
from .live_plot import HistogramPlotter, maybe_update_live_plot

LOGGER = logging.getLogger(__name__)


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
    q_conversion: PixelQConversion,
    histogram_bins: int,
    histogram_q_min: float,
    histogram_q_bin_size: float,
    tof_tick_us: float,
    plotter: HistogramPlotter,
    live_plot_refresh_every: int,
    event_log_interval: int,
) -> tuple[int, int, int, list[int]]:
    packet_count = 0
    total_events = 0
    histogram_events = 0
    hist = [0] * histogram_bins
    event_log_interval = max(1, event_log_interval)
    next_event_log = event_log_interval

    for packet in reader.read_generator():
        packet_count += 1
        events = packet.get_events()
        total_events += len(events)

        for pixel_id, tof in events:
            q = pixel_tof_to_q(q_conversion, pixel_id, float(tof) * tof_tick_us)
            if q is None:
                continue
            bram_index = int((q - histogram_q_min) / histogram_q_bin_size)
            if 0 <= bram_index < histogram_bins:
                hist[bram_index] += 1
                histogram_events += 1
                if histogram_events >= next_event_log:
                    LOGGER.info(
                        "Histogrammed %s events after %s packets (%s total source events)",
                        histogram_events,
                        packet_count,
                        total_events,
                    )
                    next_event_log += event_log_interval

        maybe_update_live_plot(
            plotter,
            hist,
            [math.sqrt(float(value)) for value in hist],
            live_plot_refresh_every,
            packet_count,
        )

    return packet_count, total_events, histogram_events, hist


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


__all__ = ["accumulate_adara_histogram", "build_reader", "run_basic_mode"]
