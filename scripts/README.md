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

## Plotting BRAM histogram output

The analyzer can write histogram text files in this format:

```text
Index:0 - Counts:21381
Index:1 - Counts:59916
```

To plot that output, install the optional plotting dependencies and run:

```bash
uv sync --group jupyter
uv run --group jupyter python scripts/plot_bram_histogram.py bram_values_python_all.txt --output-png bram_values_python_all.png --max-bin 500 --q-bin-size 0.02
```

The script plots Q on the x-axis by default. Use `--x-axis bin` for raw bin
indices, `--style bar` for a bar chart, or omit `--max-bin` to plot the full
file.
