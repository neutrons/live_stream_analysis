#!/usr/bin/env python3
"""Create a sample workspace, apply an IDF, convert to Q, and produce single I(Q)."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

from mantid.simpleapi import ConvertUnits, CreateSampleWorkspace, LoadInstrument, SumSpectra
import matplotlib.pyplot as plt


def find_idf_dir() -> Path:
    """Find the project's tests/data/idf directory by walking parent folders."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "tests" / "data" / "idf"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Could not locate a 'tests/data/idf' directory from script location.")


def resolve_idf(idf_file: Path) -> Path:
    """Resolve and validate a single IDF file path."""
    idf_path = idf_file.resolve()
    if not idf_path.exists():
        raise FileNotFoundError(f"IDF file does not exist: {idf_path}")
    return idf_path


def build_iq_workspace(idf_path: Path, output_name: str = "iq"):
    """Create sample data, load instrument, convert to Q, and ensure one histogram."""
    sample_ws = CreateSampleWorkspace(
        OutputWorkspace="sample_ws",
        WorkspaceType="Histogram",
        NumBanks=1,
        BankPixelWidth=1,
        NumEvents=50000,
        XUnit="TOF",
        XMin=1000,
        XMax=20000,
        BinWidth=50,
    )

    LoadInstrument(
        Workspace=sample_ws,
        Filename=str(idf_path),
        RewriteSpectraMap=True,
    )

    q_ws = ConvertUnits(
        InputWorkspace=sample_ws,
        OutputWorkspace=output_name,
        Target="MomentumTransfer",
        EMode="Elastic",
    )

    if q_ws.getNumberHistograms() > 1:
        q_ws = SumSpectra(InputWorkspace=q_ws, OutputWorkspace=output_name)

    return sample_ws, q_ws


def plot_iq(q_ws) -> None:
    """Plot I(Q) for the first (and expected only) histogram."""

    x = q_ws.readX(0)
    y = q_ws.readY(0)

    # Histogram X arrays are bin edges; convert to bin centers for plotting against Y.
    if len(x) == len(y) + 1:
        x = 0.5 * (x[:-1] + x[1:])

    plt.figure(figsize=(8, 5))
    plt.plot(x, y, lw=1.5)
    plt.xlabel("Q (MomentumTransfer)")
    plt.ylabel("Intensity I(Q)")
    plt.title("Sample I(Q)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


def export_pixel_geometry_csv(ws, output_csv: Path) -> int:
    """Export detector pixel geometry (detector ID, L2, theta) to CSV."""
    detector_info = ws.detectorInfo()
    detector_ids = detector_info.detectorIDs()

    output_csv = output_csv.resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    rows_written = 0
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pixel id", "L2 value", "theta value"])

        for i in range(detector_info.size()):
            if detector_info.isMonitor(i):
                continue

            # Mantid's twoTheta is in radians; convert to degrees for easier inspection.
            l2_m = detector_info.l2(i)
            theta_deg = math.degrees(detector_info.twoTheta(i))
            writer.writerow([int(detector_ids[i]), f"{l2_m:.8f}", f"{theta_deg:.8f}"])
            rows_written += 1

    return rows_written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create sample workspace, load IDF, convert to Q, and produce a single I(Q) histogram."
    )
    default_idf_file = find_idf_dir() / "NOMAD_Definition.xml"
    parser.add_argument(
        "--idf-file",
        type=Path,
        default=default_idf_file,
        help="Path to instrument definition XML file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="iq",
        help="Name of the output I(Q) workspace.",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Plot I(Q) vs Q using matplotlib.",
    )
    parser.add_argument(
        "--pixel-geometry-csv",
        type=Path,
        default=Path(__file__).resolve().parent / "pixel_geometry.csv",
        help="Output CSV path for detector geometry: pixel id, L2 (m), theta (deg).",
    )

    args = parser.parse_args()

    idf_path = resolve_idf(args.idf_file)
    sample_ws, q_ws = build_iq_workspace(idf_path=idf_path, output_name=args.output)

    print(f"Loaded IDF: {idf_path}")
    print(f"Sample workspace: {sample_ws.name()} ({sample_ws.getNumberHistograms()} histogram)")
    print(f"I(Q) workspace: {q_ws.name()} ({q_ws.getNumberHistograms()} histogram)")

    rows_written = export_pixel_geometry_csv(sample_ws, args.pixel_geometry_csv)
    print(f"Pixel geometry CSV: {args.pixel_geometry_csv.resolve()} ({rows_written} rows)")

    if args.plot:
        plot_iq(q_ws)


if __name__ == "__main__":
    main()
