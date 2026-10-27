from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import h5py
import numpy as np

from ..statistics import RunStatistics
from .histogram import PixelQConversion, pixel_tof_to_q
from .live_plot import DisplayTransform, HistogramPlotter, maybe_update_live_plot

DEFAULT_NEXUS_CHUNK_SIZE = 250_000
# One second of beam at SNS's 60 Hz, so a time-ordered replay advances the plot about once per
# second of the experiment.
DEFAULT_NEXUS_PULSES_PER_CHUNK = 60
NEXUS_REPLAY_ORDERS = ("time", "bank")
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


def _has_pulse_index(handle: h5py.File) -> bool:
    groups = list(iter_nexus_event_groups(handle))
    return bool(groups) and all("event_index" in group and "event_time_zero" in group for group in groups)


def nexus_files_have_pulse_index(nexus_files: list[str]) -> bool:
    for nexus_file in nexus_files:
        with h5py.File(nexus_file, "r") as handle:
            if not _has_pulse_index(handle):
                return False
    return True


def _pulse_count(handle: h5py.File) -> int:
    return int(next(iter_nexus_event_groups(handle))["event_time_zero"].shape[0])


def count_nexus_pulse_windows(nexus_files: list[str], pulses_per_chunk: int) -> int:
    if pulses_per_chunk <= 0:
        raise ValueError("pulses_per_chunk must be > 0")

    total_windows = 0
    for nexus_file in nexus_files:
        with h5py.File(nexus_file, "r") as handle:
            total_windows += math.ceil(_pulse_count(handle) / pulses_per_chunk)
    return total_windows


def _pulse_charges(handle: h5py.File, pulse_count: int, nexus_file: str) -> np.ndarray:
    """Return the proton charge (pC) of each pulse.

    DASlogs/proton_charge holds one value per pulse, on the same timeline as event_time_zero.
    Without it, the run total is spread evenly over the pulses so charge still advances.
    """
    log = handle["entry"].get("DASlogs/proton_charge/value")
    if log is not None and log.shape[0] == pulse_count:
        return np.asarray(log[()], dtype=np.float64)
    run_charge = _read_scalar(handle, "proton_charge") or 0.0
    LOGGER.warning(
        "NeXus file %s has no per-pulse proton charge matching its %s pulses; "
        "spreading entry/proton_charge (%s pC) evenly over them",
        nexus_file,
        pulse_count,
        run_charge,
    )
    return np.full(pulse_count, run_charge / pulse_count if pulse_count else 0.0)


def iter_nexus_pulse_windows(handle: h5py.File, nexus_file: str, pulses_per_chunk: int):
    """Yield each window of ``pulses_per_chunk`` pulses in time order, across every bank.

    Every bank records the same pulse timeline (event_time_zero) and event_index[p] is where
    pulse p starts in that bank's events, so slicing each bank between window edges replays
    the run in the order it was measured -- the order ADARA delivers it -- rather than one
    detector bank (one fixed angle, hence one Q range) at a time.

    Yields (event_ids, event_tof, elapsed_seconds, window_charge_pc) with elapsed time
    measured from this file's first pulse to the window's last.
    """
    groups = list(iter_nexus_event_groups(handle))
    pulse_times = np.asarray(groups[0]["event_time_zero"][()], dtype=np.float64)
    pulse_count = len(pulse_times)
    banks = []
    for group in groups:
        event_index = np.asarray(group["event_index"][()], dtype=np.int64)
        if event_index.shape[0] != pulse_count:
            raise ValueError(f"NeXus group {group.name} does not share the file's {pulse_count}-pulse timeline")
        event_count = int(group["event_id"].shape[0])
        if group["event_time_offset"].shape[0] != event_count:
            raise ValueError("NeXus event_id and event_time_offset datasets must have the same length")
        if event_count:
            # A trailing edge lets the last pulse run to the end of the bank's events.
            banks.append((group["event_id"], group["event_time_offset"], np.append(event_index, event_count)))
    charges = _pulse_charges(handle, pulse_count, nexus_file)

    for start in range(0, pulse_count, pulses_per_chunk):
        stop = min(start + pulses_per_chunk, pulse_count)
        ids, tofs = [], []
        for event_ids, event_tof, edges in banks:
            lo, hi = int(edges[start]), int(edges[stop])
            if hi > lo:
                ids.append(event_ids[lo:hi])
                tofs.append(event_tof[lo:hi])
        yield (
            np.concatenate(ids) if ids else np.empty(0, dtype=np.uint32),
            np.concatenate(tofs) if tofs else np.empty(0, dtype=np.float32),
            float(pulse_times[stop - 1] - pulse_times[0]),
            float(charges[start:stop].sum()),
        )


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
    replay_order: str = "time",
    pulses_per_chunk: int = DEFAULT_NEXUS_PULSES_PER_CHUNK,
    q_conversion_provider=None,
    histogram_callback=None,
    hist: list[int] | None = None,
    run_statistics: RunStatistics | None = None,
    display_transform: DisplayTransform | None = None,
) -> tuple[int, int, int, list[int], None]:
    """Histogram NeXus events into Q, in time order (pulse windows across all banks) or bank order.

    Returns (packet_count, total_events, histogram_events, hist, None), where packet_count is
    the number of pulses read in time order, or of bank groups read in bank order.
    """
    if replay_order not in NEXUS_REPLAY_ORDERS:
        raise ValueError(f"replay_order must be one of {NEXUS_REPLAY_ORDERS}, got {replay_order!r}")
    if replay_order == "time" and pulses_per_chunk <= 0:
        raise ValueError("pulses_per_chunk must be > 0")
    if replay_order == "time" and not nexus_files_have_pulse_index(nexus_files):
        LOGGER.warning(
            "NeXus input lacks per-pulse event_index/event_time_zero in some bank; replaying in bank order instead"
        )
        replay_order = "bank"

    packet_count = 0
    total_events = 0
    histogram_events = 0
    if hist is None:
        hist = [0] * histogram_bins
    elif len(hist) != histogram_bins:
        raise ValueError("existing histogram length does not match histogram_bins")
    if replay_order == "time":
        total_chunks = count_nexus_pulse_windows(nexus_files, pulses_per_chunk)
    else:
        total_chunks = count_nexus_chunks(nexus_files, chunk_size)
    processed_chunks = 0
    processed_events = 0
    event_log_interval = max(1, event_log_interval)
    next_event_log = event_log_interval
    # Bank order replays every pulse once per bank, so its statistics split the run totals in
    # proportion to events read; time order reports each pulse's real time and charge instead.
    run_total_events, run_duration_s, run_charge_pc = (
        read_nexus_run_totals(nexus_files) if run_statistics is not None and replay_order == "bank" else (0, 0.0, 0.0)
    )

    def _advance_run_progress(chunk_events: int) -> None:
        nonlocal processed_events
        if run_statistics is None or run_total_events <= 0:
            return
        processed_events += chunk_events
        fraction = min(1.0, processed_events / run_total_events)
        run_statistics.accumulator.observe_progress(run_duration_s * fraction, run_charge_pc * fraction)

    def _histogram_chunk(event_ids, event_tof, nexus_file: str) -> None:
        nonlocal processed_chunks, histogram_events, next_event_log
        active_q_conversion = q_conversion_provider() if q_conversion_provider is not None else q_conversion
        if active_q_conversion is not None:
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
            None,
            live_plot_refresh_every,
            processed_chunks,
            statistics_series=run_statistics.series if run_statistics is not None else None,
            display_transform=display_transform,
        )
        if histogram_callback is not None:
            histogram_callback(histogram_events, hist)

    # Time and charge carried over from earlier files, so several files replay as one run.
    elapsed_offset_s = 0.0
    charge_so_far_pc = 0.0
    for nexus_file in nexus_files:
        with h5py.File(nexus_file, "r") as handle:
            if replay_order == "time":
                file_elapsed_s = 0.0
                for event_ids, event_tof, file_elapsed_s, window_charge_pc in iter_nexus_pulse_windows(
                    handle, nexus_file, pulses_per_chunk
                ):
                    total_events += len(event_ids)
                    charge_so_far_pc += window_charge_pc
                    if run_statistics is not None:
                        run_statistics.accumulator.observe_progress(elapsed_offset_s + file_elapsed_s, charge_so_far_pc)
                    _histogram_chunk(event_ids, event_tof, nexus_file)
                packet_count += _pulse_count(handle)
                elapsed_offset_s += file_elapsed_s
                continue

            for group in iter_nexus_event_groups(handle):
                packet_count += 1
                total_events += int(group["event_id"].shape[0])
                for event_ids, event_tof in iter_nexus_event_chunks(group, chunk_size):
                    _advance_run_progress(len(event_ids))
                    _histogram_chunk(event_ids, event_tof, nexus_file)

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
