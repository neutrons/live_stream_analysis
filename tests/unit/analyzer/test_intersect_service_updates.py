from __future__ import annotations

from pathlib import Path

import pytest

from live_stream_analysis.intersect.data_models import CsvTextRequest, StartAdaraFileReadRequest
from live_stream_analysis.intersect.service import HistogramRuntimeState, LiveStreamAnalysisCapability


def test_set_background_updates_live_histogram_state(tmp_path: Path):
    csv_text = "\n".join(
        [
            "Q value,I(Q),Error I(Q)",
            "0.01,1.0,0.1",
            "0.03,2.0,0.2",
        ]
    )
    capability = LiveStreamAnalysisCapability()

    response = capability.set_background(CsvTextRequest(csv_text=csv_text))

    assert response.status == "updated"
    assert response.kind == "background"
    assert capability.runtime_state.background_values == [1.0, 2.0]
    assert capability.runtime_state.background_errors == [0.1, 0.2]


def test_set_normalization_updates_live_histogram_state(tmp_path: Path):
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


def test_set_background_rejects_invalid_csv():
    capability = LiveStreamAnalysisCapability()

    with pytest.raises(ValueError, match="Correction CSV"):
        capability.set_background(CsvTextRequest(csv_text="bad,data\n1,2\n"))


def test_set_background_rejects_mismatched_runtime_bins():
    runtime_state = HistogramRuntimeState(correction_bins=1475)
    capability = LiveStreamAnalysisCapability(runtime_state=runtime_state)

    with pytest.raises(ValueError, match="Background correction CSV has 1 bins but expected 1475"):
        capability.set_background(CsvTextRequest(csv_text="Q value,I\(Q\),Error I\(Q\)\n0.0,0.0,0.0\n".replace("\\(", "(").replace("\\)", ")")))


def test_start_adara_file_read_releases_runtime_gate():
    capability = LiveStreamAnalysisCapability()
    capability.runtime_state.configure_adara_file_read_gate(False)

    response = capability.start_adara_file_read(StartAdaraFileReadRequest(release=True))

    assert response.status == "updated"
    assert response.kind == "adara_file_read"
    assert response.released is True
    assert capability.runtime_state.adara_file_read_released is True
    assert capability.runtime_state.wait_for_adara_file_read_release(timeout=0.0) is True