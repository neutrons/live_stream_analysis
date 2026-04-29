from pathlib import Path

from live_stream_analysis.main import main
from live_stream_analysis.preparer.converter import write_pixel_geometry_csv
from live_stream_analysis.preparer.instrument import build_detector_geometry


def test_preparer_cli_writes_csv_outputs(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parents[1] / "data" / "idf"
    idf_path = fixture_dir / "NOMAD_Definition.xml"
    pixel_csv = tmp_path / "pixel_geometry.csv"
    iq_csv = tmp_path / "iq.csv"

    rc = main(
        [
            "preparer",
            "--idf-file",
            str(idf_path),
            "--pixel-geometry-csv",
            str(pixel_csv),
            "--iq-csv",
            str(iq_csv),
            "--q-bins",
            "120",
        ]
    )

    assert rc == 0
    assert pixel_csv.exists()
    assert iq_csv.exists()

    pixel_lines = pixel_csv.read_text(encoding="utf-8").strip().splitlines()
    iq_lines = iq_csv.read_text(encoding="utf-8").strip().splitlines()

    assert pixel_lines[0] == "pixel id,L2 value,theta value"
    assert iq_lines[0] == "Q value,I(Q)"
    assert len(pixel_lines) > 10
    assert len(iq_lines) == 121


def test_pixel_geometry_targets_match_all_idf_inputs(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parents[1] / "data"
    idf_dir = fixture_dir / "idf"
    target_dir = fixture_dir / "target"

    idf_paths = sorted(idf_dir.glob("*.xml"))
    assert idf_paths, "No IDF fixtures were found"

    for idf_path in idf_paths:
        target_path = target_dir / f"{idf_path.stem}_pixel_geometry.csv"
        assert target_path.exists(), f"Missing target CSV for {idf_path.name}"

        rows = build_detector_geometry(idf_path)
        generated_path = tmp_path / target_path.name
        write_pixel_geometry_csv(rows, generated_path)

        assert generated_path.read_text(encoding="utf-8") == target_path.read_text(encoding="utf-8")
