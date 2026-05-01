"""Unit tests for the 'analyze' CLI subparser."""

from __future__ import annotations

import struct
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
        subparsers_action = next(
            a for a in parser._actions if hasattr(a, "_name_parser_map")
        )
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
                    "--adara-file", str(path),
                    "--adara-stream", "localhost", "31415",
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
