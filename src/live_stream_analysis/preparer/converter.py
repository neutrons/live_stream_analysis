"""TOF-to-Q conversion and preparer CLI entrypoints."""

import argparse
import csv
from pathlib import Path

from .instrument import (
    build_detector_geometry,
    build_synthetic_tof_spectrum,
)


def convert_to_iq(
    geometry_rows: list[tuple[int, float, float, float, float]],
    tof_centers_us: list[float],
    y_counts: list[float],
    q_bins: int,
):
    """Convert synthetic TOF to Q and accumulate a single I(Q) histogram."""
    if not geometry_rows:
        raise ValueError("No detector geometry rows available")

    tof_min = min(tof_centers_us)
    tof_max = max(tof_centers_us)

    factors = [q_matrix_element for _, _, _, _, q_matrix_element in geometry_rows]

    q_min = min(factors) / tof_max
    q_max = max(factors) / tof_min
    if q_max <= q_min:
        raise ValueError("Invalid Q range from detector geometry")

    bin_width = (q_max - q_min) / q_bins
    inv_bin_width = 1.0 / bin_width
    iq = [0.0] * q_bins
    q_centers = [q_min + (i + 0.5) * bin_width for i in range(q_bins)]

    inv_tof = [1.0 / t for t in tof_centers_us]

    for factor in factors:
        for j, inv_t in enumerate(inv_tof):
            q = factor * inv_t
            b = int((q - q_min) * inv_bin_width)
            if 0 <= b < q_bins:
                iq[b] += y_counts[j]

    return q_centers, iq


def write_pixel_geometry_csv(
    rows: list[tuple[int, float, float, float, float]],
    output_csv: Path,
    q_matrix_scale: float = 1.0,
) -> None:
    output_csv = output_csv.resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pixel id", "L2 value", "theta value", "TOF-to-Q matrix element"])
        for det_id, l2, theta_deg, _, q_matrix_element in rows:
            writer.writerow([det_id, f"{l2:.8f}", f"{theta_deg:.8f}", f"{q_matrix_element * q_matrix_scale:.8f}"])


def write_iq_csv(q_centers: list[float], iq: list[float], output_csv: Path) -> None:
    output_csv = output_csv.resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Q value", "I(Q)"])
        for q, intensity in zip(q_centers, iq):
            writer.writerow([f"{q:.8f}", f"{intensity:.8f}"])


def plot_iq(q_centers: list[float], iq: list[float]) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Plotting requested but matplotlib is not available") from exc

    plt.figure(figsize=(8, 5))
    plt.plot(q_centers, iq, lw=1.5)
    plt.xlabel("Q (1/Angstrom)")
    plt.ylabel("Intensity I(Q)")
    plt.title("Pure-Python Synthetic I(Q)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


def run_preparer(
    idf_file: Path,
    pixel_geometry_csv: Path,
    iq_csv: Path,
    x_min: float,
    x_max: float,
    bin_width: float,
    q_bins: int,
    q_matrix_scale: float = 1.0,
    plot: bool = False,
) -> tuple[int, int]:
    """Run the end-to-end pure-Python pre-processing workflow."""
    idf_path = idf_file.resolve()
    if not idf_path.exists():
        raise FileNotFoundError(f"IDF file does not exist: {idf_path}")

    geometry_rows = build_detector_geometry(idf_path)
    write_pixel_geometry_csv(geometry_rows, pixel_geometry_csv, q_matrix_scale=q_matrix_scale)

    tof_centers, y_counts = build_synthetic_tof_spectrum(x_min, x_max, bin_width)
    q_centers, iq = convert_to_iq(geometry_rows, tof_centers, y_counts, q_bins)
    write_iq_csv(q_centers, iq, iq_csv)

    if plot:
        plot_iq(q_centers, iq)

    return len(geometry_rows), len(q_centers)


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    """Register the preparer subcommand parser."""
    parser = subparsers.add_parser(
        "preparer",
        help="Generate detector geometry and synthetic I(Q) CSV from an IDF XML file.",
    )
    parser.add_argument("--idf-file", type=Path, required=True, help="Path to Mantid IDF XML file.")
    parser.add_argument(
        "--pixel-geometry-csv",
        type=Path,
        default=Path("pixel_geometry.csv"),
        help="Output CSV path for detector geometry (pixel id, L2, theta).",
    )
    parser.add_argument(
        "--iq-csv",
        type=Path,
        default=Path("iq.csv"),
        help="Output CSV path for synthetic I(Q) histogram.",
    )
    parser.add_argument("--x-min", type=float, default=1000.0, help="TOF minimum in microseconds.")
    parser.add_argument("--x-max", type=float, default=20000.0, help="TOF maximum in microseconds.")
    parser.add_argument("--bin-width", type=float, default=50.0, help="TOF bin width in microseconds.")
    parser.add_argument("--q-bins", type=int, default=400, help="Number of bins for output I(Q) histogram.")
    parser.add_argument(
        "--q-matrix-scale",
        type=float,
        default=1.0,
        help=(
            "Scale factor applied to TOF-to-Q matrix elements in pixel geometry CSV. "
            "Use 10.0 for ADARA TOF ticks that represent 0.1 microseconds."
        ),
    )
    parser.add_argument("--plot", action="store_true", help="Plot I(Q) with matplotlib.")
    parser.set_defaults(_cmd="preparer")
    return parser


def run_from_namespace(args: argparse.Namespace) -> int:
    """Execute preparer subcommand from parsed argparse namespace."""
    n_pixels, n_q_bins = run_preparer(
        idf_file=args.idf_file,
        pixel_geometry_csv=args.pixel_geometry_csv,
        iq_csv=args.iq_csv,
        x_min=args.x_min,
        x_max=args.x_max,
        bin_width=args.bin_width,
        q_bins=args.q_bins,
        q_matrix_scale=args.q_matrix_scale,
        plot=args.plot,
    )
    print(f"Loaded IDF: {args.idf_file.resolve()}")
    print(f"Detector pixels: {n_pixels}")
    print(f"Pixel geometry CSV: {args.pixel_geometry_csv.resolve()}")
    print(f"I(Q) CSV: {args.iq_csv.resolve()} ({n_q_bins} bins)")
    print(f"Q-matrix scale applied: {args.q_matrix_scale}")
    return 0
