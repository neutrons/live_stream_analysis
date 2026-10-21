"""Unit tests for the error-decay statistics used by issue #38 forecasting."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from live_stream_analysis.analyzer.live_plot import _BrowserPlotState
from live_stream_analysis.intersect.events import build_histogram_payload
from live_stream_analysis.statistics import (
    STATISTICS_CSV_COLUMNS,
    RunAccumulator,
    RunStatistics,
    StatisticsSample,
    StatisticsSeries,
    StatisticsSnapshot,
    StatisticsWindow,
    mean_relative_uncertainty_in_window,
    write_statistics_csv,
    write_statistics_map_csv,
)


def _sample(elapsed: float, charge: float = 0.0, error: float = 0.0) -> StatisticsSample:
    return StatisticsSample(
        elapsed_seconds=elapsed,
        proton_charge_pc=charge,
        mean_relative_uncertainty=error,
        total_counts=0.0,
        nonzero_bins=0,
        histogram_events=0,
    )


class TestRunAccumulator:
    def test_charge_sums_and_elapsed_spans_first_to_last_pulse(self):
        accumulator = RunAccumulator()
        accumulator.observe_pulse(100.0, 1000.0)
        accumulator.observe_pulse(100.5, 1000.0)
        accumulator.observe_pulse(102.0, 500.0)

        assert accumulator.proton_charge_pc == pytest.approx(2500.0)
        # Elapsed is measured from the first pulse seen, not from the epoch.
        assert accumulator.elapsed_seconds == pytest.approx(2.0)

    def test_elapsed_is_zero_before_any_pulse(self):
        assert RunAccumulator().elapsed_seconds == 0.0

    def test_reset_clears_charge_and_restarts_the_clock(self):
        accumulator = RunAccumulator()
        accumulator.observe_pulse(100.0, 1000.0)
        accumulator.observe_pulse(105.0, 1000.0)
        accumulator.reset()

        assert accumulator.proton_charge_pc == 0.0
        assert accumulator.elapsed_seconds == 0.0

        accumulator.observe_pulse(200.0, 7.0)
        accumulator.observe_pulse(201.0, 3.0)
        assert accumulator.proton_charge_pc == pytest.approx(10.0)
        assert accumulator.elapsed_seconds == pytest.approx(1.0)

    def test_observe_progress_sets_absolute_totals_for_replayed_files(self):
        accumulator = RunAccumulator()
        accumulator.observe_progress(800.0, 1.0e12)
        accumulator.observe_progress(1615.7, 2.0e12)

        # NeXus replay reports cumulative progress, so totals replace rather than add up.
        assert accumulator.elapsed_seconds == pytest.approx(1615.7)
        assert accumulator.proton_charge_pc == pytest.approx(2.0e12)


class TestStatisticsSeries:
    def test_first_sample_is_always_due(self):
        assert StatisticsSeries(sample_interval_seconds=5.0).is_due(0.0)

    def test_samples_are_throttled_by_accumulated_run_time(self):
        series = StatisticsSeries(sample_interval_seconds=2.0)
        series.record(_sample(0.0))

        assert not series.is_due(1.9)
        assert series.is_due(2.0)

        series.record(_sample(2.0))
        assert not series.is_due(3.5)
        assert series.is_due(4.0)

    def test_snapshot_exposes_parallel_series(self):
        series = StatisticsSeries()
        series.record(_sample(1.0, charge=10.0, error=0.5))
        series.record(_sample(2.0, charge=20.0, error=0.3))

        snapshot = series.snapshot()
        assert snapshot.elapsed_seconds == [1.0, 2.0]
        assert snapshot.proton_charge_pc == [10.0, 20.0]
        assert snapshot.mean_relative_uncertainty == [0.5, 0.3]

    def test_latest_returns_none_before_any_sample(self):
        assert StatisticsSeries().latest() is None

    def test_run_statistics_resets_accumulator_and_series_together(self):
        """A new run must start a fresh curve, not splice onto the previous run's."""
        run_statistics = RunStatistics()
        run_statistics.accumulator.observe_pulse(10.0, 500.0)
        run_statistics.series.record(_sample(0.0, error=0.9))

        run_statistics.reset()

        assert run_statistics.accumulator.proton_charge_pc == 0.0
        assert run_statistics.series.samples() == []


class TestMeanRelativeUncertaintyInWindow:
    def test_defaults_to_every_populated_bin(self):
        q_values = [0.5, 1.5, 2.5, 3.5]
        relative_uncertainty = [0.4, 0.2, 0.0, 0.6]

        # The zero bin is unpopulated, not perfectly measured, so it must not drag the mean down.
        assert mean_relative_uncertainty_in_window(q_values, relative_uncertainty) == pytest.approx(0.4)

    def test_restricts_to_the_requested_q_window(self):
        q_values = [0.5, 1.5, 2.5, 3.5]
        relative_uncertainty = [0.9, 0.2, 0.4, 0.9]

        assert mean_relative_uncertainty_in_window(q_values, relative_uncertainty, 1.0, 3.0) == pytest.approx(0.3)

    def test_open_ended_windows_are_supported(self):
        q_values = [0.5, 1.5, 2.5]
        relative_uncertainty = [0.9, 0.2, 0.4]

        assert mean_relative_uncertainty_in_window(q_values, relative_uncertainty, 1.0, None) == pytest.approx(0.3)
        assert mean_relative_uncertainty_in_window(q_values, relative_uncertainty, None, 2.0) == pytest.approx(0.55)

    def test_returns_zero_when_no_bin_qualifies(self):
        assert mean_relative_uncertainty_in_window([0.5, 1.5], [0.0, 0.0]) == 0.0
        assert mean_relative_uncertainty_in_window([0.5, 1.5], [0.4, 0.4], 9.0, 10.0) == 0.0


class TestWriteStatisticsCsv:
    def test_writes_the_declared_columns_and_one_row_per_sample(self, tmp_path: Path):
        output = tmp_path / "stats.csv"
        write_statistics_csv(
            [
                StatisticsSample(1.0, 100.0, 0.5, 30.0, 3, 30),
                StatisticsSample(2.0, 250.0, 0.25, 120.0, 4, 120),
            ],
            str(output),
        )

        rows = list(csv.reader(output.read_text().splitlines()))
        assert rows[0] == STATISTICS_CSV_COLUMNS
        assert len(rows) == 3
        assert [float(value) for value in rows[1][:3]] == [1.0, 100.0, 0.5]
        assert [float(value) for value in rows[2][:3]] == [2.0, 250.0, 0.25]
        assert rows[2][4:] == ["4", "120"]

    def test_writes_a_header_only_file_when_nothing_was_sampled(self, tmp_path: Path):
        output = tmp_path / "stats.csv"
        write_statistics_csv([], str(output))

        assert output.read_text().splitlines() == [",".join(STATISTICS_CSV_COLUMNS)]

    def test_creates_missing_parent_directories(self, tmp_path: Path):
        output = tmp_path / "nested" / "dir" / "stats.csv"
        write_statistics_csv([StatisticsSample(1.0, 1.0, 1.0, 1.0, 1, 1)], str(output))

        assert output.exists()


def test_empty_snapshot_serializes_to_empty_series():
    assert StatisticsSnapshot([], [], []).as_payload() == {
        "elapsed_seconds": [],
        "proton_charge_pc": [],
        "mean_relative_uncertainty": [],
    }


def test_csv_browser_and_intersect_all_report_the_same_decay_point(tmp_path: Path):
    """Every consumer of the decay series must agree on the metric.

    This mirrors the bin-labelling consistency test for histograms: a forecast built from
    the INTERSECT stream has to match what the operator sees in the browser and what lands
    in the exported CSV, or the three disagree about when the run is finished.
    """
    sample = StatisticsSample(
        elapsed_seconds=12.5,
        proton_charge_pc=2.5e11,
        mean_relative_uncertainty=0.0425,
        total_counts=30.0,
        nonzero_bins=2,
        histogram_events=30,
    )
    series = StatisticsSeries()
    series.record(sample)

    output = tmp_path / "stats.csv"
    write_statistics_csv(series.samples(), str(output))
    csv_row = list(csv.reader(output.read_text().splitlines()))[1]

    browser_state = _BrowserPlotState([0.01, 0.03])
    browser_state.update([1.0, 2.0], [1.0, 1.0], [1.0, 0.5], series.snapshot())
    browser = browser_state.snapshot()

    payload = build_histogram_payload([0.01, 0.03], [1.0, 2.0], [1.0, 1.0], series.latest())

    assert float(csv_row[2]) == pytest.approx(sample.mean_relative_uncertainty)
    assert browser["statistics"]["mean_relative_uncertainty"] == [pytest.approx(sample.mean_relative_uncertainty)]
    assert payload.mean_relative_uncertainty == pytest.approx(sample.mean_relative_uncertainty)

    assert float(csv_row[0]) == pytest.approx(sample.elapsed_seconds)
    assert browser["status"]["elapsed_seconds"] == pytest.approx(sample.elapsed_seconds)
    assert payload.elapsed_seconds == pytest.approx(sample.elapsed_seconds)

    assert float(csv_row[1]) == pytest.approx(sample.proton_charge_pc)
    assert browser["status"]["proton_charge_pc"] == pytest.approx(sample.proton_charge_pc)
    assert payload.proton_charge_pc == pytest.approx(sample.proton_charge_pc)


class TestStatisticsWindow:
    def test_covers_every_bin_when_no_window_is_given(self):
        window = StatisticsWindow.build([0.5, 1.5, 2.5])

        assert window.indices == (0, 1, 2)
        assert window.group_size == 1
        assert window.map_q_values == (0.5, 1.5, 2.5)

    def test_restricts_indices_to_the_requested_window(self):
        window = StatisticsWindow.build([0.5, 1.5, 2.5, 3.5], 1.0, 3.0)

        assert window.indices == (1, 2)
        assert window.map_q_values == (1.5, 2.5)

    def test_averages_adjacent_bins_into_columns_when_over_the_cap(self):
        q_values = [index * 0.02 for index in range(1000)]
        window = StatisticsWindow.build(q_values, max_columns=250)

        assert window.group_size == 4
        assert len(window.map_q_values) == 250
        # Each column sits at the centre of the four bins it averages.
        assert window.map_q_values[0] == pytest.approx(sum(q_values[0:4]) / 4)

    def test_a_five_wide_window_at_the_default_bin_size_keeps_full_resolution(self):
        """Q 15-20 at 0.02 is 250 bins, which must not be halved by the column cap."""
        q_values = [(index + 0.5) * 0.02 for index in range(1500)]
        window = StatisticsWindow.build(q_values, 15.0, 20.0)

        assert len(window.indices) == 250
        assert window.group_size == 1
        assert len(window.map_q_values) == 250

    def test_summarize_returns_the_mean_and_the_per_column_profile(self):
        window = StatisticsWindow.build([0.5, 1.5, 2.5, 3.5])

        mean, profile = window.summarize([0.4, 0.2, 0.0, 0.6])

        assert mean == pytest.approx(0.4)
        # The empty bin is a gap, not a measured zero, so its column reports 0.0.
        assert profile == (pytest.approx(0.4), pytest.approx(0.2), 0.0, pytest.approx(0.6))

    def test_summarize_averages_only_populated_bins_within_a_column(self):
        q_values = [0.5, 1.5, 2.5, 3.5]
        window = StatisticsWindow.build(q_values, max_columns=2)

        assert window.group_size == 2
        _, profile = window.summarize([0.4, 0.0, 0.0, 0.0])

        # First column averages 0.4 alone rather than diluting it with the empty bin.
        assert profile == (pytest.approx(0.4), 0.0)

    def test_summarize_handles_a_window_with_nothing_populated(self):
        window = StatisticsWindow.build([0.5, 1.5])

        assert window.summarize([0.0, 0.0]) == (0.0, (0.0, 0.0))


class TestStatisticsMap:
    def _series(self, count: int) -> StatisticsSeries:
        window = StatisticsWindow.build([0.5, 1.5, 2.5])
        series = StatisticsSeries(window=window)
        for index in range(count):
            series.record(
                StatisticsSample(
                    elapsed_seconds=float(index),
                    proton_charge_pc=float(index) * 100.0,
                    mean_relative_uncertainty=0.1,
                    total_counts=0.0,
                    nonzero_bins=3,
                    histogram_events=0,
                    relative_uncertainty_profile=(0.1, 0.0, 0.3),
                )
            )
        return series

    def test_map_carries_the_q_axis_and_one_row_per_sample(self):
        statistics_map = self._series(3).snapshot().build_map()

        assert statistics_map.q_values == [0.5, 1.5, 2.5]
        assert statistics_map.axis_values == [0.0, 100.0, 200.0]
        # 0.0 becomes None so the renderer leaves a gap rather than drawing a measured zero.
        assert statistics_map.values == [[0.1, None, 0.3]] * 3

    def test_axis_selects_between_time_and_proton_charge(self):
        snapshot = self._series(3).snapshot()

        assert snapshot.build_map("time").axis_values == [0.0, 1.0, 2.0]
        assert snapshot.build_map("proton-charge").axis_values == [0.0, 100.0, 200.0]

    def test_rows_are_thinned_rather_than_averaged_when_over_the_cap(self):
        """Averaging two rows would invent a measurement at an accumulation level never reached."""
        statistics_map = self._series(10).snapshot().build_map("time", max_rows=4)

        assert len(statistics_map.values) <= 4
        assert statistics_map.axis_values == [0.0, 3.0, 6.0, 9.0]

    def test_map_is_empty_before_any_sample(self):
        statistics_map = StatisticsSeries().snapshot().build_map()

        assert statistics_map.values == []
        assert statistics_map.axis_values == []

    def test_write_statistics_map_csv_has_one_column_per_q(self, tmp_path: Path):
        output = tmp_path / "map.csv"
        write_statistics_map_csv(self._series(2).snapshot(), str(output))

        rows = list(csv.reader(output.read_text().splitlines()))
        assert rows[0][:2] == ["Elapsed time (s)", "Proton charge (pC)"]
        assert len(rows[0]) == 5
        assert rows[0][2].startswith("sigma/I(Q) at Q=0.5")
        assert len(rows) == 3
        assert [float(value) for value in rows[1][2:]] == [0.1, 0.0, 0.3]


class TestBrowserStatisticsMap:
    def _state_with_samples(self) -> _BrowserPlotState:
        window = StatisticsWindow.build([0.5, 1.5, 2.5])
        series = StatisticsSeries(window=window)
        series.record(StatisticsSample(1.0, 100.0, 0.2, 0.0, 3, 0, (0.2, 0.0, 0.4)))
        series.record(StatisticsSample(2.0, 200.0, 0.1, 0.0, 3, 0, (0.1, 0.0, 0.2)))

        state = _BrowserPlotState([0.5, 1.5, 2.5])
        state.update([1.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 0.0, 1.0], series.snapshot())
        return state

    def test_map_is_served_separately_from_the_polled_state(self):
        """The map dwarfs the rest of the state and is only needed while 2D is selected."""
        state = self._state_with_samples()

        assert "statistics" in state.snapshot()
        assert "values" not in state.snapshot()["statistics"]

        statistics_map = state.statistics_map("proton-charge")
        assert statistics_map["axis_values"] == [100.0, 200.0]
        assert statistics_map["values"] == [[0.2, None, 0.4], [0.1, None, 0.2]]

    def test_each_axis_is_served_independently(self):
        state = self._state_with_samples()

        assert state.statistics_map("time")["axis_values"] == [1.0, 2.0]
        assert state.statistics_map("proton-charge")["axis_values"] == [100.0, 200.0]

    def test_the_map_is_cached_until_the_next_update(self):
        state = self._state_with_samples()

        first = state.statistics_map("time")
        assert state.statistics_map("time") is first

        window = StatisticsWindow.build([0.5, 1.5, 2.5])
        series = StatisticsSeries(window=window)
        series.record(StatisticsSample(9.0, 900.0, 0.05, 0.0, 3, 0, (0.05, 0.0, 0.05)))
        state.update([1.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 0.0, 1.0], series.snapshot())

        assert state.statistics_map("time") is not first
        assert state.statistics_map("time")["axis_values"] == [9.0]

    def test_map_is_empty_before_any_statistics_arrive(self):
        state = _BrowserPlotState([0.5, 1.5])

        assert state.statistics_map("time") == {"q_values": [], "axis_values": [], "values": []}


class TestSeriesBounding:
    def test_series_halves_itself_rather_than_growing_without_limit(self):
        """A live stream can run for hours without a run boundary to reset the series."""
        series = StatisticsSeries(sample_interval_seconds=1.0, max_samples=8)
        for index in range(9):
            series.record(_sample(float(index)))

        samples = series.samples()
        assert len(samples) <= 8
        # The whole span survives at coarser resolution, start and end both intact.
        assert samples[0].elapsed_seconds == 0.0
        assert samples[-1].elapsed_seconds == 8.0
        assert series.sample_interval_seconds == 2.0

    def test_thinning_keeps_the_newest_sample_even_at_an_odd_length(self):
        series = StatisticsSeries(sample_interval_seconds=1.0, max_samples=4)
        for index in range(6):
            series.record(_sample(float(index)))

        assert series.samples()[-1].elapsed_seconds == 5.0

    def test_thinning_drops_the_middle_rather_than_the_start_of_the_run(self):
        """The steep early decay is the most informative part of the curve for a forecast."""
        series = StatisticsSeries(sample_interval_seconds=1.0, max_samples=4)
        for index in range(5):
            series.record(_sample(float(index)))

        assert [sample.elapsed_seconds for sample in series.samples()] == [0.0, 2.0, 4.0]


class TestReviewRegressions:
    def test_time_advances_without_any_charge_information(self):
        """A source with no RTDL packets must still get a usable time axis.

        Tying the clock to charge collapsed every sample onto x=0, which makes the series
        useless for forecasting against either axis.
        """
        accumulator = RunAccumulator()
        accumulator.observe_time(100.0)
        accumulator.observe_time(130.0)

        assert accumulator.elapsed_seconds == pytest.approx(30.0)
        assert accumulator.proton_charge_pc == 0.0

    def test_charge_accumulates_independently_of_the_clock(self):
        accumulator = RunAccumulator()
        accumulator.observe_time(100.0)
        accumulator.observe_charge(1000.0)
        accumulator.observe_time(101.0)
        accumulator.observe_charge(500.0)

        assert accumulator.elapsed_seconds == pytest.approx(1.0)
        assert accumulator.proton_charge_pc == pytest.approx(1500.0)

    def test_reset_restores_the_configured_interval_after_thinning(self):
        """A widened interval used to persist, degrading every later run on the stream."""
        series = StatisticsSeries(max_samples=4)
        series.configure(1.0, StatisticsWindow.build([0.5, 1.5]))
        for index in range(9):
            series.record(_sample(float(index)))
        assert series.sample_interval_seconds > 1.0

        series.reset()

        assert series.sample_interval_seconds == 1.0
        assert series.is_due(1.0)

    def test_a_zero_interval_stops_rethinning_on_every_record(self):
        """Doubling zero stays zero, so the series re-thinned on every single sample."""
        series = StatisticsSeries(sample_interval_seconds=0.0, max_samples=4)
        for index in range(6):
            series.record(_sample(float(index)))

        assert series.sample_interval_seconds > 0.0

    def test_map_rows_always_include_the_newest_sample(self):
        """On a live map the newest row is the one being watched; it must never lag."""
        window = StatisticsWindow.build([0.5, 1.5])
        series = StatisticsSeries(window=window, max_samples=10_000)
        for index in range(300):
            series.record(StatisticsSample(float(index), float(index), 0.1, 0.0, 2, 0, (0.1, 0.2)))

        statistics_map = series.snapshot().build_map("time", max_rows=256)

        assert statistics_map.axis_values[-1] == 299.0
        assert statistics_map.axis_values[0] == 0.0
        assert len(statistics_map.values) <= 256

    def test_thinning_uses_most_of_the_row_budget(self):
        """An integer stride wasted up to half the budget just past the cap."""
        window = StatisticsWindow.build([0.5, 1.5])
        series = StatisticsSeries(window=window, max_samples=10_000)
        for index in range(257):
            series.record(StatisticsSample(float(index), float(index), 0.1, 0.0, 2, 0, (0.1, 0.2)))

        statistics_map = series.snapshot().build_map("time", max_rows=256)

        assert len(statistics_map.values) == 256
