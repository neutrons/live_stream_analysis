"""Unit tests for ADARA live-stream reconnect behaviour.

These cover socket-level failures, which the file-based fixtures cannot express: an
in-process generator never raises ConnectionResetError, so a real network drop went
unexercised and the analyzer used to die on one instead of reconnecting.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path

from live_stream_analysis.analyzer.factory import build_reader
from live_stream_analysis.main import build_parser, main
from tests.unit.analyzer.adara_fixtures import event_packet

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
            run_statistics=None,
            display_transform=None,
        ):
            _ = display_transform
            _ = (reader, args, q_conversion, plotter, chunk_size, q_conversion_provider, histogram_callback)
            _ = run_statistics
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
            run_statistics=None,
            display_transform=None,
        ):
            _ = display_transform
            _ = (reader, args, q_conversion, histogram_bins, plotter, chunk_size, q_conversion_provider)
            _ = (histogram_callback, run_complete_callback, histogram_state_callback, hist, run_statistics)
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


def _serve_then_close(payload: bytes) -> tuple[int, threading.Thread]:
    """Accept one client, send ``payload``, and close cleanly, as an SMS does at shutdown."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def _serve():
        with server:
            connection, _ = server.accept()
            with connection:
                connection.sendall(payload)
                connection.shutdown(socket.SHUT_WR)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return server.getsockname()[1], thread


def test_clean_server_close_raises_instead_of_spinning():
    """recv() returns b"" forever after a clean close; readadara would loop on it at 100% CPU.

    The read runs in a thread so that a regression fails the test instead of hanging it.
    """
    port, server_thread = _serve_then_close(event_packet([(1, 1)]))
    reader = build_reader(build_parser().parse_args(["analyze", "--adara-stream", "127.0.0.1", str(port)]))
    outcome: list[object] = []

    def _read_all():
        packets = 0
        try:
            for _packet in reader.read_generator():
                packets += 1
        except ConnectionError as exc:
            outcome.append((packets, exc))

    reader_thread = threading.Thread(target=_read_all, daemon=True)
    reader_thread.start()
    reader_thread.join(timeout=5)
    server_thread.join(timeout=5)

    assert not reader_thread.is_alive(), "reader is still spinning on a closed socket"
    assert len(outcome) == 1
    packets, exc = outcome[0]
    assert packets == 1
    assert "closed by the server" in str(exc)


def test_completed_run_is_written_before_the_stream_is_interrupted(tmp_path: Path, monkeypatch):
    """A live stream ends only on Ctrl-C, which skips the end-of-process CSV write.

    Each completed run must therefore reach the histogram CSV when END_RUN arrives, or a
    live-stream user never gets one.
    """
    pixel_csv = _write_pixel_geometry(tmp_path)
    histogram_csv = tmp_path / "histogram.csv"

    class _RunThenInterruptRunner:
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
            run_statistics=None,
            display_transform=None,
        ):
            _ = (reader, args, q_conversion, plotter, chunk_size, q_conversion_provider, histogram_callback)
            _ = (run_statistics, display_transform)
            self.calls += 1
            if self.calls > 1:
                raise KeyboardInterrupt
            hist = [0] * histogram_bins if hist is None else hist
            histogram_state_callback(hist)
            hist[4950] = 7
            run_complete_callback(object())
            return 1, 7, 7, hist, None

        def run_basic_mode(self, reader, *, chunk_size: int) -> int:
            _ = (reader, chunk_size)
            return 0

    monkeypatch.setattr("live_stream_analysis.analyzer.histogram_runner.build_reader", lambda _args: _Reader())
    monkeypatch.setattr("live_stream_analysis.analyzer.factory.build_reader", lambda _args: _Reader())
    monkeypatch.setattr(
        "live_stream_analysis.analyzer.histogram_runner.create_source_runner",
        lambda _args: _RunThenInterruptRunner(),
    )

    rc = main([*_stream_args(pixel_csv, max_reconnects=-1), "--histogram-output-csv", str(histogram_csv)])

    assert rc == 130
    assert "99.01000000,7.00000000,2.64575131" in histogram_csv.read_text(encoding="utf-8").splitlines()


def test_stream_carries_on_after_end_run_with_a_reset_histogram(tmp_path: Path, monkeypatch):
    """After END_RUN a live stream keeps reading the same connection for the next run.

    The next run must start from an empty histogram, without a reconnect in between, and
    the stream only gives up once a later disconnect exhausts its reconnect budget.
    """
    pixel_csv = _write_pixel_geometry(tmp_path)
    reader_builds: list[int] = []
    finalized_totals: list[int] = []

    class _TwoRunsThenEofRunner:
        def __init__(self):
            self.calls = 0
            self.readers: list[object] = []
            self.starting_totals: list[int] = []

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
            run_statistics=None,
            display_transform=None,
        ):
            _ = (args, q_conversion, plotter, chunk_size, q_conversion_provider, histogram_callback)
            _ = (run_statistics, display_transform)
            self.calls += 1
            self.readers.append(reader)
            hist = [0] * histogram_bins if hist is None else hist
            self.starting_totals.append(sum(hist))
            histogram_state_callback(hist)
            if self.calls == 3:
                return 0, 0, 0, hist, None  # clean end of stream, no END_RUN
            hist[4950] = 7 if self.calls == 1 else 3
            finalized_totals.append(sum(hist))
            run_complete_callback(object())
            return 1, hist[4950], hist[4950], hist, None

        def run_basic_mode(self, reader, *, chunk_size: int) -> int:
            _ = (reader, chunk_size)
            return 0

    runner = _TwoRunsThenEofRunner()

    def _fake_build_reader(_args):
        reader_builds.append(1)
        return _Reader()

    monkeypatch.setattr("live_stream_analysis.analyzer.histogram_runner.build_reader", _fake_build_reader)
    monkeypatch.setattr("live_stream_analysis.analyzer.factory.build_reader", _fake_build_reader)
    monkeypatch.setattr(
        "live_stream_analysis.analyzer.histogram_runner.create_source_runner",
        lambda _args: runner,
    )

    rc = main(_stream_args(pixel_csv, max_reconnects=0))

    # The disconnect after the second run exhausts a zero reconnect budget.
    assert rc == 1
    assert runner.calls == 3
    # Both runs were read from the one connection: no reconnect between them.
    assert len(reader_builds) == 1
    assert runner.readers[0] is runner.readers[1] is runner.readers[2]
    # Each run starts from the histogram END_RUN reset, so the second run is not 7 + 3.
    assert runner.starting_totals == [0, 0, 0]
    assert finalized_totals == [7, 3]
