"""Preparer package exposing instrument parsing and TOF->Q conversion workflow."""

from .converter import add_parser, convert_to_iq, run_from_namespace, run_preparer
from .instrument import build_detector_geometry, build_synthetic_tof_spectrum
from .nexus import reduce_nexus_files, write_reduction_csv

__all__ = [
    "add_parser",
    "build_detector_geometry",
    "build_synthetic_tof_spectrum",
    "convert_to_iq",
    "reduce_nexus_files",
    "run_from_namespace",
    "run_preparer",
    "write_reduction_csv",
]
