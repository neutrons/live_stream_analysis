#!/usr/bin/env python3
"""Reduce a NeXus event file to I(Q) with Mantid, in the project's histogram CSV format.

This is the Mantid half of a like-for-like check against `live_stream_analysis analyze`.
Both sides are pointed at the same NeXus file with the same Q binning and the same
SaveDiffCal calibration, so the two CSVs can be diffed bin-by-bin with `compare_iq.py`.

Mantid is not a dependency of the package; see the README in this directory for the
standalone environment.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from mantid.simpleapi import (
    ApplyDiffCal,
    ConvertUnits,
    LoadDiffCal,
    LoadEventNexus,
    MaskDetectors,
    Rebin,
    SumSpectra,
)


def build_iq(
    nexus_file: Path,
    q_min: float,
    q_max: float,
    q_bin_size: float,
    calibration_file: Path | None,
    apply_mask: bool = False,
):
    """Load events, optionally calibrate, convert to Q, rebin, and sum to a single spectrum.

    The chain mirrors what the analyzer does per event: TOF -> d (via DIFC/DIFA/TZERO)
    -> Q = 2*pi/d, then accumulate counts into fixed-width Q bins.
    """
    workspace = LoadEventNexus(Filename=str(nexus_file), OutputWorkspace="lsa_cmp_events")

    if calibration_file is not None:
        # Same file the preparer reads for difc/difa/tzero/use.
        LoadDiffCal(
            InputWorkspace=workspace,
            Filename=str(calibration_file),
            WorkspaceName="lsa_cmp_cal",
        )
        ApplyDiffCal(InstrumentWorkspace=workspace, CalibrationWorkspace="lsa_cmp_cal_cal")
        if apply_mask:
            # ApplyDiffCal only sets difc/difa/tzero, so by default Mantid still histograms
            # pixels the analyzer skips via the `use` flag. Applying LoadDiffCal's mask
            # workspace is the obvious counterpart, but on NOMAD the two do not line up:
            # see the mask discussion in this directory's README before trusting either total.
            MaskDetectors(Workspace=workspace, MaskedWorkspace="lsa_cmp_cal_mask")

    workspace = ConvertUnits(
        InputWorkspace=workspace,
        Target="MomentumTransfer",
        EMode="Elastic",
        OutputWorkspace="lsa_cmp_q",
    )
    # PreserveEvents=False gives a plain histogram of counts per Q bin, which is what the
    # analyzer produces; leaving events on would keep a weighted event list instead.
    workspace = Rebin(
        InputWorkspace=workspace,
        Params=[q_min, q_bin_size, q_max],
        PreserveEvents=False,
        OutputWorkspace="lsa_cmp_binned",
    )
    return SumSpectra(InputWorkspace=workspace, OutputWorkspace="lsa_cmp_iq")


def write_histogram_csv(workspace, output_csv: Path) -> int:
    """Write Q value, I(Q), Error I(Q) using bin centres, matching the analyzer's CSV."""
    edges = workspace.readX(0)
    intensity = workspace.readY(0)
    error = workspace.readE(0)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Q value", "I(Q)", "Error I(Q)"])
        for index in range(len(intensity)):
            q_centre = 0.5 * (edges[index] + edges[index + 1])
            writer.writerow([f"{q_centre:.8f}", f"{intensity[index]:.8f}", f"{error[index]:.8f}"])
    return len(intensity)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nexus-file", type=Path, required=True, help="Input NeXus event file.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Where to write the I(Q) CSV.")
    parser.add_argument("--calibration-file", type=Path, default=None, help="Optional SaveDiffCal HDF5 file.")
    parser.add_argument(
        "--apply-calibration-mask",
        action="store_true",
        help="Also apply the calibration's mask workspace. See the README: it does not match the `use` flag.",
    )
    parser.add_argument("--histogram-q-min", type=float, default=0.0)
    parser.add_argument("--histogram-q-max", type=float, default=40.0)
    parser.add_argument("--histogram-q-bin-size", type=float, default=0.02)
    args = parser.parse_args()

    workspace = build_iq(
        args.nexus_file,
        args.histogram_q_min,
        args.histogram_q_max,
        args.histogram_q_bin_size,
        args.calibration_file,
        args.apply_calibration_mask,
    )
    bins = write_histogram_csv(workspace, args.output_csv)
    print(f"Wrote {bins} bins to {args.output_csv.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
