from __future__ import annotations

import argparse

import pytest

from live_stream_analysis.analyzer.histogram import bin_centre_q_values, load_pixel_q_conversion, pixel_tof_to_q
from live_stream_analysis.analyzer.histogram_runner import _HistogramSession
from live_stream_analysis.correction_totals import DURATION_COLUMN, PROTON_CHARGE_COLUMN, CorrectionTotals
from live_stream_analysis.intersect.data_models import CsvTextRequest, StartAdaraFileReadRequest
from live_stream_analysis.intersect.service import HistogramRuntimeState, LiveStreamAnalysisCapability
from live_stream_analysis.statistics import StatisticsWindow

BINS = 4


def _analysis_capability() -> LiveStreamAnalysisCapability:
    """A capability attached to a running analysis over Q [0, 0.08) in 0.02 bins, as the analyzer builds it."""
    q_values = bin_centre_q_values(0.0, 0.02, BINS)
    runtime_state = HistogramRuntimeState(correction_bins=BINS)
    _HistogramSession(
        args=argparse.Namespace(
            background_subtraction=None,
            normalization=None,
            histogram_q_min=0.0,
            histogram_q_bin_size=0.02,
            histogram_q_max=0.08,
            sample_coherent_scatter_length=None,
            sample_total_scatter_length_squared=None,
            normalize_by="none",
            histogram_snapshot_every=0,
        ),
        histogram_bins=BINS,
        q_values=q_values,
        statistics_window=StatisticsWindow.build(q_values),
        runtime_state=runtime_state,
        plotter=None,
        publisher=None,
        intersect_config=None,
    )
    return LiveStreamAnalysisCapability(runtime_state=runtime_state)


def test_set_background_bins_the_csv_by_q_value_like_the_dashboard_upload():
    """Rows land in the bin their Q falls in, rather than by position, so a CSV listing bins
    out of order or covering only part of the histogram still lines up."""
    csv_text = "Q value,I(Q),Error I(Q)\n0.05,2.0,0.2\n0.01,1.0,0.1\n"
    capability = _analysis_capability()

    response = capability.set_background(CsvTextRequest(csv_text=csv_text))

    assert response.status == "updated"
    assert response.kind == "background"
    assert response.message == "covers 2 of 4 bins"
    assert capability.runtime_state.background_values == [1.0, 0.0, 2.0, 0.0]
    assert capability.runtime_state.background_errors == [0.1, 0.0, 0.2, 0.0]


def test_set_background_goes_through_the_analysis_background_uploader():
    calls: list[tuple[str, str]] = []

    def uploader(csv_text: str, source: str) -> str:
        calls.append((csv_text, source))
        return "covers 1 of 4 bins"

    runtime_state = HistogramRuntimeState()
    runtime_state.background_uploader = uploader
    capability = LiveStreamAnalysisCapability(runtime_state=runtime_state)

    response = capability.set_background(CsvTextRequest(csv_text="Q value,I(Q),Error I(Q)\n0.01,1.0,0.1\n"))

    assert calls == [("Q value,I(Q),Error I(Q)\n0.01,1.0,0.1\n", "INTERSECT set_background")]
    assert response.message == "covers 1 of 4 bins"


def test_set_background_without_a_running_analysis_is_refused():
    """Without an analysis there is no Q grid to bin the background onto."""
    capability = LiveStreamAnalysisCapability()

    with pytest.raises(ValueError, match="no running analysis"):
        capability.set_background(CsvTextRequest(csv_text="Q value,I(Q),Error I(Q)\n0.01,1.0,0.1\n"))


def test_set_normalization_updates_live_histogram_state():
    csv_text = "\n".join(
        [
            "Q value,I(Q),Error I(Q)",
            "0.01,10.0,1.0",
            "0.03,20.0,2.0",
        ]
    )
    capability = LiveStreamAnalysisCapability()

    response = capability.set_normalization(CsvTextRequest(csv_text=csv_text))

    assert response.status == "updated"
    assert response.kind == "normalization"
    assert capability.runtime_state.normalization_values == [10.0, 20.0]
    assert capability.runtime_state.normalization_errors == [1.0, 2.0]


_CSV_WITH_TOTALS = "\n".join(
    [
        f"Q value,I(Q),Error I(Q),{PROTON_CHARGE_COLUMN},{DURATION_COLUMN}",
        "0.01,10.0,1.0,2.0e12,1443.9",
        "0.03,20.0,2.0,2.0e12,1443.9",
    ]
)


def test_set_background_records_the_background_run_totals():
    capability = _analysis_capability()

    capability.set_background(CsvTextRequest(csv_text=_CSV_WITH_TOTALS))

    assert capability.runtime_state.background_totals == CorrectionTotals(proton_charge_pc=2.0e12, duration_s=1443.9)


def test_set_normalization_records_the_normalization_run_totals():
    capability = LiveStreamAnalysisCapability()

    capability.set_normalization(CsvTextRequest(csv_text=_CSV_WITH_TOTALS))

    assert capability.runtime_state.normalization_totals == CorrectionTotals(proton_charge_pc=2.0e12, duration_s=1443.9)


def test_set_background_without_totals_clears_totals_from_a_previous_background():
    """Stale totals from an earlier CSV would rescale the new background by the wrong exposure."""
    capability = _analysis_capability()
    capability.set_background(CsvTextRequest(csv_text=_CSV_WITH_TOTALS))

    capability.set_background(CsvTextRequest(csv_text="Q value,I(Q),Error I(Q)\n0.01,1.0,0.1\n0.03,2.0,0.2\n"))

    assert capability.runtime_state.background_totals == CorrectionTotals()


def test_set_pixel_geometry_conversion_updates_live_histogram_state():
    csv_text = "\n".join(
        [
            "pixel id,L2 value,theta value,TOF-to-Q matrix element,difc,difa,tzero,use",
            "0,1.0,20.0,100.0,10.0,0.0,0.0,1",
            "1,1.1,21.0,110.0,11.0,0.0,0.0,1",
        ]
    )
    capability = LiveStreamAnalysisCapability()

    response = capability.set_pixel_geometry_conversion(CsvTextRequest(csv_text=csv_text))

    assert response.status == "updated"
    assert response.kind == "pixel_geometry_conversion"
    assert capability.runtime_state.pixel_q_conversion is not None
    assert capability.runtime_state.pixel_q_conversion.q_matrix_constants[:2] == [100.0, 110.0]


def test_set_pixel_geometry_conversion_matches_the_file_loader(tmp_path):
    """The INTERSECT update and --histogram-pixel-geometry-csv must convert TOF to Q identically."""
    csv_text = "\n".join(
        [
            "pixel id,L2 value,theta value,TOF-to-Q matrix element,difc,difa,tzero,use",
            "0,1.0,20.0,12000.0,1909.8593,-5.4,5.7,1",
            "2,1.1,21.0,11000.0,1750.7044,0.0,0.0,0",
        ]
    )
    pixel_csv = tmp_path / "pixel_geometry.csv"
    pixel_csv.write_text(csv_text + "\n", encoding="utf-8")
    capability = LiveStreamAnalysisCapability()

    capability.set_pixel_geometry_conversion(CsvTextRequest(csv_text=csv_text))

    assert capability.runtime_state.pixel_q_conversion == load_pixel_q_conversion(str(pixel_csv))
    assert pixel_tof_to_q(capability.runtime_state.pixel_q_conversion, 0, tof_us=2000.0) == pytest.approx(6.0)


def test_set_background_rejects_invalid_csv():
    capability = _analysis_capability()

    with pytest.raises(ValueError, match="Correction CSV"):
        capability.set_background(CsvTextRequest(csv_text="bad,data\n1,2\n"))


def test_set_background_rejects_a_csv_covering_none_of_the_histogram_and_keeps_the_current_one():
    capability = _analysis_capability()
    capability.set_background(CsvTextRequest(csv_text="Q value,I(Q),Error I(Q)\n0.01,1.0,0.1\n"))

    with pytest.raises(ValueError, match="no rows"):
        capability.set_background(CsvTextRequest(csv_text="Q value,I(Q),Error I(Q)\n40.0,1.0,0.1\n"))

    assert capability.runtime_state.background_values == [1.0, 0.0, 0.0, 0.0]


def test_start_adara_file_read_releases_runtime_gate():
    capability = LiveStreamAnalysisCapability()
    capability.runtime_state.configure_adara_file_read_gate(False)

    response = capability.start_adara_file_read(StartAdaraFileReadRequest(release=True))

    assert response.status == "updated"
    assert response.kind == "adara_file_read"
    assert response.released is True
    assert capability.runtime_state.adara_file_read_released is True
    assert capability.runtime_state.wait_for_adara_file_read_release(timeout=0.0) is True
