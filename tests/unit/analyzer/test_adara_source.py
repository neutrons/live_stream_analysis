from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from readadara import AdaraFileReader

from live_stream_analysis.analyzer.adara import accumulate_adara_histogram, build_reader
from live_stream_analysis.analyzer.histogram import PixelQConversion
from live_stream_analysis.analyzer.live_plot import NullHistogramPlotter
from tests.unit.analyzer.adara_fixtures import event_packet


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
        difc=[0.0, 1000.0 / (2.0 * 3.141592653589793)],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
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
    monkeypatch.setattr("live_stream_analysis.analyzer.adara.AdaraRunStatusPacket", _RunStatusPacket)
    reader = _Reader([_RunStatusPacket(1), _Packet([(1, 100)]), _RunStatusPacket(4)])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        difc=[0.0, 1000.0 / (2.0 * 3.141592653589793)],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
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

    assert packet_count == 3
    assert total_events == 1
    assert histogram_events == 0
    assert sum(hist) == 0
    assert len(completed_packets) == 1
    assert completed_packets[0].get_status() == 4
    assert stats.packet_count == 3


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
        difc=[0.0, 1000.0 / (2.0 * 3.141592653589793)],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
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


def test_accumulate_adara_histogram_ignores_non_banked_event_packets():
    reader = _Reader([_Packet([(1, 100)], format_int=0x400000), _Packet([(1, 100)], format_int=0x400001)])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        difc=[0.0, 1000.0 / (2.0 * 3.141592653589793)],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
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
        difc=[0.0, 0.0],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
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
        difc=[0.0, 0.0],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
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
        difc=[0.0, 1000.0 / (2.0 * 3.141592653589793)],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
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
