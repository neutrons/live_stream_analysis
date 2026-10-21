from __future__ import annotations

import argparse
import math
from pathlib import Path

import pytest

from live_stream_analysis.analyzer.histogram import (
    PixelQConversion,
    apply_corrections,
    bin_centre_q_values,
    compute_relative_uncertainty,
    load_pixel_q_conversion,
    load_q_matrix_constants,
    pixel_tof_to_q,
    validate_histogram_args,
    write_histogram_csv,
)
from live_stream_analysis.analyzer.live_plot import LiveHistogramPlotter, _BrowserPlotState, maybe_update_live_plot
from live_stream_analysis.correction_totals import DURATION_COLUMN, PROTON_CHARGE_COLUMN, CorrectionTotals
from live_stream_analysis.intersect.service import HistogramRuntimeState


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


def _normalization_args(normalize_by: str) -> argparse.Namespace:
    return argparse.Namespace(
        background_subtraction=None,
        normalization=None,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        histogram_q_max=0.04,
        sample_coherent_scatter_length=None,
        sample_total_scatter_length_squared=None,
        normalize_by=normalize_by,
    )


def _runtime_state_with(elapsed_seconds: float, proton_charge_pc: float) -> HistogramRuntimeState:
    state = HistogramRuntimeState()
    state.run_statistics.accumulator.observe_progress(elapsed_seconds, proton_charge_pc)
    return state


def test_apply_corrections_normalizes_by_proton_charge():
    state = _runtime_state_with(elapsed_seconds=10.0, proton_charge_pc=4.0)

    corrected, error = apply_corrections([16, 64], _normalization_args("proton-charge"), 2, runtime_state=state)

    # Intensity divides by the charge; variance divides by its square, so sigma also divides by it.
    assert corrected == [pytest.approx(4.0), pytest.approx(16.0)]
    assert error == [pytest.approx(1.0), pytest.approx(2.0)]


def test_apply_corrections_normalizes_by_elapsed_time():
    state = _runtime_state_with(elapsed_seconds=4.0, proton_charge_pc=1000.0)

    corrected, error = apply_corrections([16, 64], _normalization_args("time"), 2, runtime_state=state)

    assert corrected == [pytest.approx(4.0), pytest.approx(16.0)]
    assert error == [pytest.approx(1.0), pytest.approx(2.0)]


def test_apply_corrections_leaves_counts_unscaled_before_anything_accumulates():
    """Dividing by a not-yet-accumulated charge would blow the histogram up to infinity."""
    state = _runtime_state_with(elapsed_seconds=0.0, proton_charge_pc=0.0)

    corrected, error = apply_corrections([16, 64], _normalization_args("proton-charge"), 2, runtime_state=state)

    assert corrected == [pytest.approx(16.0), pytest.approx(64.0)]
    assert error == [pytest.approx(4.0), pytest.approx(8.0)]


def test_apply_corrections_defaults_to_no_accumulation_normalization():
    state = _runtime_state_with(elapsed_seconds=10.0, proton_charge_pc=4.0)

    corrected, _ = apply_corrections([16, 64], _normalization_args("none"), 2, runtime_state=state)

    assert corrected == [pytest.approx(16.0), pytest.approx(64.0)]


def test_relative_uncertainty_is_invariant_under_accumulation_normalization():
    """The #38 decay metric must not shift depending on whether #37 normalization is on.

    Normalizing scales I and sigma by the same divisor, so sigma/I(Q) -- and therefore the
    whole error-decay curve -- has to come out identical either way.
    """
    state = _runtime_state_with(elapsed_seconds=7.0, proton_charge_pc=1234.5)
    hist = [16, 64, 100]

    plain, plain_error = apply_corrections(hist, _normalization_args("none"), 3, runtime_state=state)
    scaled, scaled_error = apply_corrections(hist, _normalization_args("proton-charge"), 3, runtime_state=state)

    assert scaled != plain
    assert compute_relative_uncertainty(scaled, scaled_error) == [
        pytest.approx(value) for value in compute_relative_uncertainty(plain, plain_error)
    ]


def _state_with_corrections(
    *,
    elapsed_seconds: float,
    proton_charge_pc: float,
    background: tuple[list[float], list[float], CorrectionTotals | None] | None = None,
    normalization: tuple[list[float], list[float], CorrectionTotals | None] | None = None,
) -> HistogramRuntimeState:
    state = _runtime_state_with(elapsed_seconds, proton_charge_pc)
    if background is not None:
        state.background_values, state.background_errors, state.background_totals = background
    if normalization is not None:
        state.normalization_values, state.normalization_errors, state.normalization_totals = normalization
    return state


def test_apply_corrections_scales_the_background_by_its_own_proton_charge():
    """Issue #37: subtract counts-per-charge from counts-per-charge, not raw from raw.

    The background run collected 2 pC and the sample 4 pC, so the background contributes
    B/2 to a sample scaled by 1/4 -- not B/4 as it would if it rode on the sample's charge.
    """
    state = _state_with_corrections(
        elapsed_seconds=99.0,
        proton_charge_pc=4.0,
        background=([10.0, 10.0], [2.0, 2.0], CorrectionTotals(proton_charge_pc=2.0, duration_s=1.0)),
    )

    corrected, error = apply_corrections([40, 80], _normalization_args("proton-charge"), 2, runtime_state=state)

    assert corrected == [pytest.approx(40 / 4 - 10 / 2), pytest.approx(80 / 4 - 10 / 2)]
    # var = S/Qs^2 + sigma_B^2/Qb^2
    assert error == [pytest.approx(math.sqrt(40 / 16 + 4 / 4)), pytest.approx(math.sqrt(80 / 16 + 4 / 4))]


def test_apply_corrections_scales_the_normalization_by_its_own_proton_charge():
    state = _state_with_corrections(
        elapsed_seconds=99.0,
        proton_charge_pc=4.0,
        background=([10.0, 10.0], [2.0, 2.0], CorrectionTotals(proton_charge_pc=2.0, duration_s=1.0)),
        normalization=([20.0, 40.0], [10.0, 0.0], CorrectionTotals(proton_charge_pc=10.0, duration_s=1.0)),
    )

    corrected, error = apply_corrections([40, 80], _normalization_args("proton-charge"), 2, runtime_state=state)

    # (S/Qs - B/Qb) / (V/Qv) with V/Qv = [2, 4] and sigma_V/Qv = [1, 0].
    assert corrected == [pytest.approx(5.0 / 2.0), pytest.approx(15.0 / 4.0)]
    expected_variance = [
        (40 / 16 + 1.0) / 2.0**2 + (5.0**2) * 1.0**2 / 2.0**4,
        (80 / 16 + 1.0) / 4.0**2,
    ]
    assert error == [pytest.approx(math.sqrt(value)) for value in expected_variance]


def test_apply_corrections_with_equal_exposures_matches_the_unnormalized_ratio():
    """With every run at the same charge the exposure cancels out of (S - B)/V exactly."""
    totals = CorrectionTotals(proton_charge_pc=7.0, duration_s=1.0)
    background = ([3.0, 5.0], [1.0, 1.0], totals)
    normalization = ([8.0, 16.0], [0.5, 0.5], totals)
    normalized_state = _state_with_corrections(
        elapsed_seconds=1.0, proton_charge_pc=7.0, background=background, normalization=normalization
    )
    raw_state = _state_with_corrections(
        elapsed_seconds=1.0, proton_charge_pc=7.0, background=background, normalization=normalization
    )

    normalized, normalized_error = apply_corrections(
        [19, 37], _normalization_args("proton-charge"), 2, runtime_state=normalized_state
    )
    raw, raw_error = apply_corrections([19, 37], _normalization_args("none"), 2, runtime_state=raw_state)

    assert normalized == [pytest.approx(value) for value in raw]
    assert normalized_error == [pytest.approx(value) for value in raw_error]


def test_apply_corrections_scales_corrections_by_their_duration_when_normalizing_by_time():
    state = _state_with_corrections(
        elapsed_seconds=4.0,
        proton_charge_pc=1.0e6,
        background=([10.0, 10.0], [2.0, 2.0], CorrectionTotals(proton_charge_pc=1.0e9, duration_s=2.0)),
    )

    corrected, _ = apply_corrections([40, 80], _normalization_args("time"), 2, runtime_state=state)

    assert corrected == [pytest.approx(40 / 4 - 10 / 2), pytest.approx(80 / 4 - 10 / 2)]


def test_apply_corrections_keeps_legacy_behaviour_for_corrections_without_totals():
    """A CSV from before #37 has no totals, so it is subtracted on the sample's own scale."""
    state = _state_with_corrections(
        elapsed_seconds=99.0,
        proton_charge_pc=4.0,
        background=([10.0, 10.0], [2.0, 2.0], None),
    )

    corrected, _ = apply_corrections([40, 80], _normalization_args("proton-charge"), 2, runtime_state=state)

    assert corrected == [pytest.approx((40 - 10) / 4), pytest.approx((80 - 10) / 4)]


def test_apply_corrections_ignores_correction_totals_when_normalize_by_is_none():
    state = _state_with_corrections(
        elapsed_seconds=99.0,
        proton_charge_pc=4.0,
        background=([10.0, 10.0], [2.0, 2.0], CorrectionTotals(proton_charge_pc=2.0, duration_s=1.0)),
    )

    corrected, _ = apply_corrections([40, 80], _normalization_args("none"), 2, runtime_state=state)

    assert corrected == [pytest.approx(30.0), pytest.approx(70.0)]


def test_apply_corrections_stays_on_raw_counts_until_the_sample_accumulates_charge():
    """Mixing a charge-normalized background into raw sample counts would mix units."""
    state = _state_with_corrections(
        elapsed_seconds=0.0,
        proton_charge_pc=0.0,
        background=([10.0, 10.0], [2.0, 2.0], CorrectionTotals(proton_charge_pc=2.0, duration_s=1.0)),
    )

    corrected, _ = apply_corrections([40, 80], _normalization_args("proton-charge"), 2, runtime_state=state)

    assert corrected == [pytest.approx(30.0), pytest.approx(70.0)]


def test_apply_corrections_reads_totals_from_cli_correction_csvs(tmp_path: Path):
    header = f"Q value,I(Q),Error I(Q),{PROTON_CHARGE_COLUMN},{DURATION_COLUMN}"
    background_csv = tmp_path / "background.csv"
    background_csv.write_text(f"{header}\n0.01,10.0,2.0,2.0,1.0\n0.03,10.0,2.0,2.0,1.0\n", encoding="utf-8")
    normalization_csv = tmp_path / "normalization.csv"
    normalization_csv.write_text(f"{header}\n0.01,20.0,0.0,10.0,1.0\n0.03,40.0,0.0,10.0,1.0\n", encoding="utf-8")
    args = _normalization_args("proton-charge")
    args.background_subtraction = str(background_csv)
    args.normalization = str(normalization_csv)
    state = _runtime_state_with(elapsed_seconds=99.0, proton_charge_pc=4.0)

    corrected, _ = apply_corrections([40, 80], args, 2, runtime_state=state)

    assert corrected == [pytest.approx(5.0 / 2.0), pytest.approx(15.0 / 4.0)]


def test_validate_rejects_a_statistics_window_entirely_above_the_histogram():
    """An out-of-range window selects no bins and would silently produce an empty series."""
    args = argparse.Namespace(
        histogram_q_bin_size=0.02,
        histogram_q_min=0.0,
        histogram_q_max=30.0,
        tof_tick_us=1.0,
        live_plot_refresh_every=1,
        statistics_q_min=40.0,
        statistics_q_max=50.0,
        statistics_sample_interval_seconds=1.0,
    )

    with pytest.raises(ValueError, match="--statistics-q-min must be < --histogram-q-max"):
        validate_histogram_args(args)


def test_validate_rejects_a_statistics_window_entirely_below_the_histogram():
    args = argparse.Namespace(
        histogram_q_bin_size=0.02,
        histogram_q_min=10.0,
        histogram_q_max=30.0,
        tof_tick_us=1.0,
        live_plot_refresh_every=1,
        statistics_q_min=1.0,
        statistics_q_max=5.0,
        statistics_sample_interval_seconds=1.0,
    )

    with pytest.raises(ValueError, match="--statistics-q-max must be > --histogram-q-min"):
        validate_histogram_args(args)


class _CountingPlotter:
    def __init__(self):
        self.frames: list[tuple[list[float], list[float]]] = []

    def update(self, intensity, error, _relative_uncertainty, _statistics=None):
        self.frames.append((intensity, error))


def test_maybe_update_live_plot_applies_the_display_transform():
    plotter = _CountingPlotter()

    maybe_update_live_plot(
        plotter, [4, 16], None, 1, 1, display_transform=lambda hist: ([v / 4 for v in hist], [1.0, 2.0])
    )

    assert plotter.frames == [([1.0, 4.0], [1.0, 2.0])]


def test_maybe_update_live_plot_only_runs_the_transform_when_a_refresh_is_due():
    """Corrections cost a pass over every bin, so throttled packets must not pay for them."""
    plotter = _CountingPlotter()
    calls: list[int] = []

    def _transform(hist):
        calls.append(1)
        return [float(v) for v in hist], [0.0] * len(hist)

    for index in range(1, 11):
        maybe_update_live_plot(plotter, [1], None, 5, index, display_transform=_transform)

    assert len(calls) == 2
    assert len(plotter.frames) == 2


def test_maybe_update_live_plot_defaults_to_raw_counts_and_poisson_error():
    plotter = _CountingPlotter()

    maybe_update_live_plot(plotter, [4, 9], None, 1, 1)

    assert plotter.frames == [([4.0, 9.0], [2.0, 3.0])]
