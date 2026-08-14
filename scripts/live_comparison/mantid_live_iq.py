#!/usr/bin/env python3
"""Run Mantid's live-data pipeline against a replayed file and write I(Q) as CSV.

This drives StartLiveData -> MonitorLiveData -> LoadLiveData using the ADARA_FileReader
instrument in the TEST_LIVE facility, which replays a file *inside Mantid* as if it were
arriving live. It never opens a socket, so it does not consume `adara_replay_server.py`;
the two are independent demonstrations of the same idea on either side.

Its value is that Mantid accumulates in chunks, exactly as it would on a real beamline.
Comparing its result with the analyzer's single-pass reduction of the same file checks that
chunked accumulation converges on the same I(Q).

Before running, point Mantid at the file to replay by adding to ~/.mantid/Mantid.user.properties:

    fileeventdatalistener.filename=NOM_243709.nxs.h5
    fileeventdatalistener.chunks=100

The file must sit in one of Mantid's configured data search directories.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from mantid.simpleapi import (
    AlgorithmManager,
    ConvertUnits,
    Rebin,
    StartLiveData,
    SumSpectra,
    mtd,
)


def start_live_reduction(q_min: float, q_max: float, q_bin_size: float, update_every: float) -> str:
    """Start the background live job and return the accumulation workspace name.

    AccumulationMethod='Add' is the analyzer's behaviour: each chunk's counts are summed
    into the running histogram. RunTransitionBehavior='Restart' matches how we clear the
    histogram on NEW_RUN and finalise on END_RUN.
    """
    output_name = "lsa_live_iq"
    StartLiveData(
        Instrument="ADARA_FileReader",
        Listener="FileEventDataListener",
        AccumulationMethod="Add",
        RunTransitionBehavior="Restart",
        UpdateEvery=update_every,
        PreserveEvents=False,
        ProcessingAlgorithm="Rebin",
        ProcessingProperties=f"Params={q_min},{q_bin_size},{q_max}",
        OutputWorkspace=output_name,
    )
    return output_name


def wait_for_completion(poll_seconds: float, quiet_polls: int) -> None:
    """Block until the live job stops growing, then cancel the monitor.

    The replay ends when the file is exhausted; MonitorLiveData keeps running after that,
    so watch for the total counts going quiet and then stop it.
    """
    previous_total = -1.0
    unchanged = 0
    while unchanged < quiet_polls:
        time.sleep(poll_seconds)
        if "lsa_live_iq" not in mtd:
            continue
        total = float(sum(mtd["lsa_live_iq"].readY(0)))
        if total == previous_total:
            unchanged += 1
        else:
            unchanged = 0
            previous_total = total
        print(f"  accumulated counts: {total:.0f}")

    for name in AlgorithmManager.runningInstancesOf("MonitorLiveData"):
        name.cancel()


def write_histogram_csv(workspace, output_csv: Path) -> int:
    """Write Q value, I(Q), Error I(Q) using bin centres, matching the analyzer's CSV."""
    edges = workspace.readX(0)
    intensity = workspace.readY(0)
    error = workspace.readE(0)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Q value", "I(Q)", "Error I(Q)"])
        for index in range(len(intensity)):
            q_centre = 0.5 * (edges[index] + edges[index + 1])
            writer.writerow([f"{q_centre:.8f}", f"{intensity[index]:.8f}", f"{error[index]:.8f}"])
    return len(intensity)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-csv", type=Path, required=True, help="Where to write the I(Q) CSV.")
    parser.add_argument("--histogram-q-min", type=float, default=0.0)
    parser.add_argument("--histogram-q-max", type=float, default=40.0)
    parser.add_argument("--histogram-q-bin-size", type=float, default=0.02)
    parser.add_argument("--update-every", type=float, default=1.0, help="Live update interval in seconds.")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="How often to check for completion.")
    parser.add_argument("--quiet-polls", type=int, default=5, help="Consecutive unchanged polls that mean 'done'.")
    args = parser.parse_args()

    name = start_live_reduction(
        args.histogram_q_min,
        args.histogram_q_max,
        args.histogram_q_bin_size,
        args.update_every,
    )
    wait_for_completion(args.poll_seconds, args.quiet_polls)

    workspace = ConvertUnits(
        InputWorkspace=mtd[name], Target="MomentumTransfer", EMode="Elastic", OutputWorkspace="lsa_live_q"
    )
    workspace = Rebin(
        InputWorkspace=workspace,
        Params=[args.histogram_q_min, args.histogram_q_bin_size, args.histogram_q_max],
        PreserveEvents=False,
        OutputWorkspace="lsa_live_binned",
    )
    workspace = SumSpectra(InputWorkspace=workspace, OutputWorkspace="lsa_live_summed")

    bins = write_histogram_csv(workspace, args.output_csv)
    print(f"Wrote {bins} bins to {args.output_csv.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
