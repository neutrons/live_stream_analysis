from pathlib import Path

from live_stream_analysis.preparer.instrument import build_detector_geometry


def test_build_detector_geometry_for_nomad_and_powgen() -> None:
    fixture_dir = Path(__file__).parents[1] / "data" / "idf"
    nomad_rows = build_detector_geometry(fixture_dir / "NOMAD_Definition.xml")
    powgen_rows = build_detector_geometry(fixture_dir / "POWGEN_Definition_2010.xml")

    assert nomad_rows
    assert powgen_rows
    assert nomad_rows == sorted(nomad_rows, key=lambda r: r[0])
    assert powgen_rows == sorted(powgen_rows, key=lambda r: r[0])
    assert all(row[0] >= 0 for row in nomad_rows)
    assert all(row[0] >= 0 for row in powgen_rows)
    assert 100_000 <= len(nomad_rows) <= 110_000, f"NOMAD row count unexpected: {len(nomad_rows)}"
    assert 17_000 <= len(powgen_rows) <= 18_000, f"POWGEN row count unexpected: {len(powgen_rows)}"
