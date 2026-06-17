from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib.pyplot as plt


LINE_PATTERN = re.compile(r"^Index:(?P<index>\d+) - Counts:(?P<counts>\d+)$")


def parse_histogram_file(path: Path) -> tuple[list[int], list[int]]:
    indices: list[int] = []
    counts: list[int] = []

    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue

            match = LINE_PATTERN.match(line)
            if match is None:
                raise ValueError(f"Unsupported line format at {path}:{line_number}: {line}")

            indices.append(int(match.group("index")))
            counts.append(int(match.group("counts")))

    if not indices:
        raise ValueError(f"No histogram data found in {path}")

    return indices, counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plot a BRAM histogram text file.")
    parser.add_argument(
        "input_txt",
        nargs="?",
        default="bram_values_python_all.txt",
        help="Histogram text file in 'Index:<i> - Counts:<n>' format.",
    )
    parser.add_argument(
        "--output-png",
        default="bram_values_python_all.png",
        help="Output image path for the plot.",
    )
    parser.add_argument(
        "--max-bin",
        type=int,
        default=None,
        help="Optional maximum bin index to display.",
    )
    parser.add_argument(
        "--q-bin-size",
        type=float,
        default=0.02,
        help="Q width represented by each histogram bin. Used for the default x-axis.",
    )
    parser.add_argument(
        "--x-axis",
        choices=("q", "bin"),
        default="q",
        help="Plot against Q values or raw bin indices.",
    )
    parser.add_argument(
        "--style",
        choices=("line", "bar"),
        default="line",
        help="Plot style.",
    )
    parser.add_argument(
        "--title",
        default="BRAM Histogram",
        help="Plot title.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input_txt)
    output_path = Path(args.output_png)

    indices, counts = parse_histogram_file(input_path)

    if args.max_bin is not None:
        filtered = [(index, count) for index, count in zip(indices, counts, strict=True) if index <= args.max_bin]
        if not filtered:
            raise ValueError(f"No bins remain after applying --max-bin {args.max_bin}")
        indices, counts = map(list, zip(*filtered, strict=True))

    if args.x_axis == "q":
        x_values = [index * args.q_bin_size for index in indices]
        x_label = "Q"
        plot_width = args.q_bin_size if args.style == "bar" else 1.0
    else:
        x_values = indices
        x_label = "Bin index"
        plot_width = 1.0

    plt.figure(figsize=(12, 6))
    if args.style == "bar":
        plt.bar(x_values, counts, width=plot_width)
    else:
        plt.plot(x_values, counts)

    plt.title(args.title)
    plt.xlabel(x_label)
    plt.ylabel("Counts")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)

    print(f"Saved plot to {output_path}")


if __name__ == "__main__":
    main()