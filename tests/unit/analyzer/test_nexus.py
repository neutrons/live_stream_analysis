from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from live_stream_analysis.analyzer.histogram import PixelQConversion
from live_stream_analysis.analyzer.live_plot import NullHistogramPlotter
from live_stream_analysis.analyzer.nexus import (
    DEFAULT_NEXUS_PULSES_PER_CHUNK,
    accumulate_nexus_histogram,
    count_nexus_chunks,
    count_nexus_pulse_windows,
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
        replay_order="bank",
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
        replay_order="bank",
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


# --- Time-ordered replay -------------------------------------------------------------------
#
# Real NOM files store each bank's events grouped by pulse: event_index[p] is where pulse p
# starts in that bank, and every bank shares one event_time_zero pulse timeline. Pixel p below
# lands in histogram bin p - 1, so a snapshot of the histogram shows which events were read.

PULSE = 1.0 / 60.0


def _write_pulsed_nexus(
    tmp_path: Path,
    name: str,
    banks: dict[str, list[list[int]]],
    *,
    pulse_charges: list[float] | None = None,
    run_charge: float | None = None,
    first_pulse_time: float = 0.0,
) -> Path:
    """Write a NeXus file whose banks hold the given pixel ids for each pulse, all at TOF 100 us."""
    pulse_count = len(next(iter(banks.values())))
    pulse_times = np.array([first_pulse_time + index * PULSE for index in range(pulse_count)])
    path = tmp_path / name
    with h5py.File(path, "w") as handle:
        entry = handle.create_group("entry")
        for bank_name, pulses in banks.items():
            assert len(pulses) == pulse_count
            event_ids = [pixel for pulse in pulses for pixel in pulse]
            starts = np.cumsum([0] + [len(pulse) for pulse in pulses[:-1]])
            events = entry.create_group(bank_name)
            events.create_dataset("event_id", data=np.array(event_ids, dtype=np.uint32))
            events.create_dataset("event_time_offset", data=np.full(len(event_ids), 100.0, dtype=np.float32))
            events.create_dataset("event_index", data=starts.astype(np.uint64))
            events.create_dataset("event_time_zero", data=pulse_times)
        if pulse_charges is not None:
            log = entry.create_group("DASlogs").create_group("proton_charge")
            log.create_dataset("time", data=pulse_times)
            log.create_dataset("value", data=np.array(pulse_charges, dtype=np.float64))
        if run_charge is not None:
            entry.create_dataset("proton_charge", data=np.array([run_charge]))
        entry.create_dataset("duration", data=np.array([pulse_count * PULSE]))
    return path


def _three_bin_conversion() -> PixelQConversion:
    return PixelQConversion(q_matrix_constants=[0.0, 1.0, 3.0, 5.0], use=[1, 1, 1, 1])


def _replay(nexus_paths: list[Path], **kwargs):
    snapshots: list[list[int]] = []
    kwargs.setdefault("histogram_callback", lambda _count, hist: snapshots.append(list(hist)))
    result = accumulate_nexus_histogram(
        nexus_files=[str(path) for path in nexus_paths],
        q_conversion=_three_bin_conversion(),
        histogram_bins=3,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
        **kwargs,
    )
    return result, snapshots


def test_time_order_is_the_default_with_one_second_windows():
    assert DEFAULT_NEXUS_PULSES_PER_CHUNK == 60


def test_time_order_replays_each_pulse_across_every_bank_before_the_next(tmp_path: Path):
    """Bank order would finish bank1 (all of bin 0) before touching bank2, unlike the experiment."""
    path = _write_pulsed_nexus(
        tmp_path, "run.nxs.h5", {"bank1_events": [[1], [], [1, 1]], "bank2_events": [[2], [2], []]}
    )

    _, snapshots = _replay([path], pulses_per_chunk=1)

    assert snapshots == [[1, 1, 0], [1, 2, 0], [3, 2, 0]]


def test_bank_order_is_still_available(tmp_path: Path):
    path = _write_pulsed_nexus(
        tmp_path, "run.nxs.h5", {"bank1_events": [[1], [], [1, 1]], "bank2_events": [[2], [2], []]}
    )

    _, snapshots = _replay([path], replay_order="bank")

    assert snapshots == [[3, 0, 0], [3, 2, 0]]


def test_time_order_reads_pulses_in_windows(tmp_path: Path):
    path = _write_pulsed_nexus(
        tmp_path, "run.nxs.h5", {"bank1_events": [[1], [], [1, 1]], "bank2_events": [[2], [2], []]}
    )

    (packet_count, total_events, histogram_events, hist, _), snapshots = _replay([path], pulses_per_chunk=2)

    assert snapshots == [[1, 2, 0], [3, 2, 0]]
    assert hist == [3, 2, 0]
    assert (packet_count, total_events, histogram_events) == (3, 5, 5)


def test_time_order_skips_empty_banks(tmp_path: Path):
    """About a fifth of NOM's banks record nothing; their event_index is all zeros."""
    path = _write_pulsed_nexus(
        tmp_path, "run.nxs.h5", {"bank1_events": [[1], [3]], "bank2_events": [[], []], "bank3_events": [[2], [2, 3]]}
    )

    _, snapshots = _replay([path], pulses_per_chunk=1)

    assert snapshots == [[1, 1, 0], [1, 2, 2]]


def test_time_and_bank_order_give_the_same_final_histogram(tmp_path: Path):
    rng = np.random.default_rng(51)
    banks = {
        f"bank{bank}_events": [list(rng.integers(1, 4, size=rng.integers(0, 6))) for _ in range(25)]
        for bank in range(1, 6)
    }
    path = _write_pulsed_nexus(tmp_path, "run.nxs.h5", banks)

    (_, _, time_events, time_hist, _), _ = _replay([path], pulses_per_chunk=7)
    (_, _, bank_events, bank_hist, _), _ = _replay([path], replay_order="bank", chunk_size=3)

    assert time_hist == bank_hist
    assert time_events == bank_events == sum(len(pulse) for pulses in banks.values() for pulse in pulses)


def test_time_order_follows_each_files_pulses_through_several_files(tmp_path: Path):
    first = _write_pulsed_nexus(tmp_path, "a.nxs.h5", {"bank1_events": [[1], [1]], "bank2_events": [[2], []]})
    second = _write_pulsed_nexus(tmp_path, "b.nxs.h5", {"bank1_events": [[3]], "bank2_events": [[3]]})

    (packet_count, _, _, _, _), snapshots = _replay([first, second], pulses_per_chunk=1)

    assert snapshots == [[1, 1, 0], [2, 1, 0], [2, 1, 2]]
    assert packet_count == 3


def test_time_order_tracks_real_pulse_time_and_charge(tmp_path: Path):
    """Elapsed time and charge come from each pulse, as ADARA's RTDL packets give them, rather
    than from splitting the run totals in proportion to events read."""
    path = _write_pulsed_nexus(
        tmp_path,
        "run.nxs.h5",
        {"bank1_events": [[1, 1, 1], [], [1], [1]], "bank2_events": [[], [2], [], []]},
        pulse_charges=[10.0, 20.0, 0.0, 40.0],
        run_charge=70.0,
        first_pulse_time=1234.5,
    )
    run_statistics = RunStatistics()
    observed: list[tuple[float, float]] = []

    _replay(
        [path],
        pulses_per_chunk=2,
        run_statistics=run_statistics,
        histogram_callback=lambda _count, _hist: observed.append(
            (run_statistics.accumulator.elapsed_seconds, run_statistics.accumulator.proton_charge_pc)
        ),
    )

    assert observed == [pytest.approx((PULSE, 30.0)), pytest.approx((3 * PULSE, 70.0))]


def test_time_order_continues_time_and_charge_across_files(tmp_path: Path):
    first = _write_pulsed_nexus(tmp_path, "a.nxs.h5", {"bank1_events": [[1], [1]]}, pulse_charges=[1.0, 2.0])
    second = _write_pulsed_nexus(
        tmp_path, "b.nxs.h5", {"bank1_events": [[1], [1]]}, pulse_charges=[4.0, 8.0], first_pulse_time=9999.0
    )
    run_statistics = RunStatistics()
    observed: list[tuple[float, float]] = []

    _replay(
        [first, second],
        pulses_per_chunk=1,
        run_statistics=run_statistics,
        histogram_callback=lambda _count, _hist: observed.append(
            (run_statistics.accumulator.elapsed_seconds, run_statistics.accumulator.proton_charge_pc)
        ),
    )

    assert observed == [
        pytest.approx((0.0, 1.0)),
        pytest.approx((PULSE, 3.0)),
        pytest.approx((PULSE, 7.0)),
        pytest.approx((2 * PULSE, 15.0)),
    ]


def test_time_order_spreads_the_run_charge_over_pulses_without_a_per_pulse_log(tmp_path: Path, caplog):
    path = _write_pulsed_nexus(tmp_path, "run.nxs.h5", {"bank1_events": [[1], [1], [1], [1]]}, run_charge=8.0)
    run_statistics = RunStatistics()
    observed: list[float] = []

    _replay(
        [path],
        pulses_per_chunk=1,
        run_statistics=run_statistics,
        histogram_callback=lambda _count, _hist: observed.append(run_statistics.accumulator.proton_charge_pc),
    )

    assert observed == pytest.approx([2.0, 4.0, 6.0, 8.0])
    assert "per-pulse proton charge" in caplog.text


def test_time_order_falls_back_to_bank_order_for_files_without_pulse_indexing(tmp_path: Path, caplog):
    path = _write_nexus(tmp_path, "unindexed.nxs.h5", [1, 2, 1], [100.0, 100.0, 100.0])
    q_conversion = PixelQConversion(q_matrix_constants=[0.0, 1.0, 3.0], use=[1, 1, 1])

    _, _, histogram_events, hist, _ = accumulate_nexus_histogram(
        nexus_files=[str(path)],
        q_conversion=q_conversion,
        histogram_bins=3,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
        replay_order="time",
    )

    assert hist == [2, 1, 0]
    assert histogram_events == 3
    assert "event_index" in caplog.text
    assert "bank order" in caplog.text


def test_time_order_rejects_a_non_positive_window(tmp_path: Path):
    path = _write_pulsed_nexus(tmp_path, "run.nxs.h5", {"bank1_events": [[1]]})

    with pytest.raises(ValueError, match="pulses_per_chunk"):
        _replay([path], pulses_per_chunk=0)


def test_unknown_replay_order_is_rejected(tmp_path: Path):
    path = _write_pulsed_nexus(tmp_path, "run.nxs.h5", {"bank1_events": [[1]]})

    with pytest.raises(ValueError, match="replay_order"):
        _replay([path], replay_order="random")


def test_count_nexus_pulse_windows_counts_a_partial_last_window(tmp_path: Path):
    first = _write_pulsed_nexus(tmp_path, "a.nxs.h5", {"bank1_events": [[1]] * 5, "bank2_events": [[]] * 5})
    second = _write_pulsed_nexus(tmp_path, "b.nxs.h5", {"bank1_events": [[1]] * 2})

    assert count_nexus_pulse_windows([str(first), str(second)], pulses_per_chunk=2) == 4
