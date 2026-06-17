from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt


DEFAULT_COLORS = ["tab:blue", "tab:orange", "tab:green", "tab:red", "tab:brown", "tab:pink"]


def parse_histogram_csv(path: Path) -> tuple[list[float], list[float], list[float]]:
    q_values: list[float] = []
    intensity: list[float] = []
    error: list[float] = []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required_columns = {"Q value", "I(Q)", "Error I(Q)"}
        if reader.fieldnames is None or not required_columns.issubset(set(reader.fieldnames)):
            raise ValueError("CSV must include columns: 'Q value', 'I(Q)', 'Error I(Q)'")

        for row in reader:
            q_values.append(float(row["Q value"]))
            intensity.append(float(row["I(Q)"]))
            error.append(float(row["Error I(Q)"]))

    if not q_values:
        raise ValueError(f"No histogram data found in {path}")

    return q_values, intensity, error


def _filter_rows(
    q_values: list[float],
    intensity: list[float],
    error: list[float],
    x_min: float | None,
    x_max: float | None,
) -> tuple[list[float], list[float], list[float]]:
    filtered = [
        (q_value, y_value, err_value)
        for q_value, y_value, err_value in zip(q_values, intensity, error, strict=True)
        if (x_min is None or q_value >= x_min) and (x_max is None or q_value <= x_max)
    ]
    if not filtered:
        raise ValueError("No rows remain after applying the requested Q range")

    return map(list, zip(*filtered, strict=True))


def _plot_series(
    axis: plt.Axes,
    q_values: list[float],
    intensity: list[float],
    error: list[float],
    label: str,
    color: str,
) -> None:
    lower = [y_value - err_value for y_value, err_value in zip(intensity, error, strict=True)]
    upper = [y_value + err_value for y_value, err_value in zip(intensity, error, strict=True)]
    axis.plot(q_values, intensity, color=color, linewidth=1.5, label=label)
    axis.fill_between(q_values, lower, upper, color=color, alpha=0.2)


def _default_label(path: Path) -> str:
    return path.stem.replace("_", " ")


def _show_figure() -> None:
    backend = matplotlib.get_backend().lower()
    if backend == "agg" or backend.endswith(":agg"):
        print("Matplotlib is using a non-interactive backend; skipping on-screen display.")
        return
    plt.show()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plot one or more three-column histogram CSV files with shaded error bars.")
    parser.add_argument(
        "input_csv",
        nargs="?",
        help="Single input CSV with columns: Q value, I(Q), Error I(Q).",
    )
    parser.add_argument(
        "--input",
        dest="input_csvs",
        action="append",
        default=[],
        help="Additional input CSV. Repeat to compare multiple files.",
    )
    parser.add_argument(
        "--label",
        dest="labels",
        action="append",
        default=[],
        help="Legend label for each input, in the same order as the files.",
    )
    parser.add_argument(
        "--mode",
        choices=("overlay", "subplots"),
        default="overlay",
        help="Comparison layout when plotting multiple inputs.",
    )
    parser.add_argument(
        "--output-png",
        default=None,
        help="Optional output image path. If omitted, an interactive window is shown without saving.",
    )
    parser.add_argument(
        "--title",
        default="Histogram CSV",
        help="Plot title.",
    )
    parser.add_argument(
        "--x-min",
        type=float,
        default=None,
        help="Optional minimum Q to display.",
    )
    parser.add_argument(
        "--x-max",
        type=float,
        default=None,
        help="Optional maximum Q to display.",
    )
    parser.add_argument(
        "--line-color",
        default=None,
        help="Matplotlib color for single-input plots.",
    )
    parser.add_argument(
        "--hide-error-band",
        action="store_true",
        help="Plot only the line without the shaded error band.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_paths = [Path(args.input_csv)] if args.input_csv else []
    input_paths.extend(Path(path) for path in args.input_csvs)
    if not input_paths:
        raise ValueError("Provide at least one input CSV via the positional argument or --input")

    if args.labels and len(args.labels) != len(input_paths):
        raise ValueError("If --label is provided, it must be repeated once per input CSV")

    labels = args.labels or [_default_label(path) for path in input_paths]
    series: list[tuple[str, list[float], list[float], list[float]]] = []
    for label, input_path in zip(labels, input_paths, strict=True):
        q_values, intensity, error = parse_histogram_csv(input_path)
        q_values, intensity, error = _filter_rows(q_values, intensity, error, args.x_min, args.x_max)
        series.append((label, q_values, intensity, error))

    if args.mode == "subplots" and len(series) > 1:
        figure, axes = plt.subplots(len(series), 1, figsize=(12, 3.5 * len(series)), sharex=True)
        axes_list = list(axes.flat) if hasattr(axes, "flat") else [axes]
        for index, (axis, (label, q_values, intensity, error)) in enumerate(zip(axes_list, series, strict=True)):
            color = DEFAULT_COLORS[index % len(DEFAULT_COLORS)]
            _plot_series(axis, q_values, intensity, error, label, color)
            if args.hide_error_band:
                axis.collections.clear()
            axis.set_ylabel("I(Q)")
            axis.set_title(label)
            axis.grid(True, alpha=0.3)
            axis.legend(loc="best")
        axes_list[-1].set_xlabel("Q")
        figure.suptitle(args.title)
        figure.tight_layout()
    else:
        figure, axis = plt.subplots(figsize=(12, 6))
        for index, (label, q_values, intensity, error) in enumerate(series):
            color = args.line_color if len(series) == 1 and args.line_color else DEFAULT_COLORS[index % len(DEFAULT_COLORS)]
            _plot_series(axis, q_values, intensity, error, label, color)
            if args.hide_error_band:
                axis.collections.clear()
        axis.set_title(args.title)
        axis.set_xlabel("Q")
        axis.set_ylabel("I(Q)")
        axis.grid(True, alpha=0.3)
        if len(series) > 1:
            axis.legend(loc="best")
        figure.tight_layout()

    if args.output_png:
        output_path = Path(args.output_png)
        figure.savefig(output_path, dpi=200)
        print(f"Saved plot to {output_path}")

    _show_figure()


if __name__ == "__main__":
    main()