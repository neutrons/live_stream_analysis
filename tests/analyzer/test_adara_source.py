from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from readadara import AdaraFileReader

from live_stream_analysis.analyzer.adara import accumulate_adara_histogram, build_reader
from live_stream_analysis.analyzer.live_plot import NullHistogramPlotter
from tests.analyzer.adara_fixtures import event_packet


def _write_adara(tmp_path: Path, *packets: bytes) -> Path:
    path = tmp_path / "sample.adara"
    path.write_bytes(b"".join(packets))
    return path


class _Packet:
    def __init__(self, events: list[tuple[int, int]]):
        self._events = events

    def get_events(self):
        return self._events


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


def test_accumulate_adara_histogram_uses_pixel_id_then_tof_tuple_order():
    reader = _Reader([_Packet([(1, 100)])])

    packet_count, total_events, histogram_events, hist = accumulate_adara_histogram(
        reader=reader,
        q_matrix_constants=[0.0, 1000.0],
        histogram_bins=600,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
    )

    assert packet_count == 1
    assert total_events == 1
    assert histogram_events == 1
    assert hist[500] == 1
