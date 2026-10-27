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

By default the notebooks use the **tutorial dataset** in `tests/data/tutorial/`: the first part of
each real run, cut by time, small enough to keep in the repository and to run anywhere in seconds.
Reduced together, it reproduces the full-data I(Q) to 95.9 %, with every strong diamond peak within
0.002 1/Å of its full-data position. `tests/data/tutorial/README.md` describes the files, and
`scripts/tutorial_dataset/README.md` how their sizes were chosen.

| Role | Run (ONCat title) | Tutorial file, in `tests/data/tutorial/` | Full run |
| --- | --- | --- | --- |
| Sample, NeXus | NOM_244114 (Diamond at temperature 299.99 K) | `NOM_244114.nxs.h5`: first 0.67 s, 4.8 MB | 4:48, 0.40 C: `nexus_files/diamond/NOM_244114.nxs.h5` (3.3 GB) |
| Sample, ADARA | 244114, the same run as streamed by the SMS | `m00000001-f00000001-run-244114.adara`: the same 0.67 s, closed by the run's END_RUN, 7.9 MB | 16 files, 3.2 GB, on the carbon NFS share |
| Background | NOM_243710 (MT_6mm_PAC: empty PAC06 can) | `NOM_243710.nxs.h5`: first 2.5 s, 3.0 MB | 24:02, 2.00 C, plus NOM_243711 (24:03, 2.00 C): `nexus_files/mt_pac_can/` |
| Normalization | NOM_243712 (V_rod: vanadium rod) | `NOM_243712.nxs.h5`: first 0.5 s, 3.2 MB | 24:03, 2.00 C: `nexus_files/vanadium/NOM_243712.nxs.h5` (12 GB) |
| Calibration | 243451, 2026-06-09 | `tests/data/calibration/NOMAD_243451_2026-06-09_shifter.h5` | the same file |

The diamond sample is run 244114 (IPTS-37098, 2026-06-19) in both formats: the NeXus file written
when the run ended, and the ADARA packets the SMS streamed while it ran. The background and
vanadium are IPTS-37412 standards from 2026-06-12, measured with the same PAC06 can type, since
none were measured during IPTS-37098. The 2026-06-09 calibration is the newest one before either;
on run 244114 it leaves the diamond peaks a nearly constant 0.43 % low in Q.

To use the full runs, set `FULL_DATA = True` in a Python notebook's setup cell (`FULL_RUN = True`
in the stream notebooks); each CLI notebook lists the paths to swap in. The full runs need
`nexus_files/` in the repository root and the carbon NFS share next to it (see the "ADARA data on
ORC NFS shares" section of `docs/reference.md`). Titles, durations and charges are from
`oncat-nomad.csv` in the repository root.

## Installing

The notebooks need the repository, for its tutorial data, and a Python environment (3.10 or newer)
with `live_stream_analysis` and Jupyter installed. With Python's own `venv` and `pip`:

```bash
git clone https://github.com/neutrons/live_stream_analysis.git
cd live_stream_analysis
python3 -m venv env
source env/bin/activate            # on Windows: env\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e . jupyterlab
```

`-e` installs the package from your clone, so a `git pull` updates it too. Then, with the
environment still activated:

```bash
jupyter lab notebooks/
```

In VS Code, open a notebook and pick the `env` interpreter as its kernel.

### With uv

[uv](https://docs.astral.sh/uv/) is what the project's developers and CI use. It installs the
exact versions in `uv.lock` into `.venv`:

```bash
uv sync --group jupyter
uv run --group jupyter jupyter lab notebooks/
```

Keep passing `--group jupyter` to `uv run` and `uv sync`: without it, uv syncs back to the default
groups and removes the Jupyter packages. In VS Code, pick `.venv` as the kernel.

## Running them

Run `01_preparer_cli` or `01_preparer_python` first; the analyze notebooks read what it writes to
`notebooks/output/`. Each notebook's first code cell checks the environment and stops with
instructions if `live_stream_analysis` is not installed for its kernel.

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
    nexus_file=["tests/data/tutorial/NOM_244114.nxs.h5"],
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

On the tutorial dataset every notebook runs in under a minute. On the full runs, measured on one
workstation reading NeXus from local disk and ADARA from the NFS share:

| Notebook | Full data | Time |
| --- | --- | --- |
| `01_preparer_*` | 4 GB background + 12 GB vanadium | about 1 minute |
| `02_analyze_nexus_*` | NOM_244114, 412 M events | about 4 minutes |
| `03_analyze_adara_file_*` | one 210 MB ADARA file | about 1 minute; up to 6 minutes on a first, uncached NFS read |
| `04_analyze_adara_stream_*` | all 16 ADARA files (3.2 GB) | an estimated half an hour, scaled from a two-file replay |

Reading ADARA from the NFS share is I/O bound: one uncached file took 5 min 55 s of wall time but
only 45 s of CPU. Copy the files to local disk to go faster.
