"""Decay-of-error statistics accumulated against run time and proton charge.

The downstream INTERSECT service forecasts how long a run needs to reach a target error
in q-space. Forecasting needs a *history* of the error, not the current value, so this
module keeps a time series of a scalar convergence metric alongside the elapsed run time
and the integrated proton charge that produced it.

Each sample also keeps the full sigma/I(Q) profile across the Q window, not just its mean,
so the same series can be shown as a 2D map of Q against accumulated charge or time. The
mean tells you when the run converges; the map tells you *which part of q-space* is still
noisy, which a single scalar hides.
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass, field
from pathlib import Path

STATISTICS_CSV_COLUMNS = [
    "Elapsed time (s)",
    "Proton charge (pC)",
    "Mean sigma/I(Q)",
    "Total counts",
    "Nonzero bins",
    "Histogrammed events",
]

# Profiles are averaged down to this many Q columns when they are recorded, which bounds
# both memory over a long run and the size of the map served to the browser. 256 rather than
# a rounder 240 so that a 5-wide Q window at the default 0.02 bin size (250 bins) still comes
# through at full bin resolution instead of being halved.
MAX_MAP_COLUMNS = 256
# Rows are *selected*, never averaged: each row is a snapshot at one accumulation level, so
# blending two rows would invent a measurement that never happened.
MAX_MAP_ROWS = 256
# Keeping a full profile per sample costs MAX_MAP_COLUMNS floats each, so an unbounded series
# would grow without limit on a live stream that runs for hours without a run boundary.
MAX_SERIES_SAMPLES = 4096


@dataclass
class RunAccumulator:
    """Running totals of pulse time and proton charge for the current run."""

    proton_charge_pc: float = 0.0
    first_pulse_timestamp: float | None = None
    last_pulse_timestamp: float | None = None

    def observe_time(self, timestamp_s: float) -> None:
        """Advance the clock from any packet's timestamp.

        Kept separate from charge because only RTDL packets carry charge: a source with no
        RTDL packets at all would otherwise leave the time axis pinned at zero too, which
        collapses the whole decay series onto a single x value.
        """
        if self.first_pulse_timestamp is None:
            self.first_pulse_timestamp = timestamp_s
        self.last_pulse_timestamp = timestamp_s

    def observe_charge(self, charge_pc: float) -> None:
        self.proton_charge_pc += charge_pc

    def observe_pulse(self, timestamp_s: float, charge_pc: float) -> None:
        self.observe_time(timestamp_s)
        self.observe_charge(charge_pc)

    def observe_progress(self, elapsed_seconds: float, proton_charge_pc: float) -> None:
        """Set absolute totals directly, for sources that report cumulative progress."""
        self.first_pulse_timestamp = 0.0
        self.last_pulse_timestamp = elapsed_seconds
        self.proton_charge_pc = proton_charge_pc

    @property
    def elapsed_seconds(self) -> float:
        if self.first_pulse_timestamp is None or self.last_pulse_timestamp is None:
            return 0.0
        return max(0.0, self.last_pulse_timestamp - self.first_pulse_timestamp)

    def reset(self) -> None:
        self.proton_charge_pc = 0.0
        self.first_pulse_timestamp = None
        self.last_pulse_timestamp = None


@dataclass(frozen=True)
class StatisticsWindow:
    """Geometry of the Q window the decay statistics are computed over.

    Built once at startup and reused for every sample, so the per-sample work is a single
    pass over the window rather than a re-derivation of which bins are in range.
    """

    indices: tuple[int, ...]
    group_size: int
    map_q_values: tuple[float, ...]

    @classmethod
    def build(
        cls,
        q_values: list[float],
        q_min: float | None = None,
        q_max: float | None = None,
        max_columns: int = MAX_MAP_COLUMNS,
    ) -> StatisticsWindow:
        indices = tuple(
            index
            for index, q_value in enumerate(q_values)
            if (q_min is None or q_value >= q_min) and (q_max is None or q_value <= q_max)
        )
        group_size = max(1, -(-len(indices) // max_columns)) if indices else 1
        map_q_values = tuple(
            sum(q_values[index] for index in group) / len(group) for group in _chunk(indices, group_size)
        )
        return cls(indices=indices, group_size=group_size, map_q_values=map_q_values)

    def summarize(self, relative_uncertainty: list[float]) -> tuple[float, tuple[float, ...]]:
        """Return the mean sigma/I(Q) over populated bins and the per-column profile.

        Empty bins carry a relative uncertainty of 0.0 rather than infinity, so counting them
        would drag both the mean and the profile toward zero as the Q range widens. They are
        skipped, and a column with nothing populated reports 0.0 so the map can leave a gap.
        """
        total = 0.0
        count = 0
        profile: list[float] = []
        for group in _chunk(self.indices, self.group_size):
            group_total = 0.0
            group_count = 0
            for index in group:
                value = relative_uncertainty[index]
                if value == 0.0:
                    continue
                group_total += value
                group_count += 1
            profile.append(group_total / group_count if group_count else 0.0)
            total += group_total
            count += group_count
        return (total / count if count else 0.0), tuple(profile)


def _chunk(indices: tuple[int, ...], size: int):
    for start in range(0, len(indices), size):
        yield indices[start : start + size]


@dataclass(frozen=True)
class StatisticsSample:
    elapsed_seconds: float
    proton_charge_pc: float
    mean_relative_uncertainty: float
    total_counts: float
    nonzero_bins: int
    histogram_events: int
    # sigma/I(Q) across the Q window, already averaged down to at most MAX_MAP_COLUMNS.
    relative_uncertainty_profile: tuple[float, ...] = ()


@dataclass(frozen=True)
class StatisticsSnapshot:
    """An immutable view of the series, safe to hand to plotters and serializers.

    Profiles are shared by reference rather than copied -- they are tuples, so this stays
    safe while keeping snapshots cheap enough to take on every plot refresh.
    """

    elapsed_seconds: list[float]
    proton_charge_pc: list[float]
    mean_relative_uncertainty: list[float]
    map_q_values: list[float] = field(default_factory=list)
    profiles: list[tuple[float, ...]] = field(default_factory=list)

    @classmethod
    def from_samples(
        cls,
        samples: list[StatisticsSample],
        map_q_values: tuple[float, ...] = (),
    ) -> StatisticsSnapshot:
        return cls(
            elapsed_seconds=[sample.elapsed_seconds for sample in samples],
            proton_charge_pc=[sample.proton_charge_pc for sample in samples],
            mean_relative_uncertainty=[sample.mean_relative_uncertainty for sample in samples],
            map_q_values=list(map_q_values),
            profiles=[sample.relative_uncertainty_profile for sample in samples],
        )

    def as_payload(self) -> dict[str, list[float]]:
        """The 1D series only. The 2D map is served separately -- see build_map()."""
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "proton_charge_pc": self.proton_charge_pc,
            "mean_relative_uncertainty": self.mean_relative_uncertainty,
        }

    def build_map(self, axis: str = "proton-charge", max_rows: int = MAX_MAP_ROWS) -> StatisticsMap:
        """Return the 2D sigma/I(Q) map over Q and the chosen accumulation axis."""
        axis_values = self.elapsed_seconds if axis == "time" else self.proton_charge_pc
        rows = [
            (axis_values[index], self.profiles[index])
            for index in _stride(len(self.profiles), max_rows)
            if self.profiles[index]
        ]
        return StatisticsMap(
            q_values=list(self.map_q_values),
            axis_values=[value for value, _ in rows],
            # 0.0 means "no populated bin here", which is a gap rather than a measurement of zero.
            values=[[value if value > 0.0 else None for value in profile] for _, profile in rows],
        )


def _stride(count: int, limit: int) -> list[int]:
    """Row indices thinned to at most `limit`, always including the first and the last.

    A plain integer stride would drop up to stride-1 rows off the end, and on a live map the
    newest row is exactly the one being watched. Spacing the picks across the range instead
    keeps both ends and uses the whole row budget rather than ceil(count/stride) of it.
    """
    if count <= limit:
        return list(range(count))
    if limit <= 1:
        return [count - 1]
    return sorted({round(index * (count - 1) / (limit - 1)) for index in range(limit)})


@dataclass(frozen=True)
class StatisticsMap:
    """A 2D sigma/I(Q) map: Q across, accumulated charge or time down."""

    q_values: list[float]
    axis_values: list[float]
    values: list[list[float | None]]

    def as_payload(self) -> dict[str, object]:
        return {
            "q_values": self.q_values,
            "axis_values": self.axis_values,
            "values": self.values,
        }


@dataclass
class StatisticsSeries:
    """Thread-safe, append-only series of convergence samples.

    Sampling is throttled by elapsed run time rather than by update count so the series
    stays a fixed density regardless of how fast events arrive.
    """

    sample_interval_seconds: float = 1.0
    max_samples: int = MAX_SERIES_SAMPLES
    window: StatisticsWindow | None = None
    # What the interval was configured to, so a reset can undo any widening that thinning
    # applied. Without it a long run leaves every later run on the same stream degraded.
    _configured_interval_seconds: float | None = None
    _samples: list[StatisticsSample] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def configure(self, sample_interval_seconds: float, window: StatisticsWindow) -> None:
        with self._lock:
            self.sample_interval_seconds = sample_interval_seconds
            self._configured_interval_seconds = sample_interval_seconds
            self.window = window

    def is_due(self, elapsed_seconds: float) -> bool:
        """Cheap check callers make before computing a sample.

        Building a sample means re-running the corrections over every bin, which is far too
        expensive to do on every packet just to discard the result.
        """
        with self._lock:
            if not self._samples:
                return True
            return elapsed_seconds - self._samples[-1].elapsed_seconds >= self.sample_interval_seconds

    def record(self, sample: StatisticsSample) -> None:
        with self._lock:
            self._samples.append(sample)
            if len(self._samples) > self.max_samples:
                self._halve_resolution()

    def _halve_resolution(self) -> None:
        """Thin the series in place and widen the sampling interval to match.

        Dropping the oldest samples would be worse than halving: the start of a run is the
        steep part of the decay curve and the most informative for a forecast. Halving keeps
        the whole span at coarser resolution instead.
        """
        kept = self._samples[::2]
        if kept[-1] is not self._samples[-1]:
            kept.append(self._samples[-1])
        self._samples = kept

        doubled = self.sample_interval_seconds * 2
        if doubled > 0.0:
            self.sample_interval_seconds = doubled
            return
        # An interval of 0 means "sample on every update"; doubling leaves it at 0, so the
        # series would re-thin on every single record from here on. Adopt the spacing that
        # thinning just produced instead.
        span = kept[-1].elapsed_seconds - kept[0].elapsed_seconds
        if span > 0.0 and len(kept) > 1:
            self.sample_interval_seconds = span / (len(kept) - 1)

    def samples(self) -> list[StatisticsSample]:
        with self._lock:
            return list(self._samples)

    def latest(self) -> StatisticsSample | None:
        with self._lock:
            return self._samples[-1] if self._samples else None

    def snapshot(self) -> StatisticsSnapshot:
        map_q_values = self.window.map_q_values if self.window is not None else ()
        return StatisticsSnapshot.from_samples(self.samples(), map_q_values)

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            if self._configured_interval_seconds is not None:
                self.sample_interval_seconds = self._configured_interval_seconds


@dataclass
class RunStatistics:
    """The accumulated totals and the decay series they index, reset together.

    A new run must start a fresh decay curve for the same reason it starts a fresh
    histogram: splicing two runs' curves together would misreport convergence.
    """

    accumulator: RunAccumulator = field(default_factory=RunAccumulator)
    series: StatisticsSeries = field(default_factory=StatisticsSeries)

    def reset(self) -> None:
        self.accumulator.reset()
        self.series.reset()


def mean_relative_uncertainty_in_window(
    q_values: list[float],
    relative_uncertainty: list[float],
    q_min: float | None = None,
    q_max: float | None = None,
) -> float:
    """Mean sigma/I(Q) over populated bins, optionally restricted to a Q window.

    Convenience wrapper for callers that do not hold a StatisticsWindow; the analyzer builds
    the window once and calls summarize() directly.
    """
    return StatisticsWindow.build(q_values, q_min, q_max).summarize(relative_uncertainty)[0]


def write_statistics_csv(samples: list[StatisticsSample], output_path: str) -> None:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(STATISTICS_CSV_COLUMNS)
        for sample in samples:
            writer.writerow(
                [
                    f"{sample.elapsed_seconds:.8f}",
                    f"{sample.proton_charge_pc:.8f}",
                    f"{sample.mean_relative_uncertainty:.8f}",
                    f"{sample.total_counts:.8f}",
                    sample.nonzero_bins,
                    sample.histogram_events,
                ]
            )


def write_statistics_map_csv(snapshot: StatisticsSnapshot, output_path: str) -> None:
    """Write the 2D sigma/I(Q) map: one row per sample, one column per Q.

    The leading two columns carry both accumulation axes so the file can be plotted against
    either without needing the 1D CSV alongside it.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["Elapsed time (s)", "Proton charge (pC)"]
            + [f"sigma/I(Q) at Q={q_value:.8f}" for q_value in snapshot.map_q_values]
        )
        for elapsed, charge, profile in zip(
            snapshot.elapsed_seconds,
            snapshot.proton_charge_pc,
            snapshot.profiles,
            strict=True,
        ):
            writer.writerow([f"{elapsed:.8f}", f"{charge:.8f}"] + [f"{value:.8f}" for value in profile])


__all__ = [
    "MAX_MAP_COLUMNS",
    "MAX_MAP_ROWS",
    "STATISTICS_CSV_COLUMNS",
    "RunAccumulator",
    "RunStatistics",
    "StatisticsMap",
    "StatisticsSample",
    "StatisticsSeries",
    "StatisticsSnapshot",
    "StatisticsWindow",
    "mean_relative_uncertainty_in_window",
    "write_statistics_csv",
    "write_statistics_map_csv",
]
