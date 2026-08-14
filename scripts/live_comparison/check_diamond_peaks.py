#!/usr/bin/env python3
"""Measure observed diamond peak positions in histogram CSVs against the expected values.

Diamond peak positions follow from the lattice parameter alone, so they are an absolute
reference: any systematic offset between the observed and expected Q is a property of the
reduction, not of the sample. Comparing several reductions against the same reference says
which one places peaks correctly, which comparing them only against each other cannot.

Each peak's centre is refined by fitting a parabola through the maximum bin and its two
neighbours, so the result is not quantised to the bin width.

Pure Python; runs in the project's uv environment.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def read_histogram_csv(path: Path) -> tuple[list[float], list[float]]:
    q_values: list[float] = []
    intensity: list[float] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"Q value", "I(Q)"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"{path} must have columns: Q value, I(Q), Error I(Q)")
        for row in reader:
            q_values.append(float(row["Q value"]))
            intensity.append(float(row["I(Q)"]))
    return q_values, intensity


def read_reference_csv(path: Path) -> list[tuple[str, float, float]]:
    """Return (hkl, expected Q, relative intensity) for each reference reflection."""
    reflections: list[tuple[str, float, float]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            reflections.append((row["hkl"], float(row["Q (1/A)"]), float(row["rel intensity"])))
    return reflections


def refine_peak(q_values: list[float], intensity: list[float], index: int) -> float:
    """Sub-bin peak centre from a parabola through (index-1, index, index+1)."""
    if index <= 0 or index >= len(intensity) - 1:
        return q_values[index]
    left, centre, right = intensity[index - 1], intensity[index], intensity[index + 1]
    denominator = left - 2.0 * centre + right
    if denominator == 0.0:
        return q_values[index]
    offset = 0.5 * (left - right) / denominator
    if abs(offset) > 1.0:
        return q_values[index]
    bin_width = q_values[index + 1] - q_values[index]
    return q_values[index] + offset * bin_width


def find_peak_near(q_values: list[float], intensity: list[float], target_q: float, window: float):
    """Return (observed Q, peak height) for the tallest bin within +/- window of target_q."""
    candidates = [k for k, q in enumerate(q_values) if abs(q - target_q) <= window]
    if not candidates:
        return None
    best = max(candidates, key=lambda k: intensity[k])
    # A flat region is not a peak; require the maximum to stand above the window edges.
    edge = min(intensity[candidates[0]], intensity[candidates[-1]])
    if intensity[best] <= edge:
        return None
    return refine_peak(q_values, intensity, best), intensity[best]


def check_file(
    path: Path,
    reflections: list[tuple[str, float, float]],
    window: float,
    min_rel_intensity: float,
    verbose: bool,
) -> None:
    q_values, intensity = read_histogram_csv(path)
    q_lo, q_hi = min(q_values), max(q_values)
    offsets: list[float] = []

    print(f"\n=== {path.name} ===")
    if verbose:
        print(f"{'hkl':<14}{'expected Q':>12}{'observed Q':>12}{'dQ':>10}{'dQ/Q %':>9}")

    for hkl, expected_q, rel_intensity in reflections:
        if rel_intensity < min_rel_intensity or not (q_lo + window <= expected_q <= q_hi - window):
            continue
        found = find_peak_near(q_values, intensity, expected_q, window)
        if found is None:
            if verbose:
                print(f"{hkl:<14}{expected_q:>12.4f}{'not found':>12}")
            continue
        observed_q, _height = found
        delta = observed_q - expected_q
        offsets.append(delta)
        if verbose:
            print(f"{hkl:<14}{expected_q:>12.4f}{observed_q:>12.4f}{delta:>+10.4f}{100.0 * delta / expected_q:>+9.3f}")

    if not offsets:
        print("  no reflections matched")
        return
    mean_offset = sum(offsets) / len(offsets)
    mean_abs = sum(abs(value) for value in offsets) / len(offsets)
    rms = math.sqrt(sum(value * value for value in offsets) / len(offsets))
    print(f"  reflections matched : {len(offsets)}")
    print(f"  mean dQ             : {mean_offset:+.4f} 1/A   (systematic shift)")
    print(f"  mean |dQ|           : {mean_abs:.4f} 1/A")
    print(f"  RMS dQ              : {rms:.4f} 1/A")
    print(f"  worst |dQ|          : {max(abs(value) for value in offsets):.4f} 1/A")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("histogram_csv", type=Path, nargs="+", help="Histogram CSV(s) to check.")
    parser.add_argument(
        "--reference-csv",
        type=Path,
        default=Path(__file__).with_name("diamond_reflections.csv"),
        help="Reference reflections from diamond_reference.py.",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=0.12,
        help="Half-width in Q to search around each expected position (default 0.12).",
    )
    parser.add_argument(
        "--min-rel-intensity",
        type=float,
        default=0.5,
        help="Skip reference reflections weaker than this (default 0.5%%).",
    )
    parser.add_argument("--quiet", action="store_true", help="Summary only, no per-reflection table.")
    args = parser.parse_args()

    reflections = read_reference_csv(args.reference_csv)
    print(f"reference: {args.reference_csv.name} ({len(reflections)} reflections)")
    for path in args.histogram_csv:
        check_file(path, reflections, args.window, args.min_rel_intensity, not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
