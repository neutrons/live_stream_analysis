from __future__ import annotations

import math
import logging
import sys
from pathlib import Path

import h5py

from .histogram import PixelQConversion, pixel_tof_to_q
from .live_plot import HistogramPlotter, maybe_update_live_plot

DEFAULT_NEXUS_CHUNK_SIZE = 250_000
LOGGER = logging.getLogger(__name__)


def count_nexus_chunks(nexus_files: list[str], chunk_size: int) -> int:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    total_chunks = 0
    for nexus_file in nexus_files:
        with h5py.File(nexus_file, "r") as handle:
            for group in iter_nexus_event_groups(handle):
                event_count = int(group["event_id"].shape[0])
                total_chunks += max(1, math.ceil(event_count / chunk_size))
    return total_chunks


def print_nexus_progress(processed_chunks: int, total_chunks: int, current_file: str) -> None:
    if total_chunks <= 0:
        return
    percent = (processed_chunks / total_chunks) * 100.0
    print(
        f"\rProcessing NeXus chunks: {processed_chunks}/{total_chunks} ({percent:5.1f}%) [{Path(current_file).name}]",
        end="",
        file=sys.stderr,
        flush=True,
    )


def finish_nexus_progress(total_chunks: int) -> None:
    if total_chunks > 0:
        print(file=sys.stderr, flush=True)


def iter_nexus_event_groups(handle: h5py.File):
    entry = handle["entry"]
    for name in sorted(entry.keys()):
        if name.endswith("_events"):
            yield entry[name]


def iter_nexus_event_chunks(group: h5py.Group, chunk_size: int):
    event_ids = group["event_id"]
    event_tof = group["event_time_offset"]
    event_count = int(event_ids.shape[0])

    if event_tof.shape[0] != event_count:
        raise ValueError("NeXus event_id and event_time_offset datasets must have the same length")

    for start in range(0, event_count, chunk_size):
        stop = min(start + chunk_size, event_count)
        yield event_ids[start:stop], event_tof[start:stop]


def accumulate_nexus_histogram(
    nexus_files: list[str],
    q_conversion: PixelQConversion | None,
    histogram_bins: int,
    histogram_q_min: float,
    histogram_q_bin_size: float,
    tof_tick_us: float,
    plotter: HistogramPlotter,
    live_plot_refresh_every: int,
    event_log_interval: int,
    *,
    chunk_size: int = DEFAULT_NEXUS_CHUNK_SIZE,
    q_conversion_provider=None,
    histogram_callback=None,
) -> tuple[int, int, int, list[int], None]:
    packet_count = 0
    total_events = 0
    histogram_events = 0
    hist = [0] * histogram_bins
    total_chunks = count_nexus_chunks(nexus_files, chunk_size)
    processed_chunks = 0
    event_log_interval = max(1, event_log_interval)
    next_event_log = event_log_interval

    for nexus_file in nexus_files:
        with h5py.File(nexus_file, "r") as handle:
            for group in iter_nexus_event_groups(handle):
                packet_count += 1
                total_events += int(group["event_id"].shape[0])

                for event_ids, event_tof in iter_nexus_event_chunks(group, chunk_size):
                    active_q_conversion = q_conversion_provider() if q_conversion_provider is not None else q_conversion
                    if active_q_conversion is None:
                        processed_chunks += 1
                        print_nexus_progress(processed_chunks, total_chunks, nexus_file)
                        maybe_update_live_plot(
                            plotter,
                            hist,
                            [math.sqrt(float(value)) for value in hist],
                            live_plot_refresh_every,
                            processed_chunks,
                        )
                        if histogram_callback is not None:
                            histogram_callback(histogram_events, hist)
                        continue
                    for pixel_id, tof in zip(event_ids, event_tof, strict=True):
                        q = pixel_tof_to_q(active_q_conversion, pixel_id, float(tof) * tof_tick_us)
                        if q is None:
                            continue
                        bram_index = int((q - histogram_q_min) / histogram_q_bin_size)
                        if 0 <= bram_index < histogram_bins:
                            hist[bram_index] += 1
                            histogram_events += 1
                            if histogram_events >= next_event_log:
                                LOGGER.info(
                                    "Histogrammed %s events after %s NeXus chunks (%s total source events)",
                                    histogram_events,
                                    processed_chunks + 1,
                                    total_events,
                                )
                                next_event_log += event_log_interval

                    processed_chunks += 1
                    print_nexus_progress(processed_chunks, total_chunks, nexus_file)
                    maybe_update_live_plot(
                        plotter,
                        hist,
                        [math.sqrt(float(value)) for value in hist],
                        live_plot_refresh_every,
                        processed_chunks,
                    )
                    if histogram_callback is not None:
                        histogram_callback(histogram_events, hist)

    finish_nexus_progress(total_chunks)

    return packet_count, total_events, histogram_events, hist, None


def run_basic_mode(nexus_files: list[str], *, chunk_size: int = DEFAULT_NEXUS_CHUNK_SIZE) -> int:
    packet_count = 0
    event_count = 0
    total_chunks = count_nexus_chunks(nexus_files, chunk_size)
    processed_chunks = 0

    for nexus_file in nexus_files:
        with h5py.File(nexus_file, "r") as handle:
            for group in iter_nexus_event_groups(handle):
                packet_count += 1
                for event_ids, _ in iter_nexus_event_chunks(group, chunk_size):
                    event_count += int(len(event_ids))
                    processed_chunks += 1
                    print_nexus_progress(processed_chunks, total_chunks, nexus_file)

    finish_nexus_progress(total_chunks)

    print(f"Packets read : {packet_count}")
    print(f"Total events : {event_count}")
    return 0
