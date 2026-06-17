"""ADARA-specific reader construction and histogram accumulation."""

from __future__ import annotations

import argparse
import math

from .live_plot import HistogramPlotter, maybe_update_live_plot


def build_reader(args: argparse.Namespace):
    """Construct the appropriate ADARA reader from parsed arguments."""
    from readadara import AdaraFileReader, AdaraLiveStreamReader

    has_file = args.adara_file is not None
    has_nexus = args.nexus_file is not None
    has_stream = args.adara_stream is not None

    if sum((has_file, has_nexus, has_stream)) > 1:
        raise ValueError("Provide exactly one of --adara-file, --nexus-file, or --adara-stream.")

    if has_file:
        return AdaraFileReader(filename=args.adara_file)

    if has_nexus:
        return args.nexus_file

    if has_stream:
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
    q_matrix_constants: list[float],
    histogram_bins: int,
    histogram_q_bin_size: float,
    tof_tick_us: float,
    plotter: HistogramPlotter,
    live_plot_refresh_every: int,
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

        for pixel_id, tof in events:
            if pixel_id < 0 or pixel_id >= len(q_matrix_constants):
                continue
            if tof <= 0:
                continue

            q = (q_matrix_constants[pixel_id] * inv_tof_tick_us) / tof
            bram_index = int(q / histogram_q_bin_size)
            if 0 <= bram_index < histogram_bins:
                hist[bram_index] += 1
                histogram_events += 1

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
