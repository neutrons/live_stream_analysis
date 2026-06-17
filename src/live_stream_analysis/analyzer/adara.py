"""ADARA-specific reader construction and accumulation helpers."""

from __future__ import annotations

import argparse

from .adara_source import accumulate_adara_histogram, build_reader, run_basic_mode
from .histogram import load_correction_csv as _load_correction_csv
from .live_plot import compute_relative_uncertainty as _compute_relative_uncertainty
from .live_plot import create_live_histogram_plotter as _create_live_histogram_plotter

NEXUS_CHUNK_SIZE = 250_000


def run_from_namespace(args: argparse.Namespace) -> int:
    from . import histogram_runner

    histogram_runner.create_live_histogram_plotter = _create_live_histogram_plotter
    return histogram_runner.run_from_namespace(args)


__all__ = [
    "_compute_relative_uncertainty",
    "_create_live_histogram_plotter",
    "_load_correction_csv",
    "NEXUS_CHUNK_SIZE",
    "accumulate_adara_histogram",
    "build_reader",
    "run_from_namespace",
    "run_basic_mode",
]
