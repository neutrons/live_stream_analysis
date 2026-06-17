from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from live_stream_analysis.analyzer.histogram import PixelQConversion
from live_stream_analysis.analyzer.live_plot import NullHistogramPlotter
from live_stream_analysis.analyzer.nexus import accumulate_nexus_histogram, count_nexus_chunks, iter_nexus_event_chunks


def _write_nexus(tmp_path: Path, name: str, event_ids: list[int], event_tofs: list[float]) -> Path:
    path = tmp_path / name
    with h5py.File(path, "w") as handle:
        entry = handle.create_group("entry")
        events = entry.create_group("bank1_events")
        events.create_dataset("event_id", data=np.array(event_ids, dtype=np.int32))
        events.create_dataset("event_time_offset", data=np.array(event_tofs, dtype=np.float64))
    return path


def test_count_nexus_chunks_counts_partial_tail_chunk(tmp_path: Path):
    nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1, 2, 3, 4, 5], [1.0, 2.0, 3.0, 4.0, 5.0])

    result = count_nexus_chunks([str(nexus_path)], chunk_size=2)

    assert result == 3


def test_iter_nexus_event_chunks_rejects_mismatched_dataset_lengths(tmp_path: Path):
    nexus_path = tmp_path / "bad.nxs.h5"
    with h5py.File(nexus_path, "w") as handle:
        entry = handle.create_group("entry")
        events = entry.create_group("bank1_events")
        events.create_dataset("event_id", data=np.array([1, 2], dtype=np.int32))
        events.create_dataset("event_time_offset", data=np.array([1.0], dtype=np.float64))

    with h5py.File(nexus_path, "r") as handle:
        group = handle["entry"]["bank1_events"]
        with pytest.raises(ValueError, match="same length"):
            list(iter_nexus_event_chunks(group, chunk_size=2))


def test_accumulate_nexus_histogram_counts_events_into_expected_bin(tmp_path: Path):
    nexus_path = _write_nexus(tmp_path, "sample.nxs.h5", [1], [100.0])
    q_conversion = PixelQConversion(
        q_matrix_constants=[0.0, 1000.0],
        difc=[0.0, 1000.0 / (2.0 * np.pi)],
        difa=[0.0, 0.0],
        tzero=[0.0, 0.0],
        use=[1, 1],
    )

    packet_count, total_events, histogram_events, hist = accumulate_nexus_histogram(
        nexus_files=[str(nexus_path)],
        q_conversion=q_conversion,
        histogram_bins=600,
        histogram_q_min=0.0,
        histogram_q_bin_size=0.02,
        tof_tick_us=1.0,
        plotter=NullHistogramPlotter(),
        live_plot_refresh_every=1,
        chunk_size=2,
    )

    assert packet_count == 1
    assert total_events == 1
    assert histogram_events == 1
    assert hist[500] == 1
