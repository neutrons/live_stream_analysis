# Reference

Details behind the commands in the README. For a guided tour, start with the walkthrough notebooks
in `notebooks/` instead; `live_stream_analysis <command> --help` lists every option.

## Preparer

`preparer` builds the three inputs the analyzer needs besides the sample data.

### Pixel geometry

`--mode idf` (the default) reads a Mantid instrument definition (IDF) and writes one row per
detector pixel with its L2, scattering angle and TOF-to-Q constant. It also writes a synthetic
I(Q) CSV (`--iq-csv`) as a sanity check of the conversion.

```bash
live_stream_analysis preparer \
    --idf-file tests/data/idf/NOMAD_Definition.xml \
    --calibration-file tests/data/calibration/NOMAD_243451_2026-06-09_shifter.h5 \
    --pixel-geometry-csv pixel_geometry_calibrated.csv \
    --iq-csv iq.csv
```

The analyzer converts each event with `Q = (TOF-to-Q matrix element) / TOF`. Without a
calibration the matrix element comes from the IDF geometry, `4*pi*sin(theta)*252.777*L`.
`--calibration-file` takes a Mantid diffraction calibration (`SaveDiffCal` HDF5), replaces the
matrix element with `2*pi*DIFC`, and adds `difc`, `difa`, `tzero` and `use` columns. The analyzer
skips pixels with `use = 0`; `difc`, `difa` and `tzero` are kept for reference only, so DIFA and
TZERO are not applied. This matches Mantid's `ConvertUnits` to `MomentumTransfer` after
`ApplyDiffCal`, which also uses DIFC alone.

Parser coverage is validated for the NOMAD IDF fixtures; a POWGEN fixture is kept for parser
regression tests only.

### TOF units

The same geometry CSV serves NeXus and ADARA input; only the analyzer's `--tof-tick-us` differs:

| Source | Event TOF unit | Analyzer setting |
| --- | --- | --- |
| NeXus `event_time_offset` | microseconds | `--tof-tick-us 1.0` |
| ADARA banked events | 100 ns ticks | `--tof-tick-us 0.1` |

Alternatively, write an ADARA-specific CSV with `--q-matrix-scale 10` and use it with
`--tof-tick-us 1.0`. A wrong setting moves every peak by a factor of ten in Q.

### Background and normalization

`--mode background` and `--mode normalization` reduce event NeXus files to I(Q). Repeat
`--nexus-file` to combine runs: counts are summed, and so are the recorded proton charge and
duration.

```bash
live_stream_analysis preparer \
    --mode background \
    --nexus-file nexus_files/mt_pac_can/NOM_243710.nxs.h5 \
    --nexus-file nexus_files/mt_pac_can/NOM_243711.nxs.h5 \
    --reduction-output-csv background.csv \
    --q-min 0.0 \
    --q-max 40.0

live_stream_analysis preparer \
    --mode normalization \
    --nexus-file nexus_files/vanadium/NOM_243712.nxs.h5 \
    --reduction-output-csv normalization.csv \
    --q-min 0.0 \
    --q-max 40.0
```

Normalization mode also strips vanadium's small Bragg peaks: it smooths the pattern over
`--peak-window` bins (default 31) and replaces points more than `--peak-z-threshold` standard
deviations (default 3.0) above that baseline.

The output columns are `Q value`, `I(Q)`, `Error I(Q)`, `Proton charge (pC)` and `Duration (s)`.
The last two repeat the runs' summed `entry/proton_charge` and `entry/duration` on every row, and
are left blank if any input file lacks them. The reduction uses a fixed Q bin size of 0.02 and
the geometry embedded in each NeXus file, without a calibration.

## Analyzer

`analyze` reads exactly one source:

| Source | Option | Notes |
| --- | --- | --- |
| Live ADARA stream | `--adara-stream HOST PORT` | NOMAD's SMS is `bl1b-daq1.sns.gov 31415` (SNS network or VPN). Reconnects after a drop, every `--adara-stream-reconnect-delay` seconds, up to `--adara-stream-max-reconnects` times (-1 for unlimited). |
| Recorded ADARA file | `--adara-file FILE` | One file of a run. `scripts/live_comparison/adara_replay_server.py` replays several files as a stream. |
| Event NeXus file | `--nexus-file FILE` | Repeat to combine runs. |

With `--histogram-pixel-geometry-csv`, it histograms every event into I(Q) on the
`--histogram-q-min/-max/-bin-size` grid; without it, it only counts packets and events.

### Corrections and normalization

Histograms are normalized by integrated proton charge by default (`--normalize-by
proton-charge`); `time` uses accumulated run time and `none` keeps raw counts. Background and
normalization CSVs are scaled by their own recorded totals, so the corrected histogram is

```text
I(Q) = (S / Q_sample - B / Q_background) / (V / Q_normalization)
```

where each `Q` is the proton charge (or duration) of that measurement. A background measured for
twice as long as the sample is therefore subtracted at half weight. A correction CSV without
totals, such as one from an older preparer, is used on the sample's scale as before, and the
analyzer logs a warning at startup.

The sample's counting error is sqrt(N) per bin, and 1 count for an empty bin, following Mantid's
`SetUncertainties` "sqrtOrOne" convention. Plain sqrt(0) = 0 would report a bin with nothing in it
yet as perfectly known. Only the error changes: I(Q) is the same either way.

Proton charge and pulse time come from ADARA RTDL and event packets on the ADARA path. A NeXus
file is replayed bank by bank, so there they are attributed from the run's `entry/proton_charge`
and `entry/duration` in proportion to the events processed so far.

Optional `--sample-coherent-scatter-length` and `--sample-total-scatter-length-squared` convert the
corrected intensity to a Mantid-style S(Q).

### Runs on the ADARA path

The analyzer watches ADARA run-status packets. The protocol's codes are `0` NO_RUN, `1` NEW_RUN,
`2` RUN_EOF, `3` RUN_BOF, `4` END_RUN, `5` STATE and `6` PROLOGUE. Only END_RUN completes a run;
RUN_EOF marks the end of one file of a run, which is split across many files.

1. NEW_RUN clears the histogram and the run's statistics.
2. Events are histogrammed as they arrive.
3. END_RUN finalizes the run: corrections are applied, the result is written to
   `--histogram-output-csv`, the plot is refreshed, and INTERSECT histogram and `run_completed`
   events are published when enabled. A live stream only stops when interrupted, so this write is
   what leaves each completed run on disk.
4. The histogram is reset, and the stream carries on with the next run.

At shutdown, a histogram with data that has not seen END_RUN is finalized once more.

### Error-decay statistics

The analyzer records how the mean relative uncertainty σ/I(Q) falls as charge and time accumulate.
A downstream INTERSECT service uses this series to forecast how long a run must continue to reach
a target error.

| Option | Purpose |
| --- | --- |
| `--statistics-output-csv FILE` | The decay series: `Elapsed time (s)`, `Proton charge (pC)`, `Mean sigma/I(Q)`, `Total counts`, `Nonzero bins`, `Histogrammed events` |
| `--statistics-map-output-csv FILE` | The 2D map: one row per sample, one column per Q in the window, prefixed by elapsed time and proton charge |
| `--statistics-q-min`, `--statistics-q-max` | The Q window for both; defaults to every populated bin |
| `--statistics-sample-interval-seconds` | Minimum run time between samples (default 1.0) |
| `--statistics-x-axis {time,proton-charge}` | X axis of the desktop decay panel; the browser page toggles between both |

Each INTERSECT `histogram_updated` event also carries `elapsed_seconds`, `proton_charge_pc` and
`mean_relative_uncertainty`.

The live plots' **Error Decay** panel has two views:

- **1D trend**: mean σ/I(Q) against time or charge, which shows *when* the run converges.
- **2D map**: Q across, time or charge up, σ/I(Q) as colour (logarithmic by default), which shows
  *which part of Q* is still noisy.

The map averages adjacent Q bins into at most 256 columns; a 5 1/Å window at 0.02 bins is 250 bins,
so it keeps full resolution. Past 256 samples, rows are selected rather than averaged, since each
row is a snapshot at one accumulation level. Past 4096 samples, the series drops every other
sample and doubles its interval, so an hours-long stream stays bounded while keeping the steep
early decay.

Without corrections, σ/I(Q) does not depend on `--normalize-by`, since normalization scales the
intensity and its error alike. With a background that has its own totals, the background is
re-weighted against the sample and σ/I(Q) shifts accordingly.

σ/I(Q) is undefined where I(Q) = 0. Those bins are left out of the mean and drawn as gaps in the
plots. Early in a run, before most bins hold any counts, the first decay points are therefore
high rather than near zero. Every bin with background subtracted carries at least the one-count
error, so it cannot pass for converged.

### Live plot

`--live-plot-mode browser` serves a dashboard (port `--live-plot-port`, default 8000) with I(Q),
σ/I(Q), the error decay and a run summary, polled twice a second. `--live-plot-keep-open` keeps it up
after the analysis ends, `--live-plot-no-open-browser` skips opening a tab, and
`--live-plot-refresh-every N` refreshes every N packets (ADARA) or chunks of 250,000 events (NeXus).
`--live-plot-mode desktop` opens the same panels in a matplotlib window instead.

#### Replacing the background mid-run

The browser dashboard's **Background Subtraction** panel takes a background CSV, dropped onto it
or chosen with a click, and subtracts it from then on without restarting the analysis. The
histogram accumulates raw counts and the corrections are applied each time it is shown, so the
counts collected so far are kept and simply re-corrected. The file is read exactly as
`--background-subtraction` is, binned by `Q value` and scaled by its own exposure totals. A file
with no rows inside the histogram's Q range, or without the three required columns, is rejected
and the current background stays.

The plot redraws as soon as the upload is accepted, even with no data arriving. The histogram CSV
snapshots and INTERSECT events pick it up from the next packet. Error-decay points already
recorded keep the background they were computed with. The panel shows the active file and how many
bins it covers, and warns if it records no exposure total for `--normalize-by`.

INTERSECT `set_background` takes the same path: the CSV is checked and binned identically, the
dashboard shows it as the active background, and the response's `message` carries the same
summary. A desktop (`--live-plot-mode desktop`) window redraws on the next packet instead, since
matplotlib cannot be drawn from the INTERSECT thread. `set_normalization` still assigns rows to
bins by position. Anyone who can reach the dashboard can upload, so with
`--live-plot-host 0.0.0.0` keep the port on a trusted network.

## INTERSECT

The analyzer publishes INTERSECT events over RabbitMQ/AMQP: periodic `histogram_updated` snapshots
with `q`, `intensity` and `error`, and a `run_completed` event with `instrument`, `ipts` when
available and `run_number`. Settings, including the publish interval (default 5 s), are in
`configs/intersect.example.yaml`.

```bash
live_stream_analysis analyze --adara-stream bl1b-daq1.sns.gov 31415 \
    --histogram-pixel-geometry-csv pixel_geometry_calibrated.csv --tof-tick-us 0.1 \
    --enable-intersect --intersect-config configs/intersect.example.yaml

# Subscribe to both events
live_stream_analysis intersect-listen --intersect-config configs/intersect.example.yaml
```

A local RabbitMQ, MinIO, analyzer and listener stack:

```bash
docker compose -f docker-compose.intersect.yml --profile intersect --profile adara-file --profile listener up --build
```

RabbitMQ management is then on `http://localhost:15672` and the MinIO console on
`http://localhost:9001`.

## Docker

Mount the directory with your data and CSVs, and serve the browser plot on a published port:

```bash
docker build -t live-stream-analysis .
docker run --rm -p 8000:8000 -v "$PWD":/work -w /work live-stream-analysis \
    live_stream_analysis analyze \
    --nexus-file nexus_files/diamond/NOM_244114.nxs.h5 \
    --histogram-pixel-geometry-csv pixel_geometry_calibrated.csv \
    --tof-tick-us 1.0 \
    --live-plot-mode browser --live-plot-host 0.0.0.0 --live-plot-no-open-browser --live-plot-keep-open
```

`docker-compose.yml` has three profiles, none started by a bare `docker compose up`: `adara-file`
(port 8000), `nexus` (8001) and `adara-stream` (8002, NOMAD's live SMS).

## Plotting CSVs

`scripts/plot_csv_histogram.py` plots one or more histogram CSVs with error bands
(`uv sync --group jupyter` first):

```bash
uv run --group jupyter python scripts/plot_csv_histogram.py \
    --input sample.csv --label Sample \
    --input background.csv --label Background \
    --input corrected.csv --label Corrected \
    --mode overlay --show-relative-uncertainty --output-png comparison.png
```

`--mode subplots` stacks them instead; `--x-min/--x-max`, `--hide-error-band`, `--error-alpha` and
`--relative-uncertainty-min-abs-intensity` (masking bins where I(Q) is near zero) adjust the view.
`scripts/live_comparison/` compares the analyzer against Mantid and checks diamond peak positions.

## ADARA data on ORC NFS shares

Recorded NOMAD ADARA streams are kept on ORC NFS shares:

| Experiment | Size | Remote | Local directory |
| --- | --- | --- | --- |
| Fe2O3, May 2025 | 300 GiB | `10.64.135.253:/share_66aaa9bb_61c7_4f12_964e_7c14f30e2ad3` | `nomad-adara-fe2o3-share` |
| Carbon, June 2026 | 1,100 GiB | `10.64.135.253:/share_8b4e51ef_ab6f_4c13_a35e_d9bbeb751e39` | `nomad-adara-carbon-share` |

```bash
sudo apt install nfs-common
sudo mount -t nfs 10.64.135.253:/share_8b4e51ef_ab6f_4c13_a35e_d9bbeb751e39 ./nomad-adara-carbon-share
```

To create a share: in ORC's **Share** tab, create an NFS share of sub-type Perf-NFS, add an `ip`
rule of `0.0.0.0/0` (the `/0` is required), and copy the share's IP-and-path **Path**.

Packets can be read directly with [readadara](https://github.com/bsobhani/readadara):

```python
from readadara import AdaraFileReader

reader = AdaraFileReader(filename="nomad-adara-carbon-share/<run directory>/m00000001-f00000001-run-244114.adara")
for packet in reader.read_generator():
    print(packet.to_dict())
```
