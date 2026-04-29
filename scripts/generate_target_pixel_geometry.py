#!/usr/bin/env python
"""Generate target pixel geometry CSV files for all IDF fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path

from live_stream_analysis.preparer.converter import write_pixel_geometry_csv
from live_stream_analysis.preparer.instrument import build_detector_geometry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate target pixel geometry CSV files for each IDF XML file.",
    )
    parser.add_argument(
        "--idf-dir",
        type=Path,
        default=Path("tests/data/idf"),
        help="Directory containing input IDF XML files.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path("tests/data/target"),
        help="Directory where target CSV files are written.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    idf_dir = args.idf_dir.resolve()
    target_dir = args.target_dir.resolve()

    if not idf_dir.exists():
        raise FileNotFoundError(f"IDF directory does not exist: {idf_dir}")

    idf_paths = sorted(idf_dir.glob("*.xml"))
    if not idf_paths:
        raise FileNotFoundError(f"No IDF XML files found in: {idf_dir}")

    target_dir.mkdir(parents=True, exist_ok=True)

    for idf_path in idf_paths:
        rows = build_detector_geometry(idf_path)
        target_path = target_dir / f"{idf_path.stem}_pixel_geometry.csv"
        write_pixel_geometry_csv(rows, target_path)
        print(target_path)

    print(f"Wrote {len(idf_paths)} target file(s) to {target_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
