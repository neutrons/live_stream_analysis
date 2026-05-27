# live stream analysis

Repository for working on live stream analysis 

## IDF Pre-Processing Helper

The package includes a pure-Python helper CLI subcommand for pre-processing Mantid IDF XML files.
It generates:

1. Pixel geometry CSV with columns: pixel id, L2 value, theta value.
2. Synthetic I(Q) CSV from a TOF-to-Q conversion workflow.

Example:

```bash
pixi run live_stream_analysis preparer \
    --idf-file tests/data/idf/NOMAD_Definition.xml \
    --pixel-geometry-csv pixel_geometry.csv \
    --iq-csv iq.csv
```

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
pixi run python
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

### Add an access token to anaconda

Here we assume your intent is to upload the conda package to the [anaconda.org/neutrons](https://anaconda.org/neutrons) organization.
An administrator of `anaconda.org/neutrons` must create an access token for your repository in the [access settings](https://anaconda.org/neutrons/settings/access).

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

The default package publishing service is anaconda.
However, we also support PyPI publishing as well.

### Instruction for publish to PyPI

1. Make sure you have the correct access to the project on PyPI.
1. Make sure `git status` returns a clean state.
1. At the root of the repo, use `python -m build` to generate the wheel.
1. Check the wheel with `twine check dist/*`, everything should pass before we move to next step.
1. When doing manual upload test, make sure to use testpypi instead of pypi.
1. Use `twine upload --repository testpypi dist/*` to upload to testpypi, you will need to specify the testpipy url in your `~/.pypirc`, i.e.

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

Publishing to Anaconda is handled via workflow, `package.yml`.
If your target channel is not `neutrons`, make sure change it in the `package_pixi.yml` file.

## Development environment setup

### Build development environment

1. By default, we recommend providing a single `environment.yml` that covers all necessary packages for development.
2. The runtime dependency should be in `meta.yaml` for anaconda packaging, and `pyproject.toml` for PyPI publishing.
3. When performing editable install for your feature branch, make sure to use `pip install --no-deps -e .` to ensure that `pip` does not install additional packages from `pyproject.toml` into development environment by accident.

## Pixi

Pixi is a tool that helps to manage the project's dependencies and environment.
This project uses Pixi with `pyproject.toml` as the single source of truth for dependencies and packaging.

### How to use Pixi

1. Install `pixi` by running `curl -fsSL https://pixi.sh/install.sh | bash` (or following the instruction on the [official website](https://pixi.sh/))
1. If planning to build the conda package locally, you need to configure the `pixi` to use the `detached-environments` as `conda build` will fail if the environment is in the source tree (which `pixi` does by default).
    2.1. Run `pixi config set detached-environments true`
    2.2. Make sure to commit the config file `.pixi/config.toml` to the repository (it is ignored by default).
1. Run `pixi install` to install the dependencies.
1. Adjust the tasks in `pyproject.toml` to match your project's needs.
   3.1. Detailed instructions on adding tasks can be found in the [official documentation](https://pixi.sh/latest/features/tasks/).
   3.2. You can use `pixi run` to see available tasks, and use `pixi run <task-name>` to run a specific task (note: if the selected task has dependencies, they will be run first).

    ```bash
    ❯ pixi run

    Available tasks:
            conda-build
            build-docs
            build-pypi
            clean-all
            clean-conda
            clean-docs
            clean-pypi
            conda-publish
            publish-pypi
            test
    ```

1. Remember to remove the GitHub actions that still use `conda` actions.

### Pixi environment location

By default, `pixi` will create a virtual environment in the `.pixi` directory at the root of the repository.
However, when setting `detached-environments` to `true`, `pixi` will create the virtual environment in the cache directory (see [official documentation](https://pixi.sh/latest/features/environment/#caching-packages) for more information).
If you want to keep your environment between sessions, you should add the following lines to your `.bashrc` or `.bash_profile`:

```bash
export PIXI_CACHE_DIR="$HOME/.pixi/cache"
```

### Known issues

On SNS Analysis systems, the `pixi run conda-build` task may fail due to `sqlite3` file locking issue.
This is most likely due to the user directory being a shared mount, which interfering with `pixi` and `conda` environment locking.
