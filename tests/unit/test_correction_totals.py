from __future__ import annotations

from pathlib import Path

import pytest

from live_stream_analysis.correction_totals import (
    DURATION_COLUMN,
    PROTON_CHARGE_COLUMN,
    CorrectionTotals,
    correction_totals_from_rows,
    load_correction_totals,
)


def test_divisor_selects_the_axis_matching_normalize_by():
    totals = CorrectionTotals(proton_charge_pc=2.0e12, duration_s=1443.9)

    assert totals.divisor("proton-charge") == pytest.approx(2.0e12)
    assert totals.divisor("time") == pytest.approx(1443.9)
    assert totals.divisor("none") is None


def test_divisor_is_none_for_a_missing_or_non_positive_total():
    """A zero total would divide the correction by zero, so it counts as unknown."""
    assert CorrectionTotals().divisor("proton-charge") is None
    assert CorrectionTotals(proton_charge_pc=0.0, duration_s=-1.0).divisor("proton-charge") is None
    assert CorrectionTotals(proton_charge_pc=0.0, duration_s=-1.0).divisor("time") is None


def test_rows_with_totals_columns_are_parsed_from_the_first_row():
    rows = [
        {"Q value": "0.01", PROTON_CHARGE_COLUMN: "2000.5", DURATION_COLUMN: "12.25"},
        {"Q value": "0.03", PROTON_CHARGE_COLUMN: "2000.5", DURATION_COLUMN: "12.25"},
    ]

    assert correction_totals_from_rows(rows) == CorrectionTotals(proton_charge_pc=2000.5, duration_s=12.25)


def test_legacy_rows_without_totals_columns_report_unknown_totals():
    assert correction_totals_from_rows([{"Q value": "0.01", "I(Q)": "1.0"}]) == CorrectionTotals()
    assert correction_totals_from_rows([]) == CorrectionTotals()


def test_blank_totals_cells_report_unknown_totals():
    """The preparer leaves a total blank when any input file lacked it."""
    rows = [{PROTON_CHARGE_COLUMN: "", DURATION_COLUMN: "5.0"}]

    assert correction_totals_from_rows(rows) == CorrectionTotals(proton_charge_pc=None, duration_s=5.0)


def test_load_correction_totals_reads_a_csv_file(tmp_path: Path):
    path = tmp_path / "background.csv"
    path.write_text(
        f"Q value,I(Q),Error I(Q),{PROTON_CHARGE_COLUMN},{DURATION_COLUMN}\n0.01,1.0,1.0,300.0,4.0\n",
        encoding="utf-8",
    )

    assert load_correction_totals(str(path)) == CorrectionTotals(proton_charge_pc=300.0, duration_s=4.0)
