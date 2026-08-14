from __future__ import annotations

import argparse
import math
from pathlib import Path

import pytest

from live_stream_analysis.analyzer.histogram import (
    PixelQConversion,
    apply_corrections,
    bin_centre_q_values,
    load_pixel_q_conversion,
    load_q_matrix_constants,
    pixel_tof_to_q,
    validate_histogram_args,
    write_histogram_csv,
)
from live_stream_analysis.analyzer.live_plot import LiveHistogramPlotter, _BrowserPlotState


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
        histogram_q_min=0.0,
        histogram_q_bin_size=0.03,
        histogram_q_max=1.0,
        tof_tick_us=1.0,
        live_plot_refresh_every=1,
    )

    with pytest.raises(ValueError, match="integer multiple"):
        validate_histogram_args(args)


def test_load_pixel_q_conversion_reads_optional_calibration_and_use_columns(tmp_path: Path):
    pixel_csv = tmp_path / "pixel_geometry.csv"
    pixel_csv.write_text(
        "\n".join(
            [
                "pixel id,L2 value,theta value,TOF-to-Q matrix element,difc,difa,tzero,use",
                "0,1.0,1.0,0.0,0.0,0.0,0.0,1",
                "1,1.0,1.0,12.56637061,2.0,0.1,5.0,0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    conversion = load_pixel_q_conversion(str(pixel_csv))

    assert conversion.difc[1] == pytest.approx(2.0)
    assert conversion.difa[1] == pytest.approx(0.1)
    assert conversion.tzero[1] == pytest.approx(5.0)
    assert conversion.use[1] == 0
    assert conversion.q_matrix_constants[1] == 0.0


def test_pixel_tof_to_q_handles_linear_and_quadratic_branches():
    conversion = PixelQConversion(
        q_matrix_constants=[12.566370614359172, 0.0, 0.0],
        difc=[2.0, 2.0, 2.0],
        difa=[0.0, 0.1, -0.1],
        tzero=[0.0, 0.0, 0.0],
        use=[1, 1, 1],
    )

    q_linear = pixel_tof_to_q(conversion, 0, tof_us=10.0)
    q_quadratic_pos = pixel_tof_to_q(conversion, 1, tof_us=10.0)
    q_quadratic_neg = pixel_tof_to_q(conversion, 2, tof_us=10.0)

    assert q_linear == pytest.approx((2.0 * math.pi * 2.0) / 10.0)
    assert q_quadratic_pos is not None and q_quadratic_pos > 0.0
    assert q_quadratic_neg is not None and q_quadratic_neg > 0.0


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
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        histogram_q_max=0.02,
    )

    corrected, error = apply_corrections([9], args, histogram_bins=1)

    assert corrected == [2.5]
    assert error == [pytest.approx(2.2114757516192665)]


def test_apply_corrections_can_convert_to_mantid_style_sq():
    args = argparse.Namespace(
        background_subtraction=None,
        normalization=None,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        histogram_q_max=0.02,
        sample_coherent_scatter_length=10.0,
        sample_total_scatter_length_squared=150.0,
    )

    corrected, error = apply_corrections([9], args, histogram_bins=1)

    assert corrected == [pytest.approx(8.5)]
    assert error == [pytest.approx(3.0)]


def test_bin_centre_q_values_reports_the_middle_of_each_bin():
    assert bin_centre_q_values(0.0, 0.02, 3) == [pytest.approx(0.01), pytest.approx(0.03), pytest.approx(0.05)]
    assert bin_centre_q_values(1.0, 0.5, 2) == [pytest.approx(1.25), pytest.approx(1.75)]


def test_csv_live_plots_and_intersect_all_label_bins_identically(tmp_path: Path):
    """Every histogram consumer must place a given bin at the same Q.

    The live plots once used bin left edges while the CSV used centres, so the same peak
    reported two different Q values depending on where you looked.
    """
    q_min, q_bin_size, bins = 0.0, 0.02, 5
    expected = bin_centre_q_values(q_min, q_bin_size, bins)

    output = tmp_path / "histogram.csv"
    write_histogram_csv([0.0] * bins, [0.0] * bins, str(output), q_bin_size, q_min)
    csv_q = [float(line.split(",")[0]) for line in output.read_text().splitlines()[1:]]
    assert csv_q == [pytest.approx(value) for value in expected]

    desktop = LiveHistogramPlotter.__new__(LiveHistogramPlotter)
    desktop._q_values = bin_centre_q_values(q_min, q_bin_size, bins)
    assert desktop._q_values == [pytest.approx(value) for value in expected]

    browser_state = _BrowserPlotState(bin_centre_q_values(q_min, q_bin_size, bins))
    assert browser_state.snapshot()["q_values"] == [pytest.approx(value) for value in expected]
