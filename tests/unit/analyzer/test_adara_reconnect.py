"""Unit tests for ADARA live-stream reconnect behaviour.

These cover socket-level failures, which the file-based fixtures cannot express: an
in-process generator never raises ConnectionResetError, so a real network drop went
unexercised and the analyzer used to die on one instead of reconnecting.
"""

from __future__ import annotations

from pathlib import Path

from live_stream_analysis.main import main

PIXEL_GEOMETRY_ROWS = [
    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
    "0,1.0,1.0,0.0",
    "1,1.0,1.0,99.0",
]


def _write_pixel_geometry(tmp_path: Path) -> Path:
    path = tmp_path / "pixel_geometry.csv"
    path.write_text("\n".join(PIXEL_GEOMETRY_ROWS) + "\n", encoding="utf-8")
    return path


def _stream_args(pixel_csv: Path, max_reconnects: int) -> list[str]:
    return [
        "analyze",
        "--adara-stream",
        "127.0.0.1",
        "31415",
        "--histogram-pixel-geometry-csv",
        str(pixel_csv),
        "--histogram-q-max",
        "100",
        "--histogram-q-bin-size",
        "0.02",
        "--adara-stream-max-reconnects",
        str(max_reconnects),
        "--adara-stream-reconnect-delay",
        "0",
    ]


class _Reader:
    """Stand-in for AdaraLiveStreamReader; the runner stub never reads from it."""


def test_connection_reset_mid_stream_reconnects_instead_of_failing(tmp_path: Path, monkeypatch):
    """A reset part-way through a read must be treated as a disconnect, not a fatal error.

    The stub drops the first attempt, then completes a run on the reconnected stream. If the
    reset were fatal the run-complete callback would never fire.
    """
    pixel_csv = _write_pixel_geometry(tmp_path)
    reader_builds: list[int] = []

    class _DroppingRunner:
        def __init__(self):
            self.calls = 0

        def accumulate_histogram(
            self,
            reader,
            args,
            q_conversion,
            histogram_bins,
            plotter,
            *,
            chunk_size,
            q_conversion_provider=None,
            histogram_callback=None,
            run_complete_callback=None,
            histogram_state_callback=None,
            hist=None,
        ):
            _ = (reader, args, q_conversion, plotter, chunk_size, q_conversion_provider, histogram_callback)
            self.calls += 1
            if hist is None:
                hist = [0] * histogram_bins
            if histogram_state_callback is not None:
                histogram_state_callback(hist)
            if self.calls == 1:
                raise ConnectionResetError(104, "Connection reset by peer")
            if self.calls == 2:
                hist[4950] = 7
                if run_complete_callback is not None:
                    run_complete_callback(object())
                return 5, 7, 7, hist, None
            return 0, 0, 0, hist, None

        def run_basic_mode(self, reader, *, chunk_size: int) -> int:
            _ = (reader, chunk_size)
            return 0

    runner = _DroppingRunner()

    def _fake_build_reader(_args):
        reader_builds.append(1)
        return _Reader()

    monkeypatch.setattr("live_stream_analysis.analyzer.histogram_runner.build_reader", _fake_build_reader)
    monkeypatch.setattr("live_stream_analysis.analyzer.factory.build_reader", _fake_build_reader)
    monkeypatch.setattr(
        "live_stream_analysis.analyzer.histogram_runner.create_source_runner",
        lambda _args: runner,
    )

    rc = main(_stream_args(pixel_csv, max_reconnects=1))

    # The stream eventually exhausts its reconnect budget, so a non-zero exit is expected.
    assert rc == 1
    # Reconnected after the reset rather than dying on it.
    assert len(reader_builds) >= 2
    # Proof the reset was survivable: accumulation continued past it and completed a run.
    assert runner.calls >= 3


def test_refused_reconnect_is_retried_rather_than_fatal(tmp_path: Path, monkeypatch):
    """If the SMS is still restarting, the reconnect gets refused; that must be retried."""
    pixel_csv = _write_pixel_geometry(tmp_path)
    reader_builds: list[int] = []

    class _AlwaysDropsRunner:
        def accumulate_histogram(
            self,
            reader,
            args,
            q_conversion,
            histogram_bins,
            plotter,
            *,
            chunk_size,
            q_conversion_provider=None,
            histogram_callback=None,
            run_complete_callback=None,
            histogram_state_callback=None,
            hist=None,
        ):
            _ = (reader, args, q_conversion, histogram_bins, plotter, chunk_size, q_conversion_provider)
            _ = (histogram_callback, run_complete_callback, histogram_state_callback, hist)
            raise ConnectionResetError(104, "Connection reset by peer")

        def run_basic_mode(self, reader, *, chunk_size: int) -> int:
            _ = (reader, chunk_size)
            return 0

    def _fake_build_reader(_args):
        reader_builds.append(1)
        if len(reader_builds) == 1:
            return _Reader()  # initial connection succeeds
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr("live_stream_analysis.analyzer.histogram_runner.build_reader", _fake_build_reader)
    monkeypatch.setattr("live_stream_analysis.analyzer.factory.build_reader", _fake_build_reader)
    monkeypatch.setattr(
        "live_stream_analysis.analyzer.histogram_runner.create_source_runner",
        lambda _args: _AlwaysDropsRunner(),
    )

    rc = main(_stream_args(pixel_csv, max_reconnects=2))

    assert rc == 1
    # Initial connection plus more than one reconnect attempt: refusal did not abort immediately.
    assert len(reader_builds) >= 3
