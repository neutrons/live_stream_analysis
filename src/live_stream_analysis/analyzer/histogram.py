from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path

from ..correction_totals import CorrectionTotals, load_correction_totals

TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class PixelQConversion:
    q_matrix_constants: list[float]
    difc: list[float]
    difa: list[float]
    tzero: list[float]
    use: list[int]


def load_q_matrix_constants(pixel_geometry_csv: str) -> list[float]:
    return load_pixel_q_conversion(pixel_geometry_csv).q_matrix_constants


def load_pixel_q_conversion(pixel_geometry_csv: str) -> PixelQConversion:
    path = Path(pixel_geometry_csv).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Pixel geometry CSV does not exist: {path}")

    by_pixel_id: dict[int, tuple[float, float, float, float, int]] = {}
    max_pixel_id = -1
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required_columns = {"pixel id", "TOF-to-Q matrix element"}
        if reader.fieldnames is None or not required_columns.issubset(set(reader.fieldnames)):
            raise ValueError("Pixel geometry CSV must include columns: 'pixel id' and 'TOF-to-Q matrix element'")

        has_difc = "difc" in reader.fieldnames
        has_difa = "difa" in reader.fieldnames
        has_tzero = "tzero" in reader.fieldnames
        has_use = "use" in reader.fieldnames

        for row in reader:
            pixel_id = int(row["pixel id"])
            q_matrix_element = float(row["TOF-to-Q matrix element"])
            difc = float(row["difc"]) if has_difc else (q_matrix_element / TWO_PI)
            difa = float(row["difa"]) if has_difa else 0.0
            tzero = float(row["tzero"]) if has_tzero else 0.0
            use = int(float(row["use"])) if has_use else 1

            if q_matrix_element == 0.0 and difc != 0.0:
                q_matrix_element = TWO_PI * difc

            by_pixel_id[pixel_id] = (q_matrix_element, difc, difa, tzero, use)
            if pixel_id > max_pixel_id:
                max_pixel_id = pixel_id

    if max_pixel_id < 0:
        raise ValueError(f"No detector rows found in pixel geometry CSV: {path}")

    q_matrix_constants = [0.0] * (max_pixel_id + 1)
    difc_values = [0.0] * (max_pixel_id + 1)
    difa_values = [0.0] * (max_pixel_id + 1)
    tzero_values = [0.0] * (max_pixel_id + 1)
    use_values = [1] * (max_pixel_id + 1)
    for pixel_id, (q_matrix_element, difc, difa, tzero, use) in by_pixel_id.items():
        if pixel_id < 0:
            continue

        use_values[pixel_id] = use
        if use > 0:
            q_matrix_constants[pixel_id] = q_matrix_element
        difc_values[pixel_id] = difc
        difa_values[pixel_id] = difa
        tzero_values[pixel_id] = tzero

    return PixelQConversion(
        q_matrix_constants=q_matrix_constants,
        difc=difc_values,
        difa=difa_values,
        tzero=tzero_values,
        use=use_values,
    )


def _d_spacing_from_calibration(tof_us: float, difc: float, difa: float, tzero: float) -> float | None:
    if abs(difa) < 1e-12:
        if difc == 0.0:
            return None
        d_spacing = (tof_us - tzero) / difc
    else:
        half = difc / (2.0 * difa)
        discriminant = (half * half) - ((tzero - tof_us) / difa)
        if discriminant < 0.0:
            return None
        root = math.sqrt(discriminant)
        d_spacing = -half + root if difa > 0.0 else -half - root

    if d_spacing <= 0.0:
        return None
    return d_spacing


def pixel_tof_to_q(conversion: PixelQConversion, pixel_id: int, tof_us: float) -> float | None:
    if pixel_id < 0 or pixel_id >= len(conversion.q_matrix_constants):
        return None
    if tof_us <= 0.0:
        return None
    if conversion.use[pixel_id] <= 0:
        return None

    difc = conversion.difc[pixel_id]
    difa = conversion.difa[pixel_id]
    tzero = conversion.tzero[pixel_id]

    if difc == 0.0 and difa == 0.0 and tzero == 0.0:
        q_matrix_element = conversion.q_matrix_constants[pixel_id]
        if q_matrix_element <= 0.0:
            return None
        return q_matrix_element / tof_us

    d_spacing = _d_spacing_from_calibration(tof_us, difc, difa, tzero)
    if d_spacing is None:
        return None
    return TWO_PI / d_spacing


def bin_centre_q_values(q_min: float, q_bin_size: float, bins: int) -> list[float]:
    """Return the Q value at the centre of each histogram bin.

    Every consumer of a histogram -- CSV output, live plots, INTERSECT payloads -- must
    label bins the same way, otherwise the same peak reports a different Q in each place.
    Bin centres are the convention; a count in [q, q + dq) is reported at q + dq/2.
    """
    return [q_min + (index + 0.5) * q_bin_size for index in range(bins)]


def compute_relative_uncertainty(intensity: list[float], error: list[float]) -> list[float]:
    relative_uncertainty: list[float] = []
    for y_value, err_value in zip(intensity, error, strict=True):
        if y_value == 0.0:
            relative_uncertainty.append(0.0)
            continue
        relative_uncertainty.append(abs(err_value / y_value))
    return relative_uncertainty


def write_histogram_csv(
    intensity: list[float],
    error: list[float],
    output_path: str,
    q_bin_size: float,
    q_min: float,
) -> None:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    q_values = bin_centre_q_values(q_min, q_bin_size, len(intensity))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Q value", "I(Q)", "Error I(Q)"])
        for q_value, y_value, err_value in zip(q_values, intensity, error, strict=True):
            writer.writerow([f"{q_value:.8f}", f"{y_value:.8f}", f"{err_value:.8f}"])


def load_correction_csv(
    csv_path: str,
    expected_bins: int,
    q_bin_size: float,
    q_min: float,
    q_max: float,
) -> tuple[list[float], list[float]]:
    path = Path(csv_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Correction CSV does not exist: {path}")

    values = [0.0] * expected_bins
    errors = [0.0] * expected_bins
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required_columns = {"Q value", "I(Q)", "Error I(Q)"}
        if reader.fieldnames is None or not required_columns.issubset(set(reader.fieldnames)):
            raise ValueError("Correction CSV must include columns: 'Q value', 'I(Q)', 'Error I(Q)'")

        for row in reader:
            q_value = float(row["Q value"])
            intensity = float(row["I(Q)"])
            error = float(row["Error I(Q)"])
            bin_index = int((q_value - q_min) / q_bin_size)
            if 0 <= bin_index < expected_bins and q_min <= q_value <= q_max:
                values[bin_index] = intensity
                errors[bin_index] = error

    return values, errors


def preload_corrections(runtime_state, args: argparse.Namespace, histogram_bins: int) -> None:
    """Read the --background-subtraction and --normalization CSVs into the runtime state once.

    apply_corrections runs on every live-plot refresh, which is every packet by default, so
    reading the CSVs there would re-parse both files from disk tens of times a second. A
    correction later posted over INTERSECT replaces these values, as it always has.
    """
    for csv_path, kind in ((args.background_subtraction, "background"), (args.normalization, "normalization")):
        if csv_path is None:
            continue
        values, errors = load_correction_csv(
            csv_path,
            expected_bins=histogram_bins,
            q_bin_size=args.histogram_q_bin_size,
            q_min=args.histogram_q_min,
            q_max=args.histogram_q_max,
        )
        setattr(runtime_state, f"{kind}_values", values)
        setattr(runtime_state, f"{kind}_errors", errors)
        setattr(runtime_state, f"{kind}_totals", load_correction_totals(csv_path))


def apply_corrections(  # noqa: C901
    hist: list[int],
    args: argparse.Namespace,
    histogram_bins: int,
    runtime_state=None,
) -> tuple[list[float], list[float]]:
    """Return (S/Qs - B/Qb) / (V/Qv) and its propagated error.

    Each term is divided by the exposure that produced it -- the sample by what it has
    accumulated so far, background and normalization by the totals the preparer recorded --
    so a background measured for twice as long as the sample is subtracted at half weight.
    A correction without recorded totals falls back to the pre-#37 behaviour: the background
    rides on the sample's scale, and the normalization is used as-is.
    """
    sample_divisor = _accumulation_divisor(args, runtime_state)
    sample_scale = 1.0 / sample_divisor if sample_divisor is not None else 1.0
    corrected = [float(value) * sample_scale for value in hist]
    variance = [float(value) * sample_scale**2 for value in hist]

    background = None
    background_error = None
    background_totals = None
    if runtime_state is not None and runtime_state.background_values is not None:
        background = runtime_state.background_values
        background_error = runtime_state.background_errors or [0.0] * len(background)
        background_totals = runtime_state.background_totals
    elif args.background_subtraction is not None:
        background, background_error = load_correction_csv(
            args.background_subtraction,
            expected_bins=histogram_bins,
            q_bin_size=args.histogram_q_bin_size,
            q_min=args.histogram_q_min,
            q_max=args.histogram_q_max,
        )
        background_totals = load_correction_totals(args.background_subtraction)

    if background is not None and background_error is not None:
        background_scale = _correction_scale(args, sample_divisor, background_totals, fallback=sample_scale)
        corrected = [
            value - background_value * background_scale
            for value, background_value in zip(corrected, background, strict=True)
        ]
        variance = [
            value_variance + (background_sigma * background_scale) ** 2
            for value_variance, background_sigma in zip(variance, background_error, strict=True)
        ]

    normalization = None
    normalization_error = None
    normalization_totals = None
    if runtime_state is not None and runtime_state.normalization_values is not None:
        normalization = runtime_state.normalization_values
        normalization_error = runtime_state.normalization_errors or [0.0] * len(normalization)
        normalization_totals = runtime_state.normalization_totals
    elif args.normalization is not None:
        normalization, normalization_error = load_correction_csv(
            args.normalization,
            expected_bins=histogram_bins,
            q_bin_size=args.histogram_q_bin_size,
            q_min=args.histogram_q_min,
            q_max=args.histogram_q_max,
        )
        normalization_totals = load_correction_totals(args.normalization)

    if normalization is not None and normalization_error is not None:
        normalization_scale = _correction_scale(args, sample_divisor, normalization_totals, fallback=1.0)
        normalization = [value * normalization_scale for value in normalization]
        normalization_error = [sigma * normalization_scale for sigma in normalization_error]
        next_corrected: list[float] = []
        next_variance: list[float] = []
        for value, value_variance, norm, norm_sigma in zip(
            corrected,
            variance,
            normalization,
            normalization_error,
            strict=True,
        ):
            if norm <= 0.0:
                next_corrected.append(0.0)
                next_variance.append(0.0)
                continue

            quotient = value / norm
            propagated_variance = (value_variance / (norm**2)) + ((value**2) * (norm_sigma**2) / (norm**4))
            next_corrected.append(quotient)
            next_variance.append(propagated_variance)

        corrected = next_corrected
        variance = next_variance

    coherent_length = getattr(args, "sample_coherent_scatter_length", None)
    total_length_squared = getattr(args, "sample_total_scatter_length_squared", None)
    if coherent_length is not None or total_length_squared is not None:
        if coherent_length is None or total_length_squared is None:
            raise ValueError(
                "--sample-coherent-scatter-length and --sample-total-scatter-length-squared must be provided together"
            )
        coherent_length_squared = (float(coherent_length) ** 2) / 100.0
        total_scatter_length_squared = float(total_length_squared) / 100.0
        if coherent_length_squared <= 0.0:
            raise ValueError("--sample-coherent-scatter-length must be > 0")

        laue_monotonic_diffuse_scat = total_scatter_length_squared / coherent_length_squared
        corrected = [(value / coherent_length_squared) + (1.0 - laue_monotonic_diffuse_scat) for value in corrected]
        variance = [value / (coherent_length_squared**2) for value in variance]

    error = [math.sqrt(max(value, 0.0)) for value in variance]
    return corrected, error


def _correction_scale(
    args: argparse.Namespace,
    sample_divisor: float | None,
    totals: CorrectionTotals | None,
    *,
    fallback: float,
) -> float:
    """Return the factor that puts a correction on the same per-exposure scale as the sample.

    Until the sample itself has something to divide by it is still in raw counts, and dividing
    only the correction would subtract counts-per-charge from counts, so it stays unscaled too.
    """
    if sample_divisor is None:
        return 1.0
    divisor = totals.divisor(getattr(args, "normalize_by", "none")) if totals is not None else None
    return 1.0 / divisor if divisor is not None else fallback


def _accumulation_divisor(args: argparse.Namespace, runtime_state) -> float | None:
    """Return the accumulated time or charge to normalize by, or None to leave the scale alone.

    Early in a run neither quantity has accumulated yet; dividing by zero there would blow up
    the histogram, so the raw counts are reported until there is something to divide by.
    """
    normalize_by = getattr(args, "normalize_by", "none")
    if normalize_by == "none" or runtime_state is None:
        return None

    run_statistics = getattr(runtime_state, "run_statistics", None)
    if run_statistics is None:
        return None
    accumulator = run_statistics.accumulator

    divisor = accumulator.elapsed_seconds if normalize_by == "time" else accumulator.proton_charge_pc
    return divisor if divisor > 0.0 else None


def validate_histogram_args(args: argparse.Namespace) -> int:  # noqa: C901
    if args.histogram_q_bin_size <= 0:
        raise ValueError("--histogram-q-bin-size must be > 0")
    if args.histogram_q_min < 0:
        raise ValueError("--histogram-q-min must be >= 0")
    if args.histogram_q_max <= args.histogram_q_min:
        raise ValueError("--histogram-q-max must be > --histogram-q-min")
    if args.tof_tick_us <= 0:
        raise ValueError("--tof-tick-us must be > 0")
    if args.live_plot_refresh_every <= 0:
        raise ValueError("--live-plot-refresh-every must be > 0")

    statistics_q_min = getattr(args, "statistics_q_min", None)
    statistics_q_max = getattr(args, "statistics_q_max", None)
    if statistics_q_min is not None and statistics_q_max is not None and statistics_q_max <= statistics_q_min:
        raise ValueError("--statistics-q-max must be > --statistics-q-min")
    # A window entirely outside the histogram selects no bins, which would silently produce an
    # empty decay series and a header-only CSV rather than reporting the mistake.
    if statistics_q_min is not None and statistics_q_min >= args.histogram_q_max:
        raise ValueError("--statistics-q-min must be < --histogram-q-max")
    if statistics_q_max is not None and statistics_q_max <= args.histogram_q_min:
        raise ValueError("--statistics-q-max must be > --histogram-q-min")
    if getattr(args, "statistics_sample_interval_seconds", 1.0) < 0:
        raise ValueError("--statistics-sample-interval-seconds must be >= 0")

    histogram_bins_f = (args.histogram_q_max - args.histogram_q_min) / args.histogram_q_bin_size
    histogram_bins = int(round(histogram_bins_f))
    if histogram_bins <= 0 or not math.isclose(histogram_bins_f, float(histogram_bins), rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(
            "--histogram-q-max minus --histogram-q-min must be an integer multiple of --histogram-q-bin-size"
        )

    return histogram_bins
