from __future__ import annotations

from pathlib import Path

import pytest

from live_stream_analysis.intersect.data_models import CsvTextRequest
from live_stream_analysis.intersect.service import LiveStreamAnalysisCapability


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