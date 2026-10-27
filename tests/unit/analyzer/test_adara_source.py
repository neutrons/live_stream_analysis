from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from readadara import AdaraFileReader

from live_stream_analysis.analyzer.adara import accumulate_adara_histogram, build_reader
from live_stream_analysis.analyzer.histogram import PixelQConversion
from live_stream_analysis.analyzer.live_plot import NullHistogramPlotter
from live_stream_analysis.statistics import RunStatistics, StatisticsSample
from tests.unit.analyzer.adara_fixtures import event_packet, rtdl_packet


def _write_adara(tmp_path: Path, *packets: bytes) -> Path:
    path = tmp_path / "sample.adara"
    path.write_bytes(b"".join(packets))
    return path


class _Packet:
    def __init__(self, events: list[tuple[int, int]], format_int: int = 0x400001):
        self._events = events
        self._format_int = format_int

    def get_events(self):
        return self._events

    def get_format_int(self):
        return self._format_int


class _RunStatusPacket(_Packet):
    def __init__(self, status: int):
        super().__init__([])
        self._status = status

    def get_status(self):
        return self._status


class _Reader:
    def __init__(self, packets: list[_Packet]):
        self._packets = packets

    def read_generator(self):
        yield from self._packets


def test_build_reader_returns_file_reader(tmp_path: Path):
    path = _write_adara(tmp_path, event_packet([(1, 100)]))
    args = argparse.Namespace(adara_file=str(path), nexus_file=None, adara_stream=None)

    reader = build_reader(args)

    assert isinstance(reader, AdaraFileReader)


def test_build_reader_rejects_non_integer_stream_port():
    args = argparse.Namespace(adara_file=None, nexus_file=None, adara_stream=("localhost", "abc"))

    with pytest.raises(ValueError, match="PORT must be an integer"):
        build_reader(args)


def test_accumulate_adara_histogram_uses_ehsan_default_tof_then_pixel_tuple_order():
    reader = _Reader([_Packet([(1, 100)])])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
    )

    assert packet_count == 1
    assert total_events == 1
    assert histogram_events == 0
    assert sum(hist) == 0
    assert stats.skipped_invalid_pixel_ids == 1


def test_accumulate_adara_histogram_calls_run_complete_callback_only_for_end_run(monkeypatch: pytest.MonkeyPatch):
    """Only END_RUN completes a run; STATE and NEW_RUN must not fire the callback."""
    monkeypatch.setattr("live_stream_analysis.analyzer.adara.AdaraRunStatusPacket", _RunStatusPacket)
    reader = _Reader(
        [
            _RunStatusPacket(5),
            _Packet([(1, 100)]),
            _RunStatusPacket(1),
            _Packet([(100, 1)]),
            _RunStatusPacket(4),
        ]
    )
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )
    completed_packets: list[_RunStatusPacket] = []

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
        run_complete_callback=completed_packets.append,
    )

    assert packet_count == 5
    assert total_events == 2
    assert histogram_events == 1
    assert sum(hist) == 1
    assert len(completed_packets) == 1
    assert completed_packets[0].get_status() == 4
    assert stats.packet_count == 5


def test_accumulate_adara_histogram_detects_end_run_from_status_accessor_without_exact_packet_type(
    monkeypatch: pytest.MonkeyPatch,
):
    class _ForeignRunStatusPacket(_Packet):
        def __init__(self, status: int):
            super().__init__([])
            self._status = status

        def get_status(self):
            return self._status

    monkeypatch.setattr("live_stream_analysis.analyzer.adara.AdaraRunStatusPacket", None)
    reader = _Reader([_Packet([(1, 100)]), _ForeignRunStatusPacket(4)])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )
    completed_packets: list[_ForeignRunStatusPacket] = []
    histogram_snapshots: list[tuple[int, list[int]]] = []

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1000,
        event_log_interval=100_000,
        histogram_callback=lambda count, current_hist: histogram_snapshots.append((count, list(current_hist))),
        run_complete_callback=completed_packets.append,
    )

    assert packet_count == 2
    assert total_events == 1
    assert histogram_events == 0
    assert sum(hist) == 0
    assert len(completed_packets) == 1
    assert completed_packets[0].get_status() == 4
    assert histogram_snapshots[-1][0] == 0
    assert sum(histogram_snapshots[-1][1]) == 0
    assert stats.packet_count == 2


def test_accumulate_adara_histogram_returns_at_end_run(monkeypatch: pytest.MonkeyPatch):
    """On END_RUN the accumulator hands control back so the caller can finalize the run.

    Resetting the histogram is the caller's job (``_handle_run_complete`` in histogram_runner),
    so the returned histogram still holds this run's counts and later packets stay unread.
    """
    monkeypatch.setattr("live_stream_analysis.analyzer.adara.AdaraRunStatusPacket", _RunStatusPacket)
    reader = _Reader(
        [
            _Packet([(100, 1)]),
            _RunStatusPacket(4),
            _Packet([(100, 1)]),
        ]
    )
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )
    completed_packets: list[_RunStatusPacket] = []
    histogram_snapshots: list[tuple[int, list[int]]] = []

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1000,
        event_log_interval=100_000,
        histogram_callback=lambda count, current_hist: histogram_snapshots.append((count, list(current_hist))),
        run_complete_callback=completed_packets.append,
    )

    assert packet_count == 2
    assert total_events == 1
    assert histogram_events == 1
    assert sum(hist) == 1
    assert hist[500] == 1
    assert len(completed_packets) == 1
    assert completed_packets[0].get_status() == 4
    assert histogram_snapshots[-1][0] == 1
    assert sum(histogram_snapshots[-1][1]) == 1
    assert stats.packet_count == 2


def test_accumulate_adara_histogram_ignores_run_status_lookalike_packets(monkeypatch: pytest.MonkeyPatch):
    """Packets that merely expose get_status() must never be read as run boundaries.

    Real NOMAD streams carry AdaraDoublePacket device readings whose get_status() returns 1,
    which would otherwise wipe the live histogram several times per run.
    """

    class _DoublePacket(_Packet):
        def __init__(self, status: int):
            super().__init__([(100, 1)])
            self._status = status

        def get_status(self):
            return self._status

    monkeypatch.setattr("live_stream_analysis.analyzer.adara.AdaraRunStatusPacket", _RunStatusPacket)
    reader = _Reader([_DoublePacket(1), _DoublePacket(4), _Packet([(100, 1)])])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )
    completed_packets: list[_Packet] = []

    packet_count, total_events, histogram_events, hist, _stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1000,
        event_log_interval=100_000,
        run_complete_callback=completed_packets.append,
    )

    assert packet_count == 3
    assert total_events == 3
    assert histogram_events == 3
    # Nothing was treated as a boundary, so no reset and no completion.
    assert hist[500] == 3
    assert completed_packets == []


def test_accumulate_adara_histogram_resets_but_keeps_reading_at_new_run(monkeypatch: pytest.MonkeyPatch):
    """NEW_RUN clears the previous run's counts but must not end the read.

    Run files open with a NEW_RUN packet, so completing the run here would stop the read
    before any events were seen.
    """
    monkeypatch.setattr("live_stream_analysis.analyzer.adara.AdaraRunStatusPacket", _RunStatusPacket)
    reader = _Reader(
        [
            _Packet([(100, 1)]),
            _RunStatusPacket(1),
            _Packet([(100, 1)]),
        ]
    )
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )
    completed_packets: list[_RunStatusPacket] = []
    histogram_snapshots: list[tuple[int, list[int]]] = []

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1000,
        event_log_interval=100_000,
        histogram_callback=lambda count, current_hist: histogram_snapshots.append((count, list(current_hist))),
        run_complete_callback=completed_packets.append,
    )

    assert packet_count == 3
    assert total_events == 2
    assert histogram_events == 2
    # The first run's count was discarded at the boundary; only the second one survives.
    assert sum(hist) == 1
    assert hist[500] == 1
    assert completed_packets == []
    assert stats.packet_count == 3


def test_accumulate_adara_histogram_ignores_non_banked_event_packets():
    reader = _Reader([_Packet([(1, 100)], format_int=0x400000), _Packet([(1, 100)], format_int=0x400001)])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
    )

    assert packet_count == 2
    assert total_events == 1
    assert histogram_events == 0
    assert sum(hist) == 0
    assert stats.skipped_non_banked_packets == 1


def test_accumulate_adara_histogram_uses_default_tof_pixel_binning():
    reader = _Reader([_Packet([(100, 1)])])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
    )

    assert packet_count == 1
    assert total_events == 1
    assert histogram_events == 1
    assert hist[500] == 1
    assert stats.skipped_invalid_pixel_ids == 0


def test_accumulate_adara_histogram_honors_nonzero_q_min():
    reader = _Reader([_Packet([(100, 1)])])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=2.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
    )

    assert packet_count == 1
    assert total_events == 1
    assert histogram_events == 1
    assert hist[400] == 1
    assert stats.skipped_out_of_range_bins == 0


def test_accumulate_adara_histogram_tracks_invalid_and_out_of_range_events():
    reader = _Reader([_Packet([(999, 100), (1, 0), (1, 1)])])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )

    packet_count, total_events, histogram_events, hist, stats = accumulate_adara_histogram(
        reader=reader,
        q_conversion=q_conversion,
        histogram_bins=10,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        event_log_interval=100_000,
    )

    assert packet_count == 1
    assert total_events == 3
    assert histogram_events == 0
    assert sum(hist) == 0
    assert stats.skipped_invalid_pixel_ids == 1
    assert stats.skipped_zero_or_negative_tof == 0
    assert stats.skipped_unconvertible_events == 1
    assert stats.skipped_out_of_range_bins == 1


def _q_conversion_for_pixel_one() -> PixelQConversion:
    return PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        use=[1, 1],
    )


def _accumulate_from_file(path: Path, run_statistics: RunStatistics):
    return accumulate_adara_histogram(
        reader=AdaraFileReader(filename=str(path)),
        q_conversion=_q_conversion_for_pixel_one(),
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1000,
        event_log_interval=100_000,
        run_statistics=run_statistics,
    )


def test_accumulate_adara_histogram_accumulates_proton_charge_from_rtdl_packets(tmp_path: Path):
    """RTDL packets carry the per-pulse proton charge the error-decay curve is plotted against.

    They are not banked event packets, so the charge is only captured if it is read before
    the non-banked skip discards them.
    """
    path = _write_adara(
        tmp_path,
        rtdl_packet(timestamp_s=100, charge_10pc=100),
        event_packet([(1, 100)], timestamp_s=100),
        rtdl_packet(timestamp_s=103, charge_10pc=250),
        event_packet([(1, 100)], timestamp_s=103),
    )
    run_statistics = RunStatistics()

    _, _, histogram_events, _, stats = _accumulate_from_file(path, run_statistics)

    assert histogram_events == 2
    # charge_10pc is in units of 10 pC, so 100 + 250 units is 3500 pC.
    assert run_statistics.accumulator.proton_charge_pc == pytest.approx(3500.0)
    assert run_statistics.accumulator.elapsed_seconds == pytest.approx(3.0)
    # The RTDL packets are still skipped for histogramming purposes.
    assert stats.skipped_non_banked_packets == 2


def test_accumulate_adara_histogram_leaves_charge_at_zero_without_rtdl_packets(tmp_path: Path):
    path = _write_adara(tmp_path, event_packet([(1, 100)], timestamp_s=100))
    run_statistics = RunStatistics()

    _accumulate_from_file(path, run_statistics)

    assert run_statistics.accumulator.proton_charge_pc == 0.0
    assert run_statistics.accumulator.elapsed_seconds == 0.0


def test_accumulate_adara_histogram_resets_charge_and_decay_series_at_new_run(monkeypatch: pytest.MonkeyPatch):
    """A new run must start a fresh decay curve, just as it starts a fresh histogram."""
    monkeypatch.setattr("live_stream_analysis.analyzer.adara.AdaraRunStatusPacket", _RunStatusPacket)
    run_statistics = RunStatistics()
    run_statistics.accumulator.observe_pulse(50.0, 9999.0)
    run_statistics.series.record(
        StatisticsSample(
            elapsed_seconds=0.0,
            proton_charge_pc=9999.0,
            mean_relative_uncertainty=0.9,
            total_counts=1.0,
            nonzero_bins=1,
            histogram_events=1,
        )
    )

    accumulate_adara_histogram(
        reader=_Reader([_Packet([(100, 1)]), _RunStatusPacket(1), _Packet([(100, 1)])]),
        q_conversion=_q_conversion_for_pixel_one(),
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1000,
        event_log_interval=100_000,
        run_statistics=run_statistics,
    )

    assert run_statistics.accumulator.proton_charge_pc == 0.0
    assert run_statistics.series.samples() == []


def test_accumulate_adara_histogram_tracks_time_without_any_rtdl_packets(tmp_path: Path):
    """Every packet carries a timestamp, so the time axis works even with no charge source.

    Deriving the clock from RTDL packets alone pinned every sample to elapsed=0, which
    collapses the whole decay series onto a single x value.
    """
    path = _write_adara(
        tmp_path,
        event_packet([(1, 100)], timestamp_s=100),
        event_packet([(1, 100)], timestamp_s=142),
    )
    run_statistics = RunStatistics()

    _, _, _, _, stats = _accumulate_from_file(path, run_statistics)

    assert run_statistics.accumulator.elapsed_seconds == pytest.approx(42.0)
    assert run_statistics.accumulator.proton_charge_pc == 0.0
    assert stats.rtdl_packets == 0


def test_accumulate_adara_histogram_counts_rtdl_packets_for_reporting(tmp_path: Path):
    path = _write_adara(
        tmp_path,
        rtdl_packet(timestamp_s=100, charge_10pc=100),
        event_packet([(1, 100)], timestamp_s=100),
        rtdl_packet(timestamp_s=101, charge_10pc=100),
    )
    run_statistics = RunStatistics()

    _, _, _, _, stats = _accumulate_from_file(path, run_statistics)

    assert stats.rtdl_packets == 2
    assert run_statistics.accumulator.proton_charge_pc == pytest.approx(2000.0)
