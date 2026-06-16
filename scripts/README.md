# Scripts directory

This directory contains helper and verification scripts used during development.

The core package in `src/live_stream_analysis` is pure Python and does not require Mantid.
Some scripts under `scripts/preparer` are optional verification utilities that depend on Mantid.

## Mantid setup for optional verification scripts

Use a separate conda or mamba environment to avoid coupling Mantid to the main project environment:

```bash
conda create -n mantid-preparer -c conda-forge -c mantid mantid matplotlib python=3.11
conda activate mantid-preparer
python scripts/preparer/mantid_iq_from_idf.py --idf-file tests/data/idf/NOMAD_Definition.xml
```

Optional plotting mode:

```bash
python scripts/preparer/mantid_iq_from_idf.py --idf-file tests/data/idf/NOMAD_Definition.xml --plot
```

For pure-Python preparer and analyzer workflows, use the project uv environment:

```bash
uv sync
uv run live_stream_analysis preparer --help
uv run live_stream_analysis analyze --help
```
