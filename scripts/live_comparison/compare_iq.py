#!/usr/bin/env python3
"""Diff two histogram CSVs bin-by-bin and report where they disagree.

Pure Python -- no Mantid needed, so this runs in the project's uv environment even though
the CSV it compares against was produced in the Mantid environment.

Both inputs must use the project's three-column format: Q value, I(Q), Error I(Q).
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def read_histogram_csv(path: Path) -> tuple[list[float], list[float], list[float]]:
    """Return (q_values, intensity, error) from a three-column histogram CSV."""
    q_values: list[float] = []
    intensity: list[float] = []
    error: list[float] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"Q value", "I(Q)", "Error I(Q)"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"{path} must have columns: Q value, I(Q), Error I(Q)")
        for row in reader:
            q_values.append(float(row["Q value"]))
            intensity.append(float(row["I(Q)"]))
            error.append(float(row["Error I(Q)"]))
    if not q_values:
        raise ValueError(f"No rows found in {path}")
    return q_values, intensity, error


def compare(
    reference: tuple[list[float], list[float], list[float]],
    candidate: tuple[list[float], list[float], list[float]],
    q_tolerance: float,
    intensity_tolerance: float,
) -> int:
    """Print a bin-by-bin comparison. Returns a process exit code."""
    ref_q, ref_i, ref_e = reference
    cand_q, cand_i, cand_e = candidate

    if len(ref_q) != len(cand_q):
        print(f"BIN COUNT DIFFERS: reference={len(ref_q)} candidate={len(cand_q)}")
        print("  Re-run both sides with the same --histogram-q-min/-max/-bin-size.")
        return 1

    q_offsets = [abs(a - b) for a, b in zip(ref_q, cand_q, strict=True)]
    max_q_offset = max(q_offsets)
    print(f"Bins compared        : {len(ref_q)}")
    print(f"Max |dQ|             : {max_q_offset:.3e}")
    if max_q_offset > q_tolerance:
        # A constant offset of half a bin means one side is labelling bins by their left
        # edge instead of their centre; the intensities below would then be meaningless.
        print(f"  WARNING: Q axes are misaligned by up to {max_q_offset:.5f}.")
        print("  Compare the bin convention on both sides before reading the intensities.")

    # Only bins where at least one side has counts tell you anything.
    populated = [k for k in range(len(ref_i)) if ref_i[k] != 0.0 or cand_i[k] != 0.0]
    print(f"Populated bins       : {len(populated)}")
    if not populated:
        print("Both histograms are empty; nothing to compare.")
        return 0

    abs_diffs = [abs(ref_i[k] - cand_i[k]) for k in populated]
    rel_diffs = [
        abs(ref_i[k] - cand_i[k]) / abs(ref_i[k]) if ref_i[k] != 0.0 else math.inf for k in populated
    ]
    finite_rel = [value for value in rel_diffs if math.isfinite(value)]

    total_ref = sum(ref_i)
    total_cand = sum(cand_i)
    print(f"Total counts         : reference={total_ref:.1f} candidate={total_cand:.1f}")
    if total_ref != 0.0:
        print(f"  total delta        : {(total_cand - total_ref) / total_ref * 100.0:+.4f}%")
    print(f"Max |dI|             : {max(abs_diffs):.6g}")
    if finite_rel:
        print(f"Max relative dI      : {max(finite_rel) * 100.0:.4f}%")
        print(f"Mean relative dI     : {sum(finite_rel) / len(finite_rel) * 100.0:.4f}%")

    exceeding = [k for k, value in zip(populated, rel_diffs, strict=True) if value > intensity_tolerance]
    print(f"Bins over tolerance  : {len(exceeding)} (tolerance {intensity_tolerance * 100.0:.2f}%)")
    for k in exceeding[:10]:
        print(
            f"    Q={ref_q[k]:.4f}  reference={ref_i[k]:.4f}+/-{ref_e[k]:.4f}  "
            f"candidate={cand_i[k]:.4f}+/-{cand_e[k]:.4f}"
        )
    if len(exceeding) > 10:
        print(f"    ... and {len(exceeding) - 10} more")

    return 1 if exceeding else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_csv", type=Path, help="Baseline CSV (for example the Mantid output).")
    parser.add_argument("candidate_csv", type=Path, help="CSV to check (for example the analyzer output).")
    parser.add_argument(
        "--q-tolerance",
        type=float,
        default=1e-6,
        help="Largest acceptable per-bin Q difference before warning about axis alignment.",
    )
    parser.add_argument(
        "--intensity-tolerance",
        type=float,
        default=0.01,
        help="Fractional I(Q) difference counted as a disagreement (default 0.01 = 1%%).",
    )
    args = parser.parse_args()

    reference = read_histogram_csv(args.reference_csv)
    candidate = read_histogram_csv(args.candidate_csv)
    print(f"reference : {args.reference_csv}")
    print(f"candidate : {args.candidate_csv}")
    return compare(reference, candidate, args.q_tolerance, args.intensity_tolerance)


if __name__ == "__main__":
    raise SystemExit(main())
