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

## Plotting histogram CSV output

The analyzer writes three-column CSV files with this schema:

```text
Q value,I(Q),Error I(Q)
0.01000000,21381.00000000,146.22243300
```

To plot that output, install the optional plotting dependencies and run:

```bash
uv sync --group jupyter
uv run --group jupyter python scripts/plot_csv_histogram.py analyze_histogram.csv
```

Use `--show-relative-uncertainty`, `--x-max`, and repeated `--input` arguments
to compare corrected sample, background, and normalization outputs in one view.
