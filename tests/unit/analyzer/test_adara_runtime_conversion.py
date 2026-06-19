from __future__ import annotations

from live_stream_analysis.analyzer.adara import accumulate_adara_histogram
from live_stream_analysis.analyzer.histogram import PixelQConversion


class _Packet:
    def __init__(self, events):
        self._events = events

    def get_events(self):
        return self._events


class _Reader:
    def __init__(self, packets):
        self._packets = packets

    def read_generator(self):
        yield from self._packets


class _Plotter:
    def update(self, intensity, error, relative_uncertainty):
        _ = intensity, error, relative_uncertainty


def test_accumulate_adara_histogram_uses_conversion_provider():
    conversions = [
        PixelQConversion(
            q_matrix_constants=[10.0],
            difc=[0.0],
            difa=[0.0],
            tzero=[0.0],
            use=[1],
        ),
        PixelQConversion(
            q_matrix_constants=[20.0],
            difc=[0.0],
            difa=[0.0],
            tzero=[0.0],
            use=[1],
        ),
    ]

    def provider():
        return conversions.pop(0)

    packet_count, total_events, histogram_events, hist = accumulate_adara_histogram(
        reader=_Reader([_Packet([(0, 10.0)]), _Packet([(0, 10.0)])]),
        q_conversion=None,
        histogram_bins=3,
        histogram_q_min=0.0,
        histogram_q_bin_size=1.0,
        tof_tick_us=1.0,
        plotter=_Plotter(),
        live_plot_refresh_every=1,
        event_log_interval=100,
        q_conversion_provider=provider,
    )

    assert packet_count == 2
    assert total_events == 2
    assert histogram_events == 2
    assert hist == [0, 1, 1]