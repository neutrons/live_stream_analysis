"""Unit tests for the 'analyze' CLI subparser."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import h5py
import numpy as np
import pytest
from readadara import AdaraFileReader

from live_stream_analysis.analyzer import histogram as histogram_module
from live_stream_analysis.analyzer.factory import build_reader as _build_reader
from live_stream_analysis.analyzer.histogram import load_correction_csv
from live_stream_analysis.analyzer.histogram_runner import _warn_if_correction_lacks_totals
from live_stream_analysis.analyzer.live_plot import compute_relative_uncertainty
from live_stream_analysis.correction_totals import DURATION_COLUMN, PROTON_CHARGE_COLUMN
from live_stream_analysis.main import build_parser, main
from live_stream_analysis.statistics import STATISTICS_CSV_COLUMNS
from tests.unit.analyzer.adara_fixtures import event_packet, null_packet, rtdl_packet, run_status_packet


def _write_adara(tmp_path: Path, *packets: bytes) -> Path:
    path = tmp_path / "sample.adara"
    path.write_bytes(b"".join(packets))
    return path


def _write_nexus(tmp_path: Path, name: str, event_ids: list[int], event_tofs: list[float], idf_text: str) -> Path:
    path = tmp_path / name
    with h5py.File(path, "w") as handle:
        entry = handle.create_group("entry")
        instrument = entry.create_group("instrument")
        instrument_xml = instrument.create_group("instrument_xml")
        instrument_xml.create_dataset("data", data=np.array([idf_text.encode("utf-8")]))
        events = entry.create_group("bank1_events")
        events.create_dataset("event_id", data=np.array(event_ids, dtype=np.int32))
        events.create_dataset("event_time_offset", data=np.array(event_tofs, dtype=np.float64))
    return path


def _minimal_idf() -> str:
    return """<?xml version='1.0' encoding='UTF-8'?>
<instrument name='TEST' xmlns='http://www.mantidproject.org/IDF/1.0'>
    <component type='source'>
        <location z='-1.0' />
    </component>
    <type name='source' is='Source' />
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


def _minimal_preparer_idf() -> str:
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

    def test_accepts_enable_intersect_with_config(self, tmp_path: Path):
        config_path = tmp_path / "intersect.yaml"
        config_path.write_text("service: {}\n", encoding="utf-8")

        args = build_parser().parse_args(
            [
                "analyze",
                "--adara-file",
                str(tmp_path / "sample.adara"),
                "--enable-intersect",
                "--intersect-config",
                str(config_path),
            ]
        )

        assert args.enable_intersect is True
        assert args.intersect_config == str(config_path)

    def test_accepts_adara_file_delay_flags(self, tmp_path: Path):
        args = build_parser().parse_args(
            [
                "analyze",
                "--adara-file",
                str(tmp_path / "sample.adara"),
                "--adara-file-delay-read",
                "2.5",
                "--adara-file-delay-intersect",
            ]
        )

        assert args.adara_file_delay_read == 2.5
        assert args.adara_file_delay_intersect is True

    def test_enable_intersect_requires_config(self, tmp_path: Path):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "analyze",
                    "--adara-file",
                    str(tmp_path / "sample.adara"),
                    "--enable-intersect",
                ]
            )

        assert exc_info.value.code != 0

    def test_intersect_listener_subcommand_accepts_config(self, tmp_path: Path):
        config_path = tmp_path / "intersect.yaml"
        config_path.write_text("service: {}\n", encoding="utf-8")

        args = build_parser().parse_args(["intersect-listen", "--intersect-config", str(config_path)])

        assert args._cmd == "intersect-listen"
        assert args.intersect_config == str(config_path)


class TestAnalyzerHelpers:
    def test_compute_relative_uncertainty_uses_absolute_ratio_and_zero_guard(self):
        result = compute_relative_uncertainty([2.0, -4.0, 0.0], [1.0, 2.0, 3.0])

        assert result == [0.5, 0.5, 0.0]

    def test_load_correction_csv_maps_q_values_to_expected_bins(self, tmp_path: Path):
        correction_csv = tmp_path / "correction.csv"
        correction_csv.write_text(
            "\n".join(
                [
                    "Q value,I(Q),Error I(Q)",
                    "0.01000000,5.00000000,2.00000000",
                    "0.03000000,7.00000000,3.00000000",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        values, errors = load_correction_csv(
            str(correction_csv), expected_bins=3, q_bin_size=0.02, q_min=0.0, q_max=0.05
        )

        assert values == [5.0, 7.0, 0.0]
        assert errors == [2.0, 3.0, 0.0]

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

    def test_mutual_exclusion_between_adara_and_nexus(self, tmp_path: Path):
        adara_path = _write_adara(tmp_path, null_packet())
        nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1], [1.0], _minimal_idf())
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "analyze",
                    "--adara-file",
                    str(adara_path),
                    "--nexus-file",
                    str(nexus_path),
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

    def test_histogram_mode_uses_adara_event_tuple_as_pixel_id_then_tof(self, tmp_path: Path):
        path = _write_adara(tmp_path, event_packet([(1, 100)]))
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "1,1.0,1.0,1000.0",
                    "100,1.0,1.0,0.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        histogram_csv = tmp_path / "histogram_tuple_order.csv"

        rc = main(
            [
                "analyze",
                "--adara-file",
                str(path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "30",
                "--histogram-q-bin-size",
                "0.02",
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )

        assert rc == 0
        histogram_lines = histogram_csv.read_text(encoding="utf-8")
        assert "10.01000000,1.00000000,1.00000000" in histogram_lines

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

    def test_histogram_q_min_offsets_bins_and_csv_q_values(self, tmp_path: Path):
        path = _write_adara(tmp_path, event_packet([(1, 1)]))
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "0,1.0,1.0,0.0",
                    "1,1.0,1.0,0.61",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        histogram_csv = tmp_path / "histogram_q_min.csv"

        rc = main(
            [
                "analyze",
                "--adara-file",
                str(path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-min",
                "0.6",
                "--histogram-q-max",
                "1.0",
                "--histogram-q-bin-size",
                "0.02",
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )

        assert rc == 0
        histogram_lines = histogram_csv.read_text(encoding="utf-8").splitlines()
        assert histogram_lines[1] == "0.61000000,1.00000000,1.00000000"

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
        assert "99.01000000,1.00000000,1.41421356" in histogram_lines

    def test_histogram_mode_live_plot_updates_for_adara(self, tmp_path: Path, monkeypatch):
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
        updates: list[tuple[int, int]] = []

        class _StubPlotter:
            def update(self, intensity, error, _relative_uncertainty, _statistics=None):
                updates.append((len(intensity), int(round(max(error)))))

            def close(self):
                updates.append((-1, -1))

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_live_histogram_plotter",
            lambda _args, _histogram_bins, **_kwargs: _StubPlotter(),
        )

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
                "--live-plot-mode",
                "desktop",
            ]
        )

        assert rc == 0
        assert updates
        assert updates[-1] == (-1, -1)

    def test_histogram_mode_writes_the_error_decay_statistics_csv(self, tmp_path: Path):
        """The decay series is what the downstream INTERSECT service fits to forecast run length."""
        path = _write_adara(
            tmp_path,
            rtdl_packet(timestamp_s=100, charge_10pc=100),
            event_packet([(1, 1)], timestamp_s=100),
            rtdl_packet(timestamp_s=105, charge_10pc=300),
            event_packet([(1, 1)], timestamp_s=105),
        )
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
        statistics_csv = tmp_path / "statistics.csv"

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
                "--statistics-output-csv",
                str(statistics_csv),
                "--statistics-sample-interval-seconds",
                "1.0",
            ]
        )

        assert rc == 0
        rows = list(csv.reader(statistics_csv.read_text(encoding="utf-8").splitlines()))
        assert rows[0] == STATISTICS_CSV_COLUMNS
        assert len(rows) > 1

        elapsed = [float(row[0]) for row in rows[1:]]
        charge = [float(row[1]) for row in rows[1:]]
        assert elapsed == sorted(elapsed)
        assert charge == sorted(charge)
        # 100 + 300 units of 10 pC accumulated over a 5 second span.
        assert elapsed[-1] == pytest.approx(5.0)
        assert charge[-1] == pytest.approx(4000.0)

    def test_histogram_mode_normalizes_the_output_csv_by_proton_charge(self, tmp_path: Path):
        """Issue #37: counts divide by the integrated charge, and sigma divides by it too."""
        path = _write_adara(
            tmp_path,
            rtdl_packet(timestamp_s=100, charge_10pc=400),
            event_packet([(1, 1), (1, 1), (1, 1), (1, 1)], timestamp_s=100),
        )
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )

        def _run(normalize_by: str, output: Path) -> dict[float, tuple[float, float]]:
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
                    "--normalize-by",
                    normalize_by,
                    "--histogram-output-csv",
                    str(output),
                ]
            )
            assert rc == 0
            rows = list(csv.DictReader(output.read_text(encoding="utf-8").splitlines()))
            return {
                float(row["Q value"]): (float(row["I(Q)"]), float(row["Error I(Q)"]))
                for row in rows
                if float(row["I(Q)"]) != 0.0
            }

        plain = _run("none", tmp_path / "plain.csv")
        scaled = _run("proton-charge", tmp_path / "scaled.csv")

        assert plain
        # 400 units of 10 pC accumulated on the single pulse.
        charge_pc = 4000.0
        for q_value, (intensity, error) in plain.items():
            scaled_intensity, scaled_error = scaled[q_value]
            assert scaled_intensity == pytest.approx(intensity / charge_pc)
            assert scaled_error == pytest.approx(error / charge_pc)

    def test_histogram_mode_normalizes_by_proton_charge_without_being_asked(self, tmp_path: Path):
        """Proton charge is the default normalization, so raw counts need an explicit opt-out."""
        path = _write_adara(
            tmp_path,
            rtdl_packet(timestamp_s=100, charge_10pc=400),
            event_packet([(1, 1), (1, 1), (1, 1), (1, 1)], timestamp_s=100),
        )
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        default_csv = tmp_path / "default.csv"
        raw_csv = tmp_path / "raw.csv"

        def _run(extra: list[str], output: Path) -> dict[float, float]:
            base = [
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
                str(output),
            ]
            assert main(base + extra) == 0
            rows = list(csv.DictReader(output.read_text(encoding="utf-8").splitlines()))
            return {float(r["Q value"]): float(r["I(Q)"]) for r in rows if float(r["I(Q)"]) != 0.0}

        defaulted = _run([], default_csv)
        raw = _run(["--normalize-by", "none"], raw_csv)

        assert raw
        assert defaulted != raw
        for q_value, intensity in raw.items():
            assert defaulted[q_value] == pytest.approx(intensity / 4000.0)

    def test_histogram_mode_reports_raw_counts_when_the_source_has_no_charge(self, tmp_path: Path):
        """The default must degrade to raw counts rather than dividing by a zero charge."""
        path = _write_adara(tmp_path, event_packet([(1, 1), (1, 1)], timestamp_s=100))
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        output = tmp_path / "histogram.csv"

        assert (
            main(
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
                    str(output),
                ]
            )
            == 0
        )

        rows = list(csv.DictReader(output.read_text(encoding="utf-8").splitlines()))
        populated = [float(r["I(Q)"]) for r in rows if float(r["I(Q)"]) != 0.0]
        assert populated == [pytest.approx(2.0)]

    def test_statistics_series_closes_on_the_totals_the_run_actually_reached(self, tmp_path: Path):
        """A run rarely ends on a sample boundary, so the last point must be forced.

        Otherwise the fully-accumulated histogram is exported alongside a decay point from
        slightly earlier, and a forecaster reading the pair sees them disagree.
        """
        path = _write_adara(
            tmp_path,
            rtdl_packet(timestamp_s=100, charge_10pc=100),
            event_packet([(1, 1)], timestamp_s=100),
            # This pulse lands well inside the 60 second sample interval, so only a forced
            # final sample can capture it.
            rtdl_packet(timestamp_s=105, charge_10pc=300),
            event_packet([(1, 1)], timestamp_s=105),
        )
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        statistics_csv = tmp_path / "statistics.csv"

        assert (
            main(
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
                    "--statistics-output-csv",
                    str(statistics_csv),
                    "--statistics-sample-interval-seconds",
                    "60",
                ]
            )
            == 0
        )

        rows = list(csv.DictReader(statistics_csv.read_text(encoding="utf-8").splitlines()))
        last = rows[-1]
        assert float(last["Elapsed time (s)"]) == pytest.approx(5.0)
        assert float(last["Proton charge (pC)"]) == pytest.approx(4000.0)
        assert int(last["Histogrammed events"]) == 2

    def test_elapsed_time_ignores_slow_control_packet_timestamps(self, tmp_path: Path):
        """Only pulse-bearing packets advance the run clock.

        A real run file opens NEW_RUN, then device descriptors stamped when the SMS last
        configured those devices, weeks earlier for run 244114. Clocking from those put the
        run's start 2,760,701 s early, so every decay sample reported a month of elapsed time.
        """
        path = _write_adara(
            tmp_path,
            run_status_packet(run_number=1, run_start=100, status=1, timestamp_s=100),
            null_packet(format_int=0x800000, timestamp_s=10),  # device descriptor, stale
            rtdl_packet(timestamp_s=100, charge_10pc=100),
            event_packet([(1, 1)], timestamp_s=100),
            null_packet(format_int=0x800200, timestamp_s=20),  # slow-control value, stale
            rtdl_packet(timestamp_s=105, charge_10pc=300),
            event_packet([(1, 1)], timestamp_s=105),
        )
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        statistics_csv = tmp_path / "statistics.csv"

        assert (
            main(
                [
                    "analyze",
                    "--adara-file",
                    str(path),
                    "--histogram-pixel-geometry-csv",
                    str(pixel_csv),
                    "--histogram-q-max",
                    "100",
                    "--statistics-output-csv",
                    str(statistics_csv),
                ]
            )
            == 0
        )

        rows = list(csv.DictReader(statistics_csv.read_text(encoding="utf-8").splitlines()))
        elapsed = [float(row["Elapsed time (s)"]) for row in rows]
        # The clock spans the pulses at 100 s and 105 s, whatever the device packets say.
        assert elapsed[0] == pytest.approx(0.0)
        assert max(elapsed) == pytest.approx(5.0)
        assert elapsed == sorted(elapsed)
        assert float(rows[-1]["Proton charge (pC)"]) == pytest.approx(4000.0)

    def test_no_decay_sample_before_any_events_with_background_subtraction(self, tmp_path: Path):
        """With nothing histogrammed yet, the corrected histogram is just minus the background.

        Its relative error is the background's, which is small, so recording it would put a
        near-perfect point at zero charge at the head of the decay curve: no data reported as
        excellent data. Real run files open with NEW_RUN, which reports the emptied histogram.
        """
        path = _write_adara(
            tmp_path,
            run_status_packet(run_number=1, run_start=100, status=1, timestamp_s=100),
            rtdl_packet(timestamp_s=100, charge_10pc=100),
            event_packet([(1, 1)], timestamp_s=100),
            rtdl_packet(timestamp_s=105, charge_10pc=300),
            event_packet([(1, 1)], timestamp_s=105),
        )
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        background_csv = tmp_path / "background.csv"
        background_csv.write_text("Q value,I(Q),Error I(Q)\n99.01000000,100.00000000,1.00000000\n", encoding="utf-8")
        statistics_csv = tmp_path / "statistics.csv"

        assert (
            main(
                [
                    "analyze",
                    "--adara-file",
                    str(path),
                    "--histogram-pixel-geometry-csv",
                    str(pixel_csv),
                    "--histogram-q-max",
                    "100",
                    "--background-subtraction",
                    str(background_csv),
                    "--normalize-by",
                    "none",
                    "--statistics-output-csv",
                    str(statistics_csv),
                ]
            )
            == 0
        )

        rows = list(csv.DictReader(statistics_csv.read_text(encoding="utf-8").splitlines()))
        assert rows
        assert all(int(row["Histogrammed events"]) > 0 for row in rows)

    def test_histogram_mode_writes_the_2d_statistics_map_csv(self, tmp_path: Path):
        """The 2D map is the q-space decay data the ticket asks to save out."""
        path = _write_adara(
            tmp_path,
            rtdl_packet(timestamp_s=100, charge_10pc=100),
            event_packet([(1, 1)], timestamp_s=100),
            rtdl_packet(timestamp_s=105, charge_10pc=300),
            event_packet([(1, 1)], timestamp_s=105),
        )
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        map_csv = tmp_path / "statistics_map.csv"

        assert (
            main(
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
                    "--statistics-map-output-csv",
                    str(map_csv),
                    "--statistics-q-min",
                    "98.0",
                    "--statistics-q-max",
                    "100.0",
                ]
            )
            == 0
        )

        rows = list(csv.reader(map_csv.read_text(encoding="utf-8").splitlines()))
        assert rows[0][:2] == ["Elapsed time (s)", "Proton charge (pC)"]
        # One column per Q bin inside the 98-100 window at 0.02 bins.
        assert len(rows[0]) == 2 + 100
        assert all(header.startswith("sigma/I(Q) at Q=") for header in rows[0][2:])
        assert len(rows) > 1
        # The run's populated bin sits at Q=99.01 and must carry a real value.
        assert any(float(value) > 0.0 for value in rows[-1][2:])
        assert float(rows[-1][1]) == pytest.approx(4000.0)

    def test_streaming_and_final_intersect_events_are_on_the_same_scale(self, tmp_path: Path, monkeypatch):
        """Periodic events must be corrected like the final one.

        They used to publish raw counts while the run-complete event published corrected
        values, so a subscriber plotting intensity saw it jump by the whole correction factor
        at the end of the run -- ~1e12 once normalization by proton charge is on.
        """
        path = _write_adara(tmp_path, null_packet())
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        config_path = tmp_path / "intersect.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "service:",
                    "  name: nomadanalysis",
                    "  publish_interval_seconds: 1",
                    "events:",
                    "  histogram: histogram.updated",
                    "  run_complete: run.completed",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        published: list[tuple[str, object]] = []

        class _StubPublisher:
            def publish_event(self, name, payload):
                published.append((name, payload))

            def close(self):
                return

        class _StubRunner:
            def accumulate_histogram(
                self,
                reader,
                args,
                q_conversion,
                histogram_bins,
                plotter,
                *,
                chunk_size,
                q_conversion_provider=None,
                histogram_callback=None,
                run_complete_callback=None,
                histogram_state_callback=None,
                hist=None,
                run_statistics=None,
                display_transform=None,
            ):
                _ = display_transform
                _ = (reader, args, q_conversion, plotter, chunk_size, q_conversion_provider, run_complete_callback)
                hist = [0] * histogram_bins
                histogram_state_callback(hist)
                hist[4950] = 400
                # A pulse of charge, so normalization has something to divide by.
                run_statistics.accumulator.observe_pulse(100.0, 4000.0)
                histogram_callback(400, hist)
                return 1, 400, 400, hist, None

            def run_basic_mode(self, reader, *, chunk_size):
                _ = (reader, chunk_size)
                return 0

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_source_runner",
            lambda _args: _StubRunner(),
        )
        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_event_publisher",
            lambda _config, **_kwargs: _StubPublisher(),
        )

        assert (
            main(
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
                    "--enable-intersect",
                    "--intersect-config",
                    str(config_path),
                ]
            )
            == 0
        )

        histograms = [payload for name, payload in published if name == "histogram.updated"]
        assert len(histograms) >= 2
        peaks = [max(payload.intensity) for payload in histograms]
        # 400 counts over 4000 pC. Every event agrees; none reports the raw 400.
        assert peaks == [pytest.approx(0.1)] * len(peaks)

    def test_decay_series_has_no_zero_error_point_before_any_data_arrives(self, tmp_path: Path):
        """A leading (0 charge, 0 error) point reads as perfect data instead of no data.

        Anything fitting the series to forecast a time-to-target would be pulled the wrong way
        by a point claiming zero error was already achieved at zero accumulation.
        """
        path = _write_adara(
            tmp_path,
            # A banked packet whose only event cannot be converted to Q still triggers a
            # statistics callback, with charge already accumulating and the histogram empty --
            # exactly the state that produced a leading all-zero row against the live stream.
            rtdl_packet(timestamp_s=100, charge_10pc=100),
            event_packet([(0, 1)], timestamp_s=100),
            rtdl_packet(timestamp_s=105, charge_10pc=300),
            event_packet([(1, 1), (1, 1)], timestamp_s=105),
        )
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        statistics_csv = tmp_path / "statistics.csv"

        assert (
            main(
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
                    "--statistics-output-csv",
                    str(statistics_csv),
                    "--statistics-sample-interval-seconds",
                    "0",
                ]
            )
            == 0
        )

        rows = list(csv.DictReader(statistics_csv.read_text(encoding="utf-8").splitlines()))
        assert rows
        assert all(float(row["Mean sigma/I(Q)"]) > 0.0 for row in rows)
        assert all(int(row["Nonzero bins"]) > 0 for row in rows)

    def test_histogram_mode_rejects_an_inverted_statistics_q_window(self, tmp_path: Path, capsys):
        path = _write_adara(tmp_path, event_packet([(1, 1)]))
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )

        rc = main(
            [
                "analyze",
                "--adara-file",
                str(path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--statistics-q-min",
                "5.0",
                "--statistics-q-max",
                "1.0",
            ]
        )

        assert rc == 1
        assert "--statistics-q-max must be > --statistics-q-min" in capsys.readouterr().err

    def test_histogram_mode_end_run_publishes_completion_and_resets_histogram(self, tmp_path: Path, monkeypatch):
        path = _write_adara(tmp_path, null_packet())
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
        config_path = tmp_path / "intersect.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "service:",
                    "  name: nomadanalysis",
                    "  publish_interval_seconds: 1",
                    "events:",
                    "  histogram: histogram.updated",
                    "  run_complete: run.completed",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        published: list[tuple[str, object]] = []

        class _StubPublisher:
            def publish_event(self, name, payload):
                published.append((name, payload))

            def close(self):
                return

        class _StubRunner:
            def accumulate_histogram(
                self,
                reader,
                args,
                q_conversion,
                histogram_bins,
                plotter,
                *,
                chunk_size,
                q_conversion_provider=None,
                histogram_callback=None,
                run_complete_callback=None,
                histogram_state_callback=None,
                hist=None,
                run_statistics=None,
                display_transform=None,
            ):
                _ = display_transform
                _ = (
                    reader,
                    args,
                    q_conversion,
                    histogram_bins,
                    plotter,
                    chunk_size,
                    q_conversion_provider,
                    histogram_callback,
                    run_statistics,
                )
                hist = [0] * 5000
                histogram_state_callback(hist)
                hist[4950] = 2
                run_complete_callback(object())
                hist[4950] = 1
                return 5, 3, 3, hist

            def run_basic_mode(self, reader, *, chunk_size: int) -> int:
                _ = (reader, chunk_size)
                return 0

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_event_publisher",
            lambda _config, **_kwargs: _StubPublisher(),
        )
        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_source_runner",
            lambda _args: _StubRunner(),
        )

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
                "--enable-intersect",
                "--intersect-config",
                str(config_path),
            ]
        )

        assert rc == 0
        histogram_events = [payload for name, payload in published if name == "histogram.updated"]
        run_complete_events = [payload for name, payload in published if name == "run.completed"]
        assert len(run_complete_events) == 2
        assert len(histogram_events) >= 2
        assert max(histogram_events[0].intensity) == 2.0
        assert max(histogram_events[-1].intensity) == 1.0

    def test_histogram_mode_end_run_writes_finalized_run_to_output_csv(self, tmp_path: Path, monkeypatch):
        """A run file ending in END_RUN must not write the reset histogram over the finished run."""
        path = _write_adara(tmp_path, null_packet())
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n",
            encoding="utf-8",
        )
        output = tmp_path / "histogram.csv"

        class _EndRunRunner:
            def accumulate_histogram(
                self,
                reader,
                args,
                q_conversion,
                histogram_bins,
                plotter,
                *,
                chunk_size,
                q_conversion_provider=None,
                histogram_callback=None,
                run_complete_callback=None,
                histogram_state_callback=None,
                hist=None,
                run_statistics=None,
                display_transform=None,
            ):
                _ = (reader, args, q_conversion, plotter, chunk_size, q_conversion_provider, histogram_callback)
                _ = (hist, run_statistics, display_transform)
                # Mirrors accumulate_adara_histogram: END_RUN finalizes, which resets the histogram
                # in place, and the reset histogram is what gets returned.
                hist = [0] * histogram_bins
                histogram_state_callback(hist)
                hist[4950] = 7
                run_complete_callback(object())
                return 1, 7, 7, hist, None

            def run_basic_mode(self, reader, *, chunk_size: int) -> int:
                _ = (reader, chunk_size)
                return 0

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_source_runner",
            lambda _args: _EndRunRunner(),
        )

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
                str(output),
            ]
        )

        assert rc == 0
        rows = list(csv.DictReader(output.read_text(encoding="utf-8").splitlines()))
        assert sum(float(row["I(Q)"]) for row in rows) == 7.0

    def test_histogram_mode_live_plot_refresh_every_throttles_intermediate_updates(self, tmp_path: Path, monkeypatch):
        path = _write_adara(tmp_path, event_packet([(1, 1)]), event_packet([(1, 1)]), event_packet([(1, 1)]))
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
        updates: list[tuple[float, float]] = []

        class _StubPlotter:
            def update(self, intensity, _error, relative_uncertainty, _statistics=None):
                updates.append((max(intensity), max(relative_uncertainty)))

            def close(self):
                updates.append((-1.0, -1.0))

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_live_histogram_plotter",
            lambda _args, _histogram_bins, **_kwargs: _StubPlotter(),
        )

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
                "--live-plot-mode",
                "desktop",
                "--live-plot-refresh-every",
                "2",
            ]
        )

        assert rc == 0
        assert updates[:-1] == [(2.0, pytest.approx(2**-0.5)), (3.0, pytest.approx(3**-0.5))]
        assert updates[-1] == (-1.0, -1.0)

    def test_histogram_mode_live_plot_reports_relative_uncertainty(self, tmp_path: Path, monkeypatch):
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
        relative_uncertainty_updates: list[float] = []

        class _StubPlotter:
            def update(self, _intensity, _error, relative_uncertainty, _statistics=None):
                relative_uncertainty_updates.append(max(relative_uncertainty))

            def close(self):
                return

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_live_histogram_plotter",
            lambda _args, _histogram_bins, **_kwargs: _StubPlotter(),
        )

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
                "--live-plot-mode",
                "desktop",
            ]
        )

        assert rc == 0
        assert relative_uncertainty_updates[-1] == pytest.approx(1.41421356)

    def test_histogram_mode_browser_live_plot_uses_browser_plotter(self, tmp_path: Path, monkeypatch):
        path = _write_adara(tmp_path, event_packet([(1, 1)]))
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
        created: list[tuple[str, int, bool]] = []

        class _StubBrowserPlotter:
            def __init__(
                self,
                q_min: float,
                q_bin_size: float,
                histogram_bins: int,
                host: str,
                port: int,
                open_browser: bool,
                **_kwargs,
            ):
                _ = (q_min, q_bin_size, histogram_bins)
                created.append((host, port, open_browser))

            def update(self, _intensity, _error, _relative_uncertainty, _statistics=None):
                return

            def close(self):
                return

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.live_plot.BrowserHistogramPlotter",
            _StubBrowserPlotter,
        )

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
                "--live-plot-mode",
                "browser",
                "--live-plot-host",
                "0.0.0.0",
                "--live-plot-port",
                "8123",
                "--live-plot-no-open-browser",
            ]
        )

        assert rc == 0
        assert created == [("0.0.0.0", 8123, False)]


class TestNexusFileCLI:
    def test_analyze_nexus_file_exits_zero(self, tmp_path: Path):
        nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1, 1], [1.0, 1.0], _minimal_idf())
        rc = main(["analyze", "--nexus-file", str(nexus_path)])
        assert rc == 0

    def test_analyze_nexus_file_prints_progress(self, tmp_path: Path, capsys):
        nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1, 1], [1.0, 1.0], _minimal_idf())

        rc = main(["analyze", "--nexus-file", str(nexus_path)])

        assert rc == 0
        err = capsys.readouterr().err
        assert "Processing NeXus" in err
        assert "100.0%" in err

    def test_histogram_mode_accepts_multiple_nexus_files(self, tmp_path: Path, capsys):
        idf_text = _minimal_idf()
        nexus_a = _write_nexus(tmp_path, "sample_a.nxs.h5", [1], [1.0], idf_text)
        nexus_b = _write_nexus(tmp_path, "sample_b.nxs.h5", [1], [1.0], idf_text)
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "1,1.0,1.57079632679,99.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        histogram_csv = tmp_path / "histogram_nexus.csv"

        rc = main(
            [
                "analyze",
                "--nexus-file",
                str(nexus_a),
                "--nexus-file",
                str(nexus_b),
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
        histogram_lines = histogram_csv.read_text(encoding="utf-8")
        assert "99.01000000,2.00000000,1.41421356" in histogram_lines

    def test_histogram_mode_prints_nexus_progress(self, tmp_path: Path, capsys):
        idf_text = _minimal_idf()
        nexus_a = _write_nexus(tmp_path, "sample_a.nxs.h5", [1], [1.0], idf_text)
        nexus_b = _write_nexus(tmp_path, "sample_b.nxs.h5", [1], [1.0], idf_text)
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "1,1.0,1.57079632679,99.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        rc = main(
            [
                "analyze",
                "--nexus-file",
                str(nexus_a),
                "--nexus-file",
                str(nexus_b),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "100",
                "--histogram-q-bin-size",
                "0.02",
            ]
        )

        assert rc == 0
        err = capsys.readouterr().err
        assert "Processing NeXus" in err
        assert "2/2" in err

    def test_histogram_mode_progress_tracks_chunks_within_one_group(self, tmp_path: Path, capsys, monkeypatch):
        idf_text = _minimal_idf()
        nexus_path = _write_nexus(tmp_path, "sample_large.nxs.h5", [1, 1, 1], [1.0, 1.0, 1.0], idf_text)
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            "\n".join(
                [
                    "pixel id,L2 value,theta value,TOF-to-Q matrix element",
                    "1,1.0,1.57079632679,99.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("live_stream_analysis.analyzer.nexus.DEFAULT_NEXUS_CHUNK_SIZE", 2)

        rc = main(
            [
                "analyze",
                "--nexus-file",
                str(nexus_path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "100",
                "--histogram-q-bin-size",
                "0.02",
            ]
        )

        assert rc == 0
        err = capsys.readouterr().err
        assert "Processing NeXus chunks" in err
        assert "2/2" in err


class TestPreparedPixelGeometryCalibrationIntegration:
    def test_adara_histogram_uses_use_mask_from_preparer_calibrated_pixel_csv(self, tmp_path: Path, capsys):
        idf_path = tmp_path / "test_definition.xml"
        idf_path.write_text(_minimal_preparer_idf(), encoding="utf-8")
        calibration_path = _write_diffcal(
            tmp_path,
            "calibration.h5",
            detids=[1],
            difc=[1000.0],
            difa=[0.0],
            tzero=[0.0],
            use=[0],
        )
        pixel_csv = tmp_path / "pixel_geometry_calibrated.csv"
        iq_csv = tmp_path / "iq.csv"

        preparer_rc = main(
            [
                "preparer",
                "--idf-file",
                str(idf_path),
                "--calibration-file",
                str(calibration_path),
                "--pixel-geometry-csv",
                str(pixel_csv),
                "--iq-csv",
                str(iq_csv),
                "--q-bins",
                "20",
            ]
        )
        assert preparer_rc == 0

        adara_path = _write_adara(tmp_path, event_packet([(1, 100)]))
        histogram_csv = tmp_path / "histogram.csv"
        analyzer_rc = main(
            [
                "analyze",
                "--adara-file",
                str(adara_path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "30",
                "--histogram-q-bin-size",
                "0.02",
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )

        assert analyzer_rc == 0
        out = capsys.readouterr().out
        assert "Histogrammed events" in out
        assert "0" in out

    def test_nexus_histogram_uses_use_mask_from_preparer_calibrated_pixel_csv(self, tmp_path: Path, capsys):
        idf_path = tmp_path / "test_definition.xml"
        idf_path.write_text(_minimal_preparer_idf(), encoding="utf-8")
        calibration_path = _write_diffcal(
            tmp_path,
            "calibration.h5",
            detids=[1],
            difc=[1000.0],
            difa=[0.0],
            tzero=[0.0],
            use=[0],
        )
        pixel_csv = tmp_path / "pixel_geometry_calibrated.csv"
        iq_csv = tmp_path / "iq.csv"

        preparer_rc = main(
            [
                "preparer",
                "--idf-file",
                str(idf_path),
                "--calibration-file",
                str(calibration_path),
                "--pixel-geometry-csv",
                str(pixel_csv),
                "--iq-csv",
                str(iq_csv),
                "--q-bins",
                "20",
            ]
        )
        assert preparer_rc == 0

        nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1], [100.0], _minimal_idf())
        histogram_csv = tmp_path / "histogram_nexus.csv"
        analyzer_rc = main(
            [
                "analyze",
                "--nexus-file",
                str(nexus_path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "30",
                "--histogram-q-bin-size",
                "0.02",
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )

        assert analyzer_rc == 0
        out = capsys.readouterr().out
        assert "Histogrammed events" in out
        assert "0" in out

    def test_nexus_histogram_converts_with_calibrated_difc_constant_ignoring_difa_and_tzero(self, tmp_path: Path):
        """Issue #42: Q = 2*pi*DIFC / TOF. Applying DIFA/TZERO would move this event three bins up."""
        idf_path = tmp_path / "test_definition.xml"
        idf_path.write_text(_minimal_preparer_idf(), encoding="utf-8")
        calibration_path = _write_diffcal(
            tmp_path,
            "calibration.h5",
            detids=[1],
            difc=[1000.0],
            difa=[-10.0],
            tzero=[20.0],
            use=[1],
        )
        pixel_csv = tmp_path / "pixel_geometry_calibrated.csv"
        preparer_rc = main(
            [
                "preparer",
                "--idf-file",
                str(idf_path),
                "--calibration-file",
                str(calibration_path),
                "--pixel-geometry-csv",
                str(pixel_csv),
                "--iq-csv",
                str(tmp_path / "iq.csv"),
                "--q-bins",
                "20",
            ]
        )
        assert preparer_rc == 0

        nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1], [1000.0], _minimal_idf())
        histogram_csv = tmp_path / "histogram_nexus.csv"
        analyzer_rc = main(
            [
                "analyze",
                "--nexus-file",
                str(nexus_path),
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "30",
                "--histogram-q-bin-size",
                "0.02",
                "--histogram-output-csv",
                str(histogram_csv),
            ]
        )
        assert analyzer_rc == 0

        with histogram_csv.open(encoding="utf-8", newline="") as handle:
            occupied = [float(row["Q value"]) for row in csv.DictReader(handle) if float(row["I(Q)"]) != 0.0]
        # 2*pi*1000/1000 = 6.2832 falls in [6.28, 6.30); the DIFA/TZERO quadratic gives 6.3467.
        assert occupied == [pytest.approx(6.29)]


class TestCorrectionExposureNormalization:
    """Issue #37 end to end: preparer stamps each correction with its exposure, analyzer divides by it.

    The background comes from a real preparer run, so the test also pins the CSV contract
    between the two commands rather than a hand-written approximation of it.
    """

    # Matches the minimal-IDF pixel the preparer derives, so both commands land events in the
    # same Q bin: 4492.23 / 1000 us ~= 4.49.
    Q_MATRIX_ELEMENT = 4492.234481835429
    TOF_US = 1000
    BACKGROUND_EVENTS = 2
    BACKGROUND_CHARGE_PC = 2000.0
    SAMPLE_EVENTS = 8
    SAMPLE_CHARGE_PC = 4000.0

    def _pixel_csv(self, tmp_path: Path) -> Path:
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(
            f"pixel id,L2 value,theta value,TOF-to-Q matrix element\n1,1.0,90.0,{self.Q_MATRIX_ELEMENT}\n",
            encoding="utf-8",
        )
        return pixel_csv

    def _nexus(self, tmp_path: Path, name: str, events: int, proton_charge: float) -> Path:
        path = _write_nexus(tmp_path, name, [1] * events, [float(self.TOF_US)] * events, _minimal_preparer_idf())
        with h5py.File(path, "a") as handle:
            handle["entry"].create_dataset("proton_charge", data=np.array([proton_charge]))
            handle["entry"].create_dataset("duration", data=np.array([10.0]))
        return path

    def _background_csv(self, tmp_path: Path) -> Path:
        background_nexus = self._nexus(tmp_path, "background.nxs.h5", self.BACKGROUND_EVENTS, self.BACKGROUND_CHARGE_PC)
        background_csv = tmp_path / "background.csv"
        rc = main(
            [
                "preparer",
                "--mode",
                "background",
                "--nexus-file",
                str(background_nexus),
                "--reduction-output-csv",
                str(background_csv),
                "--q-min",
                "0.0",
                "--q-max",
                "30.0",
            ]
        )
        assert rc == 0
        return background_csv

    def _analyze(self, tmp_path: Path, source: list[str], background_csv: Path) -> tuple[float, float]:
        output = tmp_path / "histogram.csv"
        rc = main(
            [
                "analyze",
                *source,
                "--histogram-pixel-geometry-csv",
                str(self._pixel_csv(tmp_path)),
                "--histogram-q-max",
                "30",
                "--background-subtraction",
                str(background_csv),
                "--histogram-output-csv",
                str(output),
            ]
        )
        assert rc == 0
        rows = list(csv.DictReader(output.read_text(encoding="utf-8").splitlines()))
        populated = [(float(r["I(Q)"]), float(r["Error I(Q)"])) for r in rows if float(r["I(Q)"]) != 0.0]
        assert len(populated) == 1
        return populated[0]

    def _expected(self) -> tuple[float, float]:
        intensity = self.SAMPLE_EVENTS / self.SAMPLE_CHARGE_PC - self.BACKGROUND_EVENTS / self.BACKGROUND_CHARGE_PC
        variance = self.SAMPLE_EVENTS / self.SAMPLE_CHARGE_PC**2 + self.BACKGROUND_EVENTS / self.BACKGROUND_CHARGE_PC**2
        return intensity, variance**0.5

    def test_adara_replay_subtracts_background_per_unit_charge(self, tmp_path: Path):
        background_csv = self._background_csv(tmp_path)
        adara = _write_adara(
            tmp_path,
            rtdl_packet(timestamp_s=100, charge_10pc=int(self.SAMPLE_CHARGE_PC / 10)),
            event_packet([(1, self.TOF_US)] * self.SAMPLE_EVENTS, timestamp_s=100),
        )

        intensity, error = self._analyze(tmp_path, ["--adara-file", str(adara)], background_csv)

        expected_intensity, expected_error = self._expected()
        assert intensity == pytest.approx(expected_intensity)
        assert error == pytest.approx(expected_error)

    def test_nexus_replay_subtracts_background_per_unit_charge(self, tmp_path: Path):
        background_csv = self._background_csv(tmp_path)
        sample = self._nexus(tmp_path, "sample.nxs.h5", self.SAMPLE_EVENTS, self.SAMPLE_CHARGE_PC)

        intensity, error = self._analyze(tmp_path, ["--nexus-file", str(sample)], background_csv)

        expected_intensity, expected_error = self._expected()
        assert intensity == pytest.approx(expected_intensity)
        assert error == pytest.approx(expected_error)

    def test_startup_warns_when_a_correction_csv_has_no_exposure_totals(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """A pre-#37 CSV silently falls back to the sample's scale, so the fallback must be visible."""
        legacy_csv = tmp_path / "legacy_background.csv"
        legacy_csv.write_text("Q value,I(Q),Error I(Q)\n0.01,1.0,1.0\n", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            _warn_if_correction_lacks_totals(str(legacy_csv), "Background", "proton-charge")
            _warn_if_correction_lacks_totals(str(legacy_csv), "Background", "none")

        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "legacy_background.csv" in warnings[0].getMessage()
        assert "proton charge" in warnings[0].getMessage()

    def test_startup_does_not_warn_for_a_preparer_csv(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        background_csv = self._background_csv(tmp_path)

        with caplog.at_level(logging.WARNING):
            _warn_if_correction_lacks_totals(str(background_csv), "Background", "proton-charge")
            _warn_if_correction_lacks_totals(str(background_csv), "Background", "time")

        assert not [record for record in caplog.records if record.levelno == logging.WARNING]


class TestLivePlotShowsNormalizedHistogram:
    """The live plot must show what the run will report, not raw counts that only grow.

    Normalized by exposure, a steady source gives an I(Q) that holds roughly constant while
    its error shrinks. Raw counts instead climb for the whole run and then jump to the
    corrected curve when the run finalizes.
    """

    PIXEL_CSV = "pixel id,L2 value,theta value,TOF-to-Q matrix element\n0,1.0,1.0,0.0\n1,1.0,1.0,99.0\n"

    def _record_plot_frames(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        peaks: list[float] = []

        class _RecordingPlotter:
            def update(self, intensity, _error, _relative_uncertainty, _statistics=None):
                peaks.append(max(intensity, key=abs))

            def close(self):
                return None

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_live_histogram_plotter",
            lambda _args, _histogram_bins, **_kwargs: _RecordingPlotter(),
        )
        return peaks

    def _analyze(self, tmp_path: Path, source: list[str], *extra: str) -> None:
        pixel_csv = tmp_path / "pixel_geometry.csv"
        pixel_csv.write_text(self.PIXEL_CSV, encoding="utf-8")
        rc = main(
            [
                "analyze",
                *source,
                "--histogram-pixel-geometry-csv",
                str(pixel_csv),
                "--histogram-q-max",
                "100",
                "--live-plot-mode",
                "desktop",
                *extra,
            ]
        )
        assert rc == 0

    def _two_equal_pulses(self, tmp_path: Path) -> Path:
        return _write_adara(
            tmp_path,
            rtdl_packet(timestamp_s=100, charge_10pc=400),
            event_packet([(1, 1)] * 4, timestamp_s=100),
            rtdl_packet(timestamp_s=101, charge_10pc=400),
            event_packet([(1, 1)] * 4, timestamp_s=101),
        )

    def test_adara_live_plot_is_divided_by_the_charge_accumulated_so_far(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        peaks = self._record_plot_frames(monkeypatch)

        self._analyze(tmp_path, ["--adara-file", str(self._two_equal_pulses(tmp_path))])

        # 4 events / 4000 pC after the first pulse, 8 / 8000 pC after the second: constant.
        assert peaks
        assert peaks == [pytest.approx(4 / 4000)] * len(peaks)

    def test_adara_live_plot_shows_raw_counts_when_normalization_is_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        peaks = self._record_plot_frames(monkeypatch)

        self._analyze(tmp_path, ["--adara-file", str(self._two_equal_pulses(tmp_path))], "--normalize-by", "none")

        assert peaks[0] == pytest.approx(4.0)
        assert peaks[-1] == pytest.approx(8.0)

    def test_adara_live_plot_applies_the_background_subtraction(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        peaks = self._record_plot_frames(monkeypatch)
        background_csv = tmp_path / "background.csv"
        background_csv.write_text(
            f"Q value,I(Q),Error I(Q),{PROTON_CHARGE_COLUMN},{DURATION_COLUMN}\n99.01,2.0,1.0,8000.0,1.0\n",
            encoding="utf-8",
        )

        self._analyze(
            tmp_path,
            ["--adara-file", str(self._two_equal_pulses(tmp_path))],
            "--background-subtraction",
            str(background_csv),
        )

        assert peaks
        assert peaks == [pytest.approx(4 / 4000 - 2 / 8000)] * len(peaks)

    def test_nexus_live_plot_is_divided_by_the_charge_attributed_so_far(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """NeXus attributes the run charge by events processed, so each chunk stays on scale."""
        peaks = self._record_plot_frames(monkeypatch)
        nexus_path = tmp_path / "sample.nxs.h5"
        with h5py.File(nexus_path, "w") as handle:
            entry = handle.create_group("entry")
            for bank in ("bank1_events", "bank2_events"):
                events = entry.create_group(bank)
                events.create_dataset("event_id", data=np.array([1, 1], dtype=np.int32))
                events.create_dataset("event_time_offset", data=np.array([1.0, 1.0]))
            entry.create_dataset("proton_charge", data=np.array([8000.0]))
            entry.create_dataset("duration", data=np.array([2.0]))

        self._analyze(tmp_path, ["--nexus-file", str(nexus_path)])

        assert len(peaks) >= 2
        assert peaks == [pytest.approx(4 / 8000)] * len(peaks)

    def test_correction_csvs_are_read_once_rather_than_on_every_plot_refresh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The plot refreshes every packet by default; re-reading the CSVs each time stalls a live stream."""
        self._record_plot_frames(monkeypatch)
        background_csv = tmp_path / "background.csv"
        background_csv.write_text("Q value,I(Q),Error I(Q)\n99.01,2.0,1.0\n", encoding="utf-8")
        reads: list[str] = []
        original = histogram_module.load_correction_csv

        def _counting_load(csv_path, *args, **kwargs):
            reads.append(csv_path)
            return original(csv_path, *args, **kwargs)

        monkeypatch.setattr(histogram_module, "load_correction_csv", _counting_load)
        packets = []
        for pulse in range(10):
            packets += [rtdl_packet(timestamp_s=100 + pulse, charge_10pc=400), event_packet([(1, 1)], 100 + pulse)]

        self._analyze(
            tmp_path,
            ["--adara-file", str(_write_adara(tmp_path, *packets))],
            "--background-subtraction",
            str(background_csv),
        )

        assert reads == [str(background_csv)]
