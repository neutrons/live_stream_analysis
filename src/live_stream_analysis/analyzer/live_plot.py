from __future__ import annotations

import argparse
import sys
from typing import Protocol

import matplotlib
import matplotlib.pyplot as plt


class HistogramPlotter(Protocol):
    def update(self, intensity: list[float], error: list[float], relative_uncertainty: list[float]) -> None: ...

    def close(self) -> None: ...


class LiveHistogramPlotter:
    def __init__(self, q_bin_size: float, histogram_bins: int):
        plt.ion()
        self._figure, (self._axis, self._relative_axis) = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
        self._q_values = [(index + 0.5) * q_bin_size for index in range(histogram_bins)]
        zeros = [0.0] * histogram_bins
        (self._line,) = self._axis.plot(self._q_values, zeros, color="tab:blue", linewidth=1.5, label="I(Q)")
        self._band = self._axis.fill_between(self._q_values, zeros, zeros, color="tab:blue", alpha=0.2)
        (self._relative_line,) = self._relative_axis.plot(
            self._q_values,
            zeros,
            color="tab:orange",
            linewidth=1.5,
            label="sigma / I(Q)",
        )
        self._axis.set_xlabel("Q")
        self._axis.set_ylabel("I(Q)")
        self._axis.set_title("Live Histogram")
        self._axis.grid(True, alpha=0.3)
        self._axis.legend(loc="best")
        self._relative_axis.set_xlabel("Q")
        self._relative_axis.set_ylabel("sigma / I(Q)")
        self._relative_axis.set_title("Live Relative Uncertainty")
        self._relative_axis.grid(True, alpha=0.3)
        self._relative_axis.legend(loc="best")
        self._figure.tight_layout()
        self._figure.canvas.draw_idle()
        self._figure.canvas.flush_events()
        plt.show(block=False)

    def update(self, intensity: list[float], error: list[float], relative_uncertainty: list[float]) -> None:
        lower = [y_value - err_value for y_value, err_value in zip(intensity, error, strict=True)]
        upper = [y_value + err_value for y_value, err_value in zip(intensity, error, strict=True)]
        self._line.set_ydata(intensity)
        self._band.remove()
        self._band = self._axis.fill_between(self._q_values, lower, upper, color="tab:blue", alpha=0.2)
        self._axis.relim()
        self._axis.autoscale_view()
        self._relative_line.set_ydata(relative_uncertainty)
        self._relative_axis.relim()
        self._relative_axis.autoscale_view()
        self._figure.canvas.draw_idle()
        self._figure.canvas.flush_events()
        plt.pause(0.001)

    def close(self) -> None:
        plt.ioff()


class NullHistogramPlotter:
    def update(self, _intensity: list[float], _error: list[float], _relative_uncertainty: list[float]) -> None:
        return

    def close(self) -> None:
        return


def create_live_histogram_plotter(args: argparse.Namespace, histogram_bins: int) -> HistogramPlotter:
    if not getattr(args, "live_plot", False):
        return NullHistogramPlotter()

    backend = matplotlib.get_backend().lower()
    if backend == "agg" or backend.endswith(":agg"):
        print("Matplotlib is using a non-interactive backend; disabling live plot.", file=sys.stderr)
        return NullHistogramPlotter()

    return LiveHistogramPlotter(args.histogram_q_bin_size, histogram_bins)


def compute_relative_uncertainty(intensity: list[float], error: list[float]) -> list[float]:
    relative_uncertainty: list[float] = []
    for y_value, err_value in zip(intensity, error, strict=True):
        if y_value == 0.0:
            relative_uncertainty.append(0.0)
            continue
        relative_uncertainty.append(abs(err_value / y_value))
    return relative_uncertainty


def maybe_update_live_plot(
    plotter: HistogramPlotter,
    hist: list[int] | list[float],
    error: list[float],
    refresh_every: int,
    update_index: int,
    *,
    force: bool = False,
) -> None:
    if not force and update_index % refresh_every != 0:
        return

    intensity = [float(value) for value in hist]
    plotter.update(intensity, error, compute_relative_uncertainty(intensity, error))
