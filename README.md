# live stream analysis

Repository for working on live stream analysis

## IDF Pre-Processing Helper

The package includes a pure-Python helper CLI subcommand for pre-processing Mantid IDF XML files.
It generates:

1. Pixel geometry CSV with columns: pixel id, L2 value, theta value, TOF-to-Q matrix element.
2. Synthetic I(Q) CSV from a TOF-to-Q conversion workflow.

Example:

```bash
uv run live_stream_analysis preparer \
    --idf-file tests/data/idf/NOMAD_Definition.xml \
    --pixel-geometry-csv pixel_geometry.csv \
    --iq-csv iq.csv
```

### Optional: apply Mantid diffraction calibration

You can optionally pass a Mantid Diffraction Calibration HDF5 file (`SaveDiffCal` format)
to generate a calibration-aware pixel geometry CSV.

When `--calibration-file` is supplied, the pixel geometry CSV adds these columns:

1. `difc`
2. `difa`
3. `tzero`
4. `use`

The analyzer will use these values for TOF+pixel to Q conversion via:

1. `TOF = DIFA*d^2 + DIFC*d + TZERO`
2. `Q = 2*pi/d`

and it treats `use=0` as masked (detector excluded from histogramming).

Example with one of the provided calibration files:

```bash
uv run live_stream_analysis preparer \
    --idf-file tests/data/idf/NOMAD_Definition.xml \
    --calibration-file tests/data/calibration/NOMAD_243451_2026-06-09_shifter.h5 \
    --pixel-geometry-csv pixel_geometry_calibrated.csv \
    --iq-csv iq.csv
```

Then use the calibrated geometry directly in analyzer:

```bash
uv run live_stream_analysis analyze \
    --adara-file /path/to/file.adara \
    --histogram-pixel-geometry-csv pixel_geometry_calibrated.csv \
    --histogram-q-min 0.6 \
    --histogram-q-bin-size 0.02 \
    --histogram-q-max 100 \
    --tof-tick-us 1.0 \
    --histogram-output-csv sample_histogram.csv
```

For ADARA histogram workflows where TOF ticks are 0.1 microseconds, pre-scale the
TOF-to-Q matrix element by 10 during CSV generation:

```bash
uv run live_stream_analysis preparer \
    --idf-file tests/data/idf/NOMAD_Definition.xml \
    --pixel-geometry-csv pixel_geometry.csv \
    --iq-csv iq.csv \
    --q-matrix-scale 10
```

Then run histogram analysis from an ADARA file (or use `--adara-stream HOST PORT`):

```bash
uv run live_stream_analysis analyze \
    --adara-file /path/to/file.adara \
    --histogram-pixel-geometry-csv pixel_geometry.csv \
    --histogram-q-min 0.6 \
    --histogram-q-bin-size 0.02 \
    --histogram-q-max 100 \
    --tof-tick-us 1.0 \
    --histogram-output-csv sample_histogram.csv
```

For a full local example with background subtraction, normalization, live plotting,
and progress logging enabled:

```bash
uv run live_stream_analysis analyze \
    --adara-file adara_mount/20250201/adara_streams/NOMAD.Raw.Data.Runs.208511-208543/20250131-101613.350178410-run-208511/m00000001-f00000001-run-208511.adara \
    --histogram-pixel-geometry-csv pixel_geometry.csv \
    --histogram-q-bin-size 0.02 \
    --histogram-q-max 30 \
    --tof-tick-us 1.0 \
    --background-subtraction background.csv \
    --normalization normalization.csv \
    --histogram-output-csv analyze_histogram.csv \

    --live-plot-mode desktop \
    --live-plot-refresh-every 1000 \
    --log-level INFO \
    --event-log-interval 100000
```

For Docker, use browser live plot mode instead of desktop mode so the plot is visible from the host browser:

```bash
docker build -t live-stream-analysis .
docker run --rm -p 8000:8000 -v "$PWD":/work -w /work live-stream-analysis \
    live_stream_analysis analyze \
    --adara-file adara_mount/20250201/adara_streams/NOMAD.Raw.Data.Runs.208511-208543/20250131-101613.350178410-run-208511/m00000001-f00000001-run-208511.adara \
    --histogram-pixel-geometry-csv pixel_geometry.csv \
    --histogram-q-bin-size 0.02 \
    --histogram-q-max 30 \
    --tof-tick-us 1.0 \
    --background-subtraction background.csv \
    --normalization normalization.csv \
    --histogram-output-csv analyze_histogram.csv \
    --live-plot-mode browser \
    --live-plot-host 0.0.0.0 \
    --live-plot-port 8000 \
    --live-plot-no-open-browser \
    --live-plot-refresh-every 1000 \
    --log-level INFO \
    --event-log-interval 100000
```

Then open `http://localhost:8000` in your browser.

If you prefer Docker Compose, the repository includes [docker-compose.yml](/home/ntm/projects/illumine/live_stream_analysis/docker-compose.yml):

```bash
docker compose --profile adara-file up --build
```

That starts the ADARA file browser live-plot workflow and publishes it on `http://localhost:8000`.
Because the compose file now uses profiles, `docker compose up` by itself will not start any of the three analysis services.

The browser page now includes a small status panel with live plot update count, total counts,
nonzero bins, peak intensity, and mean relative uncertainty so you can see progress at a glance.

The same `analyze` command also accepts event NeXus sample inputs. Repeat
`--nexus-file` to combine multiple sample runs while keeping the same histogram,
background-subtraction, normalization, and CSV output path:

```bash
uv run live_stream_analysis analyze \
    --nexus-file nexus_files/diamond/NOM_243708.nxs.h5 \
    --nexus-file nexus_files/diamond/NOM_243709.nxs.h5 \
    --histogram-pixel-geometry-csv pixel_geometry.csv \
    --histogram-q-min 0.6 \
    --histogram-q-bin-size 0.02 \
    --histogram-q-max 30 \
    --tof-tick-us 1.0 \
    --background-subtraction background.csv \
    --normalization normalization.csv \
    --histogram-output-csv analyze_histogram.csv
```

For a browser live-plot version of the same two-file NeXus workflow in Docker Compose:

```bash
docker compose --profile nexus up --build
```

That publishes the NeXus browser plot on `http://localhost:8001`.

For a browser live-plot workflow against the default NOMAD live ADARA stream endpoint:

```bash
docker compose --profile adara-stream up --build
```

That service defaults to `--adara-stream bl1b-daq1.sns.gov 31415` and publishes the browser plot on `http://localhost:8002`.
It requires DNS/network access to the SNS endpoint, so use the SNS network or VPN when running it.

Available profiles are:

1. `adara-file` for the mounted ADARA file example on port `8000`
2. `nexus` for the two-file NeXus example on port `8001`
3. `adara-stream` for the live NOMAD stream example on port `8002`

To run the same analysis inside Docker, mount the directory that contains your
ADARA file, pixel geometry CSV, and desired output path into the container.
From the repository root, this works with the current image:

```bash
docker build -t live-stream-analysis .
docker run --rm -v "$PWD":/work -w /work live-stream-analysis \
    live_stream_analysis analyze \
    --adara-file adara_mount/20250201/adara_streams/NOMAD.Raw.Data.Runs.208511-208543/20250131-101613.350178410-run-208511/m00000001-f00000001-run-208511.adara \
    --histogram-pixel-geometry-csv pixel_geometry.csv \
    --histogram-q-min 0.6 \
    --histogram-q-bin-size 0.02 \
    --histogram-q-max 30 \
    --tof-tick-us 1.0 \
    --histogram-output-csv sample_histogram.csv
```

Without the `-v "$PWD":/work -w /work` mount, the container cannot see files
from your host filesystem, so relative paths like `adara_mount/...` will fail.

To inspect the generated histogram CSV interactively, install the optional plotting
dependencies and run:

```bash
uv sync --group jupyter
uv run --group jupyter python scripts/plot_csv_histogram.py sample_histogram.csv
```

If you also want to save the current view to disk, add `--output-png`:

```bash
uv run --group jupyter python scripts/plot_csv_histogram.py sample_histogram.csv --output-png sample_histogram.png
```

To inspect whether the propagated uncertainty looks reasonable, add a second panel
showing relative uncertainty, `sigma / I(Q)`:

```bash
uv run --group jupyter python scripts/plot_csv_histogram.py corrected_histogram.csv --show-relative-uncertainty
```

If the corrected signal is close to zero in part of the range, the relative
uncertainty can spike and dominate the panel. To focus on bins with a more
stable denominator, mask points where `|I(Q)|` is too small:

```bash
uv run --group jupyter python scripts/plot_csv_histogram.py \
    corrected_histogram.csv \
    --show-relative-uncertainty \
    --relative-uncertainty-min-abs-intensity 2.0
```

To compare sample, background, normalization, and corrected outputs on one figure,
either overlay them:

```bash
uv run --group jupyter python scripts/plot_csv_histogram.py \
    --input sample_histogram.csv \
    --input background.csv \
    --input normalization.csv \
    --input corrected_histogram.csv \
    --label Sample \
    --label Background \
    --label Normalization \
    --label Corrected \
    --mode overlay
```

or split them into stacked subplots when the scales differ too much:

```bash
uv run --group jupyter python scripts/plot_csv_histogram.py \
    --input sample_histogram.csv \
    --input background.csv \
    --input normalization.csv \
    --input corrected_histogram.csv \
    --label Sample \
    --label Background \
    --label Normalization \
    --label Corrected \
    --mode subplots
```

For the low-Q diagnostic view, it is often useful to combine both ideas:
overlay the curves, zoom into the low-Q region, and mask unstable relative
uncertainty bins where the corrected intensity is nearly zero:

```bash
uv run --group jupyter python scripts/plot_csv_histogram.py \
    --input sample_histogram.csv \
    --input background.csv \
    --input normalization.csv \
    --input corrected_histogram.csv \
    --label Sample \
    --label Background \
    --label Normalization \
    --label Corrected \
    --mode overlay \
    --x-max 1.5 \
    --show-relative-uncertainty \
    --relative-uncertainty-min-abs-intensity 2.0 \
    --error-alpha 0.12
```

The plotting script uses Q on the x-axis and includes shaded error bands by default.
Use `--x-min`, `--x-max`, `--hide-error-band`, `--error-alpha`,
`--show-relative-uncertainty`, or `--relative-uncertainty-min-abs-intensity`
to simplify or diagnose the view when needed.

If you use an unscaled pixel geometry CSV, set `--tof-tick-us 0.1` instead.

Note: current parser coverage is validated for NOMAD IDF fixtures. A POWGEN fixture is included in tests for parser study/regression coverage.

# Accessing Adara streams for NOMAD Fe2O3 (Linux)

If you want to use the ORC Shares NFS, you can:
  A.1. Go into "Share" tab, create share w/ "NFS" share type + sub-type of "Perf-NFS" and pick the amount of storage (I did all 300GB available)
  A.2. After it is created, add a "Rule" to share; I did "ip" and just let it be "0.0.0.0/0" for all IP to mount it (note: 0.0.0.0 won't work, the "/0" is required but UI doesn't tell you)
  A.3. Click on the Share in the UI and make note / copy  the "Path" w/ an IP address/path format (edited) 

Then, on a server (either an existing one or creating one; I created one and used Ubuntu 24.04):
  B.1. Add tools via `sudo apt install nfs-common` to mount NFS
  B.2. Create a directory i.e. /path_for_mount
  B.3. Mount the share via `sudo mount -t nfs <share path from (A.3) above> <path for mount in step (B.2) above>

Then if everything worked, you can add files to the mount!
We tested via then mounting the share using step B.3 to my laptop and read the files I made using the share on the server above

Then, you can use the [readadara](https://github.com/bsobhani/readadara) package by Alex Sohbani:
```
uv run python
```
...and then
```
from readadara import AdaraFileReader

# This will be path in B.2 above and then the `adara_streams/...` path for files on the NFS share
filename = "/path_for_mount/adara_streams/NOMAD.Raw.Data.Runs.208511-208543/20250131-101613.350178410-run-208511/m00000001-f00000001-run-208511.adara")
reader = AdaraFileReader(filename)
for packet in reader.read_generator()
    print(packet.to_dict())
```

## Repository Adjustments

### Optional: Add an access token to Anaconda

If you plan to upload conda artifacts to [anaconda.org/neutrons](https://anaconda.org/neutrons),
an administrator of `anaconda.org/neutrons` must create an access token for your repository in the [access settings](https://anaconda.org/neutrons/settings/access).

After created, the token must be stored in a `repository secret`:

1. Navigate to the main page of the repository on GitHub.com.
1. Click on the "Settings" tab.
1. In the left sidebar, navigate to the "Security" section and select "Secrets and variables" followed by "Actions".
1. Click on the "New repository secret" button.
1. Enter `ANACONDA_TOKEN` for the secret name
1. Paste the Anaconda access token
1. Click on the "Add secret" button
1. Test the setup by creating a release candidate tag,
which will result in a package built and uploaded to `https://anaconda.org/neutrons/mypackagename`

### Add an access token to codecov

Follow the instructions in the [Confluence page](https://ornl-neutrons.atlassian.net/wiki/spaces/NDPD/pages/103546883/Coverage+reports)
to create the access token.

## Packaging building instructions

The default packaging flow in this repository is uv + PyPI-style distributions.
Conda publishing is optional and can be handled separately when needed.

### Instruction for publish to PyPI

1. Make sure you have the correct access to the project on PyPI.
1. Make sure `git status` returns a clean state.
1. At the root of the repo, run `uv sync --group package`.
1. Build distributions with `uv run python -m build`.
1. Check the wheel with `uv run twine check dist/*`, everything should pass before we move to next step.
1. When doing manual upload test, make sure to use testpypi instead of pypi.
1. Use `uv run twine upload --repository testpypi dist/*` to upload to testpypi, you will need to specify the testpipy url in your `~/.pypirc`, i.e.

``````
[distutils]
index-servers = pypi, testpypi

[testpypi]
    repository = https://test.pypi.org/legacy/
    username = __token__
    password = YOUR_TESTPYPI_TOKEN

``````

1. Test the package on testpypi with `pip install --index-url https://test.pypi.org/simple/ mypackagename`.
1. If everything is good, use the Github workflow, `package.yml` to trigger the publishing to PyPI.

### Instruction for publish to Anaconda

Publishing to Anaconda is optional and handled via workflow, `package.yml`.
If your target channel is not `neutrons`, update the upload workflow configuration accordingly.

## Development environment setup

### Build development environment

1. Install `uv` from the [official docs](https://docs.astral.sh/uv/getting-started/installation/).
1. Create/update the local environment with `uv sync`.
1. Run tests with `uv run pytest`.
1. For docs, include the docs dependency group: `uv sync --group docs`.
1. For packaging checks, include the package group: `uv sync --group package`.

## UV

This project uses uv for lockfile management and development environments, with `pyproject.toml` as the source of truth for dependencies and packaging.

### How to use UV

1. Install `uv` by following the [official documentation](https://docs.astral.sh/uv/getting-started/installation/).
1. Run `uv sync` to install default development and test dependencies.
1. Run commands in the project environment with `uv run ...`.
1. Include optional groups as needed:
   1. `uv sync --group docs` for documentation tooling.
   1. `uv sync --group package` for build/twine tooling.
   1. `uv sync --group jupyter` for notebook tooling.

    ```bash
    uv sync
    uv run pytest
    uv run live_stream_analysis --help
    ```
