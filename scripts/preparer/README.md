# Mantid-based preparer scripts

This directory contains optional scripts that require Mantid.

The `live_stream_analysis` package intentionally stays pure-Python and does not declare Mantid as a dependency.
Use a separate environment only when running these scripts.

IDF XML files are stored in `tests/data/idf` inside this repository.

## Option 1: Use a Pixi environment in this repository

From the `live_stream_analysis` repository root:

```bash
pixi install
pixi add --channel conda-forge --channel mantid mantid matplotlib
python scripts/preparer/mantid_iq_from_idf.py --idf-file tests/data/idf/NOMAD_Definition.xml
python scripts/preparer/mantid_iq_from_idf.py --idf-file tests/data/idf/NOMAD_Definition.xml --plot
```

## Option 2: Create a standalone Conda environment

If you do not use Pixi, create a separate environment just for this script:

```bash
conda create -n mantid-preparer -c conda-forge -c mantid mantid matplotlib python=3.11
conda activate mantid-preparer
python scripts/preparer/mantid_iq_from_idf.py --idf-file tests/data/idf/NOMAD_Definition.xml
```

## Script location

- Script: `scripts/preparer/mantid_iq_from_idf.py`
- Default IDF: `tests/data/idf/NOMAD_Definition.xml` (resolved from repository structure)
