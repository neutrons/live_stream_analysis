"""Replacing the background (#43) or normalization (#44) while an analysis is running.

The live histogram accumulates raw counts and the corrections are applied each time it is
displayed or published, so swapping either correction between packets changes every later
corrected I(Q) without touching what has been counted. Dashboard uploads and INTERSECT
set_background / set_normalization share one path, exercised here.
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
    replace_correction,
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
KINDS = ["background", "normalization"]


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


def _installed(runtime_state: HistogramRuntimeState, kind: str):
    return getattr(runtime_state, kind)()


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


class TestReplaceCorrection:
    @pytest.mark.parametrize("kind", KINDS)
    def test_installs_values_errors_and_totals_together(self, kind):
        runtime_state = HistogramRuntimeState()

        matched = replace_correction(runtime_state, _args(), BINS, kind, _csv([(0.01, 1.0, 0.1), (0.03, 2.0, 0.2)]))

        assert matched == 2
        assert _installed(runtime_state, kind) == (
            [1.0, 2.0, 0.0, 0.0],
            [0.1, 0.2, 0.0, 0.0],
            CorrectionTotals(),
        )

    @pytest.mark.parametrize("kind", KINDS)
    def test_rejects_a_correction_that_covers_none_of_the_histogram(self, kind):
        """Almost certainly the wrong file, or one reduced on a different Q range: applying
        zeros to every bin would silently look like no correction, or zero the whole I(Q)."""
        runtime_state = HistogramRuntimeState()
        getattr(runtime_state, f"set_{kind}")([5.0] * BINS, [0.5] * BINS, CorrectionTotals())

        with pytest.raises(ValueError, match="no rows"):
            replace_correction(runtime_state, _args(), BINS, kind, _csv([(40.0, 1.0, 0.1)]))

        assert _installed(runtime_state, kind)[0] == [5.0] * BINS

    @pytest.mark.parametrize("kind", KINDS)
    def test_a_malformed_upload_leaves_the_current_correction_in_place(self, kind):
        runtime_state = HistogramRuntimeState()
        getattr(runtime_state, f"set_{kind}")([5.0] * BINS, [0.5] * BINS, CorrectionTotals())

        with pytest.raises(ValueError):
            replace_correction(runtime_state, _args(), BINS, kind, "Q value,I(Q),Error I(Q)\n0.01,not-a-number,0.1\n")

        assert _installed(runtime_state, kind)[0] == [5.0] * BINS

    def test_a_normalization_only_covers_the_bins_it_can_divide_by(self):
        """apply_corrections zeroes I(Q) wherever V <= 0, so those bins are not covered."""
        matched = replace_correction(
            HistogramRuntimeState(),
            _args(),
            BINS,
            "normalization",
            _csv([(0.01, 2.0, 0.1), (0.03, 0.0, 0.1), (0.05, -1.0, 0.1)]),
        )

        assert matched == 1

    def test_rejects_a_normalization_with_nothing_to_divide_by(self):
        runtime_state = HistogramRuntimeState()

        with pytest.raises(ValueError, match="no rows"):
            replace_correction(runtime_state, _args(), BINS, "normalization", _csv([(0.01, 0.0, 0.1)]))

        assert runtime_state.normalization() is None

    def test_rejects_an_unknown_kind(self):
        with pytest.raises(ValueError, match="kind"):
            replace_correction(HistogramRuntimeState(), _args(), BINS, "vanadium", _csv([(0.01, 1.0, 0.1)]))


class TestRuntimeStateCorrections:
    @pytest.mark.parametrize("kind", KINDS)
    def test_none_until_one_is_set(self, kind):
        assert _installed(HistogramRuntimeState(), kind) is None

    def test_apply_corrections_reads_both_corrections_set_on_the_runtime_state(self):
        runtime_state = HistogramRuntimeState()
        runtime_state.set_background([1.0] * BINS, [0.0] * BINS, CorrectionTotals())
        runtime_state.set_normalization([3.0] * BINS, [0.0] * BINS, CorrectionTotals())

        corrected, _ = apply_corrections([4, 4, 4, 4], _args(), BINS, runtime_state=runtime_state)

        assert corrected == [1.0, 1.0, 1.0, 1.0]


class _RecordingPlotter:
    accepts_updates_from_any_thread = True

    def __init__(self):
        self.frames: list[list[float]] = []
        self.corrections: list[tuple[str, str, str]] = []

    def update(self, intensity, _error, _relative_uncertainty, _statistics=None):
        self.frames.append(intensity)

    def correction_replaced(self, kind: str, source: str, message: str) -> None:
        self.corrections.append((kind, source, message))

    def close(self):
        return None


class _MainThreadOnlyPlotter(_RecordingPlotter):
    """Like the matplotlib window, which must only be drawn from the analyzer's thread."""

    accepts_updates_from_any_thread = False


def _session(plotter=None, **arg_overrides) -> _HistogramSession:
    """A session subtracting a background of 1 from every bin, with no normalization yet."""
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


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("background", [6.0] * BINS), ("normalization", [9.0 / 4.0] * BINS)],
)
def test_swapping_a_correction_mid_run_changes_later_output_but_not_the_counts(kind, expected):
    """Answers the question in #43 and #44: the counts accumulate uncorrected, and every
    refresh, snapshot and publish re-applies whatever corrections are current at that moment."""
    session = _session()
    hist = [10, 10, 10, 10]

    before, _ = session.corrected(hist)
    replace_correction(session.runtime_state, session.args, BINS, kind, _csv([(q, 4.0, 0.0) for q in Q_VALUES]))
    after, _ = session.corrected(hist)

    assert before == [9.0] * BINS
    assert after == expected
    assert hist == [10, 10, 10, 10]


class TestSessionUpload:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [("background", [6.0] * BINS), ("normalization", [9.0 / 4.0] * BINS)],
    )
    def test_redraws_the_plot_at_once_rather_than_waiting_for_the_next_packet(self, kind, expected):
        """Redraws are driven by event packets, so with the beam off an accepted upload would
        otherwise look as though it had been ignored."""
        plotter = _RecordingPlotter()
        session = _session(plotter)
        session.set_active_hist([10, 10, 10, 10])

        session.upload_correction(kind, _csv([(q, 4.0, 0.0) for q in Q_VALUES]), "upload.csv")

        assert plotter.frames == [expected]

    @pytest.mark.parametrize("kind", KINDS)
    def test_does_not_draw_a_plotter_tied_to_the_analyzer_thread(self, kind):
        """INTERSECT calls arrive on the SDK's thread; drawing matplotlib from there is unsafe."""
        plotter = _MainThreadOnlyPlotter()
        session = _session(plotter)
        session.set_active_hist([10, 10, 10, 10])

        session.upload_correction(kind, _csv([(q, 4.0, 0.0) for q in Q_VALUES]), f"INTERSECT set_{kind}")

        assert plotter.frames == []
        assert _installed(session.runtime_state, kind)[0] == [4.0] * BINS

    @pytest.mark.parametrize("kind", KINDS)
    def test_tells_the_plotter_which_correction_is_now_active(self, kind):
        plotter = _RecordingPlotter()

        _session(plotter).upload_correction(kind, _csv([(0.01, 4.0, 0.0)]), f"INTERSECT set_{kind}")

        assert plotter.corrections == [(kind, f"INTERSECT set_{kind}", "covers 1 of 4 bins")]

    @pytest.mark.parametrize("kind", KINDS)
    def test_a_rejected_correction_does_not_change_what_the_plotter_reports(self, kind):
        plotter = _RecordingPlotter()

        with pytest.raises(ValueError):
            _session(plotter).upload_correction(kind, "bad,data\n1,2\n", "wrong.csv")

        assert plotter.corrections == []

    @pytest.mark.parametrize("kind", KINDS)
    def test_before_any_counts_it_only_installs_the_correction(self, kind):
        plotter = _RecordingPlotter()
        session = _session(plotter)

        session.upload_correction(kind, _csv([(q, 4.0, 0.0) for q in Q_VALUES]), "upload.csv")

        assert plotter.frames == []
        assert _installed(session.runtime_state, kind)[0] == [4.0] * BINS

    @pytest.mark.parametrize("kind", KINDS)
    def test_summarizes_the_coverage(self, kind):
        message = _session().upload_correction(kind, _csv([(0.01, 4.0, 0.0), (0.03, 4.0, 0.0)]), "x.csv")

        assert message == "covers 2 of 4 bins"

    @pytest.mark.parametrize(
        ("kind", "consequence"),
        [("background", "subtracted on the sample's scale"), ("normalization", "applied unscaled")],
    )
    def test_warns_when_the_correction_cannot_be_put_on_the_sample_exposure_scale(self, kind, consequence):
        message = _session(normalize_by="proton-charge").upload_correction(kind, _csv([(0.01, 4.0, 0.0)]), "x.csv")

        assert "records no exposure total" in message
        assert consequence in message

    def test_rejects_an_unknown_kind_without_touching_either_correction(self):
        session = _session()

        with pytest.raises(ValueError, match="kind"):
            session.upload_correction("vanadium", _csv([(0.01, 4.0, 0.0)]), "x.csv")

        assert session.runtime_state.background()[0] == [1.0] * BINS
        assert session.runtime_state.normalization() is None


class TestBrowserUploadEndpoint:
    @pytest.fixture
    def uploads(self):
        received: list[tuple[str, str, str]] = []

        def handler(kind: str, csv_text: str, source: str) -> str:
            if "bad" in csv_text:
                raise ValueError("Correction CSV must include columns: 'Q value', 'I(Q)', 'Error I(Q)'")
            received.append((kind, csv_text, source))
            return "matched 2 of 4 bins"

        plotter = BrowserHistogramPlotter(
            0.0, 0.02, BINS, "127.0.0.1", 0, open_browser=False, correction_handler=handler
        )
        try:
            yield plotter, received
        finally:
            plotter.close()

    @staticmethod
    def _post(url: str, body: bytes, kind: str = "background", name: str = "upload.csv"):
        request = urllib.request.Request(
            f"{url}{kind}?name={name}", data=body, method="POST", headers={"Content-Type": "text/csv"}
        )
        return urllib.request.urlopen(request, timeout=5)

    @staticmethod
    def _state(url: str) -> dict:
        with urllib.request.urlopen(f"{url}state", timeout=5) as response:
            return json.loads(response.read())

    @pytest.mark.parametrize("kind", KINDS)
    def test_hands_the_uploaded_csv_its_kind_and_file_name_to_the_handler(self, uploads, kind):
        plotter, received = uploads
        body = _csv([(0.01, 1.0, 0.1)]).encode("utf-8")

        with self._post(plotter.url, body, kind=kind, name="vanadium.csv") as response:
            reply = json.loads(response.read())

        assert received == [(kind, body.decode("utf-8"), "vanadium.csv")]
        assert reply == {"status": "updated", "kind": kind, "source": "vanadium.csv", "message": "matched 2 of 4 bins"}
        assert self._state(plotter.url)[kind]["uploads_enabled"] is True

    @pytest.mark.parametrize("kind", KINDS)
    def test_shows_whichever_correction_was_last_reported_active(self, uploads, kind):
        """Set by the session for dashboard uploads and INTERSECT updates alike."""
        plotter, _ = uploads

        plotter.correction_replaced(kind, f"INTERSECT set_{kind}", "covers 2 of 4 bins")

        state = self._state(plotter.url)
        assert state[kind]["source"] == f"INTERSECT set_{kind}"
        assert state[kind]["message"] == "covers 2 of 4 bins"
        assert state[kind]["updated_at"] is not None
        other = "normalization" if kind == "background" else "background"
        assert state[other]["source"] is None

    @pytest.mark.parametrize("kind", KINDS)
    def test_reports_a_rejected_csv_and_keeps_the_previous_source(self, uploads, kind):
        plotter, _ = uploads

        with pytest.raises(urllib.error.HTTPError) as caught:
            self._post(plotter.url, b"bad,data\n1,2\n", kind=kind)

        assert caught.value.code == 400
        assert "Q value" in json.loads(caught.value.read())["error"]
        assert self._state(plotter.url)[kind]["source"] is None

    def test_rejects_an_oversized_upload_without_handing_it_on(self, uploads, monkeypatch):
        plotter, received = uploads
        monkeypatch.setattr(BrowserHistogramPlotter, "MAX_UPLOAD_BYTES", 64)

        with pytest.raises(urllib.error.HTTPError) as caught:
            self._post(plotter.url, b"x" * 65, kind="normalization")

        assert caught.value.code == 413
        assert received == []

    def test_unknown_correction_paths_are_not_found(self, uploads):
        plotter, received = uploads

        with pytest.raises(urllib.error.HTTPError) as caught:
            self._post(plotter.url, _csv([(0.01, 1.0, 0.1)]).encode("utf-8"), kind="vanadium")

        assert caught.value.code == 404
        assert received == []

    def test_shows_the_startup_corrections_as_the_initial_sources(self):
        plotter = BrowserHistogramPlotter(
            0.0,
            0.02,
            BINS,
            "127.0.0.1",
            0,
            open_browser=False,
            correction_sources={"background": "background.csv", "normalization": "normalization.csv"},
        )
        try:
            state = self._state(plotter.url)
        finally:
            plotter.close()

        assert state["background"]["source"] == "background.csv"
        assert state["normalization"]["source"] == "normalization.csv"
        assert state["background"]["uploads_enabled"] is False

    @pytest.mark.parametrize("kind", KINDS)
    def test_uploads_are_refused_when_no_handler_is_wired(self, kind):
        plotter = BrowserHistogramPlotter(0.0, 0.02, BINS, "127.0.0.1", 0, open_browser=False)
        try:
            with pytest.raises(urllib.error.HTTPError) as caught:
                self._post(plotter.url, _csv([(0.01, 1.0, 0.1)]).encode("utf-8"), kind=kind)
        finally:
            plotter.close()

        assert caught.value.code == 404


@pytest.mark.parametrize("kind", KINDS)
def test_dashboard_page_has_an_upload_control_for_each_correction(kind):
    html = _browser_plot_html()

    assert f'id="{kind}-upload"' in html
    assert f'id="{kind}-file"' in html
    assert f'id="{kind}-source"' in html


@pytest.mark.parametrize(
    ("kind", "csv_row", "expected_row"),
    [
        # Two counts at Q 99.01, less a background of 0.5.
        ("background", (99.01, 0.5, 0.0), "99.01000000,1.50000000,"),
        # Two counts at Q 99.01, over a normalization of 4.
        ("normalization", (99.01, 4.0, 0.0), "99.01000000,0.50000000,"),
    ],
)
def test_analyzer_wires_dashboard_uploads_into_the_running_histogram(
    tmp_path: Path, monkeypatch, kind, csv_row, expected_row
):
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
                factory_kwargs["correction_handler"](kind, _csv([csv_row]), "upload.csv")

        def correction_replaced(self, _kind, _source, _message):
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
    assert factory_kwargs["correction_sources"] == {"background": None, "normalization": None}
    assert expected_row in histogram_csv.read_text(encoding="utf-8")
    # INTERSECT set_background / set_normalization go through the very same session method.
    session = factory_kwargs["correction_handler"].__self__
    assert session.runtime_state.correction_uploader == session.upload_correction
