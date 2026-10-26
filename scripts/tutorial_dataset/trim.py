#!/usr/bin/env python3
"""Trim NOMAD event data to the first N pulses of a run, for a small tutorial and CI dataset.

Both formats are cut by time from the start of the run, so a trimmed file is honestly a
shorter run rather than a thinned one:

- **NeXus:** each `bankN_events` group keeps the events of its first N pulses, cut at
  `event_index[N]`. `entry/proton_charge` and `entry/duration` are recomputed from the kept
  pulses, so the analyzer and preparer normalize the trimmed run by its own exposure. Only what
  they read is copied, plus run metadata; user groups are dropped, and the event arrays are
  gzip-compressed.
- **ADARA:** the packets of a run's first file up to its (N+1)th RTDL packet, followed by the
  run's END_RUN packet, so the result is a complete NEW_RUN-to-END_RUN run that the analyzer and
  the replay server read like any other. Packets are copied byte for byte.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import h5py
import numpy as np

PULSE_RATE_HZ = 60.0
ADARA_HEADER_BYTES = 16
ADARA_RTDL_FORMAT = 0x100
ADARA_RUN_STATUS_FORMAT = 0x400301
ADARA_RUN_STATUS_END_RUN = 4

# Run-level metadata copied verbatim. Everything the analyzer and preparer need is here or in
# the event groups; user groups are left out on purpose, since they name people.
NEXUS_METADATA = (
    "definition",
    "entry_identifier",
    "experiment_identifier",
    "experiment_title",
    "run_number",
    "sample",
    "start_time",
    "title",
)
EVENT_COMPRESSION = {"compression": "gzip", "compression_opts": 9, "shuffle": True}


def trim_nexus(source: Path, destination: Path, pulses: int) -> dict[str, float]:
    """Write the first `pulses` pulses of an event NeXus file, returning what was kept."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(source, "r") as src, h5py.File(destination, "w") as dst:
        src_entry = src["entry"]
        dst_entry = dst.create_group("entry")
        _copy_metadata(src_entry, dst_entry)
        total_pulses = int(src_entry["DASlogs/proton_charge/value"].shape[0])
        kept_pulses = min(pulses, total_pulses)
        proton_charge = _trim_charge_log(src_entry, dst_entry, kept_pulses)
        total_events = sum(
            _trim_bank(src_entry[name], dst_entry.create_group(name), kept_pulses)
            for name in sorted(src_entry)
            if name.endswith("_events")
        )
        duration = kept_pulses / PULSE_RATE_HZ if kept_pulses < total_pulses else float(src_entry["duration"][0])
        _write_scalar(dst_entry, "proton_charge", proton_charge, src_entry["proton_charge"].attrs.get("units"))
        _write_scalar(dst_entry, "duration", duration, src_entry["duration"].attrs.get("units"))
        dst_entry.create_dataset("total_counts", data=np.array([total_events], dtype=np.uint64))
        dst_entry.create_dataset("total_pulses", data=np.array([kept_pulses], dtype=np.uint64))
    return {"pulses": kept_pulses, "events": total_events, "proton_charge_pc": proton_charge, "duration_s": duration}


def _copy_attrs(source, destination) -> None:
    for key, value in source.attrs.items():
        destination.attrs[key] = value


def _copy_metadata(src_entry: h5py.Group, dst_entry: h5py.Group) -> None:
    _copy_attrs(src_entry, dst_entry)
    for name in NEXUS_METADATA:
        if name in src_entry:
            src_entry.file.copy(src_entry[name], dst_entry, name=name)
    # instrument/bankN hard-links the full event arrays of entry/bankN_events, so copying the
    # group whole would copy every event. Everything but the banks is small.
    src_instrument = src_entry["instrument"]
    dst_instrument = dst_entry.create_group("instrument")
    _copy_attrs(src_instrument, dst_instrument)
    for name in src_instrument:
        if not name.startswith("bank"):
            src_entry.file.copy(src_instrument[name], dst_instrument, name=name)


def _trim_charge_log(src_entry: h5py.Group, dst_entry: h5py.Group, pulses: int) -> float:
    """Copy the first pulses of the per-pulse proton charge log, returning their total."""
    charge_log = src_entry["DASlogs/proton_charge"]
    dst_log = dst_entry.create_group("DASlogs").create_group("proton_charge")
    _copy_attrs(charge_log, dst_log)
    for name in ("value", "time"):
        _copy_attrs(charge_log[name], dst_log.create_dataset(name, data=charge_log[name][:pulses]))
    return float(dst_log["value"][:].sum())


def _write_scalar(group: h5py.Group, name: str, value: float, units) -> None:
    dataset = group.create_dataset(name, data=np.array([value], dtype=np.float64))
    if units is not None:
        dataset.attrs["units"] = units


def _trim_bank(src_bank: h5py.Group, dst_bank: h5py.Group, pulses: int) -> int:
    for key, value in src_bank.attrs.items():
        dst_bank.attrs[key] = value
    event_index = src_bank["event_index"][:]
    event_count = int(src_bank["event_id"].shape[0])
    kept_events = int(event_index[pulses]) if pulses < event_index.size else event_count
    for name, data in (
        ("event_id", src_bank["event_id"][:kept_events]),
        ("event_time_offset", src_bank["event_time_offset"][:kept_events]),
        ("event_index", event_index[:pulses]),
        ("event_time_zero", src_bank["event_time_zero"][:pulses]),
    ):
        dataset = dst_bank.create_dataset(name, data=data, **EVENT_COMPRESSION)
        for key, value in src_bank[name].attrs.items():
            dataset.attrs[key] = value
    dst_bank.create_dataset("total_counts", data=np.array([kept_events], dtype=np.uint64))
    return kept_events


def iter_adara_packets(path: Path):
    """Yield (format, raw packet bytes) for every packet in an ADARA file."""
    with path.open("rb") as handle:
        while True:
            header = handle.read(ADARA_HEADER_BYTES)
            if len(header) < ADARA_HEADER_BYTES:
                return
            payload = handle.read(int.from_bytes(header[:4], "little"))
            yield int.from_bytes(header[4:8], "little"), header + payload


def _end_run_packet(path: Path) -> bytes:
    end_run = None
    for packet_format, packet in iter_adara_packets(path):
        if packet_format == ADARA_RUN_STATUS_FORMAT:
            status = int.from_bytes(packet[ADARA_HEADER_BYTES + 8 : ADARA_HEADER_BYTES + 12], "little") >> 24
            if status == ADARA_RUN_STATUS_END_RUN:
                end_run = packet
    if end_run is None:
        raise ValueError(f"{path} has no END_RUN packet; pass the last file of the run")
    return end_run


def trim_adara(first_file: Path, last_file: Path, destination: Path, pulses: int) -> dict[str, float]:
    """Write a run's first `pulses` pulses from its first file, closed by the END_RUN in its last file."""
    end_run = _end_run_packet(last_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    kept_pulses = 0
    packets = 0
    with destination.open("wb") as out:
        for packet_format, packet in iter_adara_packets(first_file):
            if packet_format == ADARA_RTDL_FORMAT:
                if kept_pulses == pulses:
                    break
                kept_pulses += 1
            out.write(packet)
            packets += 1
        out.write(end_run)
    return {"pulses": kept_pulses, "packets": packets + 1, "duration_s": kept_pulses / PULSE_RATE_HZ}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--pulses", type=int, required=True, help="Pulses to keep from the start of the run (60 per s)."
    )
    parser.add_argument("--output", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--nexus-file", type=Path)
    source.add_argument(
        "--adara-files", type=Path, nargs=2, metavar=("FIRST", "LAST"), help="First and last file of a run."
    )
    args = parser.parse_args()
    if args.nexus_file is not None:
        kept = trim_nexus(args.nexus_file, args.output, args.pulses)
    else:
        kept = trim_adara(args.adara_files[0], args.adara_files[1], args.output, args.pulses)
    print(f"{args.output}: {args.output.stat().st_size / 1e6:.2f} MB, {kept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
