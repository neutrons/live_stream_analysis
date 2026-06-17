"""Unit tests for the 'analyze' CLI subparser."""

from __future__ import annotations

from pathlib import Path

import pytest
from readadara import AdaraFileReader

from live_stream_analysis.analyzer.adara import _build_reader
from live_stream_analysis.main import build_parser, main
from tests.analyzer.adara_fixtures import event_packet, null_packet, rtdl_packet


def _write_adara(tmp_path: Path, *packets: bytes) -> Path:
    path = tmp_path / "sample.adara"
    path.write_bytes(b"".join(packets))
    return path


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


class TestAnalyzeParser:
    def test_subcommand_registered(self):
        parser = build_parser()
        # parse_known_args returns without error when 'analyze' is listed
        # actual required args are not present, so we check via choices
        subparsers_action = next(a for a in parser._actions if hasattr(a, "_name_parser_map"))
        assert "analyze" in subparsers_action._name_parser_map

    def test_requires_one_source(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["analyze"])
        assert exc_info.value.code != 0

    def test_mutual_exclusion(self, tmp_path: Path):
        path = _write_adara(tmp_path, null_packet())
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "analyze",
                    "--adara-file",
                    str(path),
                    "--adara-stream",
                    "localhost",
                    "31415",
                ]
            )
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# --adara-file path
# ---------------------------------------------------------------------------


class TestAdaraFileCLI:
    def test_build_reader_returns_external_readadara_file_reader(self, tmp_path: Path):
        path = _write_adara(tmp_path, null_packet())
        args = build_parser().parse_args(["analyze", "--adara-file", str(path)])
        reader = _build_reader(args)
        assert isinstance(reader, AdaraFileReader)

    def test_analyze_file_exits_zero(self, tmp_path: Path):
        path = _write_adara(tmp_path, rtdl_packet(), event_packet([(1, 100)]))
        rc = main(["analyze", "--adara-file", str(path)])
        assert rc == 0

    def test_analyze_empty_file_exits_zero(self, tmp_path: Path):
        path = _write_adara(tmp_path)
        rc = main(["analyze", "--adara-file", str(path)])
        assert rc == 0

    def test_analyze_missing_file_exits_nonzero(self, tmp_path: Path):
        rc = main(["analyze", "--adara-file", str(tmp_path / "does_not_exist.adara")])
        assert rc != 0

    def test_analyze_prints_packet_count(self, tmp_path: Path, capsys):
        path = _write_adara(tmp_path, rtdl_packet(), null_packet(), event_packet([(3, 30)]))
        main(["analyze", "--adara-file", str(path)])
        out = capsys.readouterr().out
        assert "Packets read" in out
        assert "3" in out

    def test_analyze_prints_event_count(self, tmp_path: Path, capsys):
        events = [(i, i * 10) for i in range(5)]
        path = _write_adara(tmp_path, event_packet(events))
        main(["analyze", "--adara-file", str(path)])
        out = capsys.readouterr().out
        assert "Total events" in out
        assert "5" in out

    def test_histogram_mode_writes_expected_bin(self, tmp_path: Path, capsys):
        path = _write_adara(tmp_path, event_packet([(1, 1), (1, 1)]))
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "0,1.0,1.0,0.0",
                    "1,1.0,1.0,99.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        histogram_csv = tmp_path / "histogram.csv"

        rc = main(
            [
                "analyze",
                "--adara-file",
                str(path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "100",
                "--histogram-q-bin-size",
                "0.02",
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "Histogrammed events" in out
        assert "2" in out
        assert histogram_csv.exists()
        histogram_lines = histogram_csv.read_text(encoding="utf-8").splitlines()
        assert histogram_lines[0] == "Q value,I(Q),Error I(Q)"
        assert "99.01000000,2.00000000,1.41421356" in histogram_lines

    def test_histogram_mode_supports_unscaled_constants_with_tof_tick(self, tmp_path: Path, capsys):
        path = _write_adara(tmp_path, event_packet([(1, 1)]))
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "0,1.0,1.0,0.0",
                    "1,1.0,1.0,9.9",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        histogram_csv = tmp_path / "histogram_tick.csv"

        rc = main(
            [
                "analyze",
                "--adara-file",
                str(path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "100",
                "--histogram-q-bin-size",
                "0.02",
                "--tof-tick-us",
                "0.1",
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "TOF tick size (us)" in out
        assert "0.1" in out
        histogram_lines = histogram_csv.read_text(encoding="utf-8")
        assert "99.01000000,1.00000000,1.00000000" in histogram_lines

    def test_histogram_default_q_bin_size_and_q_max(self, tmp_path: Path, capsys):
        path = _write_adara(tmp_path, event_packet([(1, 1)]))
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "0,1.0,1.0,0.0",
                    "1,1.0,1.0,29.98",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        histogram_csv = tmp_path / "histogram_defaults.csv"

        rc = main(
            [
                "analyze",
                "--adara-file",
                str(path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "Histogram bins" in out
        assert "1500" in out
        histogram_lines = histogram_csv.read_text(encoding="utf-8")
        assert "29.99000000,1.00000000,1.00000000" in histogram_lines

    def test_histogram_mode_applies_background_subtraction_and_normalization(self, tmp_path: Path, capsys):
        path = _write_adara(tmp_path, event_packet([(1, 1), (1, 1), (1, 1), (1, 1)]))
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "0,1.0,1.0,0.0",
                    "1,1.0,1.0,99.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        background_csv = tmp_path / "background.csv"
        background_csv.write_text(
            "\n".join(
                [
                    "Q value,I(Q),Error I(Q)",
                    "99.01000000,2.00000000,1.41421356",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        normalization_csv = tmp_path / "normalization.csv"
        normalization_csv.write_text(
            "\n".join(
                [
                    "Q value,I(Q),Error I(Q)",
                    "99.01000000,2.00000000,1.41421356",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        histogram_csv = tmp_path / "histogram_corrected.csv"

        rc = main(
            [
                "analyze",
                "--adara-file",
                str(path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "100",
                "--histogram-q-bin-size",
                "0.02",
                "--background-subtraction",
                str(background_csv),
                "--normalization",
                str(normalization_csv),
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "Background CSV" in out
        assert "Normalization CSV" in out
        histogram_lines = histogram_csv.read_text(encoding="utf-8")
        assert "99.01000000,1.00000000,1.22474487" in histogram_lines
