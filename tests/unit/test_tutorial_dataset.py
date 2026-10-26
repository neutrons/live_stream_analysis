"""Tests for the tutorial-dataset trimmers and for the dataset committed in tests/data/tutorial/."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import h5py
import numpy as np
import pytest
from tutorial_dataset.trim import (
    ADARA_RTDL_FORMAT,
    ADARA_RUN_STATUS_FORMAT,
    iter_adara_packets,
    trim_adara,
    trim_nexus,
)

from tests.unit.analyzer.adara_fixtures import event_packet, rtdl_packet, run_status_packet

TUTORIAL = Path(__file__).resolve().parents[1] / "data" / "tutorial"
FILE_LIMIT_BYTES = 8 * 1024 * 1024  # check-added-large-files --maxkb=8192


def _write_nexus(path: Path) -> None:
    """Two banks over three pulses, with the layout of a NOMAD event file."""
    with h5py.File(path, "w") as handle:
        entry = handle.create_group("entry")
        entry.create_dataset("title", data=np.array([b"Diamond"]))
        entry.create_dataset("proton_charge", data=np.array([60.0]))
        entry.create_dataset("duration", data=np.array([3 / 60]))
        entry.create_group("user1").create_dataset("name", data=np.array([b"Someone"]))
        log = entry.create_group("DASlogs").create_group("proton_charge")
        log.create_dataset("value", data=np.array([10.0, 20.0, 30.0]))
        log.create_dataset("time", data=np.array([0.0, 1 / 60, 2 / 60]))
        instrument = entry.create_group("instrument")
        instrument.create_group("instrument_xml").create_dataset("data", data=np.array([b"<instrument/>"]))
        for bank, ids in (("bank1_events", [1, 2, 3, 4, 5, 6]), ("bank2_events", [7, 8, 9])):
            group = entry.create_group(bank)
            group.create_dataset("event_id", data=np.array(ids, dtype=np.uint32))
            group.create_dataset("event_time_offset", data=np.arange(len(ids), dtype=np.float32) + 100.0)
            # bank1: 2, 3 and 1 events per pulse; bank2: 1 event in each pulse.
            index = [0, 2, 5] if bank == "bank1_events" else [0, 1, 2]
            group.create_dataset("event_index", data=np.array(index, dtype=np.uint64))
            group.create_dataset("event_time_zero", data=np.array([0.0, 1 / 60, 2 / 60]))
            # NeXus links the same event arrays under instrument/; a naive copy duplicates them.
            instrument.create_group(bank.removesuffix("_events"))["event_id"] = group["event_id"]


class TestTrimNexus:
    def test_keeps_the_first_pulses_and_their_exposure(self, tmp_path: Path):
        source, trimmed = tmp_path / "full.nxs.h5", tmp_path / "trimmed.nxs.h5"
        _write_nexus(source)

        kept = trim_nexus(source, trimmed, pulses=2)

        assert kept == {"pulses": 2, "events": 7, "proton_charge_pc": 30.0, "duration_s": pytest.approx(2 / 60)}
        with h5py.File(trimmed) as handle:
            entry = handle["entry"]
            assert entry["bank1_events/event_id"][:].tolist() == [1, 2, 3, 4, 5]
            assert entry["bank2_events/event_id"][:].tolist() == [7, 8]
            assert entry["bank1_events/event_index"][:].tolist() == [0, 2]
            assert entry["proton_charge"][0] == pytest.approx(30.0)
            assert entry["duration"][0] == pytest.approx(2 / 60)
            assert entry["DASlogs/proton_charge/value"][:].tolist() == [10.0, 20.0]
            assert entry["instrument/instrument_xml/data"][0] == b"<instrument/>"

    def test_drops_user_groups_and_linked_instrument_banks(self, tmp_path: Path):
        source, trimmed = tmp_path / "full.nxs.h5", tmp_path / "trimmed.nxs.h5"
        _write_nexus(source)

        trim_nexus(source, trimmed, pulses=1)

        with h5py.File(trimmed) as handle:
            assert not [name for name in handle["entry"] if name.startswith("user")]
            assert list(handle["entry/instrument"]) == ["instrument_xml"]

    def test_keeping_every_pulse_keeps_the_recorded_duration(self, tmp_path: Path):
        source, trimmed = tmp_path / "full.nxs.h5", tmp_path / "trimmed.nxs.h5"
        _write_nexus(source)

        kept = trim_nexus(source, trimmed, pulses=10)

        assert kept["pulses"] == 3
        assert kept["events"] == 9
        assert kept["proton_charge_pc"] == pytest.approx(60.0)
        assert kept["duration_s"] == pytest.approx(3 / 60)


class TestTrimAdara:
    def test_keeps_whole_pulses_then_closes_the_run(self, tmp_path: Path):
        first, last, trimmed = tmp_path / "f1.adara", tmp_path / "f2.adara", tmp_path / "trimmed.adara"
        first.write_bytes(
            run_status_packet(run_number=7, run_start=0, status=1)
            + rtdl_packet(timestamp_s=1)
            + event_packet([(1, 1)], timestamp_s=1)
            + rtdl_packet(timestamp_s=2)
            + event_packet([(1, 1)], timestamp_s=2)
            + rtdl_packet(timestamp_s=3)
            + event_packet([(1, 1)], timestamp_s=3)
        )
        last.write_bytes(rtdl_packet(timestamp_s=9) + run_status_packet(run_number=7, run_start=0, status=4))

        kept = trim_adara(first, last, trimmed, pulses=2)

        formats = [packet_format for packet_format, _ in iter_adara_packets(trimmed)]
        assert kept["pulses"] == 2
        assert formats.count(ADARA_RTDL_FORMAT) == 2
        assert formats[0] == formats[-1] == ADARA_RUN_STATUS_FORMAT
        assert len(formats) == 6  # NEW_RUN, two pulses of RTDL + events, END_RUN

    def test_requires_a_run_end(self, tmp_path: Path):
        first, last = tmp_path / "f1.adara", tmp_path / "f2.adara"
        first.write_bytes(rtdl_packet(timestamp_s=1))
        last.write_bytes(rtdl_packet(timestamp_s=2))

        with pytest.raises(ValueError, match="no END_RUN"):
            trim_adara(first, last, tmp_path / "trimmed.adara", pulses=1)


class TestCommittedDataset:
    manifest = json.loads((TUTORIAL / "manifest.json").read_text(encoding="utf-8"))

    @pytest.mark.parametrize("name", sorted(manifest))
    def test_files_match_the_manifest_and_fit_the_repository(self, name: str):
        path = TUTORIAL / name
        entry = self.manifest[name]

        assert path.stat().st_size == entry["bytes"] < FILE_LIMIT_BYTES
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]

    @pytest.mark.parametrize("name", [name for name in sorted(manifest) if name.endswith(".nxs.h5")])
    def test_nexus_totals_match_the_kept_pulses(self, name: str):
        entry = self.manifest[name]
        with h5py.File(TUTORIAL / name) as handle:
            nexus = handle["entry"]
            assert nexus["proton_charge"][0] == pytest.approx(nexus["DASlogs/proton_charge/value"][:].sum())
            assert nexus["proton_charge"][0] == pytest.approx(entry["proton_charge_pc"])
            assert nexus["duration"][0] == pytest.approx(entry["pulses"] / 60)
            banks = [nexus[group] for group in nexus if group.endswith("_events")]
            assert len(banks) == 101
            assert all(bank["event_index"].shape[0] == entry["pulses"] for bank in banks)
            assert sum(bank["event_id"].shape[0] for bank in banks) == entry["events"]
            assert not [group for group in nexus if group.startswith("user")]

    def test_adara_file_is_one_whole_run(self):
        name = "m00000001-f00000001-run-244114.adara"
        packets = list(iter_adara_packets(TUTORIAL / name))
        statuses = [
            packet[16 + 8 : 16 + 12] for packet_format, packet in packets if packet_format == ADARA_RUN_STATUS_FORMAT
        ]

        assert [int.from_bytes(status, "little") >> 24 for status in statuses] == [1, 4]  # NEW_RUN ... END_RUN
        assert sum(packet_format == ADARA_RTDL_FORMAT for packet_format, _ in packets) == self.manifest[name]["pulses"]
