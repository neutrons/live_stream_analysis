from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Protocol

from .adara import accumulate_adara_histogram, build_reader
from .adara import run_basic_mode as run_adara_basic_mode
from .histogram import PixelQConversion
from .nexus import accumulate_nexus_histogram
from .nexus import run_basic_mode as run_nexus_basic_mode


class HistogramSourceRunner(Protocol):
    def accumulate_histogram(
        self,
        reader,
        args: argparse.Namespace,
        q_conversion: PixelQConversion,
        histogram_bins: int,
        plotter,
        *,
        chunk_size: int,
        q_conversion_provider=None,
        histogram_callback=None,
        run_complete_callback=None,
        histogram_state_callback=None,
    ): ...

    def run_basic_mode(self, reader, *, chunk_size: int) -> int: ...


@dataclass(frozen=True)
class _AdaraRunner:
    def accumulate_histogram(
        self,
        reader,
        args: argparse.Namespace,
        q_conversion: PixelQConversion,
        histogram_bins: int,
        plotter,
        *,
        chunk_size: int,
        q_conversion_provider=None,
        histogram_callback=None,
        run_complete_callback=None,
        histogram_state_callback=None,
    ):
        _ = chunk_size
        return accumulate_adara_histogram(
            reader=reader,
            q_conversion=q_conversion,
            histogram_bins=histogram_bins,
            histogram_q_min=args.histogram_q_min,
            histogram_q_bin_size=args.histogram_q_bin_size,
            tof_tick_us=args.tof_tick_us,
            plotter=plotter,
            live_plot_refresh_every=args.live_plot_refresh_every,
            event_log_interval=args.event_log_interval,
            q_conversion_provider=q_conversion_provider,
            histogram_callback=histogram_callback,
            run_complete_callback=run_complete_callback,
            histogram_state_callback=histogram_state_callback,
        )

    def run_basic_mode(self, reader, *, chunk_size: int) -> int:
        _ = chunk_size
        return run_adara_basic_mode(reader)


@dataclass(frozen=True)
class _NexusRunner:
    def accumulate_histogram(
        self,
        reader,
        args: argparse.Namespace,
        q_conversion: PixelQConversion,
        histogram_bins: int,
        plotter,
        *,
        chunk_size: int,
        q_conversion_provider=None,
        histogram_callback=None,
        run_complete_callback=None,
        histogram_state_callback=None,
    ):
        _ = histogram_state_callback
        packet_count, total_events, histogram_events, hist, adara_stats = accumulate_nexus_histogram(
            nexus_files=reader,
            q_conversion=q_conversion,
            histogram_bins=histogram_bins,
            histogram_q_min=args.histogram_q_min,
            histogram_q_bin_size=args.histogram_q_bin_size,
            tof_tick_us=args.tof_tick_us,
            plotter=plotter,
            live_plot_refresh_every=args.live_plot_refresh_every,
            event_log_interval=args.event_log_interval,
            chunk_size=chunk_size,
            q_conversion_provider=q_conversion_provider,
            histogram_callback=histogram_callback,
        )
        if run_complete_callback is not None:
            run_complete_callback(None)
        return packet_count, total_events, histogram_events, hist, adara_stats

    def run_basic_mode(self, reader, *, chunk_size: int) -> int:
        return run_nexus_basic_mode(reader, chunk_size=chunk_size)


def create_source_runner(args: argparse.Namespace) -> HistogramSourceRunner:
    if args.nexus_file is not None:
        return _NexusRunner()
    return _AdaraRunner()


__all__ = ["HistogramSourceRunner", "build_reader", "create_source_runner"]
