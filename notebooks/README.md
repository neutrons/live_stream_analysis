# Walkthrough notebooks

End-to-end examples that take NOMAD diamond data from raw events to a corrected,
background-subtracted and vanadium-normalized I(Q), with the live plot and the error-decay series.

Each step comes in two flavours that produce identical results:

- **`*_cli`**: the `live_stream_analysis` commands exactly as you would type them in a terminal.
- **`*_python`**: the same steps through the package's Python API.

| Notebook | What it does |
| --- | --- |
| `01_preparer_cli` / `01_preparer_python` | Build the pixel geometry, background and normalization CSVs. **Run one of these first.** |
| `02_analyze_nexus_cli` / `02_analyze_nexus_python` | Analyze a completed event NeXus file |
| `03_analyze_adara_file_cli` / `03_analyze_adara_file_python` | Analyze a recorded ADARA packet file |
| `04_analyze_adara_stream_cli` / `04_analyze_adara_stream_python` | Analyze a live ADARA stream: a local replay of recorded data, or NOMAD itself |

The preparer step is the same whichever source you analyze afterwards, so it has one notebook per
method rather than one per source. All outputs go to `notebooks/output/`, which git ignores.

## Data

| Role | Format | Run | ONCat title | Duration | Proton charge | Location |
| --- | --- | --- | --- | --- | --- | --- |
| Sample | NeXus | NOM_243708 | Diamond at temperature 300.0 K | 2:44 | 0.23 C | `nexus_files/diamond/NOM_243708.nxs.h5` (1.7 GB) |
| Sample (optional) | NeXus | NOM_243709 | Diamond at temperature 300.0 K | 26:55 | 2.00 C | `nexus_files/diamond/NOM_243709.nxs.h5` (15.4 GB) |
| Background | NeXus | NOM_243710 | MT_6mm_PAC at temperature 300.01 K (empty PAC06 can) | 24:02 | 2.00 C | `nexus_files/mt_pac_can/NOM_243710.nxs.h5` |
| Background | NeXus | NOM_243711 | MT_6mm_PAC at temperature 299.99 K (empty PAC06 can) | 24:03 | 2.00 C | `nexus_files/mt_pac_can/NOM_243711.nxs.h5` |
| Normalization | NeXus | NOM_243712 | V_rod at temperature 300.01 K (vanadium rod) | 24:03 | 2.00 C | `nexus_files/vanadium/NOM_243712.nxs.h5` (12 GB) |
| Sample | ADARA | 244114 | Diamond at temperature 299.99 K | 4:48 | 0.40 C | `../nomad-adara-carbon-share/20260619-140639.403016382-run-244114/` (16 files, 3.2 GB) |
| Calibration | HDF5 | 243451 | NOMAD shifter calibration, 2026-06-09 | | | `tests/data/calibration/NOMAD_243451_2026-06-09_shifter.h5` |

The NeXus runs are IPTS-37412 (2026-06-12); the ADARA run is IPTS-37098 (2026-06-19). Both diamond
samples sit in a PAC06 can, so one empty-can background and one vanadium normalization serve both.
Titles, durations and charges are from `oncat-nomad.csv` in the repository root. `nexus_files/`
sits in the repository root; the carbon share is the NFS mount described in the top-level README,
next to the repository.

## Running them

From the repository root:

```bash
uv sync --group jupyter
uv run --group jupyter jupyter lab notebooks/
```

In VS Code, open a notebook and pick the repository's `.venv` as the kernel, after running
`uv sync --group jupyter` once. Keep passing `--group jupyter` to `uv run` and `uv sync`: without
it, uv syncs back to the default groups and removes the Jupyter packages.

The committed notebooks include outputs from one run, so you can read them without running
anything. The embedded live plot is the exception: it only shows while its analyzer is running.

## The live plot inside the notebook

The analyzer's browser live plot is served by a small HTTP server on its own thread, and the page
polls that server for updates. The page needs nothing from the notebook kernel, so the analyze
notebooks start the analyzer as a background process and embed the page in a cell output, where it
keeps updating while other cells run. Each analyze notebook has a `LIVE_PLOT_INLINE` switch in its
setup cell. Set it to `False` to open the page in a browser tab instead.

The helpers behind this live in `live_stream_analysis.notebook`:

```python
from live_stream_analysis.notebook import start_analysis

run = start_analysis(
    nexus_file=["nexus_files/diamond/NOM_243708.nxs.h5"],
    histogram_pixel_geometry_csv="notebooks/output/pixel_geometry_calibrated.csv",
    histogram_output_csv="notebooks/output/iq.csv",
)
run.show()   # live plot in the cell output
run.wait()   # until the analysis finishes
run.stop()   # shut down the plot server
```

`start_analysis` takes the CLI options as keywords and validates them with the CLI's own parser.
`run_command("live_stream_analysis analyze ...")` does the same for a shell command string.

Things to know:

- The page loads Chart.js from a CDN, so the browser needs internet access.
- The frame points at `http://127.0.0.1:<port>/`. That works for Jupyter running on your own
  machine. With VS Code Remote, forward the port in the Ports panel if it is not forwarded
  automatically. A JupyterHub on another machine cannot reach it; use `LIVE_PLOT_INLINE = False`
  and an SSH tunnel there.
- The CLI notebooks use fixed ports 8801–8803, so two of them cannot run at once. The Python
  notebooks pick a free port.

## Runtimes

Measured on one workstation, reading NeXus from local disk and ADARA from the NFS share:

| Notebook | Data | Time |
| --- | --- | --- |
| `01_preparer_*` | 4 GB background + 12 GB vanadium | about 1 minute |
| `02_analyze_nexus_*` | NOM_243708, 232 M events | about 4 minutes |
| `03_analyze_adara_file_*` | one 210 MB ADARA file | about 1 minute; up to 6 minutes on a first, uncached NFS read |
| `04_analyze_adara_stream_*` | two ADARA files (386 MB) | about 4 minutes, longer on a first NFS read |

Reading ADARA from the NFS share is I/O bound: one uncached file took 5 min 55 s of wall time but
only 45 s of CPU. Copy the files to local disk to go faster.
