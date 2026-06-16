# live stream analysis

Repository for working on live stream analysis

## IDF Pre-Processing Helper

The package includes a pure-Python helper CLI subcommand for pre-processing Mantid IDF XML files.
It generates:

1. Pixel geometry CSV with columns: pixel id, L2 value, theta value.
2. Synthetic I(Q) CSV from a TOF-to-Q conversion workflow.

Example:

```bash
uv run live_stream_analysis preparer \
    --idf-file tests/data/idf/NOMAD_Definition.xml \
    --pixel-geometry-csv pixel_geometry.csv \
    --iq-csv iq.csv
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
    --histogram-q-bin-size 0.02 \
    --histogram-q-max 100 \
    --tof-tick-us 1.0 \
    --histogram-output-txt bram_values_python_all.txt
```

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
