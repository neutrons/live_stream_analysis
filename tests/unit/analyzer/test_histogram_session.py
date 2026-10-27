from __future__ import annotations

import argparse

import pytest

from live_stream_analysis.analyzer.histogram import (
    apply_corrections,
    bin_centre_q_values,
    compute_relative_uncertainty,
)
from live_stream_analysis.analyzer.histogram_runner import _HistogramSession
from live_stream_analysis.intersect.service import HistogramRuntimeState
from live_stream_analysis.statistics import StatisticsWindow

BINS = 4
Q_VALUES = bin_centre_q_values(0.0, 0.02, BINS)


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        background_subtraction=None,
        normalization=None,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        histogram_q_max=0.02 * BINS,
        sample_coherent_scatter_length=None,
        sample_total_scatter_length_squared=None,
        normalize_by="none",
        histogram_snapshot_every=0,
        histogram_output_csv=None,
        statistics_output_csv=None,
        statistics_map_output_csv=None,
    )


def _session(
    *,
    background: tuple[list[float], list[float]] | None = None,
    statistics_q_min: float | None = None,
) -> _HistogramSession:
    runtime_state = HistogramRuntimeState()
    runtime_state.run_statistics.accumulator.observe_progress(1.0, 1.0)
    if background is not None:
        runtime_state.background_values, runtime_state.background_errors = background
    window = StatisticsWindow.build(Q_VALUES, statistics_q_min)
    runtime_state.run_statistics.series.configure(0.0, window)
    return _HistogramSession(
        args=_args(),
        histogram_bins=BINS,
        q_values=Q_VALUES,
        statistics_window=window,
        runtime_state=runtime_state,
        plotter=None,
        publisher=None,
        intersect_config=None,
    )


def _samples(session: _HistogramSession):
    return session.runtime_state.run_statistics.series.samples()


def test_empty_bins_under_a_background_do_not_make_an_early_sample_look_converged():
    """Issue #40: the first decay point on NOM_244114 read 0.25 against 1.1 a moment later.

    Most bins were still empty, and sqrt(0) left them carrying only the background's small
    error. An unmeasured bin must never lower the mean below what the measured ones report.
    """
    session = _session(background=([0.5] * BINS, [0.01] * BINS))
    hist = [4, 0, 0, 0]
    intensity, error = apply_corrections(hist, session.args, BINS, runtime_state=session.runtime_state)
    populated_bin = compute_relative_uncertainty(intensity, error)[0]

    session.record_statistics_sample(sum(hist), hist)

    (sample,) = _samples(session)
    assert sample.mean_relative_uncertainty >= populated_bin
    assert min(sample.relative_uncertainty_profile) >= populated_bin


def test_an_empty_histogram_records_no_decay_point():
    """The emptied histogram a NEW_RUN reports is not a measurement."""
    session = _session(background=([0.5] * BINS, [0.01] * BINS))

    session.record_statistics_sample(0, [0] * BINS)

    assert _samples(session) == []


def test_a_window_with_nothing_populated_records_no_decay_point():
    session = _session(statistics_q_min=0.04)

    session.record_statistics_sample(5, [5, 0, 0, 0])

    assert _samples(session) == []


def test_decay_points_resume_once_the_window_fills():
    session = _session(statistics_q_min=0.04)

    session.record_statistics_sample(5, [5, 0, 0, 0])
    session.record_statistics_sample(14, [5, 0, 9, 0])

    (sample,) = _samples(session)
    assert sample.mean_relative_uncertainty == pytest.approx(1.0 / 3.0)
