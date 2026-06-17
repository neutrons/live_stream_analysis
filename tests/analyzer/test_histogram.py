from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from live_stream_analysis.analyzer.histogram import apply_corrections, load_q_matrix_constants, validate_histogram_args


def test_load_q_matrix_constants_maps_pixel_ids(tmp_path: Path):
    pixel_csv = tmp_path / "pixel_geometry.csv"
    pixel_csv.write_text(
        "\n".join(
            [
                "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                "0,1.0,1.0,0.0",
                "2,1.0,1.0,9.5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = load_q_matrix_constants(str(pixel_csv))

    assert result == [0.0, 0.0, 9.5]


def test_validate_histogram_args_rejects_non_multiple_q_range():
    args = argparse.Namespace(
        histogram_q_bin_size=0.03,
        histogram_q_max=1.0,
        tof_tick_us=1.0,
        live_plot_refresh_every=1,
    )

    with pytest.raises(ValueError, match="integer multiple"):
        validate_histogram_args(args)


def test_apply_corrections_propagates_background_and_normalization(tmp_path: Path):
    background_csv = tmp_path / "background.csv"
    background_csv.write_text(
        "\n".join(
            [
                "Q value,I(Q),Error I(Q)",
                "0.01000000,4.00000000,3.00000000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    normalization_csv = tmp_path / "normalization.csv"
    normalization_csv.write_text(
        "\n".join(
            [
                "Q value,I(Q),Error I(Q)",
                "0.01000000,2.00000000,0.50000000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        background_subtraction=str(background_csv),
        normalization=str(normalization_csv),
        histogram_q_bin_size=0.02,
        histogram_q_max=0.02,
    )

    corrected, error = apply_corrections([9], args, histogram_bins=1)

    assert corrected == [2.5]
    assert error == [pytest.approx(2.2114757516192665)]
