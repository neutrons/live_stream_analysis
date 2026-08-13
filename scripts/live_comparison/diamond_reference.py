#!/usr/bin/env python3
"""Generate the expected diamond powder reflections as a reference CSV.

Diamond is cubic, space group Fd-3m (227), with 8 carbon atoms per conventional cell.
Reflections are allowed when h, k, l are all odd, or all even with h+k+l divisible by 4.

    d = a / sqrt(h^2 + k^2 + l^2)
    Q = 2*pi / d = 2*pi*sqrt(h^2 + k^2 + l^2) / a

Peak POSITIONS follow from the lattice parameter alone and are therefore exact to the
precision of `a`. Peak INTENSITIES are a kinematic estimate only -- see the note on the
`rel_intensity` column below before using them quantitatively.

Default lattice parameter: a = 3.56712 A, diamond at 298 K.
Reference: T. Hom, W. Kiszenick, B. Post, "Accurate lattice constants from multiple
reflection measurements", J. Appl. Cryst. 8 (1975) 457-458, which gives a = 3.56712(1) A.

Pure Python; runs in the project's uv environment.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

# Carbon coherent neutron scattering length (fm). Sears, Neutron News 3 (1992) 26-37.
CARBON_B_COHERENT_FM = 6.646
# Isotropic displacement parameter for diamond at room temperature (A^2). Diamond is stiff,
# so this is small; values in the literature cluster around 0.19-0.24.
DEFAULT_DEBYE_WALLER_B = 0.20


def is_allowed(h: int, k: int, ell: int) -> bool:
    """Diamond-structure reflection condition."""
    all_odd = h % 2 == 1 and k % 2 == 1 and ell % 2 == 1
    all_even = h % 2 == 0 and k % 2 == 0 and ell % 2 == 0
    return all_odd or (all_even and (h + k + ell) % 4 == 0)


def structure_factor_squared(h: int) -> float:
    """|F|^2 in units of b^2 for the 8-atom conventional cell.

    F = 4b[1 + exp(i*pi*(h+k+l)/2)], giving |F|^2 = 32 b^2 for all-odd reflections and
    64 b^2 when h+k+l is a multiple of 4. For an allowed reflection the parity of h alone
    distinguishes the two cases, since h, k and l must share it.
    """
    return 32.0 if h % 2 == 1 else 64.0


def enumerate_reflections(a_lattice: float, q_max: float, debye_waller_b: float) -> list[dict]:
    """Return every allowed reflection with Q <= q_max, merged by |Q|.

    Distinct hkl families can share the same d-spacing (for example 511 and 333, or 711 and
    551). A powder pattern cannot separate them, so they are merged into one line with their
    multiplicities summed, exactly as they appear in the data.
    """
    # sqrt(N) * 2*pi / a <= q_max  =>  N <= (q_max * a / (2*pi))^2
    max_n = int((q_max * a_lattice / (2.0 * math.pi)) ** 2) + 1
    max_index = int(math.isqrt(max_n)) + 1

    by_n: dict[int, dict] = {}
    for h in range(0, max_index + 1):
        for k in range(0, h + 1):
            for ell in range(0, k + 1):
                if h == k == ell == 0 or not is_allowed(h, k, ell):
                    continue
                n_squared = h * h + k * k + ell * ell
                if n_squared > max_n:
                    continue
                entry = by_n.setdefault(
                    n_squared,
                    {"families": [], "multiplicity": 0, "f_squared": structure_factor_squared(h)},
                )
                entry["families"].append(f"{h}{k}{ell}")
                entry["multiplicity"] += cubic_multiplicity(h, k, ell)

    rows: list[dict] = []
    for n_squared in sorted(by_n):
        entry = by_n[n_squared]
        d_spacing = a_lattice / math.sqrt(n_squared)
        q_value = 2.0 * math.pi / d_spacing
        if q_value > q_max:
            continue
        # Kinematic estimate: multiplicity * |F|^2 * Lorentz(d^4) * Debye-Waller.
        debye_waller = math.exp(-debye_waller_b * q_value * q_value / (16.0 * math.pi * math.pi))
        intensity = entry["multiplicity"] * entry["f_squared"] * (d_spacing**4) * debye_waller
        rows.append(
            {
                "hkl": "/".join(sorted(set(entry["families"]))),
                "h2k2l2": n_squared,
                "d_spacing": d_spacing,
                "q_value": q_value,
                "multiplicity": entry["multiplicity"],
                "f_squared_over_b2": entry["f_squared"],
                "rel_intensity": intensity,
            }
        )

    if rows:
        brightest = max(row["rel_intensity"] for row in rows)
        for row in rows:
            row["rel_intensity"] = 100.0 * row["rel_intensity"] / brightest
    return rows


def cubic_multiplicity(h: int, k: int, ell: int) -> int:
    """Number of symmetry-equivalent planes for an hkl family in the cubic system."""
    indices = sorted([abs(h), abs(k), abs(ell)], reverse=True)
    distinct = len(set(indices))
    zeros = indices.count(0)
    if zeros == 2:
        return 6  # h00
    if zeros == 1:
        return 12 if distinct == 2 else 24  # hh0 or hk0
    if distinct == 1:
        return 8  # hhh
    if distinct == 2:
        return 24  # hhk
    return 48  # hkl


def write_csv(rows: list[dict], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["hkl", "h2+k2+l2", "d (A)", "Q (1/A)", "multiplicity", "|F|^2/b^2", "rel intensity"])
        for row in rows:
            writer.writerow(
                [
                    row["hkl"],
                    row["h2k2l2"],
                    f"{row['d_spacing']:.6f}",
                    f"{row['q_value']:.6f}",
                    row["multiplicity"],
                    f"{row['f_squared_over_b2']:.0f}",
                    f"{row['rel_intensity']:.3f}",
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lattice-parameter", type=float, default=3.56712, help="Diamond a in Angstrom.")
    parser.add_argument("--q-max", type=float, default=40.0, help="Highest Q to enumerate.")
    parser.add_argument(
        "--debye-waller-b",
        type=float,
        default=DEFAULT_DEBYE_WALLER_B,
        help="Isotropic B in A^2 used only for the relative-intensity estimate.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path(__file__).with_name("diamond_reflections.csv"),
        help="Where to write the reference CSV.",
    )
    args = parser.parse_args()

    rows = enumerate_reflections(args.lattice_parameter, args.q_max, args.debye_waller_b)
    write_csv(rows, args.output_csv)
    print(f"a = {args.lattice_parameter} A -> {len(rows)} allowed reflections up to Q = {args.q_max}")
    print(f"Wrote {args.output_csv.resolve()}")
    print()
    print(f"{'hkl':<14}{'d (A)':>10}{'Q (1/A)':>10}{'mult':>6}{'rel I':>9}")
    for row in rows[:12]:
        print(
            f"{row['hkl']:<14}{row['d_spacing']:>10.4f}{row['q_value']:>10.4f}"
            f"{row['multiplicity']:>6}{row['rel_intensity']:>9.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
