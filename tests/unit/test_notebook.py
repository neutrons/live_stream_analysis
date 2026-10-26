"""Unit tests for the notebook helpers that run the analyzer in a child process."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from live_stream_analysis.analyzer.live_plot import BrowserHistogramPlotter
from live_stream_analysis.notebook import analyze_argv, read_csv, run_command, start_analysis
from tests.unit.analyzer.adara_fixtures import event_packet


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    adara = tmp_path / "sample.adara"
    adara.write_bytes(event_packet([(1, 1), (1, 1)]))
    pixel_csv = tmp_path / "pixel_geometry.csv"
    pixel_csv.write_text(
        "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
        encoding="utf-8",
    )
    return adara, pixel_csv


class TestAnalyzeArgv:
    def test_maps_keywords_to_cli_flags(self):
        argv = analyze_argv(
            nexus_file=["a.nxs.h5", Path("b.nxs.h5")],
            histogram_q_max=30,
            live_plot_keep_open=True,
            live_plot_no_open_browser=False,
            background_subtraction=None,
        )

        assert argv == [
            "analyze",
            "--nexus-file",
            "a.nxs.h5",
            "--nexus-file",
            "b.nxs.h5",
            "--histogram-q-max",
            "30",
            "--live-plot-keep-open",
        ]

    def test_single_path_for_repeatable_option(self):
        assert analyze_argv(nexus_file="a.nxs.h5") == ["analyze", "--nexus-file", "a.nxs.h5"]

    def test_host_port_pair(self):
        argv = analyze_argv(adara_stream=("127.0.0.1", 31415))

        assert argv == ["analyze", "--adara-stream", "127.0.0.1", "31415"]

    def test_unknown_option_suggests_closest(self):
        with pytest.raises(TypeError, match="histogram_q_max"):
            analyze_argv(adara_file="x.adara", histogram_qmax=30)

    def test_flag_rejects_non_boolean(self):
        with pytest.raises(TypeError, match="is a flag"):
            analyze_argv(adara_file="x.adara", live_plot_keep_open="yes")

    def test_invalid_choice_raises_instead_of_exiting(self):
        with pytest.raises(ValueError, match="invalid choice"):
            analyze_argv(adara_file="x.adara", normalize_by="bogus")

    def test_requires_exactly_one_source(self):
        with pytest.raises(ValueError, match="required"):
            analyze_argv(histogram_q_max=30)

    def test_intersect_requires_config(self):
        with pytest.raises(ValueError, match="intersect_config"):
            analyze_argv(adara_file="x.adara", enable_intersect=True)


class TestAnalysisRun:
    def test_run_without_plot_writes_outputs(self, tmp_path: Path):
        adara, pixel_csv = _write_inputs(tmp_path)
        histogram_csv = tmp_path / "histogram.csv"

        run = start_analysis(
            live_plot=None,
            adara_file=adara,
            histogram_pixel_geometry_csv=pixel_csv,
            histogram_q_max=100,
            histogram_output_csv=histogram_csv,
            log_file=tmp_path / "run.log",
        )
        run.wait(timeout=60, poll_seconds=0.1)

        assert run.url is None
        assert run.returncode == 0
        assert "Histogrammed events  : 2" in run.log()
        histogram = read_csv(histogram_csv)
        assert histogram["I(Q)"].sum() == pytest.approx(2.0)

    def test_inline_plot_stays_up_until_stopped(self, tmp_path: Path):
        adara, pixel_csv = _write_inputs(tmp_path)

        run = start_analysis(
            adara_file=adara,
            histogram_pixel_geometry_csv=pixel_csv,
            histogram_q_max=100,
            log_file=tmp_path / "run.log",
        )
        try:
            run.wait(timeout=60, poll_seconds=0.1)

            assert run.returncode is None
            state = run.plot_state()
            assert state is not None
            assert state["closed"] is True
            assert sum(state["intensity"]) == pytest.approx(2.0)
            assert "--live-plot-no-open-browser" in run.command
        finally:
            run.stop()

        assert run.returncode is not None
        assert run.plot_state() is None

    def test_failed_run_raises_with_log(self, tmp_path: Path):
        _, pixel_csv = _write_inputs(tmp_path)

        run = start_analysis(
            live_plot=None,
            adara_file=tmp_path / "missing.adara",
            histogram_pixel_geometry_csv=pixel_csv,
            log_file=tmp_path / "run.log",
        )

        with pytest.raises(RuntimeError, match="exited with code 1"):
            run.wait(timeout=60, poll_seconds=0.1)


class TestRunCommand:
    def test_accepts_shell_command_with_continuations(self, tmp_path: Path):
        adara, pixel_csv = _write_inputs(tmp_path)
        histogram_csv = tmp_path / "histogram.csv"

        run = run_command(
            f"live_stream_analysis analyze \\\n"
            f"    --adara-file {adara} \\\n"
            f"    --histogram-pixel-geometry-csv {pixel_csv} \\\n"
            f"    --histogram-q-max 100 \\\n"
            f"    --histogram-output-csv {histogram_csv}",
            log_file=tmp_path / "run.log",
        )
        run.wait(timeout=60, poll_seconds=0.1)

        assert run.returncode == 0
        assert histogram_csv.exists()

    def test_live_plot_url_uses_loopback_for_wildcard_host(self, tmp_path: Path, monkeypatch):
        class _NeverStartedProcess:
            def __init__(self, *_args, **_kwargs):
                return

            def poll(self):
                return 0

        monkeypatch.setattr("live_stream_analysis.notebook.subprocess.Popen", _NeverStartedProcess)

        run = run_command(
            "live_stream_analysis analyze --adara-file x.adara "
            "--live-plot-mode browser --live-plot-host 0.0.0.0 --live-plot-port 8123",
            log_file=tmp_path / "run.log",
        )

        assert run.url == "http://127.0.0.1:8123/"

    def test_rejects_other_subcommands(self):
        with pytest.raises(ValueError, match="analyze"):
            run_command("live_stream_analysis preparer --mode background")


def test_browser_plot_reports_complete_while_kept_open():
    plotter = BrowserHistogramPlotter(0.0, 1.0, 2, "127.0.0.1", 0, open_browser=False)
    waiter = threading.Thread(target=plotter.wait_until_closed, daemon=True)
    waiter.start()
    try:
        deadline = time.monotonic() + 5
        while not plotter._state.snapshot()["closed"] and time.monotonic() < deadline:
            time.sleep(0.01)

        assert plotter._state.snapshot()["closed"] is True
        assert waiter.is_alive()
    finally:
        plotter.close()
    waiter.join(timeout=2)


def test_wait_for_log_raises_when_analyzer_exits_first(tmp_path: Path):
    _, pixel_csv = _write_inputs(tmp_path)

    run = start_analysis(
        live_plot=None,
        adara_file=tmp_path / "missing.adara",
        histogram_pixel_geometry_csv=pixel_csv,
        log_file=tmp_path / "run.log",
    )

    with pytest.raises(RuntimeError, match="before logging"):
        run.wait_for_log("Run-complete callback finished", timeout=60, poll_seconds=0.1)


def test_log_keeps_final_state_of_redrawn_progress_lines(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("live_stream_analysis.notebook.subprocess.Popen", lambda *_args, **_kwargs: _Exited())
    run = run_command("live_stream_analysis analyze --adara-file x.adara", log_file=tmp_path / "run.log")
    run.log_path.write_text("start\nchunk 1/3\rchunk 2/3\rchunk 3/3\ndone\n", encoding="utf-8")

    assert run.log() == "start\nchunk 3/3\ndone"
    assert run.log(lines=1) == "done"


class _Exited:
    def poll(self):
        return 0
