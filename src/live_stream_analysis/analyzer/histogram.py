from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def load_q_matrix_constants(pixel_geometry_csv: str) -> list[float]:
    path = Path(pixel_geometry_csv).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Pixel geometry CSV does not exist: {path}")

    by_pixel_id: dict[int, float] = {}
    max_pixel_id = -1
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required_columns = {"pixel id", "TOF-to-Q matrix element"}
        if reader.fieldnames is None or not required_columns.issubset(set(reader.fieldnames)):
            raise ValueError("Pixel geometry CSV must include columns: 'pixel id' and 'TOF-to-Q matrix element'")

        for row in reader:
            pixel_id = int(row["pixel id"])
            q_matrix_element = float(row["TOF-to-Q matrix element"])
            by_pixel_id[pixel_id] = q_matrix_element
            if pixel_id > max_pixel_id:
                max_pixel_id = pixel_id

    if max_pixel_id < 0:
        raise ValueError(f"No detector rows found in pixel geometry CSV: {path}")

    q_matrix_constants = [0.0] * (max_pixel_id + 1)
    for pixel_id, q_matrix_element in by_pixel_id.items():
        if pixel_id >= 0:
            q_matrix_constants[pixel_id] = q_matrix_element

    return q_matrix_constants


def write_histogram_csv(
    intensity: list[float],
    error: list[float],
    output_path: str,
    q_bin_size: float,
) -> None:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Q value", "I(Q)", "Error I(Q)"])
        for index, (y_value, err_value) in enumerate(zip(intensity, error, strict=True)):
            q_value = (index + 0.5) * q_bin_size
            writer.writerow([f"{q_value:.8f}", f"{y_value:.8f}", f"{err_value:.8f}"])


def load_correction_csv(
    csv_path: str,
    expected_bins: int,
    q_bin_size: float,
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
            bin_index = int(q_value / q_bin_size)
            if 0 <= bin_index < expected_bins and q_value <= q_max:
                values[bin_index] = intensity
                errors[bin_index] = error

    return values, errors


def apply_corrections(
    hist: list[int],
    args: argparse.Namespace,
    histogram_bins: int,
) -> tuple[list[float], list[float]]:
    corrected = [float(value) for value in hist]
    variance = [float(value) for value in hist]

    if args.background_subtraction is not None:
        background, background_error = load_correction_csv(
            args.background_subtraction,
            expected_bins=histogram_bins,
            q_bin_size=args.histogram_q_bin_size,
            q_max=args.histogram_q_max,
        )
        corrected = [value - background_value for value, background_value in zip(corrected, background)]
        variance = [
            value_variance + background_sigma**2
            for value_variance, background_sigma in zip(variance, background_error, strict=True)
        ]

    if args.normalization is not None:
        normalization, normalization_error = load_correction_csv(
            args.normalization,
            expected_bins=histogram_bins,
            q_bin_size=args.histogram_q_bin_size,
            q_max=args.histogram_q_max,
        )
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

    error = [math.sqrt(max(value, 0.0)) for value in variance]
    return corrected, error


def validate_histogram_args(args: argparse.Namespace) -> int:
    if args.histogram_q_bin_size <= 0:
        raise ValueError("--histogram-q-bin-size must be > 0")
    if args.histogram_q_max <= 0:
        raise ValueError("--histogram-q-max must be > 0")
    if args.tof_tick_us <= 0:
        raise ValueError("--tof-tick-us must be > 0")
    if args.live_plot_refresh_every <= 0:
        raise ValueError("--live-plot-refresh-every must be > 0")

    histogram_bins_f = args.histogram_q_max / args.histogram_q_bin_size
    histogram_bins = int(round(histogram_bins_f))
    if histogram_bins <= 0 or not math.isclose(histogram_bins_f, float(histogram_bins), rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("--histogram-q-max must be an integer multiple of --histogram-q-bin-size")

    return histogram_bins
