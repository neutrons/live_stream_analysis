"""Pure-Python NeXus reduction helpers for background and normalization CSVs."""

from __future__ import annotations

import csv
import tempfile
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np

from .instrument import build_detector_geometry


@dataclass(slots=True)
class ReductionResult:
    """Reduced Q-space result with propagated counting error."""

    q_values: np.ndarray
    intensity: np.ndarray
    error: np.ndarray
    total_counts: int


def _read_embedded_idf(nexus_path: Path) -> str:
    with h5py.File(nexus_path, "r") as handle:
        dataset = handle["entry/instrument/instrument_xml/data"]
        raw = dataset[0]
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)


def _geometry_from_nexus(nexus_path: Path) -> list[tuple[int, float, float, float, float]]:
    idf_text = _read_embedded_idf(nexus_path)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-8", delete=False) as handle:
        handle.write(idf_text)
        temp_path = Path(handle.name)
    try:
        return build_detector_geometry(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _build_q_lookup(geometry_rows: list[tuple[int, float, float, float, float]]) -> np.ndarray:
    max_pixel_id = max(row[0] for row in geometry_rows)
    lookup = np.zeros(max_pixel_id + 1, dtype=np.float64)
    for pixel_id, _, _, _, q_matrix_element in geometry_rows:
        lookup[pixel_id] = q_matrix_element
    return lookup


def _iter_event_groups(handle: h5py.File):
    entry = handle["entry"]
    for name in sorted(entry.keys()):
        if name.endswith("_events"):
            yield entry[name]


def _histogram_nexus_file(
    nexus_path: Path,
    q_lookup: np.ndarray,
    q_min: float,
    q_max: float,
    q_bin_size: float,
    tof_tick_us: float,
) -> tuple[np.ndarray, int]:
    histogram_bins = int(round((q_max - q_min) / q_bin_size))
    counts = np.zeros(histogram_bins, dtype=np.float64)
    inv_tof_tick_us = 1.0 / tof_tick_us

    with h5py.File(nexus_path, "r") as handle:
        for group in _iter_event_groups(handle):
            event_ids = group["event_id"][:]
            event_tof = group["event_time_offset"][:]
            if event_ids.size == 0:
                continue

            valid = (event_ids >= 0) & (event_ids < q_lookup.size) & (event_tof > 0)
            if not np.any(valid):
                continue

            event_ids = event_ids[valid]
            event_tof = event_tof[valid]
            q_values = (q_lookup[event_ids] * inv_tof_tick_us) / event_tof
            q_bins = np.floor((q_values - q_min) / q_bin_size).astype(np.int64)
            in_range = (q_bins >= 0) & (q_bins < histogram_bins)
            if np.any(in_range):
                np.add.at(counts, q_bins[in_range], 1.0)

    return counts, int(counts.sum())


def _smooth_signal(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return values.copy()
    kernel = np.ones(window, dtype=np.float64) / float(window)
    return np.convolve(values, kernel, mode="same")


def _strip_vanadium_peaks(values: np.ndarray, window: int, z_threshold: float) -> np.ndarray:
    baseline = _smooth_signal(values, window)
    residual = values - baseline
    sigma = residual.std()
    if sigma == 0.0:
        return baseline
    mask = residual > (z_threshold * sigma)
    stripped = values.copy()
    stripped[mask] = baseline[mask]
    return _smooth_signal(stripped, max(3, window // 2))


def reduce_nexus_files(
    nexus_files: list[Path],
    q_min: float,
    q_max: float,
    q_bin_size: float,
    tof_tick_us: float,
    mode: str,
    peak_window: int,
    peak_z_threshold: float,
) -> ReductionResult:
    """Reduce one or more event NeXus files into a three-column Q-space result."""
    if not nexus_files:
        raise ValueError("At least one NeXus file is required")
    if q_bin_size <= 0:
        raise ValueError("q_bin_size must be > 0")
    if q_max <= q_min:
        raise ValueError("q_max must be greater than q_min")

    geometry_rows = _geometry_from_nexus(nexus_files[0])
    q_lookup = _build_q_lookup(geometry_rows)
    histogram_bins = int(round((q_max - q_min) / q_bin_size))
    counts = np.zeros(histogram_bins, dtype=np.float64)
    total_counts = 0

    for nexus_path in nexus_files:
        file_counts, file_total = _histogram_nexus_file(
            nexus_path=nexus_path,
            q_lookup=q_lookup,
            q_min=q_min,
            q_max=q_max,
            q_bin_size=q_bin_size,
            tof_tick_us=tof_tick_us,
        )
        counts += file_counts
        total_counts += file_total

    q_values = q_min + (np.arange(histogram_bins, dtype=np.float64) + 0.5) * q_bin_size
    intensity = counts / max(len(nexus_files), 1)
    error = np.sqrt(counts) / max(len(nexus_files), 1)

    if mode == "normalization":
        intensity = _strip_vanadium_peaks(intensity, peak_window, peak_z_threshold)
        error = np.maximum(error, np.sqrt(np.clip(intensity, 0.0, None)) / max(len(nexus_files), 1))

    return ReductionResult(q_values=q_values, intensity=intensity, error=error, total_counts=total_counts)


def write_reduction_csv(result: ReductionResult, output_csv: Path) -> None:
    """Write Q, I(Q), and error columns to CSV."""
    output_csv = output_csv.resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Q value", "I(Q)", "Error I(Q)"])
        for q_value, intensity, error in zip(result.q_values, result.intensity, result.error):
            writer.writerow([f"{q_value:.8f}", f"{intensity:.8f}", f"{error:.8f}"])