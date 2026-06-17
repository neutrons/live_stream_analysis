from pathlib import Path

import h5py
import numpy as np
import pytest

from live_stream_analysis.main import main
from live_stream_analysis.preparer.converter import write_pixel_geometry_csv
from live_stream_analysis.preparer.instrument import build_detector_geometry


def _minimal_idf() -> str:
    return """<?xml version='1.0' encoding='UTF-8'?>
<instrument name='TEST' xmlns='http://www.mantidproject.org/IDF/1.0'>
    <component type='moderator'>
        <location z='-1.0' />
    </component>
    <type name='moderator' is='Source' />
    <component type='sample-position'>
        <location />
    </component>
    <type name='sample-position' is='SamplePos' />
    <idlist idname='bank1_ids'>
        <id val='1' />
    </idlist>
    <component type='bank1' idlist='bank1_ids'>
        <location x='1.0' y='0.0' z='0.0' />
    </component>
    <type name='bank1'>
        <component type='pixel'>
            <location />
        </component>
    </type>
    <type name='pixel' is='detector' />
</instrument>
"""


def _write_nexus(tmp_path: Path, name: str, event_ids: list[int], event_tofs: list[float]) -> Path:
    path = tmp_path / name
    with h5py.File(path, "w") as handle:
        entry = handle.create_group("entry")
        instrument = entry.create_group("instrument")
        instrument_xml = instrument.create_group("instrument_xml")
        instrument_xml.create_dataset("data", data=np.array([_minimal_idf().encode("utf-8")]))
        events = entry.create_group("bank1_events")
        events.create_dataset("event_id", data=np.array(event_ids, dtype=np.int32))
        events.create_dataset("event_time_offset", data=np.array(event_tofs, dtype=np.float64))
    return path


def _write_diffcal(
    tmp_path: Path,
    name: str,
    detids: list[int],
    difc: list[float],
    difa: list[float],
    tzero: list[float],
    use: list[int],
) -> Path:
    path = tmp_path / name
    with h5py.File(path, "w") as handle:
        calibration = handle.create_group("calibration")
        calibration.create_dataset("detid", data=np.array(detids, dtype=np.int32))
        calibration.create_dataset("difc", data=np.array(difc, dtype=np.float64))
        calibration.create_dataset("difa", data=np.array(difa, dtype=np.float64))
        calibration.create_dataset("tzero", data=np.array(tzero, dtype=np.float64))
        calibration.create_dataset("use", data=np.array(use, dtype=np.int32))
        instrument = calibration.create_group("instrument")
        instrument.create_dataset("name", data=np.array([b"NOMAD"]))
        instrument.create_dataset("instrument_source", data=np.array([b"NOMAD_Definition.xml"]))
    return path


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


def test_preparer_calibration_file_adds_diffcal_and_use_columns(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parents[1] / "data" / "idf"
    idf_path = fixture_dir / "NOMAD_Definition.xml"
    rows = build_detector_geometry(idf_path)
    first_detector = rows[0][0]

    calibration_file = _write_diffcal(
        tmp_path,
        "calibration.h5",
        detids=[first_detector],
        difc=[2000.0],
        difa=[0.2],
        tzero=[10.0],
        use=[0],
    )

    pixel_csv = tmp_path / "pixel_geometry_calibrated.csv"
    iq_csv = tmp_path / "iq_calibrated.csv"

    rc = main(
        [
            "preparer",
            "--idf-file",
            str(idf_path),
            "--calibration-file",
            str(calibration_file),
            "--pixel-geometry-csv",
            str(pixel_csv),
            "--iq-csv",
            str(iq_csv),
        ]
    )

    assert rc == 0
    lines = pixel_csv.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "pixel id,L2 value,theta value,TOF-to-Q matrix element,difc,difa,tzero,use"

    first_data = lines[1].split(",")
    assert int(first_data[0]) == first_detector
    assert float(first_data[3]) == pytest.approx(2.0 * np.pi * 2000.0)
    assert float(first_data[4]) == pytest.approx(2000.0)
    assert float(first_data[5]) == pytest.approx(0.2)
    assert float(first_data[6]) == pytest.approx(10.0)
    assert int(first_data[7]) == 0


def test_preparer_background_reduction_mode_writes_three_column_csv(tmp_path: Path) -> None:
    nexus_path = _write_nexus(tmp_path, "background.nxs.h5", [1, 1, 1], [1.0, 1.0, 1.0])
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
    nexus_a = _write_nexus(tmp_path, "norm_a.nxs.h5", [1, 1], [1.0, 1.0])
    nexus_b = _write_nexus(tmp_path, "norm_b.nxs.h5", [1], [1.0])
    output_csv = tmp_path / "normalization.csv"

    rc = main(
        [
            "preparer",
            "--mode",
            "normalization",
            "--nexus-file",
            str(nexus_a),
            "--nexus-file",
            str(nexus_b),
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
