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

    assert pixel_lines[0] == "pixel id,L2 value,theta value,TOF-to-Q matrix element"
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


def test_preparer_q_matrix_scale_option(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parents[1] / "data" / "idf"
    idf_path = fixture_dir / "NOMAD_Definition.xml"
    pixel_csv = tmp_path / "pixel_geometry_scaled.csv"
    iq_csv = tmp_path / "iq_scaled.csv"

    rc = main(
        [
            "preparer",
            "--idf-file",
            str(idf_path),
            "--pixel-geometry-csv",
            str(pixel_csv),
            "--iq-csv",
            str(iq_csv),
            "--q-matrix-scale",
            "10",
        ]
    )

    assert rc == 0
    lines = pixel_csv.read_text(encoding="utf-8").strip().splitlines()
    first_data = lines[1].split(",")
    scaled_q_matrix = float(first_data[3])

    rows = build_detector_geometry(idf_path)
    raw_first_q_matrix = rows[0][4]

    assert scaled_q_matrix == float(f"{raw_first_q_matrix * 10.0:.8f}")


def test_preparer_background_reduction_mode_writes_three_column_csv(tmp_path: Path) -> None:
    nexus_path = Path(__file__).parents[2] / "nexus_files" / "mt_pac_can" / "NOM_243710.nxs.h5"
    output_csv = tmp_path / "background.csv"

    rc = main(
        [
            "preparer",
            "--mode",
            "background",
            "--nexus-file",
            str(nexus_path),
            "--reduction-output-csv",
            str(output_csv),
            "--q-min",
            "0.0",
            "--q-max",
            "30.0",
        ]
    )

    assert rc == 0
    lines = output_csv.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "Q value,I(Q),Error I(Q)"
    assert len(lines) > 10


def test_preparer_normalization_reduction_mode_accepts_multiple_files(tmp_path: Path) -> None:
    base = Path(__file__).parents[2] / "nexus_files"
    output_csv = tmp_path / "normalization.csv"

    rc = main(
        [
            "preparer",
            "--mode",
            "normalization",
            "--nexus-file",
            str(base / "vanadium" / "NOM_243712.nxs.h5"),
            "--reduction-output-csv",
            str(output_csv),
            "--q-min",
            "0.0",
            "--q-max",
            "30.0",
            "--peak-window",
            "21",
        ]
    )

    assert rc == 0
    lines = output_csv.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "Q value,I(Q),Error I(Q)"
    assert len(lines) > 10
