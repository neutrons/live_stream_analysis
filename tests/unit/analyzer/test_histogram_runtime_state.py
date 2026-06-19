from __future__ import annotations

import argparse

from live_stream_analysis.analyzer.histogram import PixelQConversion, apply_corrections, pixel_tof_to_q
from live_stream_analysis.intersect.service import HistogramRuntimeState


def test_apply_corrections_prefers_runtime_state_over_cli_paths():
    args = argparse.Namespace(
        background_subtraction=None,
        normalization=None,
        histogram_q_bin_size=0.02,
        histogram_q_min=0.0,
        histogram_q_max=0.04,
    )
    runtime_state = HistogramRuntimeState(
        background_values=[1.0, 2.0],
        background_errors=[0.5, 0.25],
        normalization_values=[2.0, 4.0],
        normalization_errors=[0.1, 0.2],
    )

    corrected, error = apply_corrections([5, 10], args, 2, runtime_state=runtime_state)

    assert corrected == [2.0, 2.0]
    assert error[0] > 0.0
    assert error[1] > 0.0


def test_pixel_tof_to_q_uses_runtime_conversion_when_updated():
    runtime_state = HistogramRuntimeState(
        pixel_q_conversion=PixelQConversion(
            q_matrix_constants=[50.0],
            difc=[0.0],
            difa=[0.0],
            tzero=[0.0],
            use=[1],
        )
    )

    q_value = pixel_tof_to_q(runtime_state.pixel_q_conversion, pixel_id=0, tof_us=10.0)

    assert q_value == 5.0


def test_runtime_state_can_block_and_release_adara_file_read():
    runtime_state = HistogramRuntimeState(adara_file_read_released=False)

    assert runtime_state.wait_for_adara_file_read_release(timeout=0.0) is False

    runtime_state.release_adara_file_read()

    assert runtime_state.wait_for_adara_file_read_release(timeout=0.0) is True