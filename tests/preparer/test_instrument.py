import math
from pathlib import Path
import xml.etree.ElementTree as ET

from live_stream_analysis.preparer.instrument import (
    PI4,
    TOF_LAMBDA_CONVERSION_US_PER_M_ANGSTROM,
    build_detector_geometry,
)


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


def test_matrix_element_matches_formula_for_known_nomad_detector() -> None:
    fixture_dir = Path(__file__).parents[1] / "data" / "idf"
    idf_path = fixture_dir / "NOMAD_Definition.xml"
    rows = build_detector_geometry(idf_path)

    # Use one stable detector id to keep this a small, deterministic guardrail.
    det_id, l2, theta_deg, l_total, q_matrix_element = next(row for row in rows if row[0] == 0)
    assert det_id == 0

    root = ET.parse(idf_path).getroot()

    def find_component_position(component_type: str) -> tuple[float, float, float]:
        for component in root:
            if component.tag.rsplit("}", 1)[-1] != "component":
                continue
            if component.get("type") != component_type:
                continue
            for loc in component:
                if loc.tag.rsplit("}", 1)[-1] != "location":
                    continue
                return (
                    float(loc.get("x", "0.0")),
                    float(loc.get("y", "0.0")),
                    float(loc.get("z", "0.0")),
                )
        return (0.0, 0.0, 0.0)

    source_pos = find_component_position("moderator")
    sample_pos = find_component_position("sample-position")
    l1 = math.dist(source_pos, sample_pos)

    expected_l_total = l1 + l2
    expected_q_matrix_element = (
        PI4
        * math.sin(math.radians(theta_deg) / 2.0)
        * TOF_LAMBDA_CONVERSION_US_PER_M_ANGSTROM
        * expected_l_total
    )

    assert math.isclose(l_total, expected_l_total, rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(q_matrix_element, expected_q_matrix_element, rel_tol=1e-12, abs_tol=1e-12)
