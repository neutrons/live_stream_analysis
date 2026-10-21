from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from live_stream_analysis.analyzer.histogram import PixelQConversion
from live_stream_analysis.analyzer.live_plot import NullHistogramPlotter
from live_stream_analysis.analyzer.nexus import (
    accumulate_nexus_histogram,
    count_nexus_chunks,
    iter_nexus_event_chunks,
    read_nexus_run_totals,
)
from live_stream_analysis.statistics import RunStatistics


def _write_nexus(tmp_path: Path, name: str, event_ids: list[int], event_tofs: list[float]) -> Path:
    path = tmp_path / name
    with h5py.File(path, "w") as handle:
        entry = handle.create_group("entry")
        events = entry.create_group("bank1_events")
        events.create_dataset("event_id", data=np.array(event_ids, dtype=np.int32))
        events.create_dataset("event_time_offset", data=np.array(event_tofs, dtype=np.float64))
    return path


def test_count_nexus_chunks_counts_partial_tail_chunk(tmp_path: Path):
    nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1, 2, 3, 4, 5], [1.0, 2.0, 3.0, 4.0, 5.0])

    result = count_nexus_chunks([str(nexus_path)], chunk_size=2)

    assert result == 3


def test_iter_nexus_event_chunks_rejects_mismatched_dataset_lengths(tmp_path: Path):
    nexus_path = tmp_path / "bad.nxs.h5"
    with h5py.File(nexus_path, "w") as handle:
        entry = handle.create_group("entry")
        events = entry.create_group("bank1_events")
        events.create_dataset("event_id", data=np.array([1, 2], dtype=np.int32))
        events.create_dataset("event_time_offset", data=np.array([1.0], dtype=np.float64))

    with h5py.File(nexus_path, "r") as handle:
        group = handle["entry"]["bank1_events"]
        with pytest.raises(ValueError, match="same length"):
            list(iter_nexus_event_chunks(group, chunk_size=2))


def test_accumulate_nexus_histogram_counts_events_into_expected_bin(tmp_path: Path):
    nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1], [100.0])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        difc=[0.0, 1000.0 / (2.0 * np.pi)],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
        use=[1, 1],
    )

    packet_count, total_events, histogram_events, hist, _stats = accumulate_nexus_histogram(
        nexus_files=[str(nexus_path)],
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
        chunk_size=2,
    )

    assert packet_count == 1
    assert total_events == 1
    assert histogram_events == 1
    assert hist[500] == 1


def _write_nexus_with_run_totals(
    tmp_path: Path,
    name: str,
    banks: dict[str, tuple[list[int], list[float]]],
    duration: float | None,
    proton_charge: float | None,
) -> Path:
    path = tmp_path / name
    with h5py.File(path, "w") as handle:
        entry = handle.create_group("entry")
        for bank_name, (event_ids, event_tofs) in banks.items():
            events = entry.create_group(bank_name)
            events.create_dataset("event_id", data=np.array(event_ids, dtype=np.int32))
            events.create_dataset("event_time_offset", data=np.array(event_tofs, dtype=np.float64))
        if duration is not None:
            entry.create_dataset("duration", data=np.array([duration], dtype=np.float64))
        if proton_charge is not None:
            entry.create_dataset("proton_charge", data=np.array([proton_charge], dtype=np.float64))
    return path


def _pixel_one_conversion() -> PixelQConversion:
    return PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        difc=[0.0, 1000.0 / (2.0 * np.pi)],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
        use=[1, 1],
    )


def _accumulate(nexus_path: Path, run_statistics: RunStatistics, chunk_size: int = 2):
    return accumulate_nexus_histogram(
        nexus_files=[str(nexus_path)],
        q_conversion=_pixel_one_conversion(),
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
        chunk_size=chunk_size,
        run_statistics=run_statistics,
    )


def test_read_nexus_run_totals_reads_duration_and_proton_charge(tmp_path: Path):
    nexus_path = _write_nexus_with_run_totals(
        tmp_path,
        "sample.nxs.h5",
        {"bank1_events": ([1, 1], [100.0, 100.0]), "bank2_events": ([1, 1], [100.0, 100.0])},
        duration=1615.7,
        proton_charge=2.0e12,
    )

    total_events, duration, charge = read_nexus_run_totals([str(nexus_path)])

    assert total_events == 4
    assert duration == pytest.approx(1615.7)
    assert charge == pytest.approx(2.0e12)


def test_accumulate_nexus_histogram_reaches_the_true_run_totals(tmp_path: Path):
    """Bank-major iteration means time must be attributed by cumulative events, not per bank.

    Reading event_time_zero per bank would reset elapsed time to zero at each bank boundary,
    so the decay curve would sawtooth instead of converging.
    """
    nexus_path = _write_nexus_with_run_totals(
        tmp_path,
        "sample.nxs.h5",
        {"bank1_events": ([1, 1], [100.0, 100.0]), "bank2_events": ([1, 1], [100.0, 100.0])},
        duration=1000.0,
        proton_charge=4.0e12,
    )
    run_statistics = RunStatistics()

    _accumulate(nexus_path, run_statistics, chunk_size=1)

    assert run_statistics.accumulator.elapsed_seconds == pytest.approx(1000.0)
    assert run_statistics.accumulator.proton_charge_pc == pytest.approx(4.0e12)


def test_accumulate_nexus_histogram_advances_time_monotonically_across_banks(tmp_path: Path):
    nexus_path = _write_nexus_with_run_totals(
        tmp_path,
        "sample.nxs.h5",
        {"bank1_events": ([1, 1], [100.0, 100.0]), "bank2_events": ([1, 1], [100.0, 100.0])},
        duration=1000.0,
        proton_charge=4.0e12,
    )
    run_statistics = RunStatistics()
    run_statistics.series.sample_interval_seconds = 0.0
    observed: list[float] = []

    accumulate_nexus_histogram(
        nexus_files=[str(nexus_path)],
        q_conversion=_pixel_one_conversion(),
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
        chunk_size=1,
        run_statistics=run_statistics,
        histogram_callback=lambda _count, _hist: observed.append(run_statistics.accumulator.elapsed_seconds),
    )

    assert observed == [250.0, 500.0, 750.0, 1000.0]
    assert observed == sorted(observed)


def test_accumulate_nexus_histogram_tolerates_files_without_run_totals(tmp_path: Path):
    nexus_path = _write_nexus_with_run_totals(
        tmp_path,
        "sample.nxs.h5",
        {"bank1_events": ([1], [100.0])},
        duration=None,
        proton_charge=None,
    )
    run_statistics = RunStatistics()

    _, _, histogram_events, _, _ = _accumulate(nexus_path, run_statistics)

    assert histogram_events == 1
    assert run_statistics.accumulator.elapsed_seconds == 0.0
    assert run_statistics.accumulator.proton_charge_pc == 0.0
