from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import h5py

from ..statistics import RunStatistics
from .histogram import PixelQConversion, pixel_tof_to_q
from .live_plot import DisplayTransform, HistogramPlotter, maybe_update_live_plot

DEFAULT_NEXUS_CHUNK_SIZE = 250_000
LOGGER = logging.getLogger(__name__)


def _read_scalar(handle: h5py.File, name: str) -> float | None:
    dataset = handle["entry"].get(name)
    if dataset is None:
        return None
    value = dataset[()]
    try:
        return float(value[0] if hasattr(value, "__len__") else value)
    except (TypeError, ValueError):
        return None


def read_nexus_run_totals(nexus_files: list[str]) -> tuple[int, float, float]:
    """Return total events, run duration in seconds, and proton charge in pC across files.

    Event NeXus files store these as run-level scalars (entry/duration, entry/proton_charge).
    Per-pulse charge is also available under entry/DASlogs, but the histogram loop iterates
    bank-major -- every bank replays the whole pulse timeline -- so a per-pulse lookup would
    make elapsed time sawtooth back to zero at each bank boundary. Attributing the run totals
    in proportion to events processed keeps the series monotonic instead.
    """
    total_events = 0
    total_duration = 0.0
    total_charge = 0.0
    for nexus_file in nexus_files:
        with h5py.File(nexus_file, "r") as handle:
            for group in iter_nexus_event_groups(handle):
                total_events += int(group["event_id"].shape[0])
            duration = _read_scalar(handle, "duration")
            charge = _read_scalar(handle, "proton_charge")
            if duration is None or charge is None:
                LOGGER.warning(
                    "NeXus file %s is missing entry/duration or entry/proton_charge; "
                    "error-decay statistics will not have a time or charge axis for it",
                    nexus_file,
                )
            total_duration += duration or 0.0
            total_charge += charge or 0.0
    return total_events, total_duration, total_charge


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


def accumulate_nexus_histogram(  # noqa: C901
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
    hist: list[int] | None = None,
    run_statistics: RunStatistics | None = None,
    display_transform: DisplayTransform | None = None,
) -> tuple[int, int, int, list[int], None]:
    packet_count = 0
    total_events = 0
    histogram_events = 0
    if hist is None:
        hist = [0] * histogram_bins
    elif len(hist) != histogram_bins:
        raise ValueError("existing histogram length does not match histogram_bins")
    total_chunks = count_nexus_chunks(nexus_files, chunk_size)
    processed_chunks = 0
    processed_events = 0
    event_log_interval = max(1, event_log_interval)
    next_event_log = event_log_interval
    run_total_events, run_duration_s, run_charge_pc = (
        read_nexus_run_totals(nexus_files) if run_statistics is not None else (0, 0.0, 0.0)
    )

    def _advance_run_progress(chunk_events: int) -> None:
        nonlocal processed_events
        if run_statistics is None or run_total_events <= 0:
            return
        processed_events += chunk_events
        fraction = min(1.0, processed_events / run_total_events)
        run_statistics.accumulator.observe_progress(run_duration_s * fraction, run_charge_pc * fraction)

    for nexus_file in nexus_files:
        with h5py.File(nexus_file, "r") as handle:
            for group in iter_nexus_event_groups(handle):
                packet_count += 1
                total_events += int(group["event_id"].shape[0])

                for event_ids, event_tof in iter_nexus_event_chunks(group, chunk_size):
                    active_q_conversion = q_conversion_provider() if q_conversion_provider is not None else q_conversion
                    if active_q_conversion is None:
                        processed_chunks += 1
                        _advance_run_progress(len(event_ids))
                        print_nexus_progress(processed_chunks, total_chunks, nexus_file)
                        maybe_update_live_plot(
                            plotter,
                            hist,
                            None,
                            live_plot_refresh_every,
                            processed_chunks,
                            statistics_series=run_statistics.series if run_statistics is not None else None,
                            display_transform=display_transform,
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
                    _advance_run_progress(len(event_ids))
                    print_nexus_progress(processed_chunks, total_chunks, nexus_file)
                    maybe_update_live_plot(
                        plotter,
                        hist,
                        None,
                        live_plot_refresh_every,
                        processed_chunks,
                        statistics_series=run_statistics.series if run_statistics is not None else None,
                        display_transform=display_transform,
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
