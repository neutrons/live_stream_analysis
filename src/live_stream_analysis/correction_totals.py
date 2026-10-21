"""Exposure totals carried alongside background and normalization corrections.

A correction CSV is only comparable to the live histogram once both are expressed per unit of
exposure. The preparer records the integrated proton charge and run duration of the files it
reduced as two extra columns, repeated on every row, so that the three columns existing
consumers read stay untouched and the CSV stays a plain table.

Shared by the preparer (which writes the columns), the analyzer (which reads them from a file)
and the INTERSECT service (which reads them from posted CSV text), so it imports neither.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

PROTON_CHARGE_COLUMN = "Proton charge (pC)"
DURATION_COLUMN = "Duration (s)"


@dataclass(frozen=True)
class CorrectionTotals:
    """Integrated proton charge and duration of the runs a correction was reduced from.

    Either total is None when unknown -- a CSV written before these columns existed, or one
    reduced from a file that did not record it.
    """

    proton_charge_pc: float | None = None
    duration_s: float | None = None

    def divisor(self, normalize_by: str) -> float | None:
        """Return the total matching ``--normalize-by``, or None if it cannot be divided by."""
        if normalize_by == "proton-charge":
            value = self.proton_charge_pc
        elif normalize_by == "time":
            value = self.duration_s
        else:
            return None
        return value if value is not None and value > 0.0 else None


def _parse_total(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    return float(raw)


def correction_totals_from_rows(rows: Iterable[Mapping[str, str]]) -> CorrectionTotals:
    """Read the totals from the first row of a parsed correction CSV."""
    for row in rows:
        return CorrectionTotals(
            proton_charge_pc=_parse_total(row.get(PROTON_CHARGE_COLUMN)),
            duration_s=_parse_total(row.get(DURATION_COLUMN)),
        )
    return CorrectionTotals()


def load_correction_totals(csv_path: str) -> CorrectionTotals:
    with Path(csv_path).resolve().open("r", encoding="utf-8", newline="") as handle:
        return correction_totals_from_rows(csv.DictReader(handle))


__all__ = [
    "DURATION_COLUMN",
    "PROTON_CHARGE_COLUMN",
    "CorrectionTotals",
    "correction_totals_from_rows",
    "load_correction_totals",
]
