"""Replacing the background subtraction while an analysis is running (#43).

The live histogram accumulates raw counts and the corrections are applied each time it is
displayed or published, so swapping the background between packets changes every later
corrected I(Q) without touching what has been counted.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from live_stream_analysis.analyzer.histogram import (
    apply_corrections,
    bin_centre_q_values,
    parse_correction_csv,
    replace_background,
)
from live_stream_analysis.analyzer.histogram_runner import _HistogramSession
from live_stream_analysis.analyzer.live_plot import BrowserHistogramPlotter, _browser_plot_html
from live_stream_analysis.correction_totals import DURATION_COLUMN, PROTON_CHARGE_COLUMN, CorrectionTotals
from live_stream_analysis.intersect.service import HistogramRuntimeState
from live_stream_analysis.main import main
from live_stream_analysis.statistics import StatisticsWindow
from tests.unit.analyzer.adara_fixtures import event_packet

BINS = 4
Q_VALUES = bin_centre_q_values(0.0, 0.02, BINS)


def _args(**overrides) -> argparse.Namespace:
    values = {
        "background_subtraction": None,
        "normalization": None,
        "histogram_q_min": 0.0,
        "histogram_q_bin_size": 0.02,
        "histogram_q_max": 0.02 * BINS,
        "sample_coherent_scatter_length": None,
        "sample_total_scatter_length_squared": None,
        "normalize_by": "none",
        "histogram_snapshot_every": 0,
        "histogram_output_csv": None,
        "statistics_output_csv": None,
        "statistics_map_output_csv": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _csv(rows: list[tuple[float, float, float]]) -> str:
    return "Q value,I(Q),Error I(Q)\n" + "".join(f"{q},{i},{e}\n" for q, i, e in rows)


class TestParseCorrectionCsv:
    def test_maps_rows_to_bins_by_q_value_like_the_startup_file(self):
        """Rows land in the bin their Q falls in, so a CSV listing bins out of order still lines up."""
        values, errors, _ = parse_correction_csv(
            _csv([(0.05, 3.0, 0.3), (0.01, 1.0, 0.1)]).splitlines(),
            expected_bins=BINS,
            q_bin_size=0.02,
            q_min=0.0,
            q_max=0.08,
        )

        assert values == [1.0, 0.0, 3.0, 0.0]
        assert errors == [0.1, 0.0, 0.3, 0.0]

    def test_reads_the_exposure_totals(self):
        text = f"Q value,I(Q),Error I(Q),{PROTON_CHARGE_COLUMN},{DURATION_COLUMN}\n0.01,1.0,0.1,2e12,1443.9\n"

        _, _, totals = parse_correction_csv(
            text.splitlines(), expected_bins=BINS, q_bin_size=0.02, q_min=0.0, q_max=0.08
        )

        assert totals == CorrectionTotals(proton_charge_pc=2.0e12, duration_s=1443.9)

    def test_rejects_missing_columns(self):
        with pytest.raises(ValueError, match="Q value"):
            parse_correction_csv(["a,b\n", "1,2\n"], expected_bins=BINS, q_bin_size=0.02, q_min=0.0, q_max=0.08)


class TestReplaceBackground:
    def test_installs_values_errors_and_totals_together(self):
        runtime_state = HistogramRuntimeState()

        matched = replace_background(runtime_state, _args(), BINS, _csv([(0.01, 1.0, 0.1), (0.03, 2.0, 0.2)]))

        assert matched == 2
        assert runtime_state.background() == ([1.0, 2.0, 0.0, 0.0], [0.1, 0.2, 0.0, 0.0], CorrectionTotals())

    def test_rejects_a_background_that_covers_none_of_the_histogram(self):
        """Almost certainly the wrong file, or one reduced on a different Q range: subtracting
        zeros from every bin would silently look like no background at all."""
        runtime_state = HistogramRuntimeState()
        runtime_state.set_background([5.0] * BINS, [0.5] * BINS, CorrectionTotals())

        with pytest.raises(ValueError, match="no rows"):
            replace_background(runtime_state, _args(), BINS, _csv([(40.0, 1.0, 0.1)]))

        assert runtime_state.background()[0] == [5.0] * BINS

    def test_a_malformed_upload_leaves_the_current_background_in_place(self):
        runtime_state = HistogramRuntimeState()
        runtime_state.set_background([5.0] * BINS, [0.5] * BINS, CorrectionTotals())

        with pytest.raises(ValueError):
            replace_background(runtime_state, _args(), BINS, "Q value,I(Q),Error I(Q)\n0.01,not-a-number,0.1\n")

        assert runtime_state.background()[0] == [5.0] * BINS


class TestRuntimeStateBackground:
    def test_no_background_until_one_is_set(self):
        assert HistogramRuntimeState().background() is None

    def test_apply_corrections_reads_the_background_set_on_the_runtime_state(self):
        runtime_state = HistogramRuntimeState()
        runtime_state.set_background([1.0, 1.0, 1.0, 1.0], [0.0] * BINS, CorrectionTotals())

        corrected, _ = apply_corrections([4, 4, 4, 4], _args(), BINS, runtime_state=runtime_state)

        assert corrected == [3.0, 3.0, 3.0, 3.0]


class _RecordingPlotter:
    accepts_updates_from_any_thread = True

    def __init__(self):
        self.frames: list[list[float]] = []
        self.backgrounds: list[tuple[str, str]] = []

    def update(self, intensity, _error, _relative_uncertainty, _statistics=None):
        self.frames.append(intensity)

    def background_replaced(self, source: str, message: str) -> None:
        self.backgrounds.append((source, message))

    def close(self):
        return None


class _MainThreadOnlyPlotter(_RecordingPlotter):
    """Like the matplotlib window, which must only be drawn from the analyzer's thread."""

    accepts_updates_from_any_thread = False


def _session(plotter=None, **arg_overrides) -> _HistogramSession:
    runtime_state = HistogramRuntimeState()
    runtime_state.set_background([1.0] * BINS, [0.0] * BINS, CorrectionTotals())
    window = StatisticsWindow.build(Q_VALUES)
    runtime_state.run_statistics.series.configure(0.0, window)
    return _HistogramSession(
        args=_args(**arg_overrides),
        histogram_bins=BINS,
        q_values=Q_VALUES,
        statistics_window=window,
        runtime_state=runtime_state,
        plotter=plotter,
        publisher=None,
        intersect_config=None,
    )


def test_swapping_the_background_mid_run_changes_later_output_but_not_the_counts():
    """Answers the question in #43: the counts accumulate uncorrected, and every refresh,
    snapshot and publish re-applies whatever background is current at that moment."""
    session = _session()
    runtime_state = session.runtime_state
    hist = [10, 10, 10, 10]

    before, _ = session.corrected(hist)
    replace_background(runtime_state, session.args, BINS, _csv([(q, 4.0, 0.0) for q in Q_VALUES]))
    after, _ = session.corrected(hist)

    assert before == [9.0] * BINS
    assert after == [6.0] * BINS
    assert hist == [10, 10, 10, 10]


class TestSessionUpload:
    def test_redraws_the_plot_at_once_rather_than_waiting_for_the_next_packet(self):
        """Redraws are driven by event packets, so with the beam off an accepted upload would
        otherwise look as though it had been ignored."""
        plotter = _RecordingPlotter()
        session = _session(plotter)
        session.set_active_hist([10, 10, 10, 10])

        session.upload_background(_csv([(q, 4.0, 0.0) for q in Q_VALUES]), "empty_can.csv")

        assert plotter.frames == [[6.0] * BINS]

    def test_does_not_draw_a_plotter_tied_to_the_analyzer_thread(self):
        """INTERSECT calls arrive on the SDK's thread; drawing matplotlib from there is unsafe."""
        plotter = _MainThreadOnlyPlotter()
        session = _session(plotter)
        session.set_active_hist([10, 10, 10, 10])

        session.upload_background(_csv([(q, 4.0, 0.0) for q in Q_VALUES]), "INTERSECT set_background")

        assert plotter.frames == []
        assert session.runtime_state.background()[0] == [4.0] * BINS

    def test_tells_the_plotter_which_background_is_now_active(self):
        plotter = _RecordingPlotter()
        session = _session(plotter)

        session.upload_background(_csv([(0.01, 4.0, 0.0)]), "INTERSECT set_background")

        assert plotter.backgrounds == [("INTERSECT set_background", "covers 1 of 4 bins")]

    def test_a_rejected_background_does_not_change_what_the_plotter_reports(self):
        plotter = _RecordingPlotter()

        with pytest.raises(ValueError):
            _session(plotter).upload_background("bad,data\n1,2\n", "wrong.csv")

        assert plotter.backgrounds == []

    def test_before_any_counts_it_only_installs_the_background(self):
        plotter = _RecordingPlotter()
        session = _session(plotter)

        session.upload_background(_csv([(q, 4.0, 0.0) for q in Q_VALUES]), "empty_can.csv")

        assert plotter.frames == []
        assert session.runtime_state.background()[0] == [4.0] * BINS

    def test_summarizes_the_coverage(self):
        message = _session().upload_background(_csv([(0.01, 4.0, 0.0), (0.03, 4.0, 0.0)]), "x.csv")

        assert message == "covers 2 of 4 bins"

    def test_warns_when_the_background_cannot_be_put_on_the_sample_exposure_scale(self):
        message = _session(normalize_by="proton-charge").upload_background(_csv([(0.01, 4.0, 0.0)]), "x.csv")

        assert "records no exposure total" in message


class TestBrowserUploadEndpoint:
    @pytest.fixture
    def uploads(self):
        received: list[tuple[str, str]] = []

        def handler(csv_text: str, source: str) -> str:
            if "bad" in csv_text:
                raise ValueError("Correction CSV must include columns: 'Q value', 'I(Q)', 'Error I(Q)'")
            received.append((csv_text, source))
            return "matched 2 of 4 bins"

        plotter = BrowserHistogramPlotter(
            0.0, 0.02, BINS, "127.0.0.1", 0, open_browser=False, background_handler=handler
        )
        try:
            yield plotter, received
        finally:
            plotter.close()

    @staticmethod
    def _post(url: str, body: bytes, name: str = "empty_can.csv"):
        request = urllib.request.Request(
            f"{url}background?name={name}", data=body, method="POST", headers={"Content-Type": "text/csv"}
        )
        return urllib.request.urlopen(request, timeout=5)

    @staticmethod
    def _state(url: str) -> dict:
        with urllib.request.urlopen(f"{url}state", timeout=5) as response:
            return json.loads(response.read())

    def test_hands_the_uploaded_csv_and_its_file_name_to_the_handler(self, uploads):
        plotter, received = uploads
        body = _csv([(0.01, 1.0, 0.1)]).encode("utf-8")

        with self._post(plotter.url, body) as response:
            reply = json.loads(response.read())

        assert received == [(body.decode("utf-8"), "empty_can.csv")]
        assert reply == {"status": "updated", "source": "empty_can.csv", "message": "matched 2 of 4 bins"}
        assert self._state(plotter.url)["background"]["uploads_enabled"] is True

    def test_shows_whichever_background_was_last_reported_active(self, uploads):
        """Set by the session for dashboard uploads and INTERSECT set_background alike."""
        plotter, _ = uploads

        plotter.background_replaced("INTERSECT set_background", "covers 2 of 4 bins")

        background = self._state(plotter.url)["background"]
        assert background["source"] == "INTERSECT set_background"
        assert background["message"] == "covers 2 of 4 bins"
        assert background["updated_at"] is not None

    def test_reports_a_rejected_csv_and_keeps_the_previous_source(self, uploads):
        plotter, _ = uploads

        with pytest.raises(urllib.error.HTTPError) as caught:
            self._post(plotter.url, b"bad,data\n1,2\n")

        assert caught.value.code == 400
        assert "Q value" in json.loads(caught.value.read())["error"]
        assert self._state(plotter.url)["background"]["source"] is None

    def test_rejects_an_oversized_upload_without_handing_it_on(self, uploads, monkeypatch):
        plotter, received = uploads
        monkeypatch.setattr(BrowserHistogramPlotter, "MAX_UPLOAD_BYTES", 64)

        with pytest.raises(urllib.error.HTTPError) as caught:
            self._post(plotter.url, b"x" * 65)

        assert caught.value.code == 413
        assert received == []

    def test_shows_the_startup_background_as_the_initial_source(self):
        plotter = BrowserHistogramPlotter(
            0.0, 0.02, BINS, "127.0.0.1", 0, open_browser=False, background_source="background.csv"
        )
        try:
            background = self._state(plotter.url)["background"]
        finally:
            plotter.close()

        assert background["source"] == "background.csv"
        assert background["uploads_enabled"] is False

    def test_uploads_are_refused_when_no_handler_is_wired(self):
        plotter = BrowserHistogramPlotter(0.0, 0.02, BINS, "127.0.0.1", 0, open_browser=False)
        try:
            with pytest.raises(urllib.error.HTTPError) as caught:
                self._post(plotter.url, _csv([(0.01, 1.0, 0.1)]).encode("utf-8"))
        finally:
            plotter.close()

        assert caught.value.code == 404


def test_dashboard_page_has_a_background_upload_control():
    html = _browser_plot_html()

    assert 'id="background-upload"' in html
    assert 'id="background-file"' in html
    assert "/background?name=" in html


def test_analyzer_wires_dashboard_uploads_into_the_running_histogram(tmp_path: Path, monkeypatch):
    """An upload arriving after the first packet is what the run's final I(Q) is corrected with."""
    adara = tmp_path / "run.adara"
    adara.write_bytes(event_packet([(1, 1), (1, 1)]))
    pixel_csv = tmp_path / "pixel_geometry.csv"
    pixel_csv.write_text(
        "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
        encoding="utf-8",
    )
    histogram_csv = tmp_path / "histogram.csv"
    factory_kwargs: dict = {}

    class _UploadingPlotter:
        uploaded = False

        def update(self, *_args, **_kwargs):
            if not self.uploaded:
                self.uploaded = True
                factory_kwargs["background_handler"](_csv([(99.01, 0.5, 0.0)]), "empty_can.csv")

        def background_replaced(self, _source, _message):
            return None

        def close(self):
            return None

    def _factory(_args, _histogram_bins, **kwargs):
        factory_kwargs.update(kwargs)
        return _UploadingPlotter()

    monkeypatch.setattr("live_stream_analysis.analyzer.histogram_runner.create_live_histogram_plotter", _factory)

    rc = main(
        [
            "analyze",
            "--adara-file",
            str(adara),
            "--histogram-pixel-geometry-csv",
            str(pixel_csv),
            "--histogram-q-max",
            "100",
            "--histogram-q-bin-size",
            "0.02",
            "--histogram-output-csv",
            str(histogram_csv),
            "--live-plot-mode",
            "browser",
        ]
    )

    assert rc == 0
    assert factory_kwargs["background_source"] is None
    # INTERSECT set_background goes through the very same session method as the dashboard.
    session = factory_kwargs["background_handler"].__self__
    assert session.runtime_state.background_uploader == session.upload_background
    assert "99.01000000,1.50000000," in histogram_csv.read_text(encoding="utf-8")
