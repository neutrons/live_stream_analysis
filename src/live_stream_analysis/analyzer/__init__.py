"""Analyzer package for reading ADARA file and live-stream data."""

from .cli import add_parser
from .histogram_runner import run_from_namespace

__all__ = ["add_parser", "run_from_namespace"]
