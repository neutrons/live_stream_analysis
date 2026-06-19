"""Unit tests for the 'analyze' CLI subparser."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest
from readadara import AdaraFileReader

from live_stream_analysis.analyzer.factory import build_reader as _build_reader
from live_stream_analysis.analyzer.histogram import load_correction_csv
from live_stream_analysis.analyzer.live_plot import BrowserHistogramPlotter, compute_relative_uncertainty
from live_stream_analysis.main import build_parser, main
from tests.unit.analyzer.adara_fixtures import event_packet, null_packet, rtdl_packet


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
            def update(self, intensity, error, _relative_uncertainty):
                updates.append((len(intensity), int(round(max(error)))))

            def close(self):
                updates.append((-1, -1))

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_live_histogram_plotter",
            lambda _args, _histogram_bins: _StubPlotter(),
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
            ):
                _ = (reader, args, q_conversion, histogram_bins, plotter, chunk_size, q_conversion_provider, histogram_callback)
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
            lambda _config, runtime_state=None: _StubPublisher(),
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
            def update(self, intensity, _error, relative_uncertainty):
                updates.append((max(intensity), max(relative_uncertainty)))

            def close(self):
                updates.append((-1.0, -1.0))

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_live_histogram_plotter",
            lambda _args, _histogram_bins: _StubPlotter(),
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
            def update(self, _intensity, _error, relative_uncertainty):
                relative_uncertainty_updates.append(max(relative_uncertainty))

            def close(self):
                return

        monkeypatch.setattr(
            "live_stream_analysis.analyzer.histogram_runner.create_live_histogram_plotter",
            lambda _args, _histogram_bins: _StubPlotter(),
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
            def __init__(self, q_bin_size: float, histogram_bins: int, host: str, port: int, open_browser: bool):
                _ = (q_bin_size, histogram_bins)
                created.append((host, port, open_browser))

            def update(self, _intensity, _error, _relative_uncertainty):
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
